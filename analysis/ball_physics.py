"""
ball_physics.py
Tracks the tennis ball across a point-play clip and derives physically
grounded per-shot numbers: spin type, a "heaviness" index, depth, speed
(mph), and net-clearance height.

Design rule for this whole module: every number is derived from actual
pixel tracking plus a court-plane calibration computed from the clip
itself. When tracking or calibration isn't solid enough to support a
number, the result is marked unavailable instead of guessing. TennisAC
should never show a precise-looking stat it can't back up — qualitative
output is the honest fallback here, not a bug.
"""

import math
import cv2
import numpy as np

from analysis.swing_analysis import track_pose, find_contacts, analyze_swing


# ---------------------------------------------------------------------
# Ball detection
# ---------------------------------------------------------------------

# Optic-yellow tennis ball in HSV. This is a reasonable starting range for
# outdoor/indoor court lighting, not a universal constant — real clips
# will likely need this retuned once tested against actual footage.
BALL_HSV_LOW = np.array([25, 55, 110], dtype=np.uint8)
BALL_HSV_HIGH = np.array([45, 255, 255], dtype=np.uint8)

MIN_BALL_AREA_PX = 4
MAX_BALL_AREA_PX = 1400
MIN_CIRCULARITY = 0.55


def _candidate_blobs(frame):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, BALL_HSV_LOW, BALL_HSV_HIGH)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=1)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    blobs = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < MIN_BALL_AREA_PX or area > MAX_BALL_AREA_PX:
            continue
        perimeter = cv2.arcLength(c, True)
        if perimeter == 0:
            continue
        circularity = 4 * np.pi * area / (perimeter * perimeter)
        if circularity < MIN_CIRCULARITY:
            continue
        (x, y), radius = cv2.minEnclosingCircle(c)
        blobs.append({'x': float(x), 'y': float(y), 'r': float(radius), 'area': float(area)})
    return blobs


def track_ball(video_path, max_seconds=12.0):
    """Per-frame ball tracking at native resolution and frame rate.

    Downstream speed/spin math needs real timestamps and full temporal
    resolution, so this does NOT use the sparse frame sampling the
    shot-mechanics pipeline uses — that's fine for scoring a serve's
    overall shape, but it would blur out a ball moving 60+ mph.

    Returns a dict: {detections, fps, width, height} where detections is
    a list, one entry per processed frame, of either None (ball not
    confidently located) or {'t': seconds, 'x': px, 'y': px, 'r': px}.
    """
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    max_frames = int(max_seconds * fps)

    detections = []
    last_pos = None
    frame_idx = 0

    while frame_idx < max_frames:
        ret, frame = cap.read()
        if not ret:
            break

        blobs = _candidate_blobs(frame)
        pick = None
        if blobs:
            if last_pos is None:
                # No history yet — prefer the most ball-sized, most
                # circular-looking candidate over whatever is biggest.
                pick = min(blobs, key=lambda b: abs(b['area'] - 50))
            else:
                lx, ly = last_pos
                pick = min(blobs, key=lambda b: (b['x'] - lx) ** 2 + (b['y'] - ly) ** 2)
                dist = math.hypot(pick['x'] - lx, pick['y'] - ly)
                # A real ball can't teleport a third of the frame width in
                # one frame at normal video frame rates — treat that as a
                # false positive (shirt logo, court line glare, etc.).
                if dist > width * 0.35:
                    pick = None

        if pick is not None:
            detections.append({'t': frame_idx / fps, 'x': pick['x'], 'y': pick['y'], 'r': pick['r']})
            last_pos = (pick['x'], pick['y'])
        else:
            detections.append(None)

        frame_idx += 1

    cap.release()
    return {'detections': detections, 'fps': fps, 'width': width, 'height': height}


# ---------------------------------------------------------------------
# Court calibration
#
# Maps pixel coordinates to real-world court coordinates (feet) using a
# homography computed from the baseline + sidelines visible in frame.
# This assumes the overwhelmingly common way players film their own
# points: phone propped up near/behind the baseline, court running away
# from camera. It will not work for a side-on broadcast-style angle.
# ---------------------------------------------------------------------

COURT_LENGTH_FT = 78.0
COURT_WIDTH_DOUBLES_FT = 36.0
NET_DISTANCE_FROM_BASELINE_FT = 39.0
NET_HEIGHT_CENTER_FT = 3.0

MIN_CALIBRATION_CONFIDENCE = 0.55


def _line_angle_length(line):
    x1, y1, x2, y2 = line
    angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
    length = math.hypot(x2 - x1, y2 - y1)
    return angle, length


def find_court_homography(frame):
    """Best-effort detection of the near baseline + both sidelines.

    Returns {'H': 3x3 homography (pixel -> feet), 'confidence': 0..1} or
    {'H': None, 'confidence': 0.0} when the geometry in frame isn't clean
    enough to trust. Every caller MUST check confidence before using H —
    this is deliberately conservative because a wrong calibration doesn't
    fail loudly, it just produces a confidently wrong mph number.
    """
    h, w = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    # Court lines are painted bright against a darker playing surface.
    _, bright = cv2.threshold(gray, 170, 255, cv2.THRESH_BINARY)
    edges = cv2.Canny(bright, 50, 150)
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 180, threshold=60,
        minLineLength=w * 0.22, maxLineGap=18,
    )
    if lines is None or len(lines) < 3:
        return {'H': None, 'confidence': 0.0}

    horiz, vert = [], []
    for l in lines.reshape(-1, 4):  # OpenCV 4 returns (N,1,4), OpenCV 5 (N,4)
        angle, length = _line_angle_length(l)
        if abs(angle) < 18 or abs(abs(angle) - 180) < 18:
            horiz.append((l, length))
        elif abs(abs(angle) - 90) < 30:
            vert.append((l, length))

    if len(horiz) < 1 or len(vert) < 2:
        return {'H': None, 'confidence': 0.0}

    # Near baseline: the longest, lowest-in-frame horizontal line — the
    # baseline closest to the camera is both nearer (longer in pixels due
    # to perspective) and lower on screen than the service line or net.
    horiz.sort(key=lambda hl: (-(hl[0][1] + hl[0][3]) / 2, -hl[1]))
    baseline = max(horiz, key=lambda hl: hl[1])[0]

    # Sidelines: the two longest near-vertical lines on opposite sides of
    # the frame's horizontal center.
    left_candidates = [vl for vl in vert if (vl[0][0] + vl[0][2]) / 2 < w / 2]
    right_candidates = [vl for vl in vert if (vl[0][0] + vl[0][2]) / 2 >= w / 2]
    if not left_candidates or not right_candidates:
        return {'H': None, 'confidence': 0.0}
    left_line = max(left_candidates, key=lambda vl: vl[1])[0]
    right_line = max(right_candidates, key=lambda vl: vl[1])[0]

    def intersect(a, b):
        x1, y1, x2, y2 = a
        x3, y3, x4, y4 = b
        denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        if abs(denom) < 1e-6:
            return None
        px = ((x1 * y2 - y1 * x2) * (x3 - x4) - (x1 - x2) * (x3 * y4 - y3 * x4)) / denom
        py = ((x1 * y2 - y1 * x2) * (y3 - y4) - (y1 - y2) * (x3 * y4 - y3 * x4)) / denom
        return (px, py)

    near_left = intersect(baseline, left_line)
    near_right = intersect(baseline, right_line)
    if near_left is None or near_right is None:
        return {'H': None, 'confidence': 0.0}

    # Far corners: follow the sidelines up to the top of their detected
    # segment as a stand-in for the far baseline when it isn't itself
    # clearly visible in frame.
    far_left = (left_line[0], left_line[1]) if left_line[1] < left_line[3] else (left_line[2], left_line[3])
    far_right = (right_line[0], right_line[1]) if right_line[1] < right_line[3] else (right_line[2], right_line[3])

    src = np.array([near_left, near_right, far_right, far_left], dtype=np.float32)
    dst = np.array([
        [0, 0],
        [COURT_WIDTH_DOUBLES_FT, 0],
        [COURT_WIDTH_DOUBLES_FT, COURT_LENGTH_FT],
        [0, COURT_LENGTH_FT],
    ], dtype=np.float32)

    # Sanity checks before trusting this geometry at all: a real
    # baseline-to-net-ish view keeps the base noticeably wide, with both
    # sidelines roughly the same pixel length. Big mismatches mean the
    # detector grabbed the wrong lines.
    base_width_px = math.hypot(near_right[0] - near_left[0], near_right[1] - near_left[1])
    side_len_px = math.hypot(far_left[0] - near_left[0], far_left[1] - near_left[1])
    confidence = 0.0
    if base_width_px > w * 0.2 and side_len_px > h * 0.15:
        right_side_len_px = math.hypot(far_right[0] - near_right[0], far_right[1] - near_right[1])
        symmetry = 1.0 - min(1.0, abs(side_len_px - right_side_len_px) / max(side_len_px, right_side_len_px, 1))
        confidence = max(0.0, min(1.0, symmetry))

    if confidence < MIN_CALIBRATION_CONFIDENCE:
        return {'H': None, 'confidence': confidence}

    H, _ = cv2.findHomography(src, dst)
    return {'H': H, 'confidence': confidence}


