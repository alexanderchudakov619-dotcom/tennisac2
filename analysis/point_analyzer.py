"""
point_analyzer.py
Uses the Anthropic API to analyze tennis point strategy from video motion data.
"""
import json
import urllib.request


def analyze_point_with_ai(motion_metrics, point_result, point_context, profile):
    """
    Sends point data + player profile to Claude API and gets back
    structured strategic analysis.
    """

    # Build player context string
    player_ctx = ""
    if profile:
        parts = []
        if profile.get('name'):        parts.append(f"Player name: {profile['name']}")
        if profile.get('utr'):         parts.append(f"UTR: {profile['utr']}")
        if profile.get('player_type'): parts.append(f"Player type: {profile['player_type']}")
        if profile.get('tactical_pref'): parts.append(f"Tactical preference: {profile['tactical_pref']}")
        if profile.get('play_like'):   parts.append(f"Wants to play like: {profile['play_like']}")
        if profile.get('dominant_hand'): parts.append(f"Dominant hand: {profile['dominant_hand']}")
        if profile.get('backhand_style'): parts.append(f"Backhand: {profile['backhand_style']}")
        if profile.get('best_shot'):   parts.append(f"Best shot: {profile['best_shot']}")
        if profile.get('point_length'): parts.append(f"Prefers: {profile['point_length']}")
        if profile.get('shot_order'):  parts.append(f"Shot ranking: {profile['shot_order']}")
        player_ctx = "\n".join(parts)

    # Build motion summary
    motion_summary = f"""
Video motion data:
- Frames analyzed: {motion_metrics.get('frames_analyzed', 'N/A')}
- Duration: {motion_metrics.get('duration_sec', 'N/A')} seconds
- Upper body activity ratio: {motion_metrics.get('upper_body_ratio', 'N/A')}
- Motion consistency: {motion_metrics.get('motion_consistency', 'N/A')}
- Lateral balance: {motion_metrics.get('lateral_balance', 'N/A')}
- Contact height score: {motion_metrics.get('contact_height_score', 'N/A')}
""".strip()

    context_line = f"Player's description of the point: {point_context}" if point_context else "No additional context provided."

    prompt = f"""You are TennisAC, an expert AI tennis coach analyzing a tennis point.

PLAYER PROFILE:
{player_ctx}

POINT RESULT: The player {point_result.upper()} this point.

{context_line}

{motion_summary}

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
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1000,
        "messages": [{"role": "user", "content": prompt}]
    }).encode('utf-8')

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
            "x-api-key": ""  # handled by environment
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            text = data['content'][0]['text'].strip()
            # Strip markdown fences if present
            if text.startswith('```'):
                text = text.split('\n', 1)[1]
                text = text.rsplit('```', 1)[0]
            return json.loads(text)
    except Exception as e:
        # Fallback if API fails
        return {
            "breakdown": [
                f"Point analysis could not be completed at this time ({str(e)[:60]}).",
                "Please try again with a clear video of the full point."
            ],
            "shot_suggestions": [],
            "popup_tips": [],
            "too_good": ""
        }
