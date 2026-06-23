from flask import Flask, render_template, request, redirect, url_for, session, flash
import os
import uuid
import sqlite3
import hashlib
from analysis.video_processor import process_video
from analysis.pose_analysis import generate_feedback
from analysis.point_analyzer import analyze_point_with_ai

app = Flask(__name__)
app.secret_key = 'tennisac_secret_2025'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024

ALLOWED_EXTENSIONS = {'mp4', 'mov', 'avi', 'mkv'}

def get_db():
    db = sqlite3.connect('tennisac.db')
    db.row_factory = sqlite3.Row
    return db

def init_db():
    db = get_db()
    db.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            shot_type TEXT NOT NULL,
            overall_score REAL,
            overall_label TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    db.commit()
    db.close()

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_current_user():
    if 'user_id' not in session:
        return None
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    db.close()
    return user

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

@app.route('/')
def index():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    return render_template('index.html', user=user)

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
        existing = db.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone()
        if existing:
            flash('An account with that email already exists.')
            db.close()
            return render_template('signup.html')
        db.execute('INSERT INTO users (email, password, name) VALUES (?, ?, ?)',
                   (email, hash_password(password), name))
        db.commit()
        user = db.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
        session['user_id'] = user['id']
        db.close()
        return redirect(url_for('profile_setup'))
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE email = ? AND password = ?',
                          (email, hash_password(password))).fetchone()
        db.close()
        if user:
            session['user_id'] = user['id']
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
        db.execute('''UPDATE users SET play_like=?, utr=?, player_type=?, tactical_pref=?,
                      racquet=?, dominant_hand=?, backhand_style=?, best_shot=?,
                      point_length=?, shot_order=? WHERE id=?''',
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
        db.execute('''UPDATE users SET play_like=?, utr=?, player_type=?, tactical_pref=?,
                      racquet=?, dominant_hand=?, backhand_style=?, best_shot=?,
                      point_length=?, shot_order=? WHERE id=?''',
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

    metrics = process_video(filepath, shot_type)

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
        (user_id, shot_type, overall_score, overall_label)
        VALUES (?, ?, ?, ?)
        ''',
        (
            user['id'],
            shot_type,
            feedback.get('overall_score'),
            feedback.get('overall_label')
        )
    )
    db.commit()
    db.close()

    os.remove(filepath)

    return render_template(
        'results.html',
        metrics=metrics,
        feedback=feedback,
        shot_type=shot_type,
        user=user
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
        # Get motion data from video
        motion_metrics = process_video(filepath, 'rally')
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
        analysis = analyze_point_with_ai(motion_metrics, point_result, point_context, profile)
        return render_template('point_play_results.html',
                               analysis=analysis,
                               point_result=point_result,
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
        WHERE user_id = ?
        ORDER BY created_at DESC
        ''',
        (user['id'],)
    ).fetchall()
    db.close()

    return render_template('progress.html', user=user, history=history)

if __name__ == '__main__':
    os.makedirs('uploads', exist_ok=True)
    init_db()
    app.run(debug=True)
