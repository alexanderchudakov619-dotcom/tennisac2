"""
trajectory_viz.py
Renders the tracked ball physics as a top-down court diagram: each shot's
actual measured flight path, color-coded by spin, with its bounce marked
and colored by depth. Built as inline SVG (crisp at any size, no extra
image round-trip) straight from the real tracked coordinates in
ball_physics.py — nothing here is drawn from a guess.

What's trustworthy vs. illustrative, by design:
- The bounce point for each shot is exact: height is zero there, so the
  court-plane projection has no elevation distortion.
- The in-flight portion of each path is drawn from the same projection
  applied to an airborne (elevated) ball, which systematically reads as
  slightly farther down-court than the ball's true position at that
  instant. That's a known, bounded distortion (worst right after contact,
  zero at the bounce) — fine for showing the shape of a shot, not
  something to read a mid-flight coordinate off of.
"""

import math

from analysis.ball_physics import (
    COURT_LENGTH_FT,
    COURT_WIDTH_DOUBLES_FT,
    NET_DISTANCE_FROM_BASELINE_FT,
)

SCALE_PX_PER_FT = 8
MARGIN = 40
SINGLES_INSET_FT = 4.5
SERVICE_LINE_FROM_NET_FT = 21.0

SPIN_COLORS = {
    'Topspin': '#16a34a',
    'Slice / Backspin': '#dc2626',
    'Flat': '#d97706',
}
SPIN_FALLBACK_COLOR = '#94a3b8'

DEPTH_COLORS = {
    'Deep': '#16a34a',
    'Medium': '#d97706',
    'Short': '#dc2626',
}


def _court_to_svg(x_ft, y_ft):
    """Court space: x in [0, 36] (width), y in [0, 78] (length, 0 = near
    baseline closest to camera). SVG space: near baseline drawn at the
    bottom, since that's the player's own end of the court."""
    svg_x = MARGIN + x_ft * SCALE_PX_PER_FT
    svg_y = MARGIN + (COURT_LENGTH_FT - y_ft) * SCALE_PX_PER_FT
    return svg_x, svg_y


def _smooth(points, window=3):
    if len(points) < window + 1:
        return points
    out = []
    half = window // 2
    for i in range(len(points)):
        lo, hi = max(0, i - half), min(len(points), i + half + 1)
        chunk = points[lo:hi]
        avg_x = sum(p[0] for p in chunk) / len(chunk)
        avg_y = sum(p[1] for p in chunk) / len(chunk)
        out.append((avg_x, avg_y))
    return out


def _subsample(points, max_points=16):
    if len(points) <= max_points:
        return points
    step = len(points) / max_points
    return [points[int(i * step)] for i in range(max_points)]


def _draw_court():
    width_px = COURT_WIDTH_DOUBLES_FT * SCALE_PX_PER_FT
    length_px = COURT_LENGTH_FT * SCALE_PX_PER_FT
    net_y_ft = COURT_LENGTH_FT - NET_DISTANCE_FROM_BASELINE_FT
    near_service_y_ft = net_y_ft - SERVICE_LINE_FROM_NET_FT
    far_service_y_ft = net_y_ft + SERVICE_LINE_FROM_NET_FT

    nl_x, _ = _court_to_svg(SINGLES_INSET_FT, 0)
    nr_x, _ = _court_to_svg(COURT_WIDTH_DOUBLES_FT - SINGLES_INSET_FT, 0)
    center_x, _ = _court_to_svg(COURT_WIDTH_DOUBLES_FT / 2, 0)

    parts = [
        f'<rect x="{MARGIN}" y="{MARGIN}" width="{width_px}" height="{length_px}" '
        f'fill="#eef5ef" stroke="none" rx="4"/>',
    ]

    def line(x1_ft, y1_ft, x2_ft, y2_ft, dashed=False, w=2):
        x1, y1 = _court_to_svg(x1_ft, y1_ft)
        x2, y2 = _court_to_svg(x2_ft, y2_ft)
        dash = ' stroke-dasharray="6,5"' if dashed else ''
        return f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#ffffff" stroke-width="{w}"{dash}/>'

    # Doubles outline
    parts.append(line(0, 0, COURT_WIDTH_DOUBLES_FT, 0, w=3))
    parts.append(line(0, COURT_LENGTH_FT, COURT_WIDTH_DOUBLES_FT, COURT_LENGTH_FT, w=3))
    parts.append(line(0, 0, 0, COURT_LENGTH_FT, w=3))
    parts.append(line(COURT_WIDTH_DOUBLES_FT, 0, COURT_WIDTH_DOUBLES_FT, COURT_LENGTH_FT, w=3))
    # Singles sidelines
    parts.append(line(SINGLES_INSET_FT, 0, SINGLES_INSET_FT, COURT_LENGTH_FT))
    parts.append(line(COURT_WIDTH_DOUBLES_FT - SINGLES_INSET_FT, 0, COURT_WIDTH_DOUBLES_FT - SINGLES_INSET_FT, COURT_LENGTH_FT))
    # Service lines + center service line
    parts.append(line(SINGLES_INSET_FT, near_service_y_ft, COURT_WIDTH_DOUBLES_FT - SINGLES_INSET_FT, near_service_y_ft))
    parts.append(line(SINGLES_INSET_FT, far_service_y_ft, COURT_WIDTH_DOUBLES_FT - SINGLES_INSET_FT, far_service_y_ft))
    parts.append(line(COURT_WIDTH_DOUBLES_FT / 2, near_service_y_ft, COURT_WIDTH_DOUBLES_FT / 2, far_service_y_ft))
    # Net (dashed — not a court line, the net itself)
    net_x1, net_y = _court_to_svg(0, net_y_ft)
    net_x2, _ = _court_to_svg(COURT_WIDTH_DOUBLES_FT, net_y_ft)
    parts.append(f'<line x1="{net_x1-6:.1f}" y1="{net_y:.1f}" x2="{net_x2+6:.1f}" y2="{net_y:.1f}" '
                 f'stroke="#64748b" stroke-width="3" stroke-dasharray="2,4"/>')
    # Baseline center marks
    parts.append(f'<line x1="{center_x:.1f}" y1="{MARGIN}" x2="{center_x:.1f}" y2="{MARGIN+6}" stroke="#ffffff" stroke-width="2"/>')
    parts.append(f'<line x1="{center_x:.1f}" y1="{MARGIN+length_px-6}" x2="{center_x:.1f}" y2="{MARGIN+length_px}" stroke="#ffffff" stroke-width="2"/>')

    return parts, width_px, length_px


