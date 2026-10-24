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
    return round(100 - score if invert else score)


def label(score):
    """Convert numeric score to a label."""
    if score is None: return "N/A"
    if score >= 80:   return "Excellent"
    if score >= 60:   return "Good"
    if score >= 40:   return "Needs Work"
    return "Poor"


def metric_entry(score, definition, ai_focus, fix_low, fix_high=None, value=None, target=None, detail=None):
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
        "detail": detail,  # the exact measurement (e.g. "146° at the knee"), shown on hover
    }


# Display helpers. Heights are measured in torso lengths (shoulders-to-hips),
# which keeps them independent of how tall the player is or how far away
# the camera was; these turn them back into words people use.
def _deg(v):
    return f"{v:.0f}°" if v is not None else None

def _serve_reach(ch):
    # Full serve reach puts the hand ~2.1 torso lengths above the hips.
    if ch is None:
        return None
    pct = ch / 2.1
    if pct >= 0.9:
        return "Full reach"
    if pct >= 0.75:
        return "Just below full reach"
    return "Well below full reach"

def _pct_reach(ch):
    return f"{min(100, round(ch / 2.1 * 100))}% of your full reach" if ch is not None else None

def _words(v, bands):
    """bands: [(threshold, label), ...] checked in order with v >= threshold;
    the last label is the fallback."""
    if v is None:
        return None
    for threshold, text in bands[:-1]:
        if v >= threshold:
            return text
    return bands[-1][1]

def _by_score(score, words):
    """words = (excellent, good, needs_work, poor): the word shown always
    agrees with the metric's rating, which comes from the same score."""
    if score is None:
        return None
    if score >= 80:
        return words[0]
    if score >= 60:
        return words[1]
    if score >= 40:
        return words[2]
    return words[3]

ELBOW_WORDS = ("Almost straight", "Slightly bent", "Bent", "Very bent (jammed)")
EXTEND_WORDS = ("Extended through the ball", "Slightly bent", "Bent", "Jammed")
KNEE_WORDS = ("Deep bend", "Medium bend", "Slight bend (standing tall)", "Barely bending")
TURN_WORDS = ("Full turn", "Good turn", "Half turn", "Little turn (mostly arm)")
COIL_WORDS = ("Strong coil", "Some coil", "A little coil", "Hips and shoulders turn together")

def _detail(v, what):
    return f"{v:.0f}° {what}" if v is not None else None

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
    if std is None:
        return None
    inches = std * 20
    if inches <= 3:
        return f"Same spot every serve (within ~{max(1, round(inches))} in)"
    if inches <= 6:
        return f"Moves a little (~{round(inches)} in)"
    return f"Moves around a lot (~{round(inches)} in)"

NO_KNEE = ("We couldn't see your legs clearly enough to measure this — film with "
           "your whole body in frame, feet included.")
NO_TOSS = "Needs at least two serves in the clip to measure — upload a clip with 2–3 serves in a row."


def _separation_metric(m):
    sep = m.get("hip_shoulder_separation")
    sc = score_0_100(sep, 5, 40)
    return metric_entry(
        sc,
        definition="The gap between how far your shoulders turn and how far your hips turn — the 'coil' that stores energy, like winding a spring.",
        ai_focus="Watches how far your shoulders turn compared with your hips during the backswing.",
        fix_low="Your hips and shoulders are turning together as one block. Let your shoulders keep turning after your hips stop on the backswing, then unwind hips-first into the ball.",
        fix_high="You're creating real separation between hips and shoulders — that coil is free power.",
        value=_by_score(sc, COIL_WORDS), target="Strong coil", detail=_detail(sep, "between hips and shoulders"),
    )


HEAD_WORDS = ("Still through contact", "Mostly still", "Moves at contact", "Moves a lot at contact")
BALANCE_WORDS = ("Balanced", "Mostly balanced", "Off balance at times", "Falling off the shot")


