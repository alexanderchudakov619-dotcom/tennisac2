"""
video_processor.py
Measures stroke mechanics from a clip using body tracking (MediaPipe pose):
finds each swing, locates its contact moment, and reads real joint angles
and body rotation there.

Every number this returns is a measurement off the tracked skeleton, in
plain units (degrees, or torso-lengths for heights so it doesn't depend on
the player's size or distance from the camera). When something can't be
measured — legs out of frame, only one serve in the clip — it comes back as
None and the score shows N/A instead of a guess.
"""

import math
import cv2
import numpy as np

from analysis.swing_analysis import (
    track_pose_detailed, _torso_len, MIN_VISIBILITY,
    NOSE, L_SHOULDER, R_SHOULDER, L_ELBOW, R_ELBOW, L_WRIST, R_WRIST,
    L_HIP, R_HIP, L_ANKLE, R_ANKLE,
)

L_KNEE, R_KNEE = 25, 26

# Pose runs on ~15 frames/sec: plenty for joint angles at contact, and it
# keeps a 30-second clip affordable on a small server.
ANALYSIS_FPS = 15
MAX_SECONDS = 30.0
MIN_POSE_COVERAGE = 0.3

NO_PLAYER_MSG = ("We couldn't find a player clearly in that video. Film from the side or "
                 "behind with your whole body in frame, in decent light, and try again.")
NO_SWING_MSG = ("We could see you, but couldn't find a swing in that clip. Make sure the "
                "video includes the full stroke, from backswing through follow-through.")
UNREADABLE_MSG = "We couldn't read that video file. Try an MP4 or MOV under 60 seconds."


def _angle(a, b, c):
    """Angle ABC in degrees (works for 2D or 3D points)."""
    v1, v2 = np.asarray(a) - np.asarray(b), np.asarray(c) - np.asarray(b)
    n = np.linalg.norm(v1) * np.linalg.norm(v2)
    if n == 0:
        return None
    return math.degrees(math.acos(np.clip(np.dot(v1, v2) / n, -1.0, 1.0)))


def _visible(pose, *idxs):
    return all(pose[i][2] >= MIN_VISIBILITY for i in idxs)


def _yaw(world, left, right):
    """Direction (degrees) the line between two joints faces, seen from
    above — the shoulder line's yaw changing is the shoulders turning."""
    v = world[right] - world[left]
    return math.degrees(math.atan2(v[2], v[0]))


# Fastest plausible change in a body line's direction between two samples
# ~1/15 s apart. Faster "turns" are MediaPipe's depth estimate flipping the
# skeleton front-to-back for a frame, not the player rotating.
MAX_YAW_STEP = 60.0


def _rotation_track(yaws):
    """Unwraps a yaw series into a continuous turn, dropping samples that
    jump implausibly far from the last good one. Returns the cleaned
    series (degrees)."""
    out = []
    for y in yaws:
        if not out:
            out.append(y)
            continue
        step = ((y - out[-1]) + 180) % 360 - 180
        if abs(step) <= MAX_YAW_STEP:
            out.append(out[-1] + step)
    return out


def _robust_range(xs):
    # 5th-95th percentile spread, so one bad frame can't define the turn.
    return float(np.percentile(xs, 95) - np.percentile(xs, 5)) if len(xs) >= 3 else None


def _find_peaks(times, values, min_value, min_gap):
    """Local maxima above min_value, at least min_gap seconds apart
    (keeping the bigger peak when two are close)."""
    peaks = []
    for i in range(1, len(values) - 1):
        if values[i] >= min_value and values[i] >= values[i - 1] and values[i] >= values[i + 1]:
            if peaks and times[i] - times[peaks[-1]] < min_gap:
                if values[i] > values[peaks[-1]]:
                    peaks[-1] = i
            else:
                peaks.append(i)
    return peaks


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(float(np.mean(xs)), 1) if xs else None


