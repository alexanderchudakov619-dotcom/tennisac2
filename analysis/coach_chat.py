"""
coach_chat.py
The "Ask your coach" chat on the results page: answers a player's
questions about one analysis ("why did this score low?", "what should I
fix first?"), knowing their profile and every measured metric.
"""
import anthropic

CHAT_MODEL = "claude-opus-5-5"

# Kept to the last few turns: plenty for a conversation about one clip,
# and it caps what a single request can cost.
MAX_TURNS = 20
MAX_MESSAGE_CHARS = 2000

COACH_SYSTEM = """You are TennisAC's coach: a high-performance tennis coach talking with a player about one video analysis of their stroke. The players are intermediate to advanced (high school varsity and up), so use real tennis language: unit turn, trophy position, kinetic chain, pronation, split step, low-to-high, crosscourt, and so on.

How to coach:
- Ground every answer in the analysis below. Name the metric and the phase it belongs to when it helps ("in phase 5, the loading phase, your knee bend...").
- The analysis was measured from body tracking on a phone video. Only the metrics listed were measured; phases marked "not measured" are checkpoints the player has to look for on their own video. Never invent a number, angle or speed that isn't in the analysis. If they ask about something it doesn't cover (racquet speed, ball spin on this clip, grip), say it wasn't measured and coach from what they describe instead.
- When they ask what to work on, pick the one or two fixes that matter most (the lowest scores early in the shot usually cause problems later in it) rather than listing everything.
- Give concrete cues and drills they can take to the court, with reps or a simple progression.
- Fit the advice to their profile when it's given (UTR, backhand style, the player they want to play like, how they like to win points).
- Be direct and encouraging, like a good coach courtside. This is a small chat window: keep answers under about 150 words unless they ask for more detail. Plain sentences, with "- " bullets for drills or steps; **bold** at most a few key words; no headings or tables.
- You can't see the video itself, only the measurements. If something can only be judged by watching, tell them what to look for when they watch it back.
- Stay on tennis and this player's game. Not a substitute for a medical professional: if they mention pain or an injury, tell them to get it checked before training through it."""


def _fmt_score(data):
    if data.get("score") is None:
        return "not measured in this clip"
    parts = [f"{data['score']}/100 ({data.get('label')})"]
    if data.get("value"):
        parts.append(f"player: {data['value']}")
    if data.get("target"):
        parts.append(f"pros: {data['target']}")
    if data.get("detail"):
        parts.append(f"measured: {data['detail']}")
    return "; ".join(parts)


def build_context(shot_type, overall_score, overall_label, scores, phases, mistakes, profile):
    """The analysis written out for the coach, phase by phase."""
    lines = [f"Shot analyzed: {shot_type}",
             f"Overall score: {overall_score if overall_score is not None else 'n/a'}/100 ({overall_label})", ""]
    for p in phases:
        if p["metrics"]:
            phase_score = f"{p['score']}/100" if p["score"] is not None else "not measured"
            lines.append(f"Phase {p['number']}: {p['name']} ({phase_score})")
            for name in p["metrics"]:
                lines.append(f"  - {name}: {_fmt_score(scores[name])}")
        else:
            lines.append(f"Phase {p['number']}: {p['name']} (not measured; checkpoint: {p['focus']})")
    if mistakes:
        lines += ["", "Common mistakes flagged: " + "; ".join(f"{m['mistake']} (phase {m['phase']})" for m in mistakes)]

    known = {k: v for k, v in (profile or {}).items() if v}
    if known:
        lines += ["", "Player profile:"] + [f"  - {k.replace('_', ' ')}: {v}" for k, v in known.items()]
    return "\n".join(lines)


def clean_history(raw):
    """The conversation as the browser sent it, checked: user/assistant
    turns of plain text, starting and ending with the player, trimmed to
    the most recent turns. Returns None if it isn't a usable history."""
    if not isinstance(raw, list):
        return None
    msgs = []
    for m in raw[-MAX_TURNS:]:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
            return None
        text = m.get("content")
        if not isinstance(text, str) or not text.strip():
            return None
        msgs.append({"role": m["role"], "content": text.strip()[:MAX_MESSAGE_CHARS]})
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    if not msgs or msgs[-1]["role"] != "user":
        return None
    return msgs


def ask_coach(context, history):
    """One coach reply. Returns (reply_text, error_message); exactly one
    is set."""
    try:
        client = anthropic.Anthropic()
    except anthropic.AnthropicError:
        return None, "The coach chat isn't set up on the server yet (no API key)."

    try:
        response = client.beta.messages.create(
            model=CHAT_MODEL,
            max_tokens=2000,
            # Chat answers are short; low effort keeps replies quick.
            output_config={"effort": "low"},
            # If a request is declined, retry it on a fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=[
                {"type": "text", "text": COACH_SYSTEM},
                {"type": "text", "text": "The analysis:\n" + context,
                 "cache_control": {"type": "ephemeral"}},
            ],
            messages=history,
        )
    except anthropic.AuthenticationError:
        print("[TennisAC] Coach chat: the Anthropic API key was rejected.")
        return None, "The coach chat isn't available right now."
    except anthropic.RateLimitError:
        return None, "The coach is busy right now. Try again in a minute."
    except anthropic.BadRequestError as e:
        print(f"[TennisAC] Coach chat bad request: {e.message}")
        return None, "The coach couldn't answer that one. Try asking another way."
    except anthropic.APIStatusError as e:
        print(f"[TennisAC] Coach chat API error {e.status_code}: {e.message}")
        return None, "The coach chat hit a problem. Try again in a moment."
    except anthropic.APIConnectionError:
        print("[TennisAC] Coach chat could not reach the Anthropic API.")
        return None, "Couldn't reach the coach. Check your connection and try again."

    if response.stop_reason == "refusal":
        return None, "The coach can't help with that. Ask about your tennis instead."
    text = "\n\n".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        return None, "The coach didn't have an answer for that. Try asking another way."
    return text, None
