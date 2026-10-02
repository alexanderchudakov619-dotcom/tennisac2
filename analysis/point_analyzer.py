"""
point_analyzer.py
Uses the Anthropic API to analyze tennis point strategy from video motion data.
"""
import os
import json
import urllib.request
import urllib.error


# The model TennisAC uses for Point Play analysis.
# claude-haiku-4-5 is fast and cheap ($1/M input) — good while you're testing
# with your first users. If you want sharper tactical reasoning, swap this to
# "claude-sonnet-5" (a bit pricier but smarter).
CLAUDE_MODEL = "claude-haiku-4-5"


def _describe_ball_physics(ball_physics):
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

    # Read the API key from the environment. NEVER hard-code your key in the file.
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {
            "breakdown": [
                "TennisAC's AI coach isn't configured yet.",
                "The ANTHROPIC_API_KEY environment variable is not set on the server.",
            ],
            "shot_suggestions": [],
            "popup_tips": [],
            "too_good": "",
        }

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

    ball_physics_summary = _describe_ball_physics(ball_physics)

    prompt = f"""You are TennisAC, an expert AI tennis coach analyzing a tennis point.

PLAYER PROFILE:
{player_ctx}

POINT RESULT: The player {point_result.upper()} this point.

{context_line}

{motion_summary}

{ball_physics_summary}

Note: the motion data above is rough (frame-based motion only, not full body tracking),
so lean mostly on the point result, the player's own description, and the tracked ball
physics (when present) for your tactical read. Only reference a speed/depth/height/spin/
heaviness number if it's explicitly given above — never invent one that isn't there.

Based on this information, provide a detailed strategic point analysis.
You MUST respond with ONLY a valid JSON object in exactly this format, no other text:

{{
  "breakdown": [
    "First observation about the point strategy...",
    "Second observation...",
    "Third observation..."
  ],
  "shot_suggestions": [
    "Suggestion for an alternative shot that could have been played...",
    "Another suggestion if applicable..."
  ],
  "popup_tips": [
    "If you detect a dominant opponent pattern, give a tip here...",
    "Another pattern tip if applicable..."
  ],
  "too_good": "Only fill this in if the opponent's shot was genuinely too good and there was nothing the player could do. Otherwise leave as empty string."
}}

Rules:
- breakdown: 2-4 items analyzing what happened strategically in the point
- If point was WON: focus on what went well and 1-2 things that could be even better against tougher opponents
- If point was LOST: explain why, identify the key shot(s) that led to the loss, suggest what should have been done differently
- shot_suggestions: 1-3 specific alternative shots with reasoning. Always include at least one even if point was won.
- popup_tips: only include if you can detect a clear opponent pattern from the description. Can be empty array.
- too_good: only say "too good" if there was GENUINELY nothing the player could do from their position
- Keep each item concise, specific, and actionable. Talk directly to the player.
- Use tennis terminology correctly (down the line, crosscourt, inside-out, swing volley, etc.)
- Reference the player's profile when relevant (their UTR, playing style, preferred shots)"""

    payload = json.dumps({
        "model": CLAUDE_MODEL,
        "max_tokens": 1000,
        "messages": [{"role": "user", "content": prompt}]
    }).encode('utf-8')

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
            "x-api-key": api_key,
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            text = data['content'][0]['text'].strip()
            # Strip markdown fences if the model wrapped the JSON in ```
            if text.startswith('```'):
                text = text.split('\n', 1)[1]
                text = text.rsplit('```', 1)[0]
            return json.loads(text)

    except urllib.error.HTTPError as e:
        # Surface the real reason (bad key, wrong model name, rate limit, etc.)
        body = e.read().decode('utf-8', errors='ignore')
        print(f"[TennisAC] Anthropic API error {e.code}: {body}")
        reason = "your API key may be invalid" if e.code == 401 else f"API returned {e.code}"
        return {
            "breakdown": [
                f"The AI coach could not analyze this point ({reason}).",
                "Check the server logs for details.",
            ],
            "shot_suggestions": [],
            "popup_tips": [],
            "too_good": "",
        }

    except Exception as e:
        print(f"[TennisAC] Point analysis failed: {e}")
        return {
            "breakdown": [
                "Point analysis could not be completed at this time.",
                "Please try again with a clear video of the full point.",
            ],
            "shot_suggestions": [],
            "popup_tips": [],
            "too_good": "",
        }