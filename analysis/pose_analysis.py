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


def metric_entry(score, definition, ai_focus, fix_low, fix_high=None):
    """
    Build a metric result with a distinct definition/AI-focus/fix per metric,
    instead of leaving those fields empty and letting the template fall back
    to the same generic boilerplate for every card.
    """
    fix = fix_low if (score is None or score < 70 or not fix_high) else fix_high
    return {
        "score": score,
        "label": label(score),
        "definition": definition,
        "ai_focus": ai_focus,
        "fix": fix,
    }


# ── Shot-specific feedback rules ─────────────────────────────────────────────

def serve_feedback(m):
    """Generate serve-specific metrics and feedback."""
    results = {}
    tips = []

    # 1. Contact Height (wrist above hip — higher is better)
    ch = m.get("contact_height_score")
    ch_score = score_0_100(ch, 0.2, 0.85)  # 0.2 = barely above hip, 0.85 = well extended
    results["Contact Height"] = metric_entry(
        ch_score,
        definition="How high above your hip you're making contact with the ball, based on wrist position at the moment of impact.",
        ai_focus="Tracks your wrist's vertical position relative to your hip across the swing to find your peak contact point.",
        fix_low="Extend your arm fully and reach up into the toss instead of hitting out in front at hip height. A higher contact point reduces net errors and adds pop.",
        fix_high="You're reaching up well into the toss — keep this contact height consistent as you add pace.",
    )
    if ch_score is not None and ch_score < 60:
        tips.append("Your contact point appears low. Try extending your arm fully and reaching higher at the toss.")
    elif ch_score is not None and ch_score >= 80:
        tips.append("Good contact height — you are reaching up well into the toss.")

    # 2. Arm Extension (elbow angle — closer to 180° = fully extended)
    ae = m.get("arm_extension_max")
    ae_score = score_0_100(ae, 100, 175)   # 100 = very bent, 175 = nearly straight
    results["Arm Extension"] = metric_entry(
        ae_score,
        definition="Your elbow angle at contact — a fully extended arm (closer to 180°) gives you maximum reach and racquet head speed.",
        ai_focus="Measures your elbow joint angle at the moment of contact across the tracked frames.",
        fix_low="Your elbow is still bent at contact. Focus on 'reaching' for the ball rather than hitting with a bent arm — this adds both height and power.",
        fix_high="Your arm extension at contact is excellent — that's giving you full reach and racquet speed.",
    )
    if ae_score is not None and ae_score < 60:
        tips.append("Your elbow does not fully extend at contact. Work on straightening your arm through the swing.")

    # 3. Toss Consistency (std dev — lower is more consistent)
    tc = m.get("toss_consistency_std")
    tc_score = score_0_100(tc, 0.0, 0.15, invert=True)  # invert: lower std = better score
    results["Toss Consistency"] = metric_entry(
        tc_score,
        definition="How much your toss location varies from swing to swing, based on the spread of the release point across the clip.",
        ai_focus="Tracks the ball/hand release point on each service motion and compares their spread frame by frame.",
        fix_low="Your toss placement is drifting between swings. Practice isolated toss repetitions — release from the exact same spot every time before adding the swing.",
        fix_high="Your toss is landing in a tight, repeatable window — that consistency is a big asset.",
    )
    if tc_score is not None and tc_score < 60:
        tips.append("Your toss shows significant variation across frames. Practice isolated toss drills — release from the same point each time.")
    elif tc_score is not None and tc_score >= 80:
        tips.append("Your toss looks consistent — keep repeating this pattern.")

    # 4. Knee Bend / Athletic Stance (knee angle — lower = more bend = better loading)
    kb = m.get("knee_bend_avg")
    kb_score = score_0_100(kb, 100, 170, invert=True)   # 100 = deeply bent, 170 = almost straight
    results["Knee Bend (Loading)"] = metric_entry(
        kb_score,
        definition="How much you bend your knees before pushing up into the serve — deeper bend stores more energy for the leg drive.",
        ai_focus="Tracks your knee joint angle during the loading phase just before you drive upward.",
        fix_low="You're staying too upright before driving up. Sink lower into your legs during the loading phase so you can push off the ground with more force.",
        fix_high="Your knee bend is generating solid leg drive — that's a strong foundation for power.",
    )
    if kb_score is not None and kb_score < 50:
        tips.append("You are not bending your knees enough before serving. A deeper knee bend helps you drive upward and add power.")

    # 5. Shoulder Rotation
    sa = m.get("shoulder_angle_avg")
    sa_score = score_0_100(sa, 60, 160)
    results["Shoulder Turn"] = metric_entry(
        sa_score,
        definition="How far you rotate your shoulders away from the net on the backswing, which builds the coil you unwind into the ball.",
        ai_focus="Tracks the angle between your shoulder line and the baseline through the backswing.",
        fix_low="Your shoulder rotation is limited, which caps your power. Turn your back more fully toward the net before starting your swing.",
        fix_high="Your shoulder turn is generating a strong coil — that's translating into more racquet speed.",
    )
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
    results["Contact Point"] = metric_entry(
        ch_score,
        definition="The height of the ball at contact relative to your waist — for most forehands, contact between waist and shoulder height gives the cleanest strike.",
        ai_focus="Tracks your wrist height relative to your waist at the moment your racquet meets the ball.",
        fix_low="You're making contact low, which limits your options. Move your feet earlier so you can take the ball closer to waist height.",
        fix_high="Your contact height is right in the ideal zone for clean, powerful strikes.",
    )
    if ch_score is not None and ch_score < 50:
        tips.append("You may be hitting low on the forehand. Try to take the ball at or above waist height when possible.")

    # Arm extension
    ae = m.get("arm_extension_avg")
    ae_score = score_0_100(ae, 90, 160)
    results["Follow-Through Extension"] = metric_entry(
        ae_score,
        definition="How far your arm extends and finishes after contact, reflecting whether you're driving through the ball or stopping the swing short.",
        ai_focus="Measures your arm's extension angle through the finish of the swing.",
        fix_low="Your follow-through is cutting off early. Drive through the ball and let your racquet finish high over your opposite shoulder.",
        fix_high="You're finishing your swing fully — that follow-through is helping you drive through the ball.",
    )
    if ae_score is not None and ae_score < 60:
        tips.append("Your follow-through looks short. Extend through the ball and finish high over your shoulder.")

    # Trunk rotation
    tr = m.get("trunk_rotation_avg")
    tr_score = score_0_100(tr, 0.5, 1.2)
    results["Unit Turn / Rotation"] = metric_entry(
        tr_score,
        definition="How far your hips and shoulders rotate together as you prepare for the shot, which sets up an efficient, powerful swing.",
        ai_focus="Tracks your trunk rotation from the moment you identify the ball to the start of your forward swing.",
        fix_low="Your unit turn looks incomplete. As soon as you read the ball, turn your hips and shoulders together — don't wait until the last second.",
        fix_high="Your unit turn is complete and early — that's giving your swing a strong base to work from.",
    )
    if tr_score is not None and tr_score < 50:
        tips.append("Your unit turn may be incomplete. Rotate your hips and shoulders together early when the ball is coming.")

    # Knee bend
    kb = m.get("knee_bend_avg")
    kb_score = score_0_100(kb, 110, 170, invert=True)
    results["Footwork & Balance"] = metric_entry(
        kb_score,
        definition="Your knee bend and lower-body posture through the shot — staying lower keeps you balanced and ready to move.",
        ai_focus="Tracks your knee angle throughout the stroke to gauge how low and balanced your base is.",
        fix_low="You're playing too upright on your forehand. Bend your knees more to lower your center of gravity and stay balanced through contact.",
        fix_high="Your footwork and balance are solid — you're staying low and stable through the shot.",
    )
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
    results["Contact Point"] = metric_entry(
        ch_score,
        definition="Ball height at contact relative to your waist — for a two-handed backhand, a slightly earlier, higher contact point gives you more control.",
        ai_focus="Tracks your wrist height relative to your waist at the point of contact.",
        fix_low="Your contact point is low, which often means you're getting to the ball late. Get your feet set earlier so you can take it higher and out in front.",
        fix_high="You're meeting the ball at a strong contact height — that's giving you good control on the shot.",
    )
    if ch_score is not None and ch_score < 50:
        tips.append("Your backhand contact point looks low. Try to get into position earlier so you can take the ball at a comfortable height.")

    ae = m.get("arm_extension_avg")
    ae_score = score_0_100(ae, 80, 150)
    results["Extension Through Contact"] = metric_entry(
        ae_score,
        definition="How much room your arms have to extend through the ball — a jammed swing loses power and control.",
        ai_focus="Measures your arm extension angle from contact through the finish.",
        fix_low="Your swing looks jammed at contact. Take a small step back or adjust your spacing so you have room to extend through the shot.",
        fix_high="You're extending well through contact — that's giving the shot both power and control.",
    )
    if ae_score is not None and ae_score < 60:
        tips.append("You may be jamming the backhand. Give yourself more room to extend through the shot.")

    kb = m.get("knee_bend_avg")
    kb_score = score_0_100(kb, 110, 170, invert=True)
    results["Knee Bend"] = metric_entry(
        kb_score,
        definition="Your knee bend through the stroke — staying low keeps your head still and your strike more repeatable.",
        ai_focus="Tracks your knee angle throughout the backhand motion.",
        fix_low="You're standing too tall on your backhand. Bend your knees more and keep your head steady through contact.",
        fix_high="Your knee bend is helping you stay low and stable through the shot.",
    )
    if kb_score is not None and kb_score < 50:
        tips.append("Stay lower through the backhand — bend your knees and keep your head still.")

    tr = m.get("trunk_rotation_avg")
    tr_score = score_0_100(tr, 0.5, 1.2)
    results["Hip / Shoulder Turn"] = metric_entry(
        tr_score,
        definition="How far your hips and shoulders rotate together to prepare the shot, which is what generates power on a two-handed backhand.",
        ai_focus="Tracks your trunk rotation from your ready position through your backswing.",
        fix_low="Your hip and shoulder turn looks limited. Rotate your torso more fully away from the ball before swinging forward.",
        fix_high="Your hip and shoulder turn is generating good coil for the shot.",
    )
    if tr_score is not None and tr_score < 50:
        tips.append("Your hip and shoulder turn looks limited on the backhand. Rotate your torso more fully away from the ball before swinging forward.")

    if not tips:
        tips.append("Backhand looks solid. Keep working on depth and consistency.")

    return results, tips


