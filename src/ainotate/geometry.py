"""Rect helpers, edge points, overlap and the Skitch arrow outline. Rects are [x1, y1, x2, y2]."""
from __future__ import annotations

import math


def norm_rect(r, scale):
    """Accept [x1,y1,x2,y2] or {x,y,w,h} (DOM getBoundingClientRect shape)."""
    if isinstance(r, dict):
        x, y = r["x"], r["y"]
        w, h = r.get("w", r.get("width")), r.get("h", r.get("height"))
        r = [x, y, x + w, y + h]
    return [v * scale for v in r]


def grow(r, p):
    return [r[0] - p, r[1] - p, r[2] + p, r[3] + p]


def intersects(a, b):
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def overlap_ratio(r, frame):
    """Share of rect r's area that lies inside frame."""
    w = max(0, min(r[2], frame[2]) - max(r[0], frame[0]))
    h = max(0, min(r[3], frame[3]) - max(r[1], frame[1]))
    area = (r[2] - r[0]) * (r[3] - r[1])
    return (w * h) / area if area > 0 else 0


def inside(box, W, H, margin=0):
    return box[0] >= margin and box[1] >= margin and box[2] <= W - margin and box[3] <= H - margin


def gap(a, b):
    dx = max(a[0] - b[2], b[0] - a[2], 0)
    dy = max(a[1] - b[3], b[1] - a[3], 0)
    return math.hypot(dx, dy)


def contains(outer, inner):
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


def segments_cross(p1, p2, q1, q2):
    """True when segments p1-p2 and q1-q2 properly cross (touching ends do not count)."""
    def side(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    d1, d2 = side(q1, q2, p1), side(q1, q2, p2)
    d3, d4 = side(p1, p2, q1), side(p1, p2, q2)
    return d1 * d2 < 0 and d3 * d4 < 0


def edge_point(rect, tx, ty):
    """Point where the segment from rect center toward (tx,ty) leaves rect."""
    cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
    dx, dy = tx - cx, ty - cy
    tmax = 1.0
    for lo, hi, p, d in ((rect[0], rect[2], cx, dx), (rect[1], rect[3], cy, dy)):
        if abs(d) > 1e-9:
            tmax = min(tmax, max((lo - p) / d, (hi - p) / d))
    return cx + dx * tmax, cy + dy * tmax


def clamp_box(box, W, H):
    """Shift a pill fully inside the image (forced positions may sit near an edge).
    None if the pill is larger than the image."""
    w, h = box[2] - box[0], box[3] - box[1]
    if w > W - 4 or h > H - 4:
        return None
    x = min(max(box[0], 2), max(2, W - w - 2))
    y = min(max(box[1], 2), max(2, H - h - 2))
    return [x, y, x + w, y + h]


def skitch_arrow_points(p1, p2, u):
    """Arrow outline with Skitch's proportions (as measured by the MIT-licensed Arrowshot clone):
    flat-based head almost as wide as it is long, shaft tapering exponentially to a thin tail."""
    L = math.dist(p1, p2) or 1.0
    ux, uy = (p2[0] - p1[0]) / L, (p2[1] - p1[1]) / L
    nx, ny = -uy, ux
    head_l = min(0.52 * L, 2.0 * u)
    head_hw = 0.49 * head_l
    neck_hw, tail_hw = max(0.5, 0.44 * head_hw), max(0.3, 0.075 * head_hw)
    wx, wy = p2[0] - ux * head_l, p2[1] - uy * head_l          # centre of the head's base

    def edge(sign):
        pts = []
        for i in range(17):
            t = i / 16
            hw = tail_hw * (neck_hw / tail_hw) ** t                   # hollowed, not a straight wedge
            cx, cy = p1[0] + (wx - p1[0]) * t, p1[1] + (wy - p1[1]) * t
            pts.append((cx + nx * hw * sign, cy + ny * hw * sign))
        return pts
    return edge(1) + [(wx + nx * head_hw, wy + ny * head_hw), p2,
                      (wx - nx * head_hw, wy - ny * head_hw)] + edge(-1)[::-1]


# ---------- curved arrows ----------

def path_length(pts):
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))


