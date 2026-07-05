# TennisAC 🎾

**Your personal AI tennis coach.** Upload a video of your shot — or a whole point — and get instant, personalized feedback on your technique and strategy.

---

## What It Does

- **Shot Analysis** — upload a serve, forehand, backhand, or rally clip and get scored feedback on your mechanics
- **Point Play Analysis** — upload a full point, mark whether you won or lost it, and get AI-powered strategic coaching on your shot selection and decision-making
- **Player Profiles** — create an account and set your UTR, playing style, dominant hand, backhand type, and the pro you want to play like; every analysis is personalized to you
- **Instant Feedback** — each metric is scored 0–100 with specific coaching tips
- **Video Upload** — supports MP4, MOV, AVI, MKV (up to 100MB)

---

## Tech Stack

| Layer            | Tool                          |
|------------------|-------------------------------|
| Backend          | Python + Flask                |
| Database         | SQLite                        |
| Video Analysis   | OpenCV (motion analysis)      |
| Strategy AI      | Anthropic Claude API          |
| Frontend         | HTML / CSS / JavaScript       |
| Auth             | Session-based login           |

---

## Setup

### 1. Download the project

```bash
cd tennisac2
```

### 2. Create and activate a virtual environment

```bash
python3 -m venv venv
source venv/bin/activate        # Mac/Linux
venv\Scripts\activate           # Windows
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Set your Anthropic API key

Point Play uses the Claude API, so you need a key from https://console.anthropic.com

```bash
export ANTHROPIC_API_KEY=your-key-here     # Mac/Linux
```

### 5. Run the app

```bash
python3 app.py
```

Then open your browser to: **http://127.0.0.1:5000**

---

## Project Structure

```
tennisac2/
├── app.py                      # Flask app + all routes
├── requirements.txt
├── tennisac.db                 # SQLite database (users, profiles)
├── templates/
│   ├── index.html              # Main upload / shot analysis page
│   ├── login.html              # Login
│   ├── signup.html             # Account creation
│   ├── profile_setup.html      # Player profile setup
│   ├── results.html            # Shot analysis results
│   ├── point_play.html         # Point Play upload page
│   └── point_play_results.html # Point Play AI results
├── static/
│   ├── css/style.css
│   └── js/main.js
└── analysis/
    ├── video_processor.py      # OpenCV frame + motion analysis
    ├── pose_analysis.py        # Shot scoring + feedback rules
    └── point_analyzer.py       # Claude API strategic point analysis
```

---

## How the Analysis Works

**Shot Analysis:**
1. Video is uploaded and saved temporarily
2. `video_processor.py` extracts evenly spaced frames using OpenCV
3. Motion between frames is measured (where movement is concentrated, consistency, estimated reach)
4. `pose_analysis.py` converts these into 0–100 scores using shot-specific rules
5. Coaching tips are generated based on which metrics score low
6. Results are displayed and the uploaded file is deleted

**Point Play Analysis:**
1. Player uploads a point clip and marks won/lost, with optional context
2. Motion data, the result, and the player's full profile are sent to the Claude API
3. Claude returns a strategic breakdown, alternative shot suggestions, and opponent-pattern tips
4. Results are displayed on the Point Play results page

---

## Roadmap

- [ ] Save analysis history so players can track progress over time
- [ ] Upgrade shot analysis to true pose estimation (joint tracking) for more precise feedback
- [ ] Verified Coach Insights — feedback backed by partnered coaches
- [ ] Live chat coach
- [ ] Deploy to a public URL

---

Built by Alexander Chudakov · 2026