def _head_metric(m, results, tips):
    hm = m.get("head_movement_avg")
    sc = score_0_100(hm, 0.05, 0.35, invert=True)
    results["Head Stability"] = metric_entry(
        sc,
        definition="How still your head stays as you hit the ball. A steady head keeps your eyes on the contact point and your body on line.",
        ai_focus="Tracks your head against your shoulders in the instant around contact.",
        fix_low="Your head is moving as you hit. Keep your eyes on the contact point until the ball has left the strings, then let your head turn.",
        fix_high="Your head stays quiet through contact — that's helping you strike the ball cleanly.",
        value=_by_score(sc, HEAD_WORDS), target="Still through contact",
    )
    if sc is not None and sc < 50:
        tips.append(("Head Stability", "Your head moves at contact. Watch the ball onto the strings and hold your head still a beat after."))


def _balance_metric(m, results, tips, name, definition, fix_low, fix_high, tip):
    bal = m.get("balance_after_avg")
    sc = score_0_100(bal, 0.3, 1.0)
    results[name] = metric_entry(
        sc,
        definition=definition,
        ai_focus="Checks the frames right after contact: are your hips over your feet and your upper body upright, or are you falling away?",
        fix_low=fix_low, fix_high=fix_high,
        value=_by_score(sc, BALANCE_WORDS), target="Balanced",
        detail=f"Balanced in {round(bal * 100)}% of frames after contact" if bal is not None else None,
    )
    if sc is not None and sc < 50:
        tips.append((name, tip))


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
        ai_focus="Finds the moment you hit the ball and checks how high your hand is compared with your full reach.",
        fix_low="Extend your arm fully and reach up into the toss instead of letting it drop. A higher contact point reduces net errors and adds pop.",
        fix_high="You're reaching up well into the toss — keep this contact height consistent as you add pace.",
        value=_by_score(ch_score, ("Full reach", "Just below full reach", "Below full reach", "Well below full reach")),
        target="Full reach", detail=_pct_reach(ch),
    )
    if ch_score is not None and ch_score < 60:
        tips.append(("Contact Height", "Your contact point is below your full reach. Toss a little higher and reach up to it with a straight arm."))
    elif ch_score is not None and ch_score >= 80:
        tips.append(("Contact Height", "Good contact height — you are reaching up well into the toss."))

    # 2. Arm Extension — elbow angle at contact (180° = dead straight).
    ae = m.get("arm_extension_max")
    ae_score = score_0_100(ae, 100, 170)
    results["Arm Extension"] = metric_entry(
        ae_score,
        definition="How straight your hitting arm is when you hit the ball. Pros hit with the arm almost straight, which gives maximum reach and racquet speed.",
        ai_focus="Checks the bend in your elbow at the moment you hit the ball.",
        fix_low="Your elbow is still bent at contact. Focus on 'reaching' for the ball rather than hitting with a bent arm — this adds both height and power.",
        fix_high="Your arm extension at contact is excellent — that's giving you full reach and racquet speed.",
        value=_by_score(ae_score, ELBOW_WORDS), target="Almost straight", detail=_detail(ae, "at the elbow (180° = straight)"),
    )
    if ae_score is not None and ae_score < 60:
        tips.append(("Arm Extension", "Your elbow does not fully extend at contact. Work on straightening your arm up into the ball."))

    # 3. Toss Consistency — spread of the toss peak between serves.
    tc = m.get("toss_consistency_std")
    tc_score = score_0_100(tc, 0.0, 0.6, invert=True)
    results["Toss Consistency"] = metric_entry(
        tc_score,
        definition="How much your toss location moves from one serve to the next. A repeatable toss is the base of a repeatable serve.",
        ai_focus="Finds where your toss arm releases on each serve and compares them.",
        fix_low=NO_TOSS if tc is None else "Your toss placement is drifting between serves. Practice isolated toss repetitions — release from the exact same spot every time before adding the swing.",
        fix_high="Your toss is landing in a tight, repeatable window — that consistency is a big asset.",
        value=_toss_spread(tc), target="Same spot every serve",
    )
    if tc_score is not None and tc_score < 60:
        tips.append(("Toss Consistency", "Your toss moves around between serves. Practice isolated toss drills — release from the same point each time."))
    elif tc_score is not None and tc_score >= 80:
        tips.append(("Toss Consistency", "Your toss looks consistent — keep repeating this pattern."))

    # 4. Knee Bend — deepest knee angle while loading (smaller = more bend).
    kb = m.get("knee_bend_avg")
    kb_score = score_0_100(kb, 100, 170, invert=True)
    results["Knee Bend (Loading)"] = metric_entry(
        kb_score,
        definition="How much you bend your knees before pushing up into the serve. Pros sink into a deep knee bend to store energy, then drive up into the ball.",
        ai_focus="Watches your knees as you load and finds your lowest point before you push up.",
        fix_low=NO_KNEE if kb is None else "You're staying too upright before driving up. Sink lower into your legs during the loading phase so you can push off the ground with more force.",
        fix_high="Your knee bend is generating solid leg drive — that's a strong foundation for power.",
        value=_by_score(kb_score, KNEE_WORDS), target="Deep bend", detail=_detail(kb, "at the knee (180° = straight leg)"),
    )
    if kb_score is not None and kb_score < 50:
        tips.append(("Knee Bend (Loading)", "You are not bending your knees enough before serving. A deeper knee bend helps you drive upward and add power."))

    # 5. Shoulder Turn — how far the shoulder line rotates through the motion.
    sa = m.get("shoulder_angle_avg")
    sa_score = score_0_100(sa, 40, 110)
    results["Shoulder Turn"] = metric_entry(
        sa_score,
        definition="How far your shoulders rotate from the trophy position through contact — that turn is where much of a serve's racquet speed comes from.",
        ai_focus="Watches how far your shoulders turn from the trophy position through contact.",
        fix_low="Your shoulder rotation is limited, which caps your power. Turn your back more toward the net at the trophy position, then rotate fully through the ball.",
        fix_high="Your shoulder turn is generating a strong coil — that's translating into more racquet speed.",
        value=_by_score(sa_score, TURN_WORDS), target="Full turn", detail=_detail(sa, "of shoulder turn"),
    )
    if sa_score is not None and sa_score < 50:
        tips.append(("Shoulder Turn", "Your shoulder rotation looks limited. Turn your shoulders further at the trophy position before swinging up."))

    results["Hip–Shoulder Separation"] = _separation_metric(m)

    # Trophy position: elbow height relative to the shoulder.
    te = m.get("trophy_elbow_avg")
    te_score = score_0_100(te, -0.35, 0.05)
    results["Elbow Height (Trophy)"] = metric_entry(
        te_score,
        definition="How high your hitting elbow is in the trophy position, the loaded moment before you swing up. Level with or above the shoulder lets the racquet drop and accelerate freely.",
        ai_focus="Finds the top of your toss and checks where your hitting elbow is against your shoulder at that moment.",
        fix_low="Your elbow drops below your shoulder in the trophy position, which shortens the swing and strains the shoulder. Lift the elbow up to shoulder height as the toss goes up — think 'elbow up, racquet tip up'.",
        fix_high="Your elbow is up at shoulder height in the trophy — a strong, safe loading position.",
        value=_by_score(te_score, ("At or above the shoulder", "Just below the shoulder", "Below the shoulder", "Dropped well below")),
        target="At or above the shoulder",
    )
    if te_score is not None and te_score < 50:
        tips.append(("Elbow Height (Trophy)", "Your elbow drops in the trophy position. Lift it to shoulder height as you toss so the racquet can drop and accelerate."))

    _head_metric(m, results, tips)
    _balance_metric(
        m, results, tips, "Landing Balance",
        definition="How balanced you land after the serve: on your front foot, inside the court, upper body under control and ready for the next ball.",
        fix_low="You're falling off balance as you land. Drive up and forward into the court, land on your front foot, and let your back leg swing through to catch you.",
        fix_high="You land balanced and under control — ready to recover for the next shot.",
        tip="You're off balance on the landing. Drive up and into the court and land on your front foot, ready to split step.",
    )

    if not tips:
        tips.append("Your serve mechanics look solid overall. Keep focusing on consistency under match pressure.")

    return results, tips