def resample(pts, n, upto=None):
    """n + 1 points evenly spaced by arc length over the first `upto` of polyline pts."""
    L = path_length(pts) if upto is None else upto
    out, k, done = [], 0, 0.0          # done: arc length at the start of segment k
    for i in range(n + 1):
        s = L * i / n
        while k < len(pts) - 2 and done + math.dist(pts[k], pts[k + 1]) < s:
            done += math.dist(pts[k], pts[k + 1])
            k += 1
        a, b = pts[k], pts[min(k + 1, len(pts) - 1)]
        d = math.dist(a, b)
        f = min(1.0, max(0.0, (s - done) / d)) if d > 0 else 0.0
        out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
    return out


def segment_hits_rect(a, b, r):
    """True when segment a-b touches rect r (Liang-Barsky clipping)."""
    t0, t1 = 0.0, 1.0
    for p, q in ((a[0] - b[0], a[0] - r[0]), (b[0] - a[0], r[2] - a[0]),
                 (a[1] - b[1], a[1] - r[1]), (b[1] - a[1], r[3] - a[1])):
        if abs(p) < 1e-12:
            if q < 0:
                return False
        elif p < 0:
            t0 = max(t0, q / p)
        else:
            t1 = min(t1, q / p)
    return t0 <= t1


def quad_bezier(p0, c, p2, n=40):
    return [((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * c[0] + t * t * p2[0],
             (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * c[1] + t * t * p2[1])
            for t in (i / n for i in range(n + 1))]


def in_rect(p, r):
    return r[0] <= p[0] <= r[2] and r[1] <= p[1] <= r[3]


def _exit_t(a, b, r):
    """Parameter along a->b (a inside r) where the segment leaves r."""
    t = 1.0
    for lo, hi, p, d in ((r[0], r[2], a[0], b[0] - a[0]), (r[1], r[3], a[1], b[1] - a[1])):
        if d > 1e-9:
            t = min(t, (hi - p) / d)
        elif d < -1e-9:
            t = min(t, (lo - p) / d)
    return max(0.0, t)


def clip_path(pts, start, end):
    """The part of a centre-to-centre polyline between leaving box `start` and first touching box
    `end`; None when it never leaves `start`, comes back into it, or never reaches `end`."""
    i = next((k for k, p in enumerate(pts) if not in_rect(p, start)), None)
    if not i:
        return None
    a, b = pts[i - 1], pts[i]
    t = _exit_t(a, b, start)
    out = [(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)]
    for k in range(i, len(pts)):
        p = pts[k]
        if in_rect(p, start):
            return None
        if in_rect(p, end):
            q = out[-1] if k == i else pts[k - 1]
            t = 1 - _exit_t(p, q, end)
            out.append((q[0] + (p[0] - q[0]) * t, q[1] + (p[1] - q[1]) * t))
            return out
        out.append(p)
    return None


def skitch_curve_points(path, u):
    """skitch_arrow_points along a curve: the tapered body follows the polyline (offset by the same
    exponential half-width along its normals) and the flat-based head points along the final
    tangent, from the curve point one head length before the tip."""
    L = path_length(path)
    p2 = path[-1]
    head_l = min(0.52 * L, 2.0 * u)
    head_hw = 0.49 * head_l
    neck_hw, tail_hw = max(0.5, 0.44 * head_hw), max(0.3, 0.075 * head_hw)
    body = resample(path, 24, L - head_l)
    wx, wy = body[-1]
    hl = math.dist((wx, wy), p2) or 1
    hx, hy = -(p2[1] - wy) / hl, (p2[0] - wx) / hl            # normal of the head's axis
    left, right = [], []
    for i, (x, y) in enumerate(body):
        if i == len(body) - 1:
            nx, ny = hx, hy
        else:
            a, b = body[max(0, i - 1)], body[i + 1]
            d = math.dist(a, b) or 1
            nx, ny = -(b[1] - a[1]) / d, (b[0] - a[0]) / d
        hw = tail_hw * (neck_hw / tail_hw) ** (i / (len(body) - 1))
        left.append((x + nx * hw, y + ny * hw))
        right.append((x - nx * hw, y - ny * hw))
    return left + [(wx + hx * head_hw, wy + hy * head_hw), p2,
                   (wx - hx * head_hw, wy - hy * head_hw)] + right[::-1]