def rally_feedback(m):
    """General rally / movement feedback."""
    results = {}
    tips = []

    kb = m.get("knee_bend_avg")
    kb_score = score_0_100(kb, 100, 170, invert=True)
    results["Recovery Stance"] = metric_entry(
        kb_score,
        definition="Your knee bend during recovery between shots — a lower stance lets you push off quickly in any direction.",
        ai_focus="Tracks your knee angle during the recovery moments between shots in the rally.",
        fix_low="Your recovery stance is too upright. Stay on the balls of your feet with knees bent so you can react and change direction faster.",
        fix_high="Your recovery stance is athletic and ready — that's helping you react quickly between shots.",
    )
    if kb_score is not None and kb_score < 50:
        tips.append("Your recovery stance looks too upright. Stay on the balls of your feet with knees bent so you can react faster.")

    tr = m.get("trunk_rotation_avg")
    tr_score = score_0_100(tr, 0.5, 1.2)
    results["Body Rotation"] = metric_entry(
        tr_score,
        definition="How much your trunk rotates into each shot, showing whether you're using your whole body or just your arm.",
        ai_focus="Tracks your trunk rotation across the shots in this rally.",
        fix_low="You're relying on your arm rather than your body. Rotate your hips and shoulders into each shot for more consistent power.",
        fix_high="You're rotating your body well into each shot — that's a repeatable power source.",
    )
    if tr_score is not None and tr_score < 50:
        tips.append("Work on rotating your whole body into shots rather than just swinging with your arm.")

    ae = m.get("arm_extension_avg")
    ae_score = score_0_100(ae, 90, 160)
    results["Swing Extension"] = metric_entry(
        ae_score,
        definition="How fully your arm extends through your shots during the rally, reflecting whether you're driving through the ball consistently.",
        ai_focus="Measures your arm extension angle across the shots in this rally.",
        fix_low="Your swings are cutting short during the rally. Focus on driving through each ball rather than just blocking it back.",
        fix_high="You're extending well through your shots — that's a sign of controlled, repeatable power.",
    )
    if ae_score is not None and ae_score < 50:
        tips.append("Your swings are cutting short during the rally. Focus on driving through each ball rather than just blocking it back.")

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
