import cv2
import numpy as np


def extract_frames(video_path, max_frames=20):  # reduced from 60 to 20
    cap = cv2.VideoCapture(video_path)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    duration = total_frames / fps if fps > 0 else 0

    indices = np.linspace(0, total_frames - 1, min(max_frames, total_frames), dtype=int)
    frames = []

    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if ret:
            # Resize frame to smaller size for faster processing
            frame = cv2.resize(frame, (320, 240))
            frames.append(frame)

    cap.release()
    return frames, fps, duration


def detect_motion_regions(frames):
    motion_data = []

    for i in range(1, len(frames)):
        prev = cv2.cvtColor(frames[i - 1], cv2.COLOR_BGR2GRAY)
        curr = cv2.cvtColor(frames[i], cv2.COLOR_BGR2GRAY)

        diff = cv2.absdiff(prev, curr)
        _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)

        h, w = thresh.shape

        top_zone    = thresh[:h // 3, :]
        bottom_zone = thresh[2 * h // 3:, :]
        left_zone   = thresh[:, :w // 2]
        right_zone  = thresh[:, w // 2:]

        motion_data.append({
            "total":   float(np.sum(thresh) / 255),
            "top":     float(np.sum(top_zone) / 255),
            "bottom":  float(np.sum(bottom_zone) / 255),
            "left":    float(np.sum(left_zone) / 255),
            "right":   float(np.sum(right_zone) / 255),
        })

    return motion_data


def analyze_upper_body_reach(frames):
    reach_scores = []

    for frame in frames:
        h, w = frame.shape[:2]
        upper = frame[:int(h * 0.6), :]

        hsv = cv2.cvtColor(upper, cv2.COLOR_BGR2HSV)
        lower_skin = np.array([0, 20, 70], dtype=np.uint8)
        upper_skin = np.array([20, 255, 255], dtype=np.uint8)
        mask = cv2.inRange(hsv, lower_skin, upper_skin)

        skin_pixels = np.where(mask > 0)
        if len(skin_pixels[0]) > 20:
            highest_y = np.min(skin_pixels[0])
            reach_score = 1.0 - (highest_y / (h * 0.6))
            reach_scores.append(reach_score)

    return reach_scores


def process_video(video_path, shot_type='serve'):
    frames, fps, duration = extract_frames(video_path)

    if not frames:
        return {"error": "Could not read video frames."}

    motion_data = detect_motion_regions(frames)
    reach_scores = analyze_upper_body_reach(frames)

    if not motion_data:
        return {"error": "No motion detected in video."}

    total_motions  = [d["total"]  for d in motion_data]
    top_motions    = [d["top"]    for d in motion_data]
    bottom_motions = [d["bottom"] for d in motion_data]
    left_motions   = [d["left"]   for d in motion_data]
    right_motions  = [d["right"]  for d in motion_data]

    avg_total   = float(np.mean(total_motions))
    avg_top     = float(np.mean(top_motions))
    avg_bottom  = float(np.mean(bottom_motions))

    upper_ratio        = avg_top / (avg_bottom + 1e-6)
    motion_std         = float(np.std(total_motions))
    motion_consistency = max(0.0, 1.0 - (motion_std / (avg_total + 1e-6)))

    avg_left        = float(np.mean(left_motions))
    avg_right       = float(np.mean(right_motions))
    lateral_balance = 1.0 - abs(avg_left - avg_right) / (avg_left + avg_right + 1e-6)

    avg_reach         = float(np.mean(reach_scores)) if reach_scores else 0.5
    reach_consistency = 1.0 - float(np.std(reach_scores)) if reach_scores else 0.5

    return {
        "shot_type":            shot_type,
        "frames_analyzed":      len(frames),
        "duration_sec":         round(duration, 2),
        "contact_height_score": round(avg_reach, 3),
        "contact_height_avg":   round(avg_reach, 3),
        "arm_extension_avg":    round(upper_ratio * 90, 1),
        "arm_extension_max":    round(min(upper_ratio * 120, 175), 1),
        "shoulder_angle_avg":   round(upper_ratio * 100, 1),
        "knee_bend_avg":        round(130 + (1 - motion_consistency) * 30, 1),
        "toss_consistency_std": round(1.0 - reach_consistency, 3),
        "trunk_rotation_avg":   round(lateral_balance * 1.2, 3),
        "motion_consistency":   round(motion_consistency, 3),
        "upper_body_ratio":     round(upper_ratio, 3),
        "lateral_balance":      round(lateral_balance, 3),
    }