def _contact_zone_score(ch):
    """Groundstroke contact height (torso lengths above the hips): waist to
    chest is the ideal strike zone; lower costs more than higher."""
    if ch is None:
        return None
    if ch < 0.15:      # below the waist
        return round(max(0, 55 - (0.15 - ch) * 120))
    if ch <= 0.9:      # waist to chest/shoulder: the strike zone
        return round(80 + (min(ch, 0.6) - 0.15) / 0.45 * 20)
    return round(max(50, 100 - (ch - 0.9) * 80))  # above the shoulder


def _groundstroke(m, results, tips, side_name, contact_fix, extension_lo, extension_hi):
    ch = m.get("contact_height_avg")
    ch_score = _contact_zone_score(ch)
    results["Contact Point"] = metric_entry(
        ch_score,
        definition=f"The height you meet the ball on your {side_name}, relative to your body — between waist and shoulder gives the cleanest, most controllable strike.",
        ai_focus="Finds the moment you hit the ball and checks how high it is on your body.",
        fix_low=contact_fix,
        fix_high="Your contact height is right in the ideal zone for clean, powerful strikes.",
        value=_height_zone(ch), target="Waist to chest",
    )
    if ch_score is not None and ch_score < 50:
        tips.append(("Contact Point", f"You're meeting the ball low on the {side_name}. Move your feet earlier so you can take it between waist and chest height."))

    ae = m.get("arm_extension_avg")
    ae_score = score_0_100(ae, extension_lo, extension_hi)
    results["Extension Through Contact"] = metric_entry(
        ae_score,
        definition="How extended your hitting arm is at contact — a jammed, bent arm loses power and control.",
        ai_focus="Checks the bend in your hitting arm at the moment you hit the ball.",
        fix_low="Your arm looks jammed at contact. Adjust your spacing so the ball is a comfortable arm's length away, and drive through it.",
        fix_high="You're extending well through contact — that's giving the shot both power and control.",
        value=_by_score(ae_score, EXTEND_WORDS), target="Extended through the ball", detail=_detail(ae, "at the elbow (180° = straight)"),
    )
    if ae_score is not None and ae_score < 60:
        tips.append(("Extension Through Contact", f"Your {side_name} looks jammed at contact. Give yourself more room to extend through the ball."))

    tr = m.get("trunk_rotation_avg")
    tr_score = score_0_100(tr, 30, 100)
    results["Unit Turn / Rotation"] = metric_entry(
        tr_score,
        definition="How far your shoulders rotate from the backswing through contact — the body, not the arm, is the engine of the stroke.",
        ai_focus="Watches how far your shoulders turn from the backswing through contact.",
        fix_low="Your unit turn looks incomplete. As soon as you read the ball, turn your hips and shoulders together — don't wait until the last second.",
        fix_high="Your unit turn is complete and early — that's giving your swing a strong base to work from.",
        value=_by_score(tr_score, TURN_WORDS), target="Full turn", detail=_detail(tr, "of shoulder turn"),
    )
    if tr_score is not None and tr_score < 50:
        tips.append(("Unit Turn / Rotation", f"Your shoulder turn on the {side_name} is limited. Turn early and fully, then rotate through the ball."))

    results["Hip–Shoulder Separation"] = _separation_metric(m)

    kb = m.get("knee_bend_avg")
    kb_score = score_0_100(kb, 110, 170, invert=True)
    results["Knee Bend"] = metric_entry(
        kb_score,
        definition="How low you get through the shot — bent knees keep your base stable and your head still at contact.",
        ai_focus="Watches your knees around the moment you hit the ball and finds your lowest point.",
        fix_low=NO_KNEE if kb is None else f"You're playing too upright on your {side_name}. Bend your knees more to lower your center of gravity and stay balanced through contact.",
        fix_high="Your footwork and balance are solid — you're staying low and stable through the shot.",
        value=_by_score(kb_score, KNEE_WORDS), target="Deep bend", detail=_detail(kb, "at the knee (180° = straight leg)"),
    )
    if kb_score is not None and kb_score < 50:
        tips.append(("Knee Bend", f"You appear upright on the {side_name}. Bend your knees to stay low and balanced through the shot."))

    _head_metric(m, results, tips)

    ft = m.get("follow_through_avg")
    ft_score = score_0_100(ft, -0.4, 0.3)
    results["Follow-Through"] = metric_entry(
        ft_score,
        definition=f"How high and complete your swing finishes after contact. A low-to-high swing that finishes over the shoulder is what puts topspin and control on the {side_name}. (A slice finishes lower by design.)",
        ai_focus="Follows your hitting hand after contact and checks how high it finishes compared with your shoulders.",
        fix_low=f"Your swing stops short after contact. Let the racquet keep going up and across so your {side_name} finishes over your shoulder — don't decelerate into the ball.",
        fix_high="You're finishing high and complete — that low-to-high path is giving the ball shape and margin.",
        value=_by_score(ft_score, ("Finishes over the shoulder", "Finishes around shoulder height", "Finishes low", "Cut short")),
        target="Over the shoulder",
    )
    if ft_score is not None and ft_score < 50:
        tips.append(("Follow-Through", f"Your {side_name} finish is cut short. Swing through the ball and finish with the racquet over your shoulder."))

    _balance_metric(
        m, results, tips, "Balance & Finish",
        definition="How stable you are once the shot is done: holding your finish with your hips over your feet, not falling away from the ball.",
        fix_low=f"You're falling off your {side_name} after contact. Set your feet earlier, stay low through the ball, and hold your finish for a moment before recovering.",
        fix_high="You hold a stable, balanced finish — a sign the whole body is working together.",
        tip=f"You're falling off balance after your {side_name}. Get set earlier and hold your finish before you recover.",
    )


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
        ai_focus="Watches how low you get around every shot in the rally.",
        fix_low=NO_KNEE if kb is None else "Your stance is too upright. Stay on the balls of your feet with knees bent so you can react and change direction faster.",
        fix_high="Your stance is athletic and ready — that's helping you react quickly between shots.",
        value=_by_score(kb_score, KNEE_WORDS), target="Deep bend", detail=_detail(kb, "at the knee (180° = straight leg)"),
    )
    if kb_score is not None and kb_score < 50:
        tips.append(("Athletic Stance", "Your stance looks too upright. Stay on the balls of your feet with knees bent so you can react faster."))

    tr = m.get("trunk_rotation_avg")
    tr_score = score_0_100(tr, 20, 90)
    results["Body Rotation"] = metric_entry(
        tr_score,
        definition="How much your shoulders rotate into each shot, showing whether you're using your whole body or just your arm.",
        ai_focus="Watches how far your shoulders turn on every swing in the rally.",
        fix_low="You're relying on your arm rather than your body. Rotate your hips and shoulders into each shot for more consistent power.",
        fix_high="You're rotating your body well into each shot — that's a repeatable power source.",
        value=_by_score(tr_score, TURN_WORDS), target="Full turn", detail=_detail(tr, "of shoulder turn"),
    )
    if tr_score is not None and tr_score < 50:
        tips.append(("Body Rotation", "Work on rotating your whole body into shots rather than just swinging with your arm."))

    ae = m.get("arm_extension_avg")
    ae_score = score_0_100(ae, 90, 150)
    results["Swing Extension"] = metric_entry(
        ae_score,
        definition="How extended your hitting arm is at contact across the rally, reflecting whether you're driving through the ball or getting jammed.",
        ai_focus="Checks the bend in your hitting arm on every swing in the rally.",
        fix_low="Your swings are getting jammed during the rally. Work on spacing — move so the ball arrives a comfortable arm's length away.",
        fix_high="You're extending well through your shots — that's a sign of controlled, repeatable power.",
        value=_by_score(ae_score, EXTEND_WORDS), target="Extended through the ball", detail=_detail(ae, "at the elbow (180° = straight)"),
    )
    if ae_score is not None and ae_score < 50:
        tips.append(("Swing Extension", "Your swings are getting jammed during the rally. Focus on spacing so you can drive through each ball."))

    if not tips:
        tips.append("Movement and rally mechanics look consistent. Keep working on positioning before each shot.")

    return results, tips


