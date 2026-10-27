# TennisAC: AI Tennis Coach

#### Video Demo: <PASTE YOUR YOUTUBE URL HERE>

#### Live site: https://tennisacapp.com

#### Description:

TennisAC is a web app that coaches tennis players from an ordinary phone video. You upload a clip of a serve, forehand, backhand, rally, or a whole point, and TennisAC tracks your body through the stroke, scores each part of your technique from 0 to 100, explains what each score means, and tells you exactly what to fix and how to train it. It is built for intermediate to advanced players, around high school varsity level and up, who already know the basics and want the kind of detailed feedback a coach gives in a lesson.

I have played tennis since I was five, and the problem I wanted to solve comes straight from that experience. In a lesson, a coach can point out that your elbow drops in the trophy position or that you are hitting your forehand jammed and late. Outside of lessons, you can feel that a shot is wrong but you usually do not know why. TennisAC tries to close that gap: it measures what actually happened in the video and turns it into structured, coach-style feedback you can take back to the court. It is not meant to replace a real coach, but to help players understand their own mechanics between lessons.

## Features

- **Shot analysis.** Upload a serve, forehand, backhand, or rally clip. TennisAC uses MediaPipe pose tracking to find each swing and its contact point, then measures real joint angles and rotation: contact height, arm extension, knee bend, shoulder turn, hip–shoulder separation (the "coil"), toss consistency, elbow height in the trophy position, head stability at contact, follow-through height, and balance after the shot.
- **Breakdown by the phases of the shot.** Results are organized the way professional video analysts break down a stroke, following phases my coach gave me: eight phases for every groundstroke (ready position, split step and first rotation, pre-extension, load, movement to the ball, movement to contact, follow-through, recovery) and nine for the serve (initial position through recovery for the +1). Measured metrics sit inside their phase with a score. Phases the camera can't measure yet are shown as checkpoints telling you what to look for when you watch your video back.
- **Clear scores and fixes.** Every metric shows your result in plain tennis language next to the pro target, then expands to explain what it means, what the AI looked at, and how to improve. A "Common mistakes spotted" list names problems the way a coach would ("Dropped elbow at the trophy", "Too close to the body (jammed)").
- **Weighted serve score.** The serve's overall score weights what matters most: toss 15%, trophy position 15%, contact 20%, with the rest shared across the other metrics.
- **Ask your coach.** A chat panel on every result where you can ask "What should I fix first?" or "Give me a drill for this week" and get answers based on your exact numbers and your player profile.
- **Point Play.** Upload a full point and say whether you won or lost it. TennisAC tracks the ball (speed, depth, net clearance, spin, and how heavy the ball is) and uses Claude to analyze your strategy. It labels each of your shots as defence, neutral, or attack, rates your decision-making (building the point, shot selection, rally length awareness, and counterpunching), and suggests better shots with the reason they're better.
- **Player profiles.** Your UTR, play style, dominant hand, backhand type, favorite shots, and the pro you want to play like all shape the feedback.
- **Progress and coach mode.** Every analysis is saved so you can track progress over time and compare a before and after. Coaches can create teams and see their players' analyses.
- **Sharing and challenges.** Share a result as a public card or enter the serve, forehand, or backhand challenge leaderboard.
- **Works without an account.** Anyone can try it as a guest, and everything moves into an account if they sign up later.

## Design decisions

**Measuring instead of guessing.** My first version of this project was a command-line program where the player rated their own mechanics from 1 to 10. That was easy to build, but it only reflected what the player already thought. For the web app I wanted real measurements, so I moved to MediaPipe pose tracking. Every number comes from the tracked skeleton, and heights are measured in torso lengths so the result doesn't depend on how tall the player is or how far away the camera is. When something can't be measured, like legs out of frame or only one serve in the clip, the app shows "Not measured" instead of inventing a score. A wrong number shown as fact is worse than no number at all.