def pixel_to_court(H, x, y):
    pt = np.array([[[x, y]]], dtype=np.float32)
    out = cv2.perspectiveTransform(pt, H)
    return float(out[0][0][0]), float(out[0][0][1])


def _ground_level_row_fn(H_inv):
    """A function col -> pixel row for where the net's physical position
    (height 0, i.e. ground level directly under the net cord) projects to,
    as a function of image column. Exact, since a homography maps the
    straight real-world net line to a straight pixel line."""
    p_left = cv2.perspectiveTransform(
        np.array([[[0.0, COURT_LENGTH_FT - NET_DISTANCE_FROM_BASELINE_FT]]], dtype=np.float32), H_inv
    )[0][0]
    p_right = cv2.perspectiveTransform(
        np.array([[[COURT_WIDTH_DOUBLES_FT, COURT_LENGTH_FT - NET_DISTANCE_FROM_BASELINE_FT]]], dtype=np.float32), H_inv
    )[0][0]
    if abs(p_right[0] - p_left[0]) < 1e-6:
        return lambda col: p_left[1]
    slope = (p_right[1] - p_left[1]) / (p_right[0] - p_left[0])
    intercept = p_left[1] - slope * p_left[0]
    return lambda col: slope * col + intercept


def find_net_line(frame, H):
    """Best-effort detection of the net cord's pixel line. Needed to read
    ball height at the net without assuming an unverified camera height —
    the net cord gives a second, independent real-world height reference
    (its own known height) sitting right in the frame.

    Returns (slope, intercept) for net_row = slope*col + intercept in
    image space, or None if not confidently found.
    """
    h, w = frame.shape[:2]
    try:
        H_inv = np.linalg.inv(H)
    except np.linalg.LinAlgError:
        return None

    ground_row_fn = _ground_level_row_fn(H_inv)
    mid_col = w / 2
    expected_ground_row = ground_row_fn(mid_col)

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 40, 120)

    # The net cord sits above (smaller row than) its ground projection —
    # search a band from well above that row down to it.
    top = max(0, int(expected_ground_row - h * 0.25))
    bottom = min(h, int(expected_ground_row) + 5)
    if bottom - top < 5:
        return None
    band = edges[top:bottom, :]

    lines = cv2.HoughLinesP(band, 1, np.pi / 180, threshold=40, minLineLength=w * 0.18, maxLineGap=15)
    if lines is None:
        return None

    best = max(lines.reshape(-1, 4), key=lambda l: math.hypot(l[2] - l[0], l[3] - l[1]))
    x1, y1, x2, y2 = best
    y1 += top
    y2 += top
    if abs(x2 - x1) < 1e-6:
        return None
    slope = (y2 - y1) / (x2 - x1)
    intercept = y1 - slope * x1

    # Net cord must sit clearly above its own ground projection at the
    # midpoint — otherwise this almost certainly isn't the net.
    net_row_mid = slope * mid_col + intercept
    if expected_ground_row - net_row_mid < 4:
        return None
    return (slope, intercept)


# ---------------------------------------------------------------------
# Shot segmentation (split a rally's ball track into individual shots,
# bounded by bounces)
# ---------------------------------------------------------------------