# ── Phases of the shot ───────────────────────────────────────────────────────
# How professional video analysts break a shot down: one set of phases for
# every shot except the serve, and a separate set for the serve. Each phase
# says what to look for and which of our metrics measure it; a phase with no
# metric is shown as a checkpoint for the player to look for on their video.
# Metric names cover forehand, backhand and rally — missing ones are skipped.

SHOT_PHASES = [
    {"name": "Ready or neutral position",
     "focus": "Feet a little wider than your shoulders, knees soft, weight on the balls of your feet, racquet out in front.",
     "metrics": ["Athletic Stance"]},
    {"name": "Activation — split step and first rotation",
     "focus": "Split step as your opponent hits, land, and turn hips and shoulders together the moment you read the ball.",
     "metrics": ["Unit Turn / Rotation", "Body Rotation"]},
    {"name": "Pre-extension",
     "focus": "Racquet goes back early and compact, non-hitting hand stays on the racquet or across your body, racquet head up.",
     "metrics": []},
    {"name": "Load",
     "focus": "Weight sinks into your outside leg, knees bend, and shoulders turn further than hips to wind up the coil.",
     "metrics": ["Knee Bend", "Hip–Shoulder Separation"]},
    {"name": "Movement towards the ball",
     "focus": "Big steps to cover distance, then small adjustment steps so the ball arrives at the right distance from you.",
     "metrics": []},
    {"name": "Movement to the contact point",
     "focus": "Unwind hips then shoulders and meet the ball out in front, between waist and chest, with your arm extended.",
     "metrics": ["Contact Point", "Extension Through Contact", "Swing Extension", "Head Stability"]},
    {"name": "Follow through / Deceleration",
     "focus": "Keep rotating and let the racquet finish over your shoulder or across your body. Stay balanced, head still.",
     "metrics": ["Follow-Through", "Balance & Finish"]},
    {"name": "Recovery steps",
     "focus": "Shuffle or crossover back toward the middle of your court so you're set for your next split step.",
     "metrics": []},
]

