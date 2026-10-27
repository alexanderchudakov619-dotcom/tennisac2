"""
point_analyzer.py
Uses the Anthropic API to analyze tennis point strategy from video motion data.
"""
from typing import List, Literal, Optional

import anthropic
from pydantic import BaseModel, Field

from analysis.coaching_styles import style_prompt


# The model TennisAC uses for Point Play analysis.
# claude-haiku-4-5 is fast and cheap ($1/M input) — good while you're testing
# with your first users. If you want sharper tactical reasoning, swap this to
# "claude-sonnet-5" (a bit pricier but smarter).
CLAUDE_MODEL = "claude-haiku-4-5"


# The shape Claude's answer must take (enforced by structured outputs, so
# the JSON always parses and every field is there).

class FlowShot(BaseModel):
    shot: str = Field(description="Which of the player's shots, e.g. 'Your return', 'Your 2nd forehand', 'Your final shot'")
    situation: Literal["defence", "neutral", "attack"]
    note: str = Field(description="One short sentence: why this was defence/neutral/attack and whether the shot choice fit it")


class TacticRating(BaseModel):
    score: Optional[int] = Field(description="0-100, or null if there isn't enough information in the point to judge it")
    note: str = Field(description="One or two sentences on what in this point earned the score, or why it can't be judged")


class Tactics(BaseModel):
    building_the_point: TacticRating = Field(description="Defence -> neutral -> attack: progressing the point with the right shot for each situation")
    shot_selection: TacticRating = Field(description="Attacking short balls, not overhitting neutral balls, not going for too much from defence")
    rally_length: TacticRating = Field(description="Rally length awareness: patience, not finishing faster than needed; the first 4 shots (serve, return, +1)")
    counterpunch: TacticRating = Field(description="Redirecting the opponent's pace from defence (often down the line). score must be null unless one actually happened")


class CoachPoint(BaseModel):
    text: str = Field(description="The point itself, one or two sentences")
    detail: str = Field(description="Shown when the player taps the card: 1-2 sentences on why it matters in this point, then what to train next (a drill, pattern or cue)")


class PointAnalysis(BaseModel):
    breakdown: List[CoachPoint]
    shot_suggestions: List[CoachPoint]
    popup_tips: List[CoachPoint]
    too_good: str
    point_flow: List[FlowShot]
    tactics: Tactics


TACTIC_NAMES = {
    "building_the_point": "Defence → Neutral → Attack",
    "shot_selection": "Shot Selection",
    "rally_length": "Rally Length Awareness",
    "counterpunch": "Counterpunch",
}


def tactical_score(tactics):
    """Average of the tactics that could be judged; a counterpunch that
    never happened (null) doesn't count against the player."""
    scores = [max(0, min(100, t["score"])) for t in (tactics or {}).values()
              if isinstance(t, dict) and isinstance(t.get("score"), int)]
    return round(sum(scores) / len(scores)) if scores else None


def _fallback(*lines):
    return {"breakdown": list(lines), "shot_suggestions": [], "popup_tips": [], "too_good": "",
            "point_flow": [], "tactics": None}


def describe_ball_physics(ball_physics):
    """Turns the tracked per-shot physics into plain-language lines for
    the prompt. Only ever states a number when its 'available' flag is
    True — there is no fallback guess here, because a wrong number stated
    as fact is worse than no number at all."""
    if not ball_physics or not ball_physics.get('shots'):
        return ""

    lines = []
    for i, shot in enumerate(ball_physics['shots'], start=1):
        parts = []
        speed = shot.get('speed_mph', {})
        if speed.get('available'):
            parts.append(f"speed {speed['value']} mph")
        depth = shot.get('depth', {})
        if depth.get('available'):
            parts.append(f"landed {depth['label'].lower()} ({depth['from_baseline_ft']} ft from the baseline)")
        height = shot.get('height', {})
        if height.get('available'):
            parts.append(f"cleared the net by {height['net_clearance_ft']} ft")
        spin = shot.get('spin', {})
        if spin.get('available'):
            parts.append(f"{spin['type'].lower()} spin (from {' + '.join(spin['sources'])})")
        swing = shot.get('swing')
        if swing:
            swing_parts = [f"player's {swing['shot_type'].lower()}"]
            if swing['path']['available']:
                swing_parts.append(f"swing path {swing['path']['label'].lower()} ({swing['path']['angle_deg']} deg)")
            if swing['roll']['available']:
                swing_parts.append(f"{swing['roll']['label'].lower()} forearm roll")
            if swing['hand_speed_mph']['available']:
                swing_parts.append(f"hand speed {swing['hand_speed_mph']['value']} mph")
            swing_parts.append(f"contact {swing['contact_height'].lower()}")
            parts.insert(0, ", ".join(swing_parts))
        heaviness = shot.get('heaviness', {})
        if heaviness.get('available'):
            parts.append(f"heaviness index {heaviness['score']}/100")
        if parts:
            lines.append(f"Shot {i}: " + ", ".join(parts) + ".")

    if not lines:
        return "Ball tracking ran on this clip but wasn't confident enough to report per-shot numbers — don't invent any."
    return "Tracked ball physics (measured from the video, not a guess):\n" + "\n".join(lines)


