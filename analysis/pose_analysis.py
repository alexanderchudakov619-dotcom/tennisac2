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


def metric_entry(score, definition, ai_focus, fix_low, fix_high=None, value=None, target=None):
    """
    Build a metric result with a distinct definition/AI-focus/fix per metric,
    instead of leaving those fields empty and letting the template fall back
    to the same generic boilerplate for every card. `value` is the player's
    measured number in plain words/units; `target` is the tour-level range.
    """
    fix = fix_low if (score is None or score < 70 or not fix_high) else fix_high
    return {
        "score": score,
        "label": label(score),
        "definition": definition,
        "ai_focus": ai_focus,
        "fix": fix,
        "value": value,
        "target": target,
    }


# Display helpers. Heights are measured in torso lengths (shoulders-to-hips),
# which keeps them independent of how tall the player is or how far away
# the camera was; these turn them back into words people use.
def _deg(v):
    return f"{v:.0f}°" if v is not None else None

def _serve_reach(ch):
    # Full serve reach puts the hand ~2.1 torso lengths above the hips.
    return f"{min(100, round(ch / 2.1 * 100))}% of full reach" if ch is not None else None

def _height_zone(ch):
    if ch is None:
        return None
    if ch >= 1.0:
        return "Shoulder height or above"
    if ch >= 0.55:
        return "Chest height"
    if ch >= 0.15:
        return "Waist height"
    return "Below the waist"

def _toss_spread(std):
    # ~20-inch torso for an average adult; close enough to put it in inches.
    return f"About {std * 20:.0f} in between serves" if std is not None else None

NO_KNEE = ("We couldn't see your legs clearly enough to measure this — film with "
           "your whole body in frame, feet included.")
NO_TOSS = "Needs at least two serves in the clip to measure — upload a clip with 2–3 serves in a row."


def _separation_metric(m):
    sep = m.get("hip_shoulder_separation")
    sc = score_0_100(sep, 5, 40)
    return metric_entry(
        sc,
        definition="The gap between how far your shoulders turn and how far your hips turn — the 'coil' that stores energy, like winding a spring.",
        ai_focus="Compares your shoulder-line and hip-line rotation frame by frame from the 3D skeleton and finds the biggest gap.",
        fix_low="Your hips and shoulders are turning together as one block. Let your shoulders keep turning after your hips stop on the backswing, then unwind hips-first into the ball.",
        fix_high="You're creating real separation between hips and shoulders — that coil is free power.",
        value=_deg(sep), target="30°+",
    )


# ── Shot-specific feedback rules ─────────────────────────────────────────────