def segment_shots(detections, fps, min_gap_sec=0.25):
    """Finds bounce points (local max in pixel-y = ball nearest the
    ground, with a down-then-up reversal) and splits the trajectory into
    shot segments between them.

    Returns a list of segments, each a list of (index, detection dict)
    tuples with non-null detections only. A segment that ends in a real
    bounce ends on the bounce frame; the final segment (trailing off when
    tracking is lost) does not.
    """
    pts = [(i, d) for i, d in enumerate(detections) if d is not None]
    if len(pts) < 6:
        return [pts] if pts else []

    bounce_indices = []
    min_gap_frames = max(1, int(min_gap_sec * fps))
    for k in range(2, len(pts) - 2):
        _, prev2 = pts[k - 2]
        _, cur = pts[k]
        _, next2 = pts[k + 2]
        falling = (cur['y'] - prev2['y']) > 0
        rising = (next2['y'] - cur['y']) < 0
        if falling and rising:
            if not bounce_indices or (k - bounce_indices[-1]) >= min_gap_frames:
                bounce_indices.append(k)

    if not bounce_indices:
        return [pts]

    segments = []
    start = 0
    for b in bounce_indices:
        segments.append(pts[start:b + 1])
        start = b + 1
    if start < len(pts):
        segments.append(pts[start:])
    return [s for s in segments if len(s) >= 3]


# ---------------------------------------------------------------------
# Per-shot physics
# ---------------------------------------------------------------------

FT_PER_SEC_TO_MPH = 3600.0 / 5280.0


def _fit_line(ts, ys):
    """Least-squares slope (units of ys per second)."""
    ts = np.array(ts)
    ys = np.array(ys)
    if len(ts) < 2 or np.std(ts) == 0:
        return 0.0
    slope, _ = np.polyfit(ts, ys, 1)
    return float(slope)


def _bounce_spin_score(segment, next_segment):
    """Spin evidence from the bounce kick: the boundary between this
    shot's flight (tail, descending into the bounce) and the next shot's
    opening trajectory (head, right after the bounce) — where topspin,
    slice, and flat shots visibly diverge. Returns a score in [-1, 1]
    (+ = topspin, - = slice), or None when there's no clean bounce.
    """
    if not next_segment or len(segment) < 3 or len(next_segment) < 3:
        return None

    k = min(5, len(segment) - 1, len(next_segment) - 1)
    pre = segment[-(k + 1):]
    post = next_segment[:k + 1]
    pre_slope = _fit_line([d['t'] for _, d in pre], [d['y'] for _, d in pre])     # > 0: falling into the bounce
    post_slope = _fit_line([d['t'] for _, d in post], [d['y'] for _, d in post])  # < 0: rising back up after it

    if pre_slope <= 0 or post_slope >= 0:
        # Doesn't look like a clean bounce in the tracked data — don't
        # force a classification onto noise.
        return None

    # A rise ratio of 0.6+ reads as topspin and 0.25 or less as slice;
    # those map to +/-SPIN_THRESHOLD so bounce-only reads stay unchanged.
    rise_ratio = -post_slope / pre_slope
    return float(np.clip((rise_ratio - 0.425) / 0.175 * SPIN_THRESHOLD, -1.0, 1.0))


SPIN_THRESHOLD = 0.3


def _classify_spin(bounce_score, swing=None):
    """Fuses bounce-kick evidence with swing evidence (path, rise,
    forearm roll — see swing_analysis.py) into one spin call. Either
    source alone is enough; when both are present they're weighted, and
    disagreement lowers the stated confidence instead of being hidden.
    """
    swing_score = swing.get('spin_score') if swing else None
    sources = []
    if bounce_score is not None:
        sources.append(('bounce', bounce_score, 1.0))
    if swing_score is not None:
        # On serves the arm always travels up, so the swing says much
        # less about spin than it does on groundstrokes.
        weight = swing['quality'] * (0.4 if swing.get('shot_type') == 'Serve / Overhead' else 1.0)
        sources.append(('swing', swing_score, weight))
    if not sources:
        return {'available': False}

    total_w = sum(w for _, _, w in sources)
    score = sum(sc * w for _, sc, w in sources) / total_w
    if score >= SPIN_THRESHOLD:
        spin_type = 'Topspin'
    elif score <= -SPIN_THRESHOLD:
        spin_type = 'Slice / Backspin'
    else:
        spin_type = 'Flat'

    confidence = 0.5 + min(0.25, abs(score) * 0.3)
    names = [n for n, _, _ in sources]
    if len(sources) == 2:
        (_, a, _), (_, b, _) = sources
        if (a >= SPIN_THRESHOLD and b <= -SPIN_THRESHOLD) or (a <= -SPIN_THRESHOLD and b >= SPIN_THRESHOLD):
            confidence = min(confidence, 0.5)
            note = "Swing and bounce disagreed on this one, so treat the spin call as a rough read."
        else:
            confidence += 0.1
            note = "Read from both the swing (path, rise, forearm roll) and how the ball kicked off the bounce."
    elif names == ['swing']:
        note = "Read from the swing (path, rise, forearm roll) — the bounce wasn't tracked cleanly."
    else:
        note = "Read from how the ball kicked off the bounce — the swing wasn't visible for this shot."

    return {'available': True, 'type': spin_type, 'confidence': round(min(0.9, confidence), 2),
            'score': round(score, 2), 'sources': names, 'note': note}