def render_trajectory_svg(ball_physics):
    """Returns an inline SVG string for the rally's shots, or None when
    there's no calibrated trajectory data to draw."""
    if not ball_physics or not ball_physics.get('calibrated'):
        return None

    shots = ball_physics.get('shots', [])
    plottable = [s for s in shots if s.get('trajectory_ft')]
    if not plottable:
        return None

    parts, width_px, length_px = _draw_court()

    for i, shot in enumerate(shots):
        traj = shot.get('trajectory_ft')
        if not traj:
            continue

        spin = shot.get('spin', {})
        color = SPIN_COLORS.get(spin.get('type'), SPIN_FALLBACK_COLOR) if spin.get('available') else SPIN_FALLBACK_COLOR

        svg_pts = [_court_to_svg(x, y) for x, y in traj]
        svg_pts = _smooth(svg_pts)
        svg_pts = _subsample(svg_pts)
        path_d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in svg_pts)

        heaviness = shot.get('heaviness', {})
        stroke_w = 2.5
        if heaviness.get('available'):
            stroke_w = 2 + (heaviness['score'] / 100.0) * 3.5

        parts.append(f'<path d="{path_d}" fill="none" stroke="{color}" stroke-width="{stroke_w:.1f}" '
                     f'stroke-linecap="round" stroke-linejoin="round" opacity="0.9"/>')

        # Start marker (contact / pickup point)
        sx, sy = svg_pts[0]
        parts.append(f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="3.5" fill="{color}" opacity="0.6"/>')

        # Bounce marker, colored by depth zone when known
        bx, by = svg_pts[-1]
        depth = shot.get('depth', {})
        bounce_color = DEPTH_COLORS.get(depth.get('label'), color) if depth.get('available') else color
        parts.append(f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="6" fill="{bounce_color}" stroke="#ffffff" stroke-width="1.5"/>')

        # Label: shot number + speed, offset from the path so it doesn't
        # sit on top of the line.
        label_bits = [f"#{i + 1}"]
        speed = shot.get('speed_mph', {})
        if speed.get('available'):
            label_bits.append(f"{speed['value']} mph")
        label = " · ".join(label_bits)
        mid_x, mid_y = svg_pts[len(svg_pts) // 2]
        parts.append(
            f'<text x="{mid_x + 8:.1f}" y="{mid_y - 6:.1f}" font-family="Barlow Condensed, sans-serif" '
            f'font-size="12" font-weight="700" fill="{color}" paint-order="stroke" '
            f'stroke="#ffffff" stroke-width="3">{label}</text>'
        )

    total_w = width_px + MARGIN * 2
    total_h = length_px + MARGIN * 2
    svg = (
        f'<svg viewBox="0 0 {total_w} {total_h}" width="100%" style="max-width:420px;display:block;margin:0 auto" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Shot trajectory map">'
        + "".join(parts) +
        '</svg>'
    )
    return svg
