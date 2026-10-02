from flask import Flask, render_template, request, redirect, url_for, session, flash, abort, g, has_request_context
import os
import uuid
import secrets
import hashlib
import json
import traceback
import threading
from contextlib import contextmanager
from datetime import timedelta
import psycopg2
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2.extras
import psycopg2.pool
from analysis.video_processor import process_video
from analysis.pose_analysis import generate_feedback
from analysis.point_analyzer import analyze_point_with_ai
from analysis.ball_physics import analyze_point_ball_physics
from analysis.trajectory_viz import render_trajectory_svg

app = Flask(__name__)
# Signs the login cookie. If this leaks, anyone can forge a cookie and sign
# in as any user (including admin), so it lives in the environment, never
# in the code. Changing it signs everyone out once.
app.secret_key = os.environ.get('SECRET_KEY')
if not app.secret_key:
    raise RuntimeError(
        "SECRET_KEY is not set. Set it to a long random string (e.g. in Render's "
        "environment settings, or in your shell before running locally)."
    )
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024
# Keep people signed in for 360 days. Without this, Flask's login cookie only
# lasts until the browser closes — and phones close background browsers
# constantly, so users kept finding themselves logged out.
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=360)

ALLOWED_EXTENSIONS = {'mp4', 'mov', 'avi', 'mkv'}

# Only this account can view the /admin/users page.
ADMIN_EMAIL = 'alexanderchudakov619@gmail.com'

DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. TennisAC now runs on Postgres — set DATABASE_URL "
        "to your database's connection string (e.g. in Render's environment settings, "
        "or in your shell before running locally)."
    )

# A pool of real connections, reused across requests, instead of opening a
# fresh TCP+TLS connection to Postgres on every single request. Sized above
# the gunicorn worker*thread count (see Procfile) so no request ever waits
# on a free connection under normal load.
_pool = psycopg2.pool.ThreadedConnectionPool(
    minconn=1,
    maxconn=20,
    dsn=DATABASE_URL,
    cursor_factory=psycopg2.extras.RealDictCursor,
)

class DB:
    """Thin wrapper so the rest of the app can keep using the same
    db.execute(...).fetchone()/.fetchall() / db.commit() / db.close() shape
    it used with sqlite3, while actually borrowing a pooled psycopg2
    connection underneath and returning it to the pool on close()."""
    def __init__(self, conn):
        self._conn = conn

    def execute(self, query, params=()):
        cur = self._conn.cursor()
        cur.execute(query, params)
        return cur

    def commit(self):
        self._conn.commit()

    def close(self):
        # Safe to call twice: the request teardown below closes anything a
        # route didn't (e.g. because it raised halfway through).
        if self._conn is None:
            return
        conn, self._conn = self._conn, None
        try:
            if not conn.closed:
                conn.rollback()  # never hand back a connection mid-transaction
            _pool.putconn(conn, close=bool(conn.closed))
        except Exception:
            # The pool must never be the thing that takes a request down.
            try:
                _pool.putconn(conn, close=True)
            except Exception:
                pass

def _healthy_connection():
    """A pooled connection that is actually alive. Neon's free tier suspends
    the database after ~5 idle minutes, which silently kills every pooled
    connection — handing one of those out would 500 the next request."""
    for _ in range(3):
        conn = _pool.getconn()
        try:
            if not conn.closed:
                with conn.cursor() as cur:
                    cur.execute('SELECT 1')
                conn.rollback()
                return conn
        except psycopg2.Error:
            pass
        _pool.putconn(conn, close=True)
    return _pool.getconn()

def get_db():
    db = DB(_healthy_connection())
    if has_request_context():
        g.setdefault('_dbs', []).append(db)
    return db

@app.teardown_request
def _return_db_connections(exc):
    for db in g.pop('_dbs', []):
        db.close()