**Breaking analysis down by shot type and by phase.** Each shot type has its own metrics and its own coaching rules, because a good serve and a good forehand look completely different. Telling the app what kind of shot it is looking at also makes it much less likely to give advice that doesn't fit. Organizing results by the phases of the shot came from my coach, who explained that this is how professional video analysts work. It also helps players see that a problem early in the shot, like a poor load, usually causes the problem they notice later, like a weak contact.

**Using AI where it actually helps.** The biomechanics are measured by code, not by a language model. I use Claude where language and judgment matter: the "Ask your coach" chat and the strategy analysis in Point Play. The model is always given the real measurements and is told never to invent a number that wasn't measured. Point Play also asks the player whether they won or lost the point before analyzing it, so the AI doesn't have to guess the outcome. Its answers come back in a fixed structure, so the page always has every field it needs.

**Making it feel like a coach, not a calculator.** AI feedback can feel robotic or generic, so every metric has its own definition, its own explanation, and its own fix, written in the language players and coaches actually use.

**Being careful with cost and privacy.** Videos are deleted right after they're analyzed and are never stored. The chat is limited per browser per hour, and only the person who got a result can ask questions about it. Shared cards show no name or video.

## Files

- `app.py`: the Flask app. All routes (upload and analysis, results, Point Play, progress and compare, teams and coach mode, sharing, challenges, accounts and password reset, privacy, and the coach chat endpoint), the Postgres database setup, and search-engine files (`robots.txt`, `sitemap.xml`).
- `analysis/video_processor.py`: runs pose tracking on a clip, finds each swing's contact moment, and measures the joint angles, rotation, toss, trophy position, head movement, balance, and follow-through.
- `analysis/swing_analysis.py`: loads the MediaPipe pose model and tracks the skeleton frame by frame, plus swing-path and hand-speed analysis used by Point Play.
- `analysis/pose_analysis.py`: turns measurements into 0–100 scores, plain-language results, coaching fixes and tips; defines the phases of the shot and the serve, the serve weights, and the common-mistake labels.
- `analysis/ball_physics.py`: tracks the ball and the court to measure speed, depth, net clearance, spin, and heaviness of each shot.
- `analysis/trajectory_viz.py`: draws the shot trajectory map shown in Point Play.
- `analysis/point_analyzer.py`: sends the point, the player's profile, and the measured ball physics to Claude and returns the structured strategy analysis, including the tactics ratings.
- `analysis/coach_chat.py`: builds the analysis context for the "Ask your coach" chat and calls Claude for each reply.
- `templates/`: the HTML pages (Jinja templates), including `results.html` (shot breakdown and chat), `point_play_results.html`, `progress.html`, `compare.html`, `shared_card.html`, and the account pages.
- `static/`: CSS, JavaScript, shot photos, and icons.
- `models/pose_landmarker_lite.task`: the MediaPipe pose model.
- `requirements.txt`, `build.sh`, `Procfile`: dependencies and the build and start commands for deployment on Render.

## Running it locally

1. Create a virtual environment and install dependencies: `python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt`
2. Set environment variables:
   - `DATABASE_URL`: a Postgres connection string (required)
   - `SECRET_KEY`: any long random string, used to sign session cookies
   - `ANTHROPIC_API_KEY`: needed for Point Play and the coach chat
   - `RESEND_API_KEY` (optional): for password-reset emails
3. Run `python3 app.py` and open http://127.0.0.1:5000

## Tech stack

Python, Flask, PostgreSQL, MediaPipe and OpenCV for pose and ball tracking, NumPy, the Anthropic Claude API, and HTML, CSS and JavaScript. Hosted on Render.

## AI usage

In line with CS50's policy, I used AI tools (Claude) as a helper while building this project, for example to explain concepts, debug, and help write and review code. The project idea, the tennis knowledge behind the metrics, and the design decisions are my own, and the coaching phases come from my coach.

Built by Alexander Chudakov, 2026.
