"""
swing_analysis.py
Reads the hitter's swing around each ball contact in a point-play clip —
shot type, swing path, forearm/wrist roll (pronation), hand speed, and
contact height — and turns it into spin evidence that ball_physics.py
fuses with what the bounce shows.

Same design rule as ball_physics.py: every field is gated behind an
'available' flag. Pose comes from a single 2D camera, so this only
reads the player nearest the camera (the one big enough to track
reliably), and it reports what 2D landmarks can actually support —
e.g. hand speed, not a made-up racket-head number.
"""

import math
import os
import cv2
import numpy as np

MODEL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          'models', 'pose_landmarker_lite.task')

# MediaPipe pose landmark indices.
NOSE = 0
L_SHOULDER, R_SHOULDER = 11, 12
L_ELBOW, R_ELBOW = 13, 14
L_WRIST, R_WRIST = 15, 16
L_PINKY, R_PINKY = 17, 18
L_INDEX, R_INDEX = 19, 20
L_HIP, R_HIP = 23, 24
L_ANKLE, R_ANKLE = 27, 28

POSE_INPUT_MAX_WIDTH = 640
MIN_VISIBILITY = 0.5

# Ball within this many torso-lengths of a wrist counts as "on the
# strings" — a racket reaches roughly one torso length past the hand.
CONTACT_MAX_DIST_TORSOS = 1.5
MIN_CONTACT_GAP_SEC = 0.5

FT_PER_SEC_TO_MPH = 3600.0 / 5280.0


# ---------------------------------------------------------------------
# Pose tracking
# ---------------------------------------------------------------------

def _load_landmarker():
    """Returns a MediaPipe PoseLandmarker in video mode, or None when
    MediaPipe or the model file isn't available — callers then fall back
    to bounce-only spin instead of failing the whole analysis."""
    if not os.path.exists(MODEL_PATH):
        return None
    try:
        from mediapipe.tasks.python import BaseOptions, vision
    except ImportError:
        return None
    options = vision.PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=MODEL_PATH),
        running_mode=vision.RunningMode.VIDEO,
        # Two, so the near player can be picked by size instead of
        # MediaPipe's choice flipping between the two players.
        num_poses=2,
        min_pose_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    return vision.PoseLandmarker.create_from_options(options)


def _torso_len(p):
    sh = (p[L_SHOULDER][:2] + p[R_SHOULDER][:2]) / 2
    hp = (p[L_HIP][:2] + p[R_HIP][:2]) / 2
    return float(np.linalg.norm(sh - hp))


def track_pose(video_path, max_seconds=12.0):
    """Per-frame pose of the player nearest the camera.

    Returns a list (one entry per frame, aligned with track_ball's
    detections) of None or an (33, 3) array of [x_px, y_px, visibility],
    or None overall if pose tracking isn't available on this server.
    """
    landmarker = _load_landmarker()
    if landmarker is None:
        return None
    import mediapipe as mp

    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    max_frames = int(max_seconds * fps)
    poses = []
    frame_idx = 0
    try:
        while frame_idx < max_frames:
            ret, frame = cap.read()
            if not ret:
                break
            h, w = frame.shape[:2]
            small = frame
            if w > POSE_INPUT_MAX_WIDTH:
                small = cv2.resize(frame, (POSE_INPUT_MAX_WIDTH, int(h * POSE_INPUT_MAX_WIDTH / w)))
            rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            result = landmarker.detect_for_video(image, int(frame_idx * 1000 / fps))

            best = None
            for lms in result.pose_landmarks:
                # Landmarks are normalized; scale back to the original
                # frame so they share pixel space with the ball tracker.
                arr = np.array([[lm.x * w, lm.y * h, lm.visibility] for lm in lms])
                if best is None or _torso_len(arr) > _torso_len(best):
                    best = arr
            poses.append(best)
            frame_idx += 1
    finally:
        cap.release()
        landmarker.close()
    return poses


# ---------------------------------------------------------------------
# Contact detection
# ---------------------------------------------------------------------

def _nearest_ball(detections, f, reach=3):
    """Ball position at frame f, or the closest tracked frame within
    `reach` — the ball is often motion-blurred out right at contact."""
    for off in range(reach + 1):
        for g in (f - off, f + off):
            if 0 <= g < len(detections) and detections[g] is not None:
                return np.array([detections[g]['x'], detections[g]['y']])
    return None