SERVE_PHASES = [
    {"name": "Initial position",
     "focus": "Same stance and routine every point: weight balanced, racquet and ball hand together, relaxed grip.",
     "metrics": []},
    {"name": "First weight transfer",
     "focus": "Weight rocks back onto the back foot as your arms begin to drop. Smooth rhythm, no rush.",
     "metrics": []},
    {"name": "Second weight transfer and toss",
     "focus": "Weight moves forward onto the front foot as the toss arm lifts straight up. Same release point and height every time.",
     "metrics": ["Toss Consistency"]},
    {"name": "Pre-extension",
     "focus": "Toss arm stays up, pointing at the ball. Hitting arm reaches trophy position with the body tall before the drop.",
     "metrics": ["Elbow Height (Trophy)"]},
    {"name": "Loading phase",
     "focus": "Knees bend, hips push toward the court, shoulders tilt and turn away from the net to store energy.",
     "metrics": ["Knee Bend (Loading)", "Shoulder Turn", "Hip–Shoulder Separation"]},
    {"name": "Motion towards the ball",
     "focus": "Legs drive up first, then hips and shoulders unwind, while the racquet drops behind your back and accelerates up.",
     "metrics": []},
    {"name": "Impact",
     "focus": "Contact at full reach, arm almost straight, ball slightly in front, forearm pronating through the hit.",
     "metrics": ["Contact Height", "Arm Extension", "Head Stability"]},
    {"name": "Deceleration",
     "focus": "Arm keeps pronating and finishes across your body. Land on your front foot inside the baseline, balanced.",
     "metrics": ["Landing Balance"]},
    {"name": "Recovery and preparation for the +1",
     "focus": "Recover straight into a split step, ready for the return and your first shot after the serve.",
     "metrics": []},
]


