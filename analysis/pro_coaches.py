"""
pro_coaches.py
Pro Coaches Feedback: matches a player's weakest areas to instruction from
partner coaches and academies who have given TennisAC permission to use
their material. Content is added by the admin (one entry per topic it
teaches); nothing here is ever written by the AI or attributed to a coach
without permission.
"""
from urllib.parse import urlparse

from analysis.pose_analysis import COMMON_MISTAKES
from analysis.point_analyzer import TACTIC_NAMES

# What a piece of content can be tagged with: any shot metric we measure,
# or any Point Play tactic.
METRIC_TOPICS = sorted(COMMON_MISTAKES)
TACTIC_TOPICS = list(TACTIC_NAMES.values())
TOPICS = METRIC_TOPICS + TACTIC_TOPICS

# '' means the content applies to any shot.
SHOT_TYPES = ('', 'serve', 'forehand', 'backhand', 'rally', 'point')

MAX_TIPS = 3


def safe_url(url):
    """Only plain http(s) links are kept (no javascript: and the like)."""
    url = (url or '').strip()
    return url if urlparse(url).scheme in ('http', 'https') and urlparse(url).netloc else None


def topics_for_shot(scores):
    """The metrics a shot analysis most needs help on: everything under
    50, worst first, or failing that the two lowest under 70."""
    rated = sorted((d['score'], name) for name, d in scores.items() if d.get('score') is not None)
    weak = [name for score, name in rated if score < 50]
    return weak or [name for score, name in rated[:2] if score < 70]


def topics_for_point(analysis):
    """The tactics a Point Play point most needs help on, the same way."""
    tactics = (analysis or {}).get('tactics') or {}
    rated = sorted((t['score'], TACTIC_NAMES[key]) for key, t in tactics.items()
                   if key in TACTIC_NAMES and isinstance(t, dict) and isinstance(t.get('score'), int))
    weak = [name for score, name in rated if score < 50]
    return weak or [name for score, name in rated[:2] if score < 70]


def find_pro_tips(db, topics, shot_type):
    """Active partner content for these topics, in the order of the
    topics (worst first), at most MAX_TIPS and one per topic."""
    if not topics:
        return []
    rows = db.execute(
        '''SELECT * FROM coach_content
           WHERE active AND topic = ANY(%s) AND (shot_type IS NULL OR shot_type = '' OR shot_type = %s)
           ORDER BY created_at DESC''', (list(topics), shot_type)
    ).fetchall()
    by_topic = {}
    for r in rows:
        by_topic.setdefault(r['topic'], dict(r))
    return [by_topic[t] for t in topics if t in by_topic][:MAX_TIPS]


def pro_tips_text(tips):
    """Partner content written out for the coach chat, so it can quote it
    with credit."""
    if not tips:
        return ''
    lines = ["Partner coach material (shared with TennisAC with permission). You may quote or paraphrase "
             "it faithfully, crediting the coach by name. Never change its meaning, and never attribute "
             "anything else to a named coach:"]
    for t in tips:
        who = t['coach_name'] + (f" ({t['coach_credential']})" if t.get('coach_credential') else '')
        lines.append(f"  - On {t['topic']}, {who}: \"{t['title']}\" — {t['body']}")
    return "\n".join(lines)