def process_video(video_path, shot_type='serve', dominant_hand=None):
    cap = cv2.VideoCapture(video_path)
    opened = cap.isOpened()
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    ok, _ = cap.read() if opened else (False, None)
    cap.release()
    if not opened or not ok or fps <= 0:
        return {"error": "unreadable", "error_message": UNREADABLE_MSG}
    duration = total_frames / fps if total_frames else 0

    stride = max(1, round(fps / ANALYSIS_FPS))
    poses, worlds, fps = track_pose_detailed(video_path, max_seconds=MAX_SECONDS, stride=stride)
    if poses is None:
        return {"error": "pose_unavailable",
                "error_message": "Analysis is temporarily unavailable on the server. Please try again shortly."}

    sampled = [i for i in range(len(poses)) if i % stride == 0]
    tracked = [i for i in sampled if poses[i] is not None]
    coverage = len(tracked) / len(sampled) if sampled else 0
    if len(tracked) < 6 or coverage < MIN_POSE_COVERAGE:
        return {"error": "no_player", "error_message": NO_PLAYER_MSG}

    torso = float(np.median([_torso_len(poses[i]) for i in tracked]))
    if torso < 5:
        return {"error": "no_player", "error_message": NO_PLAYER_MSG}
    t = {i: i / fps for i in tracked}

    # --- Hitting hand: from the profile when we have it, otherwise the
    # wrist that moves fastest over the clip.
    def wrist_speeds(w):
        sp = {}
        for a, b in zip(tracked, tracked[1:]):
            if b - a > 3 * stride or not _visible(poses[a], w) or not _visible(poses[b], w):
                continue
            d = np.linalg.norm(poses[b][w][:2] - poses[a][w][:2]) / torso
            sp[b] = d / (t[b] - t[a])
        return sp

    if dominant_hand in ('Right', 'Left'):
        hit = R_WRIST if dominant_hand == 'Right' else L_WRIST
    else:
        r_sp, l_sp = wrist_speeds(R_WRIST), wrist_speeds(L_WRIST)
        r95 = np.percentile(list(r_sp.values()), 95) if r_sp else 0
        l95 = np.percentile(list(l_sp.values()), 95) if l_sp else 0
        hit = R_WRIST if r95 >= l95 else L_WRIST
    toss = L_WRIST if hit == R_WRIST else R_WRIST
    side = 'R' if hit == R_WRIST else 'L'
    shoulder, elbow = (R_SHOULDER, R_ELBOW) if side == 'R' else (L_SHOULDER, L_ELBOW)

    # --- Find each swing's contact moment.
    if shot_type == 'serve':
        # Serve contact is the hitting hand's highest point above the head.
        idx = [i for i in tracked if _visible(poses[i], hit, NOSE)]
        heights = [(poses[i][NOSE][1] - poses[i][hit][1]) / torso for i in idx]
        peaks = _find_peaks([t[i] for i in idx], heights, min_value=0.6, min_gap=1.5)
        contacts = [idx[p] for p in peaks]
        if not contacts and idx and max(heights) > 0.3:
            contacts = [idx[int(np.argmax(heights))]]
    else:
        # Groundstrokes: contact sits at the hand's peak speed.
        sp = wrist_speeds(hit)
        idx = sorted(sp)
        vals = [sp[i] for i in idx]
        peaks = _find_peaks([t[i] for i in idx], vals, min_value=4.0, min_gap=0.8)
        contacts = [idx[p] for p in peaks]
        if not contacts and vals and max(vals) > 2.0:
            contacts = [idx[int(np.argmax(vals))]]
    if not contacts:
        return {"error": "no_swing", "error_message": NO_SWING_MSG}

    def frames_between(t0, t1):
        return [i for i in tracked if t0 <= t[i] <= t1]

    per_swing = []
    for c in contacts:
        p = poses[c]
        wld = worlds[c]
        hip_mid = (p[L_HIP][:2] + p[R_HIP][:2]) / 2
        m = {}

        # Contact height: hitting hand above the hips, in torso lengths.
        if _visible(p, hit, L_HIP, R_HIP):
            m['contact_height'] = (hip_mid[1] - p[hit][1]) / torso

        # Arm extension: elbow angle at contact (3D when available, since
        # a 2D angle shrinks when the arm points toward the camera).
        if _visible(p, shoulder, elbow, hit):
            pts = wld if wld is not None else p[:, :2]
            m['elbow_angle'] = _angle(pts[shoulder], pts[elbow], pts[hit])

        # Knee bend: deepest knee angle while loading, before contact.
        load = frames_between(t[c] - 1.0, t[c]) if shot_type == 'serve' else frames_between(t[c] - 0.6, t[c] + 0.1)
        knees = []
        for i in load:
            q, wq = poses[i], worlds[i]
            pts = wq if wq is not None else q[:, :2]
            for hp, kn, an in ((L_HIP, L_KNEE, L_ANKLE), (R_HIP, R_KNEE, R_ANKLE)):
                if _visible(q, hp, kn, an):
                    a = _angle(pts[hp], pts[kn], pts[an])
                    if a is not None:
                        knees.append(a)
        if len(knees) >= 2:
            # Near-lowest rather than the single lowest reading, so one
            # glitchy frame can't invent a deep knee bend.
            m['knee_angle'] = float(np.percentile(knees, 10))

        # Rotation through the swing, from the 3D skeleton: how far the
        # shoulder line turns, and the max gap between shoulder and hip
        # turn (hip-shoulder separation — the "coil").
        win = [i for i in frames_between(t[c] - 1.0, t[c] + 0.3) if worlds[i] is not None
               and _visible(poses[i], L_SHOULDER, R_SHOULDER, L_HIP, R_HIP)]
        if len(win) >= 4:
            sh = _rotation_track([_yaw(worlds[i], L_SHOULDER, R_SHOULDER) for i in win])
            rot = _robust_range(sh)
            # A real stroke turns the shoulders well under 180°; more than
            # that means the tracking was unstable on this swing.
            if rot is not None and len(sh) >= 0.7 * len(win) and rot <= 180:
                m['shoulder_rotation'] = rot
            seps = []
            for i in win:
                d = abs(((_yaw(worlds[i], L_SHOULDER, R_SHOULDER) - _yaw(worlds[i], L_HIP, R_HIP)) + 180) % 360 - 180)
                # Shoulders and hips facing 90°+ apart isn't anatomically
                # possible; it's a mirrored frame.
                if d <= 80:
                    seps.append(d)
            if len(seps) >= 3:
                m['separation'] = float(np.percentile(seps, 90))

        # Serve toss: where the tossing hand peaks, relative to the hips.
        if shot_type == 'serve':
            tw = [i for i in frames_between(t[c] - 1.5, t[c] - 0.15) if _visible(poses[i], toss, L_HIP, R_HIP)]
            if tw:
                top = min(tw, key=lambda i: poses[i][toss][1])
                hm = (poses[top][L_HIP][:2] + poses[top][R_HIP][:2]) / 2
                m['toss_x'] = (poses[top][toss][0] - hm[0]) / torso
        per_swing.append(m)

    def all_of(key):
        return [s[key] for s in per_swing if s.get(key) is not None]

    toss_xs = all_of('toss_x')
    toss_std = round(float(np.std(toss_xs)), 3) if len(toss_xs) >= 2 else None
    elbows = all_of('elbow_angle')

    return {
        "shot_type":            shot_type,
        "frames_analyzed":      len(tracked),
        "duration_sec":         round(duration, 2),
        "swings_detected":      len(contacts),
        "contact_times":        [round(t[c], 2) for c in contacts],
        "pose_coverage":        round(coverage, 2),
        "hitting_hand":         'Right' if side == 'R' else 'Left',
        # Heights in torso lengths above the hips.
        "contact_height_score": _mean(all_of('contact_height')),
        "contact_height_avg":   _mean(all_of('contact_height')),
        # Angles in degrees.
        "arm_extension_avg":    _mean(elbows),
        "arm_extension_max":    round(max(elbows), 1) if elbows else None,
        "knee_bend_avg":        _mean(all_of('knee_angle')),
        "shoulder_angle_avg":   _mean(all_of('shoulder_rotation')),
        "trunk_rotation_avg":   _mean(all_of('shoulder_rotation')),
        "hip_shoulder_separation": _mean(all_of('separation')),
        # Spread of the toss across serves, in torso lengths (needs 2+ serves).
        "toss_consistency_std": toss_std,
    }