def serve_feedback(m):
    """Generate serve-specific metrics and feedback."""
    results = {}
    tips = []

    # 1. Contact Height — hand above hips at contact, in torso lengths.
    ch = m.get("contact_height_score")
    ch_score = score_0_100(ch, 1.0, 2.1)
    results["Contact Height"] = metric_entry(
        ch_score,
        definition="How high you meet the ball compared with your full reach — reaching up fully gives a better angle into the box.",
        ai_focus="Finds the moment your hitting hand peaks above your head and measures how high that is relative to your body.",
        fix_low="Extend your arm fully and reach up into the toss instead of letting it drop. A higher contact point reduces net errors and adds pop.",
        fix_high="You're reaching up well into the toss — keep this contact height consistent as you add pace.",
        value=_serve_reach(ch), target="90%+ of full reach",
    )
    if ch_score is not None and ch_score < 60:
        tips.append("Your contact point is below your full reach. Toss a little higher and reach up to it with a straight arm.")
    elif ch_score is not None and ch_score >= 80:
        tips.append("Good contact height — you are reaching up well into the toss.")

    # 2. Arm Extension — elbow angle at contact (180° = dead straight).
    ae = m.get("arm_extension_max")
    ae_score = score_0_100(ae, 100, 170)
    results["Arm Extension"] = metric_entry(
        ae_score,
        definition="Your elbow angle at contact — tour servers hit with the arm nearly straight (about 155° or more), which gives maximum reach and racquet speed.",
        ai_focus="Measures the angle at your elbow (shoulder–elbow–wrist) in 3D at the moment of contact.",
        fix_low="Your elbow is still bent at contact. Focus on 'reaching' for the ball rather than hitting with a bent arm — this adds both height and power.",
        fix_high="Your arm extension at contact is excellent — that's giving you full reach and racquet speed.",
        value=_deg(ae), target="155°+",
    )
    if ae_score is not None and ae_score < 60:
        tips.append("Your elbow does not fully extend at contact. Work on straightening your arm up into the ball.")

    # 3. Toss Consistency — spread of the toss peak between serves.
    tc = m.get("toss_consistency_std")
    tc_score = score_0_100(tc, 0.0, 0.6, invert=True)
    results["Toss Consistency"] = metric_entry(
        tc_score,
        definition="How much your toss location moves from one serve to the next. A repeatable toss is the base of a repeatable serve.",
        ai_focus="Finds the top of your tossing hand's motion on each serve and measures how far it drifts side to side between serves.",
        fix_low=NO_TOSS if tc is None else "Your toss placement is drifting between serves. Practice isolated toss repetitions — release from the exact same spot every time before adding the swing.",
        fix_high="Your toss is landing in a tight, repeatable window — that consistency is a big asset.",
        value=_toss_spread(tc), target="Within ~3 in",
    )
    if tc_score is not None and tc_score < 60:
        tips.append("Your toss moves around between serves. Practice isolated toss drills — release from the same point each time.")
    elif tc_score is not None and tc_score >= 80:
        tips.append("Your toss looks consistent — keep repeating this pattern.")

    # 4. Knee Bend — deepest knee angle while loading (smaller = more bend).
    kb = m.get("knee_bend_avg")
    kb_score = score_0_100(kb, 100, 170, invert=True)
    results["Knee Bend (Loading)"] = metric_entry(
        kb_score,
        definition="How much you bend your knees before pushing up into the serve. Tour servers sink to roughly 110–120° at the knee to store energy for the leg drive.",
        ai_focus="Tracks your knee angle (hip–knee–ankle) through the loading phase and finds the deepest point before you drive up.",
        fix_low=NO_KNEE if kb is None else "You're staying too upright before driving up. Sink lower into your legs during the loading phase so you can push off the ground with more force.",
        fix_high="Your knee bend is generating solid leg drive — that's a strong foundation for power.",
        value=_deg(kb), target="≤ 120°",
    )
    if kb_score is not None and kb_score < 50:
        tips.append("You are not bending your knees enough before serving. A deeper knee bend helps you drive upward and add power.")

    # 5. Shoulder Turn — how far the shoulder line rotates through the motion.
    sa = m.get("shoulder_angle_avg")
    sa_score = score_0_100(sa, 40, 110)
    results["Shoulder Turn"] = metric_entry(
        sa_score,
        definition="How far your shoulders rotate from the trophy position through contact — that turn is where much of a serve's racquet speed comes from.",
        ai_focus="Tracks the direction your shoulder line faces in 3D from the windup through contact and measures the total turn.",
        fix_low="Your shoulder rotation is limited, which caps your power. Turn your back more toward the net at the trophy position, then rotate fully through the ball.",
        fix_high="Your shoulder turn is generating a strong coil — that's translating into more racquet speed.",
        value=_deg(sa), target="95°+",
    )
    if sa_score is not None and sa_score < 50:
        tips.append("Your shoulder rotation looks limited. Turn your shoulders further at the trophy position before swinging up.")

    results["Hip–Shoulder Separation"] = _separation_metric(m)

    if not tips:
        tips.append("Your serve mechanics look solid overall. Keep focusing on consistency under match pressure.")

    return results, tips


def _groundstroke(m, results, tips, side_name, contact_fix, extension_lo, extension_hi):
    ch = m.get("contact_height_avg")
    ch_score = score_0_100(ch, 0.0, 0.8)
    results["Contact Point"] = metric_entry(
        ch_score,
        definition=f"The height you meet the ball on your {side_name}, relative to your body — between waist and shoulder gives the cleanest, most controllable strike.",
        ai_focus="Finds the moment your hand is moving fastest (contact) and measures its height relative to your hips.",
        fix_low=contact_fix,
        fix_high="Your contact height is right in the ideal zone for clean, powerful strikes.",
        value=_height_zone(ch), target="Waist to chest",
    )
    if ch_score is not None and ch_score < 50:
        tips.append(f"You're meeting the ball low on the {side_name}. Move your feet earlier so you can take it between waist and chest height.")

    ae = m.get("arm_extension_avg")
    ae_score = score_0_100(ae, extension_lo, extension_hi)
    results["Extension Through Contact"] = metric_entry(
        ae_score,
        definition="How extended your hitting arm is at contact — a jammed, bent arm loses power and control.",
        ai_focus="Measures your elbow angle (shoulder–elbow–wrist) in 3D at contact.",
        fix_low="Your arm looks jammed at contact. Adjust your spacing so the ball is a comfortable arm's length away, and drive through it.",
        fix_high="You're extending well through contact — that's giving the shot both power and control.",
        value=_deg(ae), target=f"{extension_hi - 10}°+",
    )
    if ae_score is not None and ae_score < 60:
        tips.append(f"Your {side_name} looks jammed at contact. Give yourself more room to extend through the ball.")

    tr = m.get("trunk_rotation_avg")
    tr_score = score_0_100(tr, 30, 100)
    results["Unit Turn / Rotation"] = metric_entry(
        tr_score,
        definition="How far your shoulders rotate from the backswing through contact — the body, not the arm, is the engine of the stroke.",
        ai_focus="Tracks the direction your shoulder line faces in 3D through the swing and measures the total turn.",
        fix_low="Your unit turn looks incomplete. As soon as you read the ball, turn your hips and shoulders together — don't wait until the last second.",
        fix_high="Your unit turn is complete and early — that's giving your swing a strong base to work from.",
        value=_deg(tr), target="85°+",
    )
    if tr_score is not None and tr_score < 50:
        tips.append(f"Your shoulder turn on the {side_name} is limited. Turn early and fully, then rotate through the ball.")

    results["Hip–Shoulder Separation"] = _separation_metric(m)

    kb = m.get("knee_bend_avg")
    kb_score = score_0_100(kb, 110, 170, invert=True)
    results["Knee Bend"] = metric_entry(
        kb_score,
        definition="How low you get through the shot — bent knees keep your base stable and your head still at contact.",
        ai_focus="Tracks your knee angle (hip–knee–ankle) around contact and finds the lowest point.",
        fix_low=NO_KNEE if kb is None else f"You're playing too upright on your {side_name}. Bend your knees more to lower your center of gravity and stay balanced through contact.",
        fix_high="Your footwork and balance are solid — you're staying low and stable through the shot.",
        value=_deg(kb), target="≤ 125°",
    )
    if kb_score is not None and kb_score < 50:
        tips.append(f"You appear upright on the {side_name}. Bend your knees to stay low and balanced through the shot.")


