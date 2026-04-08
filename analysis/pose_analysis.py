"""
pose_analysis.py
Converts raw metrics from video_processor into human-readable scores and feedback.
Each shot type has its own set of rules.
"""


# ── Scoring helpers ──────────────────────────────────────────────────────────

def score_0_100(value, low, high, invert=False):
    """Map a value between low and high to a 0-100 score."""
    if value is None:
        return None
    clamped = max(low, min(high, value))
    score = (clamped - low) / (high - low) * 100
    return round(100 - score if invert else score, 1)


def label(score):
    """Convert numeric score to a label."""
    if score is None: return "N/A"
    if score >= 80:   return "Excellent"
    if score >= 60:   return "Good"
    if score >= 40:   return "Needs Work"
    return "Poor"


# ── Shot-specific feedback rules ─────────────────────────────────────────────

def serve_feedback(m):
    """Generate serve-specific metrics and feedback."""
    results = {}
    tips = []

    # 1. Contact Height (wrist above hip — higher is better)
    ch = m.get("contact_height_score")
    ch_score = score_0_100(ch, 0.2, 0.85)  # 0.2 = barely above hip, 0.85 = well extended
    results["Contact Height"] = {"score": ch_score, "label": label(ch_score)}
    if ch_score is not None and ch_score < 60:
        tips.append("Your contact point appears low. Try extending your arm fully and reaching higher at the toss.")
    elif ch_score is not None and ch_score >= 80:
        tips.append("Good contact height — you are reaching up well into the toss.")

    # 2. Arm Extension (elbow angle — closer to 180° = fully extended)
    ae = m.get("arm_extension_max")
    ae_score = score_0_100(ae, 100, 175)   # 100 = very bent, 175 = nearly straight
    results["Arm Extension"] = {"score": ae_score, "label": label(ae_score)}
    if ae_score is not None and ae_score < 60:
        tips.append("Your elbow does not fully extend at contact. Work on straightening your arm through the swing.")

    # 3. Toss Consistency (std dev — lower is more consistent)
    tc = m.get("toss_consistency_std")
    tc_score = score_0_100(tc, 0.0, 0.15, invert=True)  # invert: lower std = better score
    results["Toss Consistency"] = {"score": tc_score, "label": label(tc_score)}
    if tc_score is not None and tc_score < 60:
        tips.append("Your toss shows significant variation across frames. Practice isolated toss drills — release from the same point each time.")
    elif tc_score is not None and tc_score >= 80:
        tips.append("Your toss looks consistent — keep repeating this pattern.")

    # 4. Knee Bend / Athletic Stance (knee angle — lower = more bend = better loading)
    kb = m.get("knee_bend_avg")
    kb_score = score_0_100(kb, 100, 170, invert=True)   # 100 = deeply bent, 170 = almost straight
    results["Knee Bend (Loading)"] = {"score": kb_score, "label": label(kb_score)}
    if kb_score is not None and kb_score < 50:
        tips.append("You are not bending your knees enough before serving. A deeper knee bend helps you drive upward and add power.")

    # 5. Shoulder Rotation
    sa = m.get("shoulder_angle_avg")
    sa_score = score_0_100(sa, 60, 160)
    results["Shoulder Turn"] = {"score": sa_score, "label": label(sa_score)}
    if sa_score is not None and sa_score < 50:
        tips.append("Your shoulder rotation looks limited. Try to rotate your shoulders more fully on the backswing.")

    if not tips:
        tips.append("Your serve mechanics look solid overall. Keep focusing on consistency under match pressure.")

    return results, tips


def forehand_feedback(m):
    """Generate forehand-specific feedback."""
    results = {}
    tips = []

    # Contact Point (wrist height — for forehand, mid-to-high is ideal)
    ch = m.get("contact_height_avg")
    ch_score = score_0_100(ch, 0.0, 0.5)
    results["Contact Point"] = {"score": ch_score, "label": label(ch_score)}
    if ch_score is not None and ch_score < 50:
        tips.append("You may be hitting low on the forehand. Try to take the ball at or above waist height when possible.")

    # Arm extension
    ae = m.get("arm_extension_avg")
    ae_score = score_0_100(ae, 90, 160)
    results["Follow-Through Extension"] = {"score": ae_score, "label": label(ae_score)}
    if ae_score is not None and ae_score < 60:
        tips.append("Your follow-through looks short. Extend through the ball and finish high over your shoulder.")

    # Trunk rotation
    tr = m.get("trunk_rotation_avg")
    tr_score = score_0_100(tr, 0.5, 1.2)
    results["Unit Turn / Rotation"] = {"score": tr_score, "label": label(tr_score)}
    if tr_score is not None and tr_score < 50:
        tips.append("Your unit turn may be incomplete. Rotate your hips and shoulders together early when the ball is coming.")

    # Knee bend
    kb = m.get("knee_bend_avg")
    kb_score = score_0_100(kb, 110, 170, invert=True)
    results["Footwork & Balance"] = {"score": kb_score, "label": label(kb_score)}
    if kb_score is not None and kb_score < 50:
        tips.append("You appear upright on forehands. Bend your knees to stay low and balanced through the shot.")

    if not tips:
        tips.append("Forehand mechanics look good! Focus on shot selection and placement during rallies.")

    return results, tips