def build_phases(shot_type, scores):
    """The shot broken into its phases, in order, each with the metrics we
    measured in it and their average score (None if nothing was measured)."""
    phases = []
    for number, p in enumerate(SERVE_PHASES if shot_type == 'serve' else SHOT_PHASES, 1):
        names = [n for n in p["metrics"] if n in scores]
        valid = [scores[n]["score"] for n in names if scores[n]["score"] is not None]
        score = round(sum(valid) / len(valid)) if valid else None
        phases.append({
            "number": number,
            "name": p["name"],
            "focus": p["focus"],
            "metrics": names,
            "score": score,
            "label": label(score),
        })
    return phases


# Serve overall score is weighted by what matters most (planning doc,
# Lesson 4): toss 15%, trophy position 15%, contact 20%, the rest shared.
# Racquet acceleration (15% in the plan) can't be measured from body
# tracking alone, so the weights of what we did measure are rescaled.
SERVE_WEIGHTS = {
    "Toss Consistency": 15,
    "Elbow Height (Trophy)": 5, "Knee Bend (Loading)": 5, "Hip–Shoulder Separation": 5,
    "Contact Height": 10, "Arm Extension": 10,
    "Shoulder Turn": 12, "Head Stability": 13, "Landing Balance": 10,
}

# The classic error each metric points to when it scores poorly, so the
# player sees the mistake by name.
COMMON_MISTAKES = {
    "Contact Height": "Contact below full reach",
    "Arm Extension": "Bent arm at contact",
    "Toss Consistency": "Toss drifting between serves",
    "Knee Bend (Loading)": "Not loading the legs",
    "Shoulder Turn": "Limited shoulder turn",
    "Elbow Height (Trophy)": "Dropped elbow at the trophy",
    "Landing Balance": "Falling off balance on landing",
    "Hip–Shoulder Separation": "Hips and shoulders turning together",
    "Head Stability": "Head moving at contact",
    "Contact Point": "Low contact point",
    "Extension Through Contact": "Too close to the body (jammed)",
    "Unit Turn / Rotation": "No rotation — arm-only swing",
    "Knee Bend": "Standing too tall",
    "Follow-Through": "Short follow-through",
    "Balance & Finish": "Falling off the shot",
    "Athletic Stance": "Standing tall between shots",
    "Body Rotation": "Arm-only swings",
    "Swing Extension": "Jammed swings",
}