def compute_shot_metrics(segment, fps, H=None, net_line=None, next_segment=None, swing=None):
    """Derives speed/depth/height/spin/heaviness for one shot segment.

    `segment` is a list of (frame_index, detection) tuples as produced by
    segment_shots(); `next_segment` (optional) is the shot that follows
    it, used only to read the bounce kick for spin; `swing` (optional) is
    the near player's swing for this shot from swing_analysis.py. Every field in the
    returned dict carries an 'available' flag — render/report a number
    only when it's True.
    """
    result = {
        'speed_mph': {'available': False},
        'depth': {'available': False},
        'height': {'available': False},
        'spin': {'available': False},
        'heaviness': {'available': False},
        'swing': swing,  # None when the near player didn't hit this shot
        'trajectory_ft': None,  # [[x_ft, y_ft], ...] for drawing, when calibrated
    }
    if len(segment) < 4:
        result['spin'] = _classify_spin(None, swing)
        return result

    dets = [d for _, d in segment]
    ts = [d['t'] for d in dets]
    xs_px = [d['x'] for d in dets]
    ys_px = [d['y'] for d in dets]

    result['spin'] = _classify_spin(_bounce_spin_score(segment, next_segment), swing)

    # Everything below needs real-world units, which needs a trustworthy
    # court calibration for this frame.
    if H is None:
        return result

    court_pts = [pixel_to_court(H, x, y) for x, y in zip(xs_px, ys_px)]
    court_xs = [p[0] for p in court_pts]
    court_ys = [p[1] for p in court_pts]
    result['trajectory_ft'] = [[round(x, 2), round(y, 2)] for x, y in court_pts]

    # --- Speed: steepest short window of real-world displacement over
    # time, taken from the fastest-moving window — closest to contact,
    # before drag/spin bleed off pace.
    window = max(2, len(court_pts) // 3)
    best_speed = 0.0
    for i in range(0, len(court_pts) - window):
        dx = court_xs[i + window] - court_xs[i]
        dy = court_ys[i + window] - court_ys[i]
        dt = ts[i + window] - ts[i]
        if dt <= 0:
            continue
        dist_ft = math.hypot(dx, dy)
        speed_fts = dist_ft / dt
        best_speed = max(best_speed, speed_fts)
    if best_speed > 0:
        mph = best_speed * FT_PER_SEC_TO_MPH
        # Cap absurd outliers from a bad detection jump rather than report them.
        if mph <= 160:
            result['speed_mph'] = {'available': True, 'value': round(mph, 1)}

    # --- Depth: where the shot's bounce lands relative to the baseline.
    # Only meaningful when this segment actually ends on a real bounce
    # (i.e. a next segment exists) rather than trailing off mid-flight.
    bounce_y_ft = court_ys[-1]
    depth_from_baseline_ft = COURT_LENGTH_FT - bounce_y_ft
    if next_segment is not None and 0 <= depth_from_baseline_ft <= COURT_LENGTH_FT:
        if depth_from_baseline_ft <= 4:
            label = 'Deep'
        elif depth_from_baseline_ft <= 12:
            label = 'Medium'
        else:
            label = 'Short'
        result['depth'] = {
            'available': True,
            'from_baseline_ft': round(depth_from_baseline_ft, 1),
            'label': label,
        }

    # --- Height: net clearance, read directly off a single frame near
    # the net using the net cord's own known height as the vertical
    # reference — this needs no assumption about camera height.
    if net_line is not None:
        slope, intercept = net_line
        net_depth_ft = COURT_LENGTH_FT - NET_DISTANCE_FROM_BASELINE_FT
        net_crossing_idx = min(range(len(court_ys)), key=lambda i: abs(court_ys[i] - net_depth_ft))
        # Only trust this if the ball was actually tracked near the net's
        # real-world depth, not far up- or down-court from it.
        if abs(court_ys[net_crossing_idx] - net_depth_ft) <= 6:
            col = xs_px[net_crossing_idx]
            net_row = slope * col + intercept
            H_inv = None
            try:
                H_inv = np.linalg.inv(H)
            except np.linalg.LinAlgError:
                pass
            if H_inv is not None:
                ground_row_fn = _ground_level_row_fn(H_inv)
                ground_row = ground_row_fn(col)
                net_height_px = ground_row - net_row
                if net_height_px > 2:
                    px_per_ft = net_height_px / NET_HEIGHT_CENTER_FT
                    ball_row = ys_px[net_crossing_idx]
                    clearance_ft = (net_row - ball_row) / px_per_ft
                    clearance_ft = max(0.0, min(15.0, clearance_ft))
                    result['height'] = {
                        'available': True,
                        'net_clearance_ft': round(clearance_ft, 1),
                        'note': "Measured against the net cord's own height in this frame, not an assumed camera height.",
                    }

    # --- Heaviness: composite index from speed + spin "kick" magnitude —
    # not a physical measurement, explicitly a 0-100 feel index, per the
    # same speed-times-rotation logic that makes a shot feel heavy.
    if result['speed_mph']['available'] and result['spin']['available']:
        speed_component = min(1.0, result['speed_mph']['value'] / 90.0)
        spin_component = result['spin']['confidence'] if result['spin']['type'] == 'Topspin' else result['spin']['confidence'] * 0.5
        heaviness_score = round((0.6 * speed_component + 0.4 * spin_component) * 100)
        result['heaviness'] = {
            'available': True,
            'score': heaviness_score,
            'note': "Index combining shot speed and topspin kick — not the ball's physical weight, which never changes.",
        }

    return result


def analyze_point_ball_physics(video_path, max_seconds=12.0, dominant_hand=None):
    """Top-level entry point: tracks the ball and the near player's pose,
    calibrates the court off the first frame, segments the rally into
    shots, and returns per-shot physics (with the swing read for every
    shot the near player hit). Safe to call on any clip — degrades to empty/unavailable
    results rather than raising when tracking or calibration fails.
    """
    track = track_ball(video_path, max_seconds=max_seconds)
    detections, fps = track['detections'], track['fps']

    cap = cv2.VideoCapture(video_path)
    ret, first_frame = cap.read()
    cap.release()

    calibration = {'H': None, 'confidence': 0.0}
    net_line = None
    if ret:
        calibration = find_court_homography(first_frame)
        if calibration['H'] is not None:
            net_line = find_net_line(first_frame, calibration['H'])

    found_frames = sum(1 for d in detections if d is not None)
    tracking_rate = found_frames / len(detections) if detections else 0.0

    shots = []
    pose_tracked = False
    if tracking_rate >= 0.4:
        segments = segment_shots(detections, fps)

        # Each bounce-to-bounce segment holds at most one hit; match the
        # near player's contacts to the segment they fall inside.
        swings = {}
        poses = track_pose(video_path, max_seconds=max_seconds)
        if poses:
            pose_tracked = sum(1 for p in poses if p is not None) >= len(poses) * 0.3
        if pose_tracked:
            for f, wrist_idx in find_contacts(detections, poses, fps):
                for i, segment in enumerate(segments):
                    if segment[0][0] <= f <= segment[-1][0] and i not in swings:
                        swings[i] = analyze_swing(poses, detections, f, wrist_idx, fps,
                                                  H=calibration['H'], dominant_hand=dominant_hand)
                        break

        for i, segment in enumerate(segments):
            next_segment = segments[i + 1] if i + 1 < len(segments) else None
            metrics = compute_shot_metrics(segment, fps, H=calibration['H'], net_line=net_line,
                                           next_segment=next_segment, swing=swings.get(i))
            shots.append(metrics)

    return {
        'shots': shots,
        'tracking_rate': round(tracking_rate, 2),
        'calibration_confidence': round(calibration['confidence'], 2),
        'calibrated': calibration['H'] is not None,
        'net_detected': net_line is not None,
        'pose_tracked': pose_tracked,
    }