def _ball_velocity(detections, f, direction, span):
    """Mean ball pixel velocity over `span` frames before (direction=-1)
    or after (direction=+1) frame f, from the tracked points available."""
    pts = []
    rng = range(f - span, f) if direction < 0 else range(f + 1, f + span + 1)
    for g in rng:
        if 0 <= g < len(detections) and detections[g] is not None:
            pts.append((g, detections[g]['x'], detections[g]['y']))
    if len(pts) < 2:
        return None
    (g0, x0, y0), (g1, x1, y1) = pts[0], pts[-1]
    return np.array([x1 - x0, y1 - y0]) / (g1 - g0)


def find_contacts(detections, poses, fps):
    """Frames where the near player's hand meets the ball and the ball's
    direction changes — i.e. that player actually hit it. Returns a list
    of (frame_index, hitting_wrist_landmark_index)."""
    candidates = []
    for f, p in enumerate(poses):
        if p is None:
            continue
        ball = _nearest_ball(detections, f, reach=1)
        torso = _torso_len(p)
        if ball is None or torso < 5:
            continue
        for wi in (L_WRIST, R_WRIST):
            if p[wi][2] < MIN_VISIBILITY:
                continue
            d = np.linalg.norm(ball - p[wi][:2]) / torso
            if d <= CONTACT_MAX_DIST_TORSOS:
                candidates.append((d, f, wi))

    span = max(2, int(0.1 * fps))
    gap = max(1, int(MIN_CONTACT_GAP_SEC * fps))
    contacts = []
    for d, f, wi in sorted(candidates):
        if any(abs(f - cf) < gap for cf, _ in contacts):
            continue
        v_in = _ball_velocity(detections, f, -1, span)
        v_out = _ball_velocity(detections, f, +1, span)
        if v_in is None or v_out is None:
            continue
        n_in, n_out = np.linalg.norm(v_in), np.linalg.norm(v_out)
        if n_in == 0 or n_out == 0:
            continue
        turn = math.degrees(math.acos(np.clip(np.dot(v_in, v_out) / (n_in * n_out), -1, 1)))
        # A ball just flying past the player keeps its line; a hit
        # sends it back the other way.
        if turn >= 60:
            contacts.append((f, wi))
    return sorted(contacts)


# ---------------------------------------------------------------------
# Swing features
# ---------------------------------------------------------------------

def _landmark(poses, f, idx):
    if 0 <= f < len(poses) and poses[f] is not None and poses[f][idx][2] >= MIN_VISIBILITY:
        return poses[f][idx][:2]
    return None


def _px_per_ft_at_feet(pose, H):
    """Real-world scale at the player's own depth on court, from the
    court homography — lets hand speed be stated in mph without assuming
    the player's height."""
    if H is None or pose is None:
        return None
    feet = (pose[L_ANKLE][:2] + pose[R_ANKLE][:2]) / 2
    try:
        H_inv = np.linalg.inv(H)
    except np.linalg.LinAlgError:
        return None
    pt = cv2.perspectiveTransform(np.array([[feet]], dtype=np.float64), H)[0][0]
    ends = np.array([[[pt[0] - 0.5, pt[1]], [pt[0] + 0.5, pt[1]]]], dtype=np.float64)
    px = cv2.perspectiveTransform(ends, H_inv)[0]
    scale = float(np.linalg.norm(px[1] - px[0]))
    return scale if scale > 0.5 else None


def _signed_angle_deg(a, b):
    ang = math.degrees(math.atan2(b[1], b[0]) - math.atan2(a[1], a[0]))
    return (ang + 180) % 360 - 180