def backhand_feedback(m):
    """Generate backhand-specific feedback (two-handed assumed)."""
    results = {}
    tips = []

    ch = m.get("contact_height_avg")
    ch_score = score_0_100(ch, 0.0, 0.45)
    results["Contact Point"] = {"score": ch_score, "label": label(ch_score)}
    if ch_score is not None and ch_score < 50:
        tips.append("Your backhand contact point looks low. Try to get into position earlier so you can take the ball at a comfortable height.")

    ae = m.get("arm_extension_avg")
    ae_score = score_0_100(ae, 80, 150)
    results["Extension Through Contact"] = {"score": ae_score, "label": label(ae_score)}
    if ae_score is not None and ae_score < 60:
        tips.append("You may be jamming the backhand. Give yourself more room to extend through the shot.")

    kb = m.get("knee_bend_avg")
    kb_score = score_0_100(kb, 110, 170, invert=True)
    results["Knee Bend"] = {"score": kb_score, "label": label(kb_score)}
    if kb_score is not None and kb_score < 50:
        tips.append("Stay lower through the backhand — bend your knees and keep your head still.")

    tr = m.get("trunk_rotation_avg")
    tr_score = score_0_100(tr, 0.5, 1.2)
    results["Hip / Shoulder Turn"] = {"score": tr_score, "label": label(tr_score)}

    if not tips:
        tips.append("Backhand looks solid. Keep working on depth and consistency.")

    return results, tips


def rally_feedback(m):
    """General rally / movement feedback."""
    results = {}
    tips = []

    kb = m.get("knee_bend_avg")
    kb_score = score_0_100(kb, 100, 170, invert=True)
    results["Recovery Stance"] = {"score": kb_score, "label": label(kb_score)}
    if kb_score is not None and kb_score < 50:
        tips.append("Your recovery stance looks too upright. Stay on the balls of your feet with knees bent so you can react faster.")

    tr = m.get("trunk_rotation_avg")
    tr_score = score_0_100(tr, 0.5, 1.2)
    results["Body Rotation"] = {"score": tr_score, "label": label(tr_score)}
    if tr_score is not None and tr_score < 50:
        tips.append("Work on rotating your whole body into shots rather than just swinging with your arm.")

    ae = m.get("arm_extension_avg")
    ae_score = score_0_100(ae, 90, 160)
    results["Swing Extension"] = {"score": ae_score, "label": label(ae_score)}

    if not tips:
        tips.append("Movement and rally mechanics look consistent. Keep working on positioning before each shot.")

    return results, tips


# ── Main entry point ──────────────────────────────────────────────────────────

def generate_feedback(metrics, shot_type='serve'):
    """
    Given metrics dict from video_processor and a shot_type string,
    return a dict with per-metric scores and a list of coaching tips.
    """
    if "error" in metrics:
        return {"scores": {}, "tips": ["Could not process video. Please try again with a clearer clip."]}

    dispatch = {
        "serve":     serve_feedback,
        "forehand":  forehand_feedback,
        "backhand":  backhand_feedback,
        "rally":     rally_feedback,
    }

    fn = dispatch.get(shot_type, serve_feedback)
    scores, tips = fn(metrics)

    # Overall score = average of available metric scores
    valid = [v["score"] for v in scores.values() if v["score"] is not None]
    overall = round(sum(valid) / len(valid), 1) if valid else None

    return {
        "scores": scores,
        "tips": tips,
        "overall_score": overall,
        "overall_label": label(overall),
    }


# ── Profile-aware feedback wrapper ────────────────────────────────────────────

def _profile_intro(profile, shot_type):
    """Generate a personalized intro tip based on player profile."""
    tips = []
    if not profile:
        return tips

    play_like = profile.get('play_like')
    utr = profile.get('utr')
    tactical = profile.get('tactical_pref', '')
    best_shot = profile.get('best_shot')
    backhand = profile.get('backhand_style')
    point_len = profile.get('point_length', '')

    if play_like:
        tips.append(f"Based on your goal to play like {play_like}, focus on the mechanics that define their game — consistency, court position, and shot selection.")

    if utr:
        try:
            utr_val = float(utr)
            if utr_val < 5:
                tips.append("At your current UTR, consistency should be your #1 priority over power. Build reliable mechanics first.")
            elif utr_val < 8:
                tips.append("At your level, small technique improvements compound quickly. Focus on the feedback below and drill it repeatedly.")
            else:
                tips.append("At a high UTR, marginal gains matter. The details in your mechanics below can be the difference in tight matches.")
        except:
            pass

    if shot_type == 'backhand' and backhand:
        tips.append(f"Your {backhand} backhand technique is being analyzed — the feedback below is tailored to that style.")

    if 'quick finish' in tactical.lower() and shot_type in ['serve', 'forehand']:
        tips.append("Since you prefer finishing points quickly, work on generating more power and disguise on this shot to set up early winners.")
    elif 'resistance' in tactical.lower():
        tips.append("As a player who likes long rallies, focus on consistency and depth — make sure your mechanics hold up over extended exchanges.")

    return tips


# Monkey-patch generate_feedback to accept profile
_original_generate_feedback = generate_feedback

def generate_feedback(metrics, shot_type='serve', profile=None):
    result = _original_generate_feedback(metrics, shot_type)
    if profile:
        intro_tips = _profile_intro(profile, shot_type)
        result['tips'] = intro_tips + result['tips']
        name = profile.get('name', '')
        if name:
            result['greeting'] = f"Here's your analysis, {name.split()[0]}."
    return result