def forehand_feedback(m):
    """Generate forehand-specific feedback."""
    results, tips = {}, []
    _groundstroke(m, results, tips, "forehand",
                  "You're making contact low, which limits your options. Move your feet earlier so you can take the ball closer to waist height.",
                  90, 150)
    if not tips:
        tips.append("Forehand mechanics look good! Focus on shot selection and placement during rallies.")
    return results, tips


def backhand_feedback(m):
    """Generate backhand-specific feedback."""
    results, tips = {}, []
    _groundstroke(m, results, tips, "backhand",
                  "Your contact point is low, which often means you're getting to the ball late. Get your feet set earlier so you can take it higher and out in front.",
                  80, 140)
    if not tips:
        tips.append("Backhand looks solid. Keep working on depth and consistency.")
    return results, tips


def rally_feedback(m):
    """General rally / movement feedback, averaged over every swing found."""
    results = {}
    tips = []

    kb = m.get("knee_bend_avg")
    kb_score = score_0_100(kb, 100, 170, invert=True)
    results["Athletic Stance"] = metric_entry(
        kb_score,
        definition="How low you stay through your shots in the rally — a lower stance lets you push off quickly in any direction.",
        ai_focus="Tracks your knee angle around each shot in the rally and averages the lowest points.",
        fix_low=NO_KNEE if kb is None else "Your stance is too upright. Stay on the balls of your feet with knees bent so you can react and change direction faster.",
        fix_high="Your stance is athletic and ready — that's helping you react quickly between shots.",
        value=_deg(kb), target="≤ 120°",
    )
    if kb_score is not None and kb_score < 50:
        tips.append("Your stance looks too upright. Stay on the balls of your feet with knees bent so you can react faster.")

    tr = m.get("trunk_rotation_avg")
    tr_score = score_0_100(tr, 20, 90)
    results["Body Rotation"] = metric_entry(
        tr_score,
        definition="How much your shoulders rotate into each shot, showing whether you're using your whole body or just your arm.",
        ai_focus="Tracks your shoulder-line rotation in 3D through every swing in the rally.",
        fix_low="You're relying on your arm rather than your body. Rotate your hips and shoulders into each shot for more consistent power.",
        fix_high="You're rotating your body well into each shot — that's a repeatable power source.",
        value=_deg(tr), target="75°+",
    )
    if tr_score is not None and tr_score < 50:
        tips.append("Work on rotating your whole body into shots rather than just swinging with your arm.")

    ae = m.get("arm_extension_avg")
    ae_score = score_0_100(ae, 90, 150)
    results["Swing Extension"] = metric_entry(
        ae_score,
        definition="How extended your hitting arm is at contact across the rally, reflecting whether you're driving through the ball or getting jammed.",
        ai_focus="Measures your elbow angle at contact on every swing in the rally.",
        fix_low="Your swings are getting jammed during the rally. Work on spacing — move so the ball arrives a comfortable arm's length away.",
        fix_high="You're extending well through your shots — that's a sign of controlled, repeatable power.",
        value=_deg(ae), target="140°+",
    )
    if ae_score is not None and ae_score < 50:
        tips.append("Your swings are getting jammed during the rally. Focus on spacing so you can drive through each ball.")

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
    tactical = profile.get('tactical_pref') or ''
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
