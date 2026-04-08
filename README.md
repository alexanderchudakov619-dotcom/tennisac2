# TennisAC 🎾

**Your personal AI tennis coach.** Upload a video of any shot — serve, forehand, backhand, or rally — and get instant biomechanical feedback powered by computer vision.

---

## What It Does

- **Video Upload** — supports MP4, MOV, AVI (up to 100MB)
- **Pose Tracking** — uses MediaPipe to track 33 body landmarks frame by frame
- **Shot Analysis** — measures contact height, arm extension, knee bend, trunk rotation, toss consistency
- **Instant Feedback** — scores each metric and gives you specific coaching tips
- **Supports 4 shot types** — Serve, Forehand, Backhand, Rally

---

## Tech Stack

| Layer      | Tool             |
|------------|------------------|
| Backend    | Python + Flask   |
| Vision     | OpenCV           |
| Pose       | MediaPipe Pose   |
| Frontend   | HTML/CSS/JS      |

---

## Setup

### 1. Clone or download the project

```bash
cd tennisac
```

### 2. Create a virtual environment (recommended)

```bash
python -m venv venv
source venv/bin/activate        # Mac/Linux
venv\Scripts\activate           # Windows
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Run the app

```bash
python app.py
```

Then open your browser to: **http://127.0.0.1:5000**

---

## Project Structure

```
tennisac/
├── app.py                  # Flask app + routes
├── requirements.txt
├── templates/
│   ├── index.html          # Upload page
│   └── results.html        # Results + feedback page
├── static/
│   ├── css/style.css
│   └── js/main.js
├── uploads/                # Temp video storage (auto-cleared)
└── analysis/
    ├── video_processor.py  # OpenCV + MediaPipe frame analysis
    └── pose_analysis.py    # Scoring + feedback rules
```

---

## How the Analysis Works

1. Video is uploaded and saved temporarily
2. `video_processor.py` extracts up to 60 evenly spaced frames using OpenCV
3. MediaPipe Pose detects 33 body landmarks per frame
4. Key angles and distances are calculated (elbow angle, wrist height, knee bend, etc.)
5. `pose_analysis.py` converts raw measurements into 0–100 scores using shot-specific thresholds
6. Feedback tips are generated based on which metrics fall below ideal ranges
7. Results are displayed and the uploaded file is deleted

---

## Future Features

- [ ] Session history (SQLite database)
- [ ] Week-over-week shot comparison
- [ ] Player movement heatmap
- [ ] Mobile-optimized video recording
- [ ] Coach review notes section

---

Built for CS50 Final Project · 2025