def find_mistakes(scores, phases):
    """Common mistakes spotted in this clip: any metric scoring under 50,
    named the way a coach would say it, in the order of the shot."""
    out = []
    for p in phases:
        for name in p["metrics"]:
            sc = scores[name]["score"]
            if sc is not None and sc < 50 and name in COMMON_MISTAKES:
                out.append({"mistake": COMMON_MISTAKES[name], "metric": name,
                            "phase": p["number"], "phase_name": p["name"]})
    return out


def _phase_notes(tips, phases):
    """Tag each tip with the phase its metric belongs to and order them
    through the shot; general tips (no metric) go last."""
    phase_of = {name: p for p in phases for name in p["metrics"]}
    notes = []
    for tip in tips:
        metric, text = tip if isinstance(tip, tuple) else (None, tip)
        p = phase_of.get(metric)
        notes.append({"text": text,
                      "phase": p["number"] if p else None,
                      "phase_name": p["name"] if p else None})
    return sorted(notes, key=lambda n: n["phase"] or 99)


# ── Main entry point ──────────────────────────────────────────────────────────

def generate_feedback(metrics, shot_type='serve'):
    """
    Given metrics dict from video_processor and a shot_type string,
    return a dict with per-metric scores and a list of coaching tips.
    """
    if "error" in metrics:
        return {"scores": {}, "phases": [], "mistakes": [],
                "tips": [{"text": "Could not process video. Please try again with a clearer clip.", "phase": None, "phase_name": None}]}

    dispatch = {
        "serve":     serve_feedback,
        "forehand":  forehand_feedback,
        "backhand":  backhand_feedback,
        "rally":     rally_feedback,
    }

    fn = dispatch.get(shot_type, serve_feedback)
    scores, tips = fn(metrics)

    # Overall score = weighted average of the metrics we could measure
    # (serve weights from the plan; every other shot weights them equally).
    weights = SERVE_WEIGHTS if shot_type == 'serve' else {}
    valid = [(v["score"], weights.get(n, 1)) for n, v in scores.items() if v["score"] is not None]
    overall = round(sum(s * w for s, w in valid) / sum(w for _, w in valid)) if valid else None
    phases = build_phases(shot_type, scores)

    return {
        "scores": scores,
        "phases": phases,
        "mistakes": find_mistakes(scores, phases),
        "tips": _phase_notes(tips, phases),
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
        result['tips'] = [{"text": t, "phase": None, "phase_name": None} for t in intro_tips] + result['tips']
        name = profile.get('name', '')
        if name:
            result['greeting'] = f"Here's your analysis, {name.split()[0]}."
    return result