def analyze_swing(poses, detections, f, wrist_idx, fps, H=None, dominant_hand=None):
    """Reads the swing around contact frame f. Returns a dict of display
    fields plus a 'spin_score' in [-1, 1] (+ = topspin, - = slice) and a
    'quality' in [0, 1] saying how much of the swing was actually seen."""
    p = poses[f]
    torso = _torso_len(p)
    right_side = wrist_idx == R_WRIST
    elbow_idx = R_ELBOW if right_side else L_ELBOW
    pinky_idx = R_PINKY if right_side else L_PINKY
    index_idx = R_INDEX if right_side else L_INDEX

    k = max(1, round(0.05 * fps))       # velocity half-window
    pre = max(2, round(0.15 * fps))     # backswing-to-contact window
    post = max(2, round(0.15 * fps))    # contact-to-follow-through window

    out = {'shot_type': None, 'path': {'available': False}, 'roll': {'available': False},
           'hand_speed_mph': {'available': False}, 'contact_height': None,
           'two_handed': False, 'spin_score': None, 'quality': 0.0}

    ball = _nearest_ball(detections, f)
    hip_mid = (p[L_HIP][:2] + p[R_HIP][:2]) / 2
    shoulder_mid = (p[L_SHOULDER][:2] + p[R_SHOULDER][:2]) / 2
    contact_pt = ball if ball is not None else p[wrist_idx][:2]

    # --- Shot type. Forehand vs backhand is decided by which side of the
    # body the ball is met on, using the player's own anatomical
    # left->right hip axis, so it works filmed from behind or in front.
    overhead = contact_pt[1] < p[NOSE][1] - 0.3 * torso
    if overhead:
        out['shot_type'] = 'Serve / Overhead'
    else:
        hand = dominant_hand or ('Right' if right_side else 'Left')
        hip_axis = p[R_HIP][:2] - p[L_HIP][:2]
        on_right = float(np.dot(contact_pt - hip_mid, hip_axis)) > 0
        forehand = on_right == (hand == 'Right')
        out['shot_type'] = 'Forehand' if forehand else 'Backhand'
    out['two_handed'] = (p[L_WRIST][2] >= MIN_VISIBILITY and p[R_WRIST][2] >= MIN_VISIBILITY
                         and np.linalg.norm(p[L_WRIST][:2] - p[R_WRIST][:2]) < 0.35 * torso)

    # Contact height relative to the body (image rows grow downward).
    rel = (hip_mid[1] - contact_pt[1]) / torso
    if overhead:
        out['contact_height'] = 'Above head'
    elif rel >= 0.75:
        out['contact_height'] = 'Shoulder height or above'
    elif rel >= 0.15:
        out['contact_height'] = 'Waist to chest'
    else:
        out['contact_height'] = 'Below the waist'

    evidence = []  # (score in [-1, 1], weight)

    # --- Swing path at contact: direction the hand is travelling as it
    # meets the ball. Upward = brushing up the back of the ball (topspin),
    # downward = cutting under it (slice).
    w_before, w_after = _landmark(poses, f - k, wrist_idx), _landmark(poses, f + k, wrist_idx)
    if w_before is not None and w_after is not None:
        v = (w_after - w_before) / (2 * k / fps)  # px/s
        speed_px = float(np.linalg.norm(v))
        if speed_px > 0:
            up_frac = -v[1] / speed_px
            angle = math.degrees(math.asin(np.clip(up_frac, -1, 1)))
            if angle >= 25:
                label = 'Low-to-high, steep'
            elif angle >= 8:
                label = 'Low-to-high'
            elif angle > -8:
                label = 'Level'
            else:
                label = 'High-to-low'
            out['path'] = {'available': True, 'angle_deg': round(angle), 'label': label}
            evidence.append((float(np.clip(up_frac / 0.6, -1, 1)), 0.45))

        scale = _px_per_ft_at_feet(p, H)
        if scale is not None and speed_px > 0:
            mph = speed_px / scale * FT_PER_SEC_TO_MPH
            if mph <= 100:
                out['hand_speed_mph'] = {
                    'available': True, 'value': round(mph, 1),
                    'note': "Speed of the hitting hand at contact — the racket head moves faster than this.",
                }

    # --- Overall rise from backswing to follow-through, in torso
    # lengths — a topspin swing finishes well above where it started.
    w_start, w_end = _landmark(poses, f - pre, wrist_idx), _landmark(poses, f + post, wrist_idx)
    if w_start is not None and w_end is not None:
        rise = (w_start[1] - w_end[1]) / torso
        evidence.append((float(np.clip(rise / 1.0, -1, 1)), 0.3))

    # --- Forearm/wrist roll (pronation): rotation of the hand (pinky ->
    # index) relative to the forearm through contact. Big roll on an
    # upward path is the "windshield wiper" finish that adds topspin.
    def hand_angle(g):
        e, w = _landmark(poses, g, elbow_idx), _landmark(poses, g, wrist_idx)
        pk, ix = _landmark(poses, g, pinky_idx), _landmark(poses, g, index_idx)
        if e is None or w is None or pk is None or ix is None:
            return None
        return _signed_angle_deg(w - e, ix - pk)

    a0, a1 = hand_angle(f - k), hand_angle(f + post)
    if a0 is not None and a1 is not None:
        roll = abs((a1 - a0 + 180) % 360 - 180)
        if roll >= 45:
            label = 'Strong'
        elif roll >= 20:
            label = 'Moderate'
        else:
            label = 'Minimal'
        out['roll'] = {'available': True, 'degrees': round(roll), 'label': label}
        # Roll only adds spin in the direction the path is already going.
        if out['path']['available']:
            direction = 1 if out['path']['angle_deg'] >= 0 else -1
            evidence.append((direction * float(np.clip(roll / 60.0, 0, 1)), 0.25))

    if evidence:
        total_w = sum(w for _, w in evidence)
        out['spin_score'] = sum(s * w for s, w in evidence) / total_w
        out['quality'] = round(total_w, 2)  # weights sum to 1.0 when everything was seen
    return out