def analyze_point_with_ai(motion_metrics, point_result, point_context, profile, ball_physics=None):
    """
    Sends point data + player profile to Claude and gets back
    structured strategic analysis.
    """

    # The key comes from the ANTHROPIC_API_KEY environment variable. NEVER
    # hard-code your key in the file.
    try:
        client = anthropic.Anthropic()
    except anthropic.AnthropicError:
        return _fallback("TennisAC's AI coach isn't configured yet.",
                         "The ANTHROPIC_API_KEY environment variable is not set on the server.")

    # Build player context string
    player_ctx = ""
    if profile:
        parts = []
        if profile.get('name'):          parts.append(f"Player name: {profile['name']}")
        if profile.get('utr'):           parts.append(f"UTR: {profile['utr']}")
        if profile.get('player_type'):   parts.append(f"Player type: {profile['player_type']}")
        if profile.get('tactical_pref'): parts.append(f"Tactical preference: {profile['tactical_pref']}")
        if profile.get('play_like'):     parts.append(f"Wants to play like: {profile['play_like']}")
        if profile.get('dominant_hand'): parts.append(f"Dominant hand: {profile['dominant_hand']}")
        if profile.get('backhand_style'):parts.append(f"Backhand: {profile['backhand_style']}")
        if profile.get('best_shot'):     parts.append(f"Best shot: {profile['best_shot']}")
        if profile.get('point_length'):  parts.append(f"Prefers: {profile['point_length']}")
        if profile.get('shot_order'):    parts.append(f"Shot ranking: {profile['shot_order']}")
        player_ctx = "\n".join(parts)

    # Build motion summary
    motion_summary = f"""
Video motion data:
- Duration: {motion_metrics.get('duration_sec', 'N/A')} seconds
- Swings detected for the player: {motion_metrics.get('swings_detected', 'N/A')}
- Average knee angle at the lowest point around contact (degrees; ~110-125 is a strong athletic base, 150+ is upright): {motion_metrics.get('knee_bend_avg', 'N/A')}
- Average shoulder rotation through each swing (degrees; tour players ~85+): {motion_metrics.get('trunk_rotation_avg', 'N/A')}
- Average hip-shoulder separation (degrees; 30+ is a strong coil): {motion_metrics.get('hip_shoulder_separation', 'N/A')}
- Average elbow angle at contact (degrees; low means a jammed arm): {motion_metrics.get('arm_extension_avg', 'N/A')}
(Body mechanics are measured from pose tracking; N/A means the player wasn't tracked clearly enough — don't invent them.)
""".strip()

    context_line = (
        f"Player's description of the point: {point_context}"
        if point_context else "No additional context provided."
    )

    ball_physics_summary = describe_ball_physics(ball_physics)
    style = style_prompt((profile or {}).get('coach_style'))
    style_line = f"\n{style} Apply this to how every text and detail is written; the scores stay objective.\n" if style else ""

    prompt = f"""You are TennisAC, an expert AI tennis coach analyzing a tennis point.

PLAYER PROFILE:
{player_ctx}
{style_line}
POINT RESULT: The player {point_result.upper()} this point.

{context_line}

{motion_summary}

{ball_physics_summary}

Note: the motion data above is rough (frame-based motion only, not full body tracking),
so lean mostly on the point result, the player's own description, and the tracked ball
physics (when present) for your tactical read. Only reference a speed/depth/height/spin/
heaviness number if it's explicitly given above — never invent one that isn't there.

Based on this information, provide a detailed strategic point analysis.

Rules:
- breakdown: 2-4 items analyzing what happened strategically in the point
- If point was WON: focus on what went well and 1-2 things that could be even better against tougher opponents
- If point was LOST: explain why, identify the key shot(s) that led to the loss, suggest what should have been done differently
- shot_suggestions: 1-3 specific alternative shots with reasoning. Always include at least one even if point was won.
- popup_tips: only include if you can detect a clear opponent pattern from the description. Can be empty array.
- too_good: only say "too good" if there was GENUINELY nothing the player could do from their position
- Keep each item concise, specific, and actionable. Talk directly to the player.
- Every breakdown, shot_suggestions and popup_tips item has a text and a detail. The detail is the deeper "why" for that item in this point plus one concrete thing to train (a drill, pattern or cue) — don't just restate the text.
- Use tennis terminology correctly (down the line, crosscourt, inside-out, swing volley, etc.)
- Reference the player's profile when relevant (their UTR, playing style, preferred shots)

Tactics (how coaches break down decision-making in a rally):
- point_flow: the player's shots in order (as many as you can tell apart, up to 8), each labelled by the situation the player was in when hitting it:
  defence = not in control, pushed wide or deep, trying to neutralize; neutral = both players trying to take control;
  attack = in control, a short or weak ball to hurt, creating chances to force an error or hit a winner.
  Base it on the description and the tracked depth/speed. If the shots can't be told apart, give fewer entries rather than inventing any.
- tactics: score each 0-100 with a short note tied to something that happened in this point.
  - building_the_point: did the player progress defence -> neutral -> attack with the right shot for each situation (neutralize when stretched, build when neutral, finish when in control)?
  - shot_selection: attacking short balls, not overhitting neutral balls, not going for winners from defence, keeping rally control.
  - rally_length: patience and discipline — playing as long as it takes instead of forcing it early; for short points, the quality of the first 4 shots (serve, return, +1).
  - counterpunch: a shot from defence, on the run but balanced, that uses the opponent's pace to redirect (often down the line) and turn the point. If no counterpunch happened in this point, score must be null and the note should say it didn't come up.
  - Use null for any category the information can't support, and say why in the note. A null is better than a guess."""

    try:
        response = client.with_options(timeout=60.0).messages.parse(
            model=CLAUDE_MODEL,
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
            output_format=PointAnalysis,
        )
    except anthropic.AuthenticationError:
        print("[TennisAC] Point analysis: the Anthropic API key was rejected.")
        return _fallback("The AI coach could not analyze this point (your API key may be invalid).",
                         "Check the server logs for details.")
    except anthropic.RateLimitError:
        return _fallback("The AI coach is busy right now.", "Please try this point again in a minute.")
    except anthropic.APIStatusError as e:
        # Surface the real reason (wrong model name, bad request, outage, etc.)
        print(f"[TennisAC] Anthropic API error {e.status_code}: {e.message}")
        return _fallback(f"The AI coach could not analyze this point (API returned {e.status_code}).",
                         "Check the server logs for details.")
    except anthropic.APIConnectionError:
        print("[TennisAC] Point analysis could not reach the Anthropic API.")
        return _fallback("Point analysis could not be completed at this time.",
                         "Please try again in a moment.")
    except ValueError as e:
        # A reply cut off mid-JSON fails validation (pydantic and JSON
        # errors are both ValueErrors).
        print(f"[TennisAC] Point analysis reply didn't parse: {e}")
        return _fallback("Point analysis could not be completed at this time.",
                         "Please try again with a clear video of the full point.")

    if response.parsed_output is None:
        print(f"[TennisAC] Point analysis returned no usable answer (stop_reason={response.stop_reason}).")
        return _fallback("Point analysis could not be completed at this time.",
                         "Please try again with a clear video of the full point.")
    analysis = response.parsed_output.model_dump()
    analysis["tactical_score"] = tactical_score(analysis["tactics"])
    return analysis