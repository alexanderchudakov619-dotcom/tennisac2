from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
import os
import uuid
import hashlib
import json
import traceback
from datetime import timedelta
import psycopg2
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
# Keep people signed in for 90 days. Without this, Flask's login cookie only
# lasts until the browser closes — and phones close background browsers
# constantly, so users kept finding themselves logged out.
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=90)

ALLOWED_EXTENSIONS = {'mp4', 'mov', 'avi', 'mkv'}

# Only this account can view the /admin/users page.
ADMIN_EMAIL = 'alexanderchudakov619@gmail.com'

# Free trial: one shot analysis without an account. Tracked per browser
# (signed session cookie) and per network, so clearing cookies alone doesn't
# reset it. The per-network cap is 2, not 1, so two players on the same
# home or club wifi can each get their own free analysis.
TRIAL_LIMIT_PER_IP = 2

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
        _pool.putconn(self._conn)

def get_db():
    return DB(_pool.getconn())

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
    return hashlib.sha256(password.encode()).hexdigest()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

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

def trial_available():
    if session.get('trial_used'):
        return False
    db = get_db()
    used_here = db.execute('SELECT 1 FROM trial_uses WHERE visitor_id = %s LIMIT 1',
                           (get_visitor_id(),)).fetchone()
    ip_count = db.execute('SELECT COUNT(*) AS n FROM trial_uses WHERE ip_hash = %s',
                          (client_ip_hash(),)).fetchone()['n']
    db.close()
    return not used_here and ip_count < TRIAL_LIMIT_PER_IP

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
    """One free shot analysis, no account or card needed. The signup ask
    comes after they've seen their result, not before."""
    if get_current_user():
        return redirect(url_for('index'))

    if request.method == 'GET':
        return render_template('index.html', user=None, trial=True, trial_left=trial_available())

    if not trial_available():
        flash("You've used your free analysis — create a free account to keep analyzing.")
        return redirect(url_for('signup'))

    file = request.files.get('video')
    shot_type = request.form.get('shot_type', 'serve')
    if shot_type not in ('serve', 'forehand', 'backhand', 'rally'):
        shot_type = 'serve'
    if not file or file.filename == '' or not allowed_file(file.filename):
        return redirect(url_for('try_free'))

    ext = file.filename.rsplit('.', 1)[1].lower()
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], f"{uuid.uuid4().hex}.{ext}")
    file.save(filepath)
    try:
        metrics = process_video(filepath, shot_type)
    finally:
        os.remove(filepath)

    feedback = generate_feedback(metrics, shot_type)
    visitor_id = get_visitor_id()
    db = get_db()
    db.execute('INSERT INTO trial_uses (visitor_id, ip_hash) VALUES (%s, %s)', (visitor_id, client_ip_hash()))
    token = create_result_card(db, shot_type, feedback, visitor_id=visitor_id)
    db.commit()
    db.close()
    session['trial_used'] = True
    session['trial_card'] = token

    return render_template('results.html', metrics=metrics, feedback=feedback,
                           shot_type=shot_type, user=None, trial=True, card_token=token)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        name = request.form.get('name', '').strip()
        if not email or not password or not name:
            flash('Please fill in all required fields.')
            return render_template('signup.html')
        db = get_db()
        existing = db.execute('SELECT id FROM users WHERE email = %s', (email,)).fetchone()
        if existing:
            flash('An account with that email already exists.')
            db.close()
            return render_template('signup.html')
        db.execute('INSERT INTO users (email, password, name) VALUES (%s, %s, %s)',
                   (email, hash_password(password), name))
        db.commit()
        user = db.execute('SELECT * FROM users WHERE email = %s', (email,)).fetchone()

        # Coming from the free trial: keep that analysis in their history
        # so signing up doesn't throw away the result that convinced them.
        trial_token = session.get('trial_card')
        if trial_token:
            card = db.execute('SELECT * FROM result_cards WHERE token = %s AND user_id IS NULL',
                              (trial_token,)).fetchone()
            if card:
                db.execute('UPDATE result_cards SET user_id = %s WHERE token = %s', (user['id'], trial_token))
                db.execute(
                    '''INSERT INTO analysis_history (user_id, shot_type, overall_score, overall_label, scores_json, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s)''',
                    (user['id'], card['shot_type'], card['overall_score'], card['overall_label'],
                     card['scores_json'], card['created_at']),
                )
        if session.get('visitor_id'):
            db.execute('UPDATE users SET trial_visitor_id = %s WHERE id = %s', (session['visitor_id'], user['id']))
        db.commit()
        session.pop('trial_card', None)
        session['user_id'] = user['id']
        session.permanent = True
        db.close()
        return redirect(url_for('profile_setup'))
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE email = %s AND password = %s',
                          (email, hash_password(password))).fetchone()
        db.close()
        if user:
            session['user_id'] = user['id']
            session.permanent = True
            return redirect(url_for('index'))
        else:
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

    if file.filename == '' or not allowed_file(file.filename):
        return redirect(url_for('index'))

    ext = file.filename.rsplit('.', 1)[1].lower()
    unique_name = f"{uuid.uuid4().hex}.{ext}"
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
    file.save(filepath)

    try:
        metrics = process_video(filepath, shot_type)
    finally:
        # Always clean up the upload, even if processing raises — otherwise a
        # bad clip (or any error) leaves the file behind forever, and under
        # concurrent traffic the uploads folder can fill the disk and take
        # the whole app down for everyone.
        os.remove(filepath)

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
        ext = file.filename.rsplit('.', 1)[1].lower()
        unique_name = f"{uuid.uuid4().hex}.{ext}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
        file.save(filepath)
        # Get motion data from video, plus real per-shot ball physics
        # (speed, depth, net clearance, spin, heaviness) tracked straight
        # off the pixels — both need the file before it's cleaned up.
        try:
            motion_metrics = process_video(filepath, 'rally')
            try:
                ball_physics = analyze_point_ball_physics(filepath, dominant_hand=user['dominant_hand'])
            except Exception:
                # Ball/swing tracking is the most fragile part (it depends on the
                # clip and on OpenCV/MediaPipe versions). If it breaks, still give
                # the player their coaching instead of an "Internal Server Error",
                # and log the real error for Render's Logs tab.
                print("[TennisAC] Ball physics failed:\n" + traceback.format_exc())
                ball_physics = None
        finally:
            # Always clean up, even if processing raises (see /analyze).
            os.remove(filepath)
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
        return render_template('point_play_results.html',
                               analysis=analysis,
                               point_result=point_result,
                               ball_physics=ball_physics,
                               trajectory_svg=trajectory_svg,
                               user=user)
    return render_template('point_play.html', user=user)


@app.route('/progress')
def progress():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))

    db = get_db()
    history = db.execute(
        '''
        SELECT *
        FROM analysis_history
        WHERE user_id = %s
        ORDER BY created_at DESC
        ''',
        (user['id'],)
    ).fetchall()
    db.close()

    # Attach each session's saved metric breakdown (Contact Height, Arm Extension, etc.)
    # so the Progress page can expand a past analysis and show it, not just the overall score.
    history_with_scores = []
    for item in history:
        row = dict(item)
        try:
            row['scores'] = json.loads(row['scores_json']) if row['scores_json'] else {}
        except (TypeError, ValueError):
            row['scores'] = {}
        history_with_scores.append(row)

    return render_template('progress.html', user=user, history=history_with_scores)

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
            (SELECT COUNT(*) FROM trial_uses) AS trials,
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