def init_db():
    db = get_db()
    db.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            name TEXT NOT NULL,
            play_like TEXT,
            utr TEXT,
            player_type TEXT,
            tactical_pref TEXT,
            racquet TEXT,
            dominant_hand TEXT,
            backhand_style TEXT,
            best_shot TEXT,
            point_length TEXT,
            shot_order TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    db.execute('''
        CREATE TABLE IF NOT EXISTS analysis_history (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            shot_type TEXT NOT NULL,
            overall_score REAL,
            overall_label TEXT,
            scores_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # scores_json holds the per-metric breakdown (Contact Height, Arm Extension, etc.)
    # for each saved analysis. Databases created before this column existed need it
    # added on top of their existing table.
    existing_cols = [row['column_name'] for row in db.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'analysis_history'"
    ).fetchall()]
    if 'scores_json' not in existing_cols:
        db.execute('ALTER TABLE analysis_history ADD COLUMN scores_json TEXT')

    # Progress and Admin both filter analysis_history by user_id and sort by
    # created_at — without an index, that's a full table scan on every load,
    # and it gets slower as more users generate more history.
    db.execute('CREATE INDEX IF NOT EXISTS idx_analysis_history_user ON analysis_history (user_id, created_at DESC)')

    # plan gates paid features (the pro side-by-side) — 'free' until a user
    # sets up a payment plan. trial_visitor_id links an account back to the
    # free trial it came from, to measure trial -> signup conversion.
    db.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS plan TEXT NOT NULL DEFAULT 'free'")
    db.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_visitor_id TEXT')

    db.execute('''
        CREATE TABLE IF NOT EXISTS trial_uses (
            id SERIAL PRIMARY KEY,
            visitor_id TEXT NOT NULL,
            ip_hash TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_trial_uses_visitor ON trial_uses (visitor_id)')
    db.execute('CREATE INDEX IF NOT EXISTS idx_trial_uses_ip ON trial_uses (ip_hash)')

    # A snapshot of every analysis delivered (trial or signed-in), behind an
    # unguessable token so it can be shared as a public card at /r/<token>.
    db.execute('''
        CREATE TABLE IF NOT EXISTS result_cards (
            token TEXT PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            visitor_id TEXT,
            shot_type TEXT NOT NULL,
            overall_score REAL,
            overall_label TEXT,
            scores_json TEXT,
            views INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Saved Point Play results, so players and their coaches can revisit
    # them. ball_physics_json keeps the per-shot numbers (speed, spin...).
    db.execute('''
        CREATE TABLE IF NOT EXISTS point_play_history (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            point_result TEXT,
            point_context TEXT,
            analysis_json TEXT,
            ball_physics_json TEXT,
            trajectory_svg TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_point_play_user ON point_play_history (user_id, created_at DESC)')

    # Challenge leaderboard: set when the owner enters this result (shown
    # as first name + last initial); NULL means not entered.
    db.execute('ALTER TABLE result_cards ADD COLUMN IF NOT EXISTS challenge_name TEXT')

    # Coach mode: a coach owns a team; players join it with its code, which
    # lets that coach see their analysis history.
    db.execute('''
        CREATE TABLE IF NOT EXISTS teams (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            coach_id INTEGER NOT NULL REFERENCES users(id),
            join_code TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS team_members (
            team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id),
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (team_id, user_id)
        )
    ''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_team_members_user ON team_members (user_id)')

    # One row per time someone shares their result — the core growth
    # metric is distinct shared cards per 100 cards delivered.
    db.execute('''
        CREATE TABLE IF NOT EXISTS share_events (
            id SERIAL PRIMARY KEY,
            token TEXT NOT NULL REFERENCES result_cards(token),
            method TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    db.commit()
    db.close()

os.makedirs('uploads', exist_ok=True)
init_db()

def hash_password(password):
    return generate_password_hash(password)

def password_matches(stored, password):
    """Accounts made before the switch to salted hashes still store a bare
    SHA-256 hex digest; accept those too (login then upgrades them)."""
    if stored.startswith(('scrypt:', 'pbkdf2:')):
        return check_password_hash(stored, password)
    return stored == hashlib.sha256(password.encode()).hexdigest()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Video analysis (OpenCV + MediaPipe) is the only heavy thing this app does.
# Render's instance has 512 MB, and one analysis peaks around 275 MB, so
# running them one at a time is what keeps several simultaneous uploads from
# crashing the server for everyone. Others wait their turn (they're quick).
_analysis_slot = threading.BoundedSemaphore(1)
ANALYSIS_WAIT_SEC = 150

class ServerBusy(Exception):
    pass

@contextmanager
def uploaded_video(file):
    """Saves the upload, holds the analysis slot while the caller works on
    it, and always deletes the file afterwards — even if analysis raises."""
    ext = file.filename.rsplit('.', 1)[1].lower()
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], f"{uuid.uuid4().hex}.{ext}")
    file.save(filepath)
    try:
        if not _analysis_slot.acquire(timeout=ANALYSIS_WAIT_SEC):
            raise ServerBusy()
        try:
            yield filepath
        finally:
            _analysis_slot.release()
    finally:
        if os.path.exists(filepath):
            os.remove(filepath)

UNREADABLE_VIDEO_MSG = ("We couldn't read that video. Try a different clip — MP4 or MOV, "
                        "under 60 seconds, with you clearly in frame.")

def get_current_user():
    if 'user_id' not in session:
        return None
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = %s', (session['user_id'],)).fetchone()
    db.close()
    return user

def get_visitor_id():
    """A stable anonymous id for this browser, kept in the signed session
    cookie. Makes the session permanent so it survives closing the browser."""
    if 'visitor_id' not in session:
        session['visitor_id'] = uuid.uuid4().hex
        session.permanent = True
    return session['visitor_id']

def client_ip_hash():
    # Render sits behind a proxy, so the real client is the first
    # X-Forwarded-For hop. Only a salted hash is stored, never the raw IP.
    ip = (request.headers.get('X-Forwarded-For') or request.remote_addr or '').split(',')[0].strip()
    return hashlib.sha256(f"{app.secret_key}:{ip}".encode()).hexdigest()

def claim_guest_analyses(db, user_id):
    """Moves every analysis this browser ran as a guest into the account
    that just signed up or logged in, so nothing they did is lost."""
    visitor_id = session.get('visitor_id')
    if not visitor_id:
        return
    cards = db.execute('SELECT * FROM result_cards WHERE visitor_id = %s AND user_id IS NULL ORDER BY created_at',
                       (visitor_id,)).fetchall()
    for card in cards:
        db.execute('UPDATE result_cards SET user_id = %s WHERE token = %s', (user_id, card['token']))
        db.execute(
            '''INSERT INTO analysis_history (user_id, shot_type, overall_score, overall_label, scores_json, created_at)
               VALUES (%s, %s, %s, %s, %s, %s)''',
            (user_id, card['shot_type'], card['overall_score'], card['overall_label'],
             card['scores_json'], card['created_at']),
        )

def create_result_card(db, shot_type, feedback, user_id=None, visitor_id=None):
    token = uuid.uuid4().hex
    db.execute(
        '''INSERT INTO result_cards (token, user_id, visitor_id, shot_type, overall_score, overall_label, scores_json)
           VALUES (%s, %s, %s, %s, %s, %s, %s)''',
        (token, user_id, visitor_id, shot_type, feedback.get('overall_score'),
         feedback.get('overall_label'), json.dumps(feedback.get('scores', {}))),
    )
    return token

def parse_shot_order(shot_order_str):
    if not shot_order_str:
        return ['', '', '', '', '', '']
    parts = shot_order_str.split(', ')
    shots = []
    for part in parts:
        if '. ' in part:
            shots.append(part.split('. ', 1)[1].strip())
        else:
            shots.append(part.strip())
    while len(shots) < 6:
        shots.append('')
    return shots[:6]

@app.before_request
def keep_signed_in():
    # Upgrades sessions created before the 90-day setting existed, so people
    # already signed in don't have to log in again to get it.
    if 'user_id' in session and not session.permanent:
        session.permanent = True

@app.route('/')
def index():
    user = get_current_user()
    if not user:
        return redirect(url_for('try_free'))
    return render_template('index.html', user=user, is_admin=(user['email'] == ADMIN_EMAIL))

@app.route('/try', methods=['GET', 'POST'])
def try_free():
    """Shot analysis as a guest — free, unlimited, no account needed. The
    signup ask (for personalized coaching, Point Play, progress) comes after
    they've seen a result, not before."""
    if get_current_user():
        return redirect(url_for('index'))

    if request.method == 'GET':
        return render_template('index.html', user=None, trial=True)

    file = request.files.get('video')
    shot_type = request.form.get('shot_type', 'serve')
    if shot_type not in ('serve', 'forehand', 'backhand', 'rally'):
        shot_type = 'serve'
    if not file or file.filename == '' or not allowed_file(file.filename):
        return redirect(url_for('try_free'))

    with uploaded_video(file) as filepath:
        metrics = process_video(filepath, shot_type)
    if metrics.get('error'):
        flash(metrics.get('error_message', UNREADABLE_VIDEO_MSG))
        return redirect(url_for('try_free') + '#analyze')

    feedback = generate_feedback(metrics, shot_type)
    visitor_id = get_visitor_id()
    db = get_db()
    db.execute('INSERT INTO trial_uses (visitor_id, ip_hash) VALUES (%s, %s)', (visitor_id, client_ip_hash()))
    token = create_result_card(db, shot_type, feedback, visitor_id=visitor_id)
    db.commit()
    db.close()

    return render_template('results.html', metrics=metrics, feedback=feedback,
                           shot_type=shot_type, user=None, trial=True, card_token=token)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        name = request.form.get('name', '').strip()
        if not email or not password or not name:
            flash('Please fill in all required fields.')
            return render_template('signup.html')
        db = get_db()
        existing = db.execute('SELECT id FROM users WHERE LOWER(email) = %s', (email,)).fetchone()
        if existing:
            flash('An account with that email already exists.')
            db.close()
            return render_template('signup.html')
        db.execute('INSERT INTO users (email, password, name) VALUES (%s, %s, %s)',
                   (email, hash_password(password), name))
        db.commit()
        user = db.execute('SELECT * FROM users WHERE LOWER(email) = %s', (email,)).fetchone()

        # Coming from guest analyses: keep them in their history so signing
        # up doesn't throw away the results that convinced them.
        claim_guest_analyses(db, user['id'])
        if session.get('visitor_id'):
            db.execute('UPDATE users SET trial_visitor_id = %s WHERE id = %s', (session['visitor_id'], user['id']))
        db.commit()
        session['user_id'] = user['id']
        session.permanent = True
        db.close()
        return redirect(url_for('profile_setup'))
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE LOWER(email) = %s', (email,)).fetchone()
        if user and password_matches(user['password'], password):
            if not user['password'].startswith(('scrypt:', 'pbkdf2:')):
                db.execute('UPDATE users SET password = %s WHERE id = %s', (hash_password(password), user['id']))
            claim_guest_analyses(db, user['id'])
            db.commit()
            db.close()
            session['user_id'] = user['id']
            session.permanent = True
            return redirect(url_for('index'))
        db.close()
        flash('Invalid email or password.')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/profile/setup', methods=['GET', 'POST'])
def profile_setup():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    if request.method == 'POST':
        shot_order = (
            f"1. {request.form.get('shot1','')}, "
            f"2. {request.form.get('shot2','')}, "
            f"3. {request.form.get('shot3','')}, "
            f"4. {request.form.get('shot4','')}, "
            f"5. {request.form.get('shot5','')}, "
            f"6. {request.form.get('shot6','')}"
        )
        db = get_db()
        db.execute('''UPDATE users SET play_like=%s, utr=%s, player_type=%s, tactical_pref=%s,
                      racquet=%s, dominant_hand=%s, backhand_style=%s, best_shot=%s,
                      point_length=%s, shot_order=%s WHERE id=%s''',
                   (request.form.get('play_like'), request.form.get('utr'),
                    request.form.get('player_type'), request.form.get('tactical_pref'),
                    request.form.get('racquet'), request.form.get('dominant_hand'),
                    request.form.get('backhand_style'), request.form.get('best_shot'),
                    request.form.get('point_length'), shot_order, user['id']))
        db.commit()
        db.close()
        return redirect(url_for('index'))
    saved_shots = parse_shot_order(user['shot_order'])
    return render_template('profile_setup.html', user=user, saved_shots=saved_shots)

@app.route('/profile/edit', methods=['GET', 'POST'])
def profile_edit():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    if request.method == 'POST':
        shot_order = (
            f"1. {request.form.get('shot1','')}, "
            f"2. {request.form.get('shot2','')}, "
            f"3. {request.form.get('shot3','')}, "
            f"4. {request.form.get('shot4','')}, "
            f"5. {request.form.get('shot5','')}, "
            f"6. {request.form.get('shot6','')}"
        )
        db = get_db()
        db.execute('''UPDATE users SET play_like=%s, utr=%s, player_type=%s, tactical_pref=%s,
                      racquet=%s, dominant_hand=%s, backhand_style=%s, best_shot=%s,
                      point_length=%s, shot_order=%s WHERE id=%s''',
                   (request.form.get('play_like'), request.form.get('utr'),
                    request.form.get('player_type'), request.form.get('tactical_pref'),
                    request.form.get('racquet'), request.form.get('dominant_hand'),
                    request.form.get('backhand_style'), request.form.get('best_shot'),
                    request.form.get('point_length'), shot_order, user['id']))
        db.commit()
        db.close()
        return redirect(url_for('index'))
    saved_shots = parse_shot_order(user['shot_order'])
    return render_template('profile_setup.html', user=user, edit=True, saved_shots=saved_shots)

@app.route('/analyze', methods=['POST'])
def analyze():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))

    if 'video' not in request.files:
        return redirect(url_for('index'))

    file = request.files['video']
    shot_type = request.form.get('shot_type', 'serve')
    if shot_type not in ('serve', 'forehand', 'backhand', 'rally'):
        shot_type = 'serve'

    if file.filename == '' or not allowed_file(file.filename):
        return redirect(url_for('index'))

    with uploaded_video(file) as filepath:
        metrics = process_video(filepath, shot_type, dominant_hand=user['dominant_hand'])
    if metrics.get('error'):
        flash(metrics.get('error_message', UNREADABLE_VIDEO_MSG))
        return redirect(url_for('index') + '#analyze')

    profile = {
        'name': user['name'],
        'play_like': user['play_like'],
        'utr': user['utr'],
        'player_type': user['player_type'],
        'tactical_pref': user['tactical_pref'],
        'dominant_hand': user['dominant_hand'],
        'backhand_style': user['backhand_style'],
        'best_shot': user['best_shot'],
        'point_length': user['point_length'],
        'shot_order': user['shot_order'],
    }

    feedback = generate_feedback(metrics, shot_type, profile=profile)

    db = get_db()
    db.execute(
        '''
        INSERT INTO analysis_history
        (user_id, shot_type, overall_score, overall_label, scores_json)
        VALUES (%s, %s, %s, %s, %s)
        ''',
        (
            user['id'],
            shot_type,
            feedback.get('overall_score'),
            feedback.get('overall_label'),
            json.dumps(feedback.get('scores', {}))
        )
    )
    token = create_result_card(db, shot_type, feedback, user_id=user['id'])
    db.commit()
    db.close()

    return render_template(
        'results.html',
        metrics=metrics,
        feedback=feedback,
        shot_type=shot_type,
        user=user,
        card_token=token
    )

@app.route('/point-play', methods=['GET', 'POST'])
def point_play():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    if request.method == 'POST':
        if 'video' not in request.files:
            return redirect(url_for('point_play'))
        file = request.files['video']
        point_result = request.form.get('point_result', 'lost')
        point_context = request.form.get('point_context', '').strip()
        if file.filename == '' or not allowed_file(file.filename):
            return redirect(url_for('point_play'))
        # Get motion data from video, plus real per-shot ball physics
        # (speed, depth, net clearance, spin, heaviness) tracked straight
        # off the pixels — both need the file before it's cleaned up.
        with uploaded_video(file) as filepath:
            motion_metrics = process_video(filepath, 'rally', dominant_hand=user['dominant_hand'])
            try:
                ball_physics = analyze_point_ball_physics(filepath, dominant_hand=user['dominant_hand'])
            except Exception:
                # Ball/swing tracking is the most fragile part (it depends on the
                # clip and on OpenCV/MediaPipe versions). If it breaks, still give
                # the player their coaching instead of an "Internal Server Error",
                # and log the real error for Render's Logs tab.
                print("[TennisAC] Ball physics failed:\n" + traceback.format_exc())
                ball_physics = None
        # Only an unreadable file stops Point Play — if the body mechanics
        # couldn't be read (player small or far away), the point breakdown
        # and ball physics are still worth showing.
        if motion_metrics.get('error') == 'unreadable':
            flash(motion_metrics['error_message'])
            return redirect(url_for('point_play'))
        # Build profile
        profile = {
            'name': user['name'], 'play_like': user['play_like'],
            'utr': user['utr'], 'player_type': user['player_type'],
            'tactical_pref': user['tactical_pref'], 'dominant_hand': user['dominant_hand'],
            'backhand_style': user['backhand_style'], 'best_shot': user['best_shot'],
            'point_length': user['point_length'], 'shot_order': user['shot_order'],
        }
        # Run AI analysis
        analysis = analyze_point_with_ai(motion_metrics, point_result, point_context, profile, ball_physics=ball_physics)
        try:
            trajectory_svg = render_trajectory_svg(ball_physics) if ball_physics else None
        except Exception:
            print("[TennisAC] Trajectory map failed:\n" + traceback.format_exc())
            trajectory_svg = None
        db = get_db()
        db.execute(
            '''INSERT INTO point_play_history
               (user_id, point_result, point_context, analysis_json, ball_physics_json, trajectory_svg)
               VALUES (%s, %s, %s, %s, %s, %s)''',
            (user['id'], point_result, point_context[:2000], json.dumps(analysis),
             # numpy scalars (np.bool_, np.int64) from the trackers -> plain JSON
             json.dumps(ball_physics, default=lambda o: o.item() if hasattr(o, 'item') else str(o)) if ball_physics else None,
             trajectory_svg),
        )
        db.commit()
        db.close()
        return render_template('point_play_results.html',
                               analysis=analysis,
                               point_result=point_result,
                               ball_physics=ball_physics,
                               trajectory_svg=trajectory_svg,
                               user=user)
    return render_template('point_play.html', user=user)

def load_point_plays(user_id):
    db = get_db()
    rows = db.execute(
        '''SELECT id, point_result, point_context, analysis_json, created_at
           FROM point_play_history WHERE user_id = %s ORDER BY created_at DESC''', (user_id,)
    ).fetchall()
    db.close()
    out = []
    for r in rows:
        row = dict(r)
        try:
            breakdown = (json.loads(row['analysis_json'] or '{}').get('breakdown') or [''])
        except (TypeError, ValueError, AttributeError):
            breakdown = ['']
        row['summary'] = breakdown[0] if breakdown else ''
        out.append(row)
    return out

@app.route('/point-play/<int:play_id>')
def point_play_saved(play_id):
    """A saved Point Play result, for the player or their coach."""
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    db = get_db()
    play = db.execute('SELECT * FROM point_play_history WHERE id = %s', (play_id,)).fetchone()
    allowed = play is not None and can_view_player(db, user, play['user_id'])
    db.close()
    if not allowed:
        abort(404)
    try:
        analysis = json.loads(play['analysis_json'] or '{}')
        ball_physics = json.loads(play['ball_physics_json']) if play['ball_physics_json'] else None
    except (TypeError, ValueError):
        analysis, ball_physics = {}, None
    return render_template('point_play_results.html', analysis=analysis, point_result=play['point_result'],
                           ball_physics=ball_physics, trajectory_svg=play['trajectory_svg'], user=user)


def load_history(user_id):
    """A player's saved analyses, newest first, each with its per-metric
    breakdown (Contact Height, Arm Extension, etc.) parsed for display."""
    db = get_db()
    history = db.execute(
        'SELECT * FROM analysis_history WHERE user_id = %s ORDER BY created_at DESC',
        (user_id,)
    ).fetchall()
    db.close()
    rows = []
    for item in history:
        row = dict(item)
        try:
            row['scores'] = json.loads(row['scores_json']) if row['scores_json'] else {}
        except (TypeError, ValueError):
            row['scores'] = {}
        rows.append(row)
    return rows

@app.route('/progress')
def progress():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    return render_template('progress.html', user=user, player=user, history=load_history(user['id']),
                           point_plays=load_point_plays(user['id']))

# ── Coach mode ────────────────────────────────────────────────────────────

JOIN_CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'  # no 0/O or 1/I mix-ups

@app.template_filter('nicedate')
def nicedate(value):
    try:
        return value.strftime('%b %-d, %Y')
    except AttributeError:
        return value or ''

def can_view_player(db, viewer, player_id):
    """A player's analyses are visible to themselves and to the coach of
    any team they've joined."""
    if viewer['id'] == player_id:
        return True
    return db.execute(
        '''SELECT 1 FROM team_members m JOIN teams t ON t.id = m.team_id
           WHERE m.user_id = %s AND t.coach_id = %s LIMIT 1''', (player_id, viewer['id'])
    ).fetchone() is not None

@app.route('/compare')
def compare():
    """Before/after: two analyses of the same player side by side, metric
    by metric, so the effect of working on a fix is visible."""
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    try:
        ids = [int(request.args['a']), int(request.args['b'])]
    except (KeyError, ValueError):
        abort(404)
    db = get_db()
    rows = db.execute('SELECT * FROM analysis_history WHERE id = ANY(%s)', (ids,)).fetchall()
    if len(rows) != 2 or rows[0]['user_id'] != rows[1]['user_id'] or not can_view_player(db, user, rows[0]['user_id']):
        db.close()
        abort(404)
    player = db.execute('SELECT * FROM users WHERE id = %s', (rows[0]['user_id'],)).fetchone()
    db.close()
    before, after = sorted((dict(r) for r in rows), key=lambda r: r['created_at'])
    for r in (before, after):
        try:
            r['scores'] = json.loads(r['scores_json']) if r['scores_json'] else {}
        except (TypeError, ValueError):
            r['scores'] = {}
    names = list(after['scores']) + [n for n in before['scores'] if n not in after['scores']]
    metrics = []
    for n in names:
        b, a = before['scores'].get(n, {}), after['scores'].get(n, {})
        delta = (round(a['score'] - b['score'], 1)
                 if isinstance(a.get('score'), (int, float)) and isinstance(b.get('score'), (int, float)) else None)
        metrics.append({'name': n, 'before': b, 'after': a, 'delta': delta})
    overall_delta = (round(after['overall_score'] - before['overall_score'], 1)
                     if after['overall_score'] is not None and before['overall_score'] is not None else None)
    return render_template('compare.html', user=user, player=player, before=before, after=after,
                           metrics=metrics, overall_delta=overall_delta)

def coached_team(db, team_id, user):
    return db.execute('SELECT * FROM teams WHERE id = %s AND coach_id = %s', (team_id, user['id'])).fetchone()

@app.route('/teams')
def teams():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    db = get_db()
    coaching = db.execute(
        '''SELECT t.*, COUNT(m.user_id) AS players
           FROM teams t LEFT JOIN team_members m ON m.team_id = t.id
           WHERE t.coach_id = %s GROUP BY t.id ORDER BY t.created_at''', (user['id'],)
    ).fetchall()
    member_of = db.execute(
        '''SELECT t.*, u.name AS coach_name
           FROM team_members m JOIN teams t ON t.id = m.team_id JOIN users u ON u.id = t.coach_id
           WHERE m.user_id = %s ORDER BY m.joined_at''', (user['id'],)
    ).fetchall()
    db.close()
    return render_template('teams.html', user=user, coaching=coaching, member_of=member_of)

@app.route('/teams/create', methods=['POST'])
def create_team():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    name = request.form.get('name', '').strip()[:80]
    if not name:
        flash('Give your team a name.')
        return redirect(url_for('teams'))
    db = get_db()
    for _ in range(10):
        code = ''.join(secrets.choice(JOIN_CODE_ALPHABET) for _ in range(6))
        if not db.execute('SELECT 1 FROM teams WHERE join_code = %s', (code,)).fetchone():
            break
    team = db.execute('INSERT INTO teams (name, coach_id, join_code) VALUES (%s, %s, %s) RETURNING id',
                      (name, user['id'], code)).fetchone()
    db.commit()
    db.close()
    return redirect(url_for('team_detail', team_id=team['id']))

@app.route('/teams/join', methods=['POST'])
def join_team():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    code = ''.join(ch for ch in request.form.get('code', '').upper() if ch.isalnum())
    db = get_db()
    team = db.execute('SELECT * FROM teams WHERE join_code = %s', (code,)).fetchone()
    if not team:
        db.close()
        flash("That team code didn't match a team. Check it with your coach and try again.")
        return redirect(url_for('teams'))
    db.execute('INSERT INTO team_members (team_id, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING',
               (team['id'], user['id']))
    db.commit()
    db.close()
    flash(f"You joined {team['name']}. Your coach can now see your analyses.")
    return redirect(url_for('teams'))

@app.route('/teams/<int:team_id>/leave', methods=['POST'])
def leave_team(team_id):
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    db = get_db()
    db.execute('DELETE FROM team_members WHERE team_id = %s AND user_id = %s', (team_id, user['id']))
    db.commit()
    db.close()
    flash("You left the team. That coach can no longer see your analyses.")
    return redirect(url_for('teams'))

@app.route('/teams/<int:team_id>')
def team_detail(team_id):
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    db = get_db()
    team = coached_team(db, team_id, user)
    if not team:
        db.close()
        abort(404)
    roster = db.execute(
        '''SELECT u.id, u.name, u.email, u.utr, m.joined_at,
                  COUNT(a.id) AS analyses, MAX(a.created_at) AS last_active
           FROM team_members m JOIN users u ON u.id = m.user_id
           LEFT JOIN analysis_history a ON a.user_id = u.id
           WHERE m.team_id = %s GROUP BY u.id, m.joined_at ORDER BY u.name''', (team_id,)
    ).fetchall()
    players = []
    for r in roster:
        p = dict(r)
        recent = db.execute(
            '''SELECT shot_type, overall_score FROM analysis_history
               WHERE user_id = %s AND overall_score IS NOT NULL ORDER BY created_at DESC LIMIT 6''', (r['id'],)
        ).fetchall()
        p['latest'] = recent[0] if recent else None
        p['point_plays'] = db.execute('SELECT COUNT(*) AS n FROM point_play_history WHERE user_id = %s',
                                      (r['id'],)).fetchone()['n']
        # Trend: last three scores against the three before them.
        if len(recent) >= 4:
            new = [x['overall_score'] for x in recent[:3]]
            old = [x['overall_score'] for x in recent[3:]]
            p['trend'] = round(sum(new) / len(new) - sum(old) / len(old), 1)
        else:
            p['trend'] = None
        players.append(p)
    db.close()
    return render_template('team.html', user=user, team=team, players=players)

@app.route('/teams/<int:team_id>/players/<int:player_id>')
def team_player(team_id, player_id):
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    db = get_db()
    team = coached_team(db, team_id, user)
    player = db.execute(
        '''SELECT u.* FROM team_members m JOIN users u ON u.id = m.user_id
           WHERE m.team_id = %s AND m.user_id = %s''', (team_id, player_id)
    ).fetchone() if team else None
    db.close()
    if not player:
        abort(404)
    return render_template('progress.html', user=user, player=player, team=team,
                           history=load_history(player_id), point_plays=load_point_plays(player_id))

@app.route('/teams/<int:team_id>/players/<int:player_id>/remove', methods=['POST'])
def remove_player(team_id, player_id):
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    db = get_db()
    if coached_team(db, team_id, user):
        db.execute('DELETE FROM team_members WHERE team_id = %s AND user_id = %s', (team_id, player_id))
        db.commit()
    db.close()
    return redirect(url_for('team_detail', team_id=team_id))

@app.errorhandler(ServerBusy)
def server_busy(e):
    return render_template('error.html', user=get_current_user_safe(), title="Lots of players right now",
                           message="TennisAC is analyzing other players' videos. Give it a minute and upload again."), 503

@app.errorhandler(413)
def too_large(e):
    return render_template('error.html', user=get_current_user_safe(), title="That video is too big",
                           message="Videos need to be under 100 MB. Trim it to the shot or point you want analyzed (10–60 seconds) and try again."), 413

@app.errorhandler(404)
def not_found(e):
    return render_template('error.html', user=get_current_user_safe(), title="Page not found",
                           message="That link doesn't go anywhere. It may have been typed wrong."), 404

@app.errorhandler(Exception)
def unexpected_error(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    # Logged with a [TennisAC] tag so it's easy to find in Render's Logs tab.
    print("[TennisAC] Unexpected error on " + request.path + ":\n" + traceback.format_exc())
    return render_template('error.html', user=get_current_user_safe(), title="Something went wrong",
                           message="That one's on us, not you. Try again — if it keeps happening, let us know what you were doing."), 500

def get_current_user_safe():
    # Error pages must render even when the database is what failed.
    try:
        return get_current_user()
    except Exception:
        return None

@app.route('/r/<token>')
def shared_card(token):
    """Public, read-only card for a shared result — what other players
    see when someone posts their analysis. Shows no name or email."""
    db = get_db()
    card = db.execute('SELECT * FROM result_cards WHERE token = %s', (token,)).fetchone()
    if not card:
        db.close()
        abort(404)
    db.execute('UPDATE result_cards SET views = views + 1 WHERE token = %s', (token,))
    db.commit()
    db.close()

    scores = json.loads(card['scores_json'] or '{}')
    rated = [(name, d) for name, d in scores.items() if isinstance(d.get('score'), (int, float))]
    biggest_fix = min(rated, key=lambda item: item[1]['score']) if rated else None
    return render_template('shared_card.html', card=card, scores=scores, biggest_fix=biggest_fix,
                           user=get_current_user())

@app.route('/r/<token>/share', methods=['POST'])
def log_share(token):
    """Records that the owner shared this card. Only the person who got
    the result can log a share for it, so the metric can't be padded."""
    db = get_db()
    card = db.execute('SELECT user_id, visitor_id FROM result_cards WHERE token = %s', (token,)).fetchone()
    owner = card and ((card['user_id'] and card['user_id'] == session.get('user_id'))
                      or (card['visitor_id'] and card['visitor_id'] == session.get('visitor_id')))
    if owner:
        method = request.form.get('method', '')[:20]
        db.execute('INSERT INTO share_events (token, method) VALUES (%s, %s)', (token, method))
        db.commit()
    db.close()
    return ('', 204) if owner else ('', 403)

CHALLENGE_SHOTS = ('serve', 'forehand', 'backhand')

@app.route('/r/<token>/challenge', methods=['POST'])
def enter_challenge(token):
    user = get_current_user()
    if not user:
        return redirect(url_for('signup'))
    parts = (user['name'] or 'Player').split()
    display = parts[0] + (f" {parts[-1][0]}." if len(parts) > 1 else '')
    db = get_db()
    card = db.execute('SELECT * FROM result_cards WHERE token = %s AND user_id = %s', (token, user['id'])).fetchone()
    if card and card['shot_type'] in CHALLENGE_SHOTS and card['overall_score'] is not None:
        db.execute('UPDATE result_cards SET challenge_name = %s WHERE token = %s', (display, token))
        db.commit()
        db.close()
        return redirect(url_for('challenge', shot=card['shot_type']))
    db.close()
    abort(404)

@app.route('/challenge')
def challenge():
    """Public leaderboard — each entrant's best entered score per shot."""
    shot = request.args.get('shot', 'serve')
    if shot not in CHALLENGE_SHOTS:
        shot = 'serve'
    db = get_db()
    board = db.execute(
        '''SELECT DISTINCT ON (user_id) user_id, token, challenge_name, overall_score, created_at
           FROM result_cards
           WHERE challenge_name IS NOT NULL AND shot_type = %s AND overall_score IS NOT NULL
           ORDER BY user_id, overall_score DESC, created_at''', (shot,)
    ).fetchall()
    db.close()
    board = sorted(board, key=lambda r: (-r['overall_score'], r['created_at']))[:50]
    return render_template('challenge.html', user=get_current_user(), shot=shot, shots=CHALLENGE_SHOTS, board=board)

@app.route('/admin/users')
def admin_users():
    user = get_current_user()
    if not user or user['email'] != ADMIN_EMAIL:
        return redirect(url_for('index'))

    db = get_db()
    users = db.execute(
        '''
        SELECT u.*,
               COUNT(a.id) AS analysis_count,
               MAX(a.created_at) AS last_active
        FROM users u
        LEFT JOIN analysis_history a ON a.user_id = u.id
        GROUP BY u.id
        ORDER BY u.created_at DESC
        '''
    ).fetchall()

    growth = db.execute(
        '''
        SELECT
            (SELECT COUNT(*) FROM result_cards) AS delivered,
            (SELECT COUNT(DISTINCT token) FROM share_events) AS shared,
            (SELECT COUNT(*) FROM share_events) AS share_events,
            (SELECT COALESCE(SUM(views), 0) FROM result_cards) AS card_views,
            (SELECT COUNT(*) FROM trial_uses) AS guest_analyses,
            (SELECT COUNT(DISTINCT visitor_id) FROM trial_uses) AS trials,
            (SELECT COUNT(*) FROM users WHERE trial_visitor_id IN (SELECT visitor_id FROM trial_uses)) AS trial_signups
        '''
    ).fetchone()
    db.close()
    growth = dict(growth)
    growth['share_rate'] = round(100 * growth['shared'] / growth['delivered'], 1) if growth['delivered'] else None
    growth['trial_conversion'] = round(100 * growth['trial_signups'] / growth['trials'], 1) if growth['trials'] else None

    return render_template('admin_users.html', user=user, users=users, growth=growth)

if __name__ == '__main__':
    app.run(debug=True)
