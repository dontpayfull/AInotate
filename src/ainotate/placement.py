"""Layout: the auto / tight crop, and where labels, badges and loupes go. render.py draws.

Label placement works on a ContentMap built once per render: page edges (text, lines, icons),
the gaps inside text lines filled, grown by ~0.4 u. A label over content is a near-hard fault,
not just a score term. Candidates come from aligned spots around the target plus a coarse grid
over the whole frame, so a clean gutter or empty band further away beats a cramped spot on text;
the arrow (straight, or curved around content) is then priced for the best few.

Label placement idea adapted from github/awesome-copilot skills/image-annotations (MIT).
"""
from __future__ import annotations

import math
from itertools import accumulate

from PIL import Image, ImageChops, ImageFilter, ImageStat

from .geometry import (clamp_box, contains, edge_point, gap, grow, inside, intersects, path_length, resample,
                       segment_hits_rect)
from .marks import loupe_sizes, path_hits, route_arrow
from .style import load_font, mark_unit, pill_size


class RenderError(Exception):
    """A valid spec that cannot be drawn (no room for a label, rect outside the image or crop,
    frame too small)."""


# ---------- crop ----------

def _min_context(crop, W, H):
    """Grow a crop to at least half the width and 45% of the height of the capture."""
    for lo, hi, full_len, frac in ((0, 2, W, 0.5), (1, 3, H, 0.45)):
        need = full_len * frac - (crop[hi] - crop[lo])
        if need > 0:
            crop[lo] = max(0, crop[lo] - need / 2)
            crop[hi] = min(full_len, crop[hi] + need / 2)
            short = full_len * frac - (crop[hi] - crop[lo])
            if short > 0:   # hit an edge: grow on the other side
                crop[lo], crop[hi] = (max(0, crop[lo] - short), crop[hi]) if crop[hi] == full_len \
                    else (crop[lo], min(full_len, crop[hi] + short))
    return crop


def auto_crop(spec, img, rects, pills, scale, context=True):
    """Frame the marks plus crop_pad. "auto" (context=True) keeps a minimum of context around
    them; "tight" (context=False) does not: only the marks and crop_pad."""
    pad = float(spec.get("crop_pad", 260)) * max(1, img.width / 2000)
    boxes = [r for r in rects if r] + [[x, y, x + 1, y + 1] for (x, y), _ in pills]
    if not boxes:
        return None

    def frame(bs):
        crop = [max(0, min(b[0] for b in bs) - pad), max(0, min(b[1] for b in bs) - pad),
                min(img.width, max(b[2] for b in bs) + pad), min(img.height, max(b[3] for b in bs) + pad)]
        return _min_context(crop, img.width, img.height) if context else crop
    crop = frame(boxes)
    if pills:   # second pass: make room for the full text, not just its anchor
        cw, ch = crop[2] - crop[0], crop[3] - crop[1]
        u0 = mark_unit(cw, ch, scale)
        f0 = load_font(int(1.35 * u0))
        boxes += [[x, y, x + pill_size(f0, t, u0)[0], y + pill_size(f0, t, u0)[1]] for (x, y), t in pills]
        crop = frame(boxes)
    return crop


# ---------- page content ----------

CONTENT_EDGE = 24     # FIND_EDGES strength that counts as page content


class ContentMap:
    """Page content on a grid of ~u/4 cells, built once per render. Text, icons and images count
    fully: edge pixels, the gaps inside text lines closed (horizontal runs of edges up to 1.5 u
    apart), grown by ~0.4 u. Rules (straight edge runs of 4 u or more: table lines, borders, panel
    edges) count three quarters, and never make a spot forbidden: a label over a hairline is a
    smaller fault than one over text, and in a dense table it is often the least bad spot.
    cover(box) = weighted share of the box's cells holding content, text(box) = the share holding
    text / icons only; both O(1) from prefix sums."""

    WEAK = 0.75

    def __init__(self, edges, u):
        self.u, self.edges = u, edges
        W, H = self.size = edges.size
        c = self.c = max(1, min(max(3, int(round(u / 4))), max(1, min(W, H))))
        on = lambda v: 255 if v > 0 else 0
        if W > 2 and H > 2:   # FIND_EDGES marks the image border itself: not content
            inner, edges = edges.crop((1, 1, W - 1, H - 1)), Image.new("L", edges.size, 0)
            edges.paste(inner, (1, 1))
        self.edges = edges
        e = edges.point(lambda v: 255 if v > CONTENT_EDGE else 0)
        L = max(4, int(2 * u))
        runs = [e.filter(ImageFilter.BoxBlur(k)).point(lambda v: 255 if v >= 230 else 0).filter(ImageFilter.BoxBlur(k))
                .point(on) for k in ((L, 0), (0, L))]
        rules = ImageChops.multiply(e, ImageChops.lighter(*runs))
        cells = lambda img: img.reduce(c).point(lambda v: 255 if v >= 16 else 0)
        strong = cells(ImageChops.subtract(e, rules))
        g = max(1, math.ceil(0.75 * u / c))
        line = strong.filter(ImageFilter.BoxBlur((g, 0))).point(on)                      # dilate along x
        line = ImageChops.invert(ImageChops.invert(line).filter(ImageFilter.BoxBlur((g, 0))).point(on))
        r = max(1, round(0.4 * u / c))
        self.mask = ImageChops.lighter(line, strong).filter(ImageFilter.MaxFilter(2 * r + 1))
        weak = cells(rules).filter(ImageFilter.MaxFilter(3))
        self.w, self.h = self.mask.size
        self.strong = [1 if v > 127 else 0 for v in self.mask.tobytes()]
        self.weak = [0 if s else (1 if k > 127 else 0) for s, k in zip(self.strong, weak.tobytes())]
        self.bits = [s + self.WEAK * k for s, k in zip(self.strong, self.weak)]
        self.Ps, self.Pw = self._prefix(self.strong), self._prefix(self.weak)

    def _prefix(self, bits):
        P, prev = [[0] * (self.w + 1)], [0] * (self.w + 1)
        for y in range(self.h):
            row = [0] + list(accumulate(bits[y * self.w:(y + 1) * self.w]))
            prev = [a + b for a, b in zip(prev, row)]
            P.append(prev)
        return P

    def _sums(self, box):
        if box[2] <= 0 or box[3] <= 0 or box[0] >= self.size[0] or box[1] >= self.size[1]:
            return 0.0, 0.0
        c = self.c
        x0, y0 = max(0, int(box[0] // c)), max(0, int(box[1] // c))
        x1, y1 = min(self.w, max(x0 + 1, math.ceil(box[2] / c))), min(self.h, max(y0 + 1, math.ceil(box[3] / c)))
        if x1 <= x0 or y1 <= y0:
            return 0.0, 0.0
        n = (x1 - x0) * (y1 - y0)
        return tuple((P[y1][x1] - P[y0][x1] - P[y1][x0] + P[y0][x0]) / n for P in (self.Ps, self.Pw))

    def cover(self, box):
        s, k = self._sums(box)
        return s + self.WEAK * k

    def text(self, box):
        return self._sums(box)[0]

    def along(self, pts):
        """Weighted share of the points that sit on content."""
        if not pts:
            return 0.0
        c, w, h = self.c, self.w, self.h
        return sum(self.bits[min(h - 1, max(0, int(y // c))) * w + min(w - 1, max(0, int(x // c)))]
                   for x, y in pts) / len(pts)


def busy(edges, box):
    """How much page content (text, lines, icons) a box would cover: mean edge strength."""
    return ImageStat.Stat(edges.crop([int(v) for v in box])).mean[0]


# ---------- labels ----------

def side_of(box, target):
    """Which side of target box sits on: l, r, t or b (the largest gap wins)."""
    gaps = {"l": target[0] - box[2], "r": box[0] - target[2], "t": target[1] - box[3], "b": box[1] - target[3]}
    return max(gaps, key=gaps.get)


def _hits_lines(box, lines, pad):
    g = grow(box, pad)
    for P in lines:
        xs, ys = [p[0] for p in P], [p[1] for p in P]
        if intersects(g, [min(xs), min(ys), max(xs), max(ys)]) and \
                any(segment_hits_rect(p, q, g) for p, q in zip(P, P[1:])):
            return True
    return False


def _beside(box, target, u):
    """A label without an arrow reads as belonging to its target only right next to it and level
    with it: within 1.5 u, its centre inside the target's span on the facing side."""
    if gap(box, target) > 1.2 * u:
        return False
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return target[1] <= cy <= target[3] if side_of(box, target) in "lr" else target[0] <= cx <= target[2]


def _runs_over(path, boxes, start, pad):
    """True when the path touches one of boxes (grown by pad). Unlike marks.path_hits, a box next
    to the label it starts from still counts: an arrow squeezed past a neighbour runs over it."""
    xs, ys = [p[0] for p in path], [p[1] for p in path]
    span = [min(xs), min(ys), max(xs), max(ys)]
    for o in boxes:
        g = grow(o, pad)
        if contains(o, start) or not intersects(span, g):
            continue
        if any(segment_hits_rect(p, q, g) for p, q in zip(path, path[1:])):
            return True
    return False


def _arrow_body(p1, p2, u):
    L = math.dist(p1, p2)
    if L < 1.5 * u:
        return []
    n = max(3, int(L / (0.5 * u)))
    return [(p1[0] + (p2[0] - p1[0]) * k / n, p1[1] + (p2[1] - p1[1]) * k / n) for k in range(1, n)]


def _candidates(W, H, target, size, near, u, arrow=True):
    pw, ph = size
    tx0, ty0, tx1, ty1 = target
    tcx, tcy = (tx0 + tx1) / 2, (ty0 + ty1) / 2
    out = []
    distances = {0.3 * u, 0.8 * u, 1.4 * u} if not arrow else {1.1 * u, near * u, (near + 1.5) * u, (near + 3.5) * u, (near + 6.5) * u}
    for d in sorted(distances):
        out += [(tx0 - d - pw, tcy - ph / 2), (tx1 + d, tcy - ph / 2),             # beside, centered
                (tcx - pw / 2, ty0 - d - ph), (tcx - pw / 2, ty1 + d),             # above / below, centered
                (tx0, ty0 - d - ph), (tx0, ty1 + d), (tx1 - pw, ty0 - d - ph), (tx1 - pw, ty1 + d)]
        for k in range(16):
            a = k * math.pi / 8
            ex = tcx + math.cos(a) * ((tx1 - tx0) / 2 + d + pw / 2)
            ey = tcy + math.sin(a) * ((ty1 - ty0) / 2 + d + ph / 2)
            out.append((ex - pw / 2, ey - ph / 2))
    if arrow:
        step = max(4.0, u / 2)
        nx, ny = int((W - pw - 8) / step) + 1, int((H - ph - 8) / step) + 1
        out += [(4 + i * step, 4 + j * step) for i in range(max(0, nx)) for j in range(max(0, ny))]
    return out


DYNAMIC_MAX = 5.5    # the most the order-dependent terms of a label spot can add (side hint + alignment)


def _static_spots(cmap, target, size, obstacles, u, near, curve, arrow, tip):
    """Every candidate spot that passes the order-independent checks, with its order-independent
    score, best first. Cached on the content map: the label planner places the same label in
    several orders, and this part (content under the label and its arrow) is most of the work."""
    key = (tuple(target), tuple(size), near, curve, arrow, tip, tuple(tuple(o) for o in obstacles))
    cache = cmap.__dict__.setdefault("_spots", {})
    if key not in cache:
        W, H = cmap.size
        cache[key] = sorted((r for r in (_static(cmap, target, size, obstacles, u, near, curve, arrow, tip, x, y)
                                         for x, y in _candidates(W, H, target, size, near, u, arrow=arrow)) if r),
                            key=lambda t: -t[0])
    return cache[key]


def _static(cmap, target, size, obstacles, u, near, curve, arrow, tip, x, y):
    W, H = cmap.size
    pw, ph = size
    box = [x, y, x + pw, y + ph]
    if not inside(box, W, H, 4) or intersects(box, grow(target, u if arrow else 0.5 * u)):
        return None
    if any(intersects(box, o) for o in obstacles):
        return None
    tcx, tcy = (target[0] + target[2]) / 2, (target[1] + target[3]) / 2
    cushion = grow(box, 0.2 * u)
    s = -120 * cmap.cover(cushion) - (300 if cmap.text(cushion) > 0.12 else 0) - 25 * cmap.cover(grow(box, 1.2 * u))
    d = gap(box, target) / u
    if arrow:
        s -= 1.2 * max(0.0, d - near) + 0.06 * max(0.0, d - 10) ** 2
        p1, p2 = edge_point(box, tcx, tcy), edge_point(grow(target, tip), x + pw / 2, y + ph / 2)
        s -= (8 if curve else 20) * cmap.along(_arrow_body(p1, p2, u))
        if math.dist(p1, p2) < 3.1 * u and not _beside(box, target, u):   # no arrow drawn: must touch
            s -= 25
    else:
        s -= 8 * max(0.0, d - near) + (0 if _beside(box, target, u) else 10)
    side = side_of(box, target)
    lcx, lcy = x + pw / 2, y + ph / 2
    flat = (target[2] - target[0]) > 3 * (target[3] - target[1])
    if side in "lr":
        s -= (0.8 if flat else 0.3) * abs(lcy - tcy) / u
    elif min(abs(x - target[0]), abs(lcx - tcx)) > 0.25 * u:
        s -= 0.3 * min(abs(x - target[0]), abs(lcx - tcx)) / u
    return s - sum(max(0.0, 1.5 - gap(box, o) / u) for o in obstacles), box


def place_label(cmap, target, size, obstacles, placed, u, near=2.5, curve=False, arrow=True, lines=(),
                prev=(), tip=0, style="skitch", mine=(), with_score=False):
    """Best spot for a label of `size` pointing at `target`, or None; (box, score) with_score.

    Hard: inside the frame, off the target, off every obstacle (marks, other targets), placed box
    (labels, badges) and drawn arrow in `lines`. Near-hard: page content under the label (cmap).
    Soft: an empty band around it (gutters, margins), distance, content under its arrow, the same
    side as the previous label (`prev` = [(label box, its target)]), alignment with a flat target.
    `mine`: the mark's own badge; the label avoids it, its arrow may pass under it (at a small cost).
    An arrow may cross a mark that contains the target (a row box around a cell): it has to."""
    W, H = cmap.size
    pw, ph = size
    if pw > W - 8 or ph > H - 8:
        return None
    blocked = list(obstacles) + list(placed)
    hint = side_of(*prev[-1]) if prev else None
    rows = [p[0][1] for p in prev]
    cols = [p[0][0] for p in prev]

    def dynamic(s, box):
        """The order-dependent part: labels, badges and arrows placed so far, the previous side."""
        if any(intersects(box, o) for o in placed) or (lines and _hits_lines(box, lines, 0.3 * u)):
            return None
        if hint and side_of(box, target) == hint:
            s += 4
        if any(abs(box[1] - r) < 0.25 * u for r in rows) or any(abs(box[0] - c) < 0.25 * u for c in cols):
            s += 1.5
        return s - sum(max(0.0, 1.5 - gap(box, o) / u) + 4 * max(0.0, 1 - gap(box, o) / u) for o in placed), box

    def cheap(x, y):
        r = _static(cmap, target, size, obstacles, u, near, curve, arrow, tip, x, y)
        return r and dynamic(*r)

    def distinct(scored):   # the best few distinct spots
        picks = []
        for s, box in sorted(scored, key=lambda t: -t[0]):
            if all(not intersects(grow(box, -0.25 * min(pw, ph)), p[1]) for p in picks):
                picks.append((s, box))
                if len(picks) == 8:
                    break
        return picks

    spots, scored, picks = _static_spots(cmap, target, size, obstacles, u, near, curve, arrow, tip), [], []
    for k in range(0, len(spots), 256):   # best static first; stop once no later spot can make the picks
        scored += [r for r in (dynamic(*t) for t in spots[k:k + 256]) if r]
        picks = distinct(scored)
        if len(picks) == 8 and k + 256 < len(spots) and spots[k + 256][0] + DYNAMIC_MAX < picks[-1][0]:
            break
    if not picks:
        return None
    refined = []
    for s, box in picks:
        best = (s, box)
        for dx in (-0.5 * u, -0.25 * u, 0, 0.25 * u, 0.5 * u):
            for dy in (-0.5 * u, -0.25 * u, 0, 0.25 * u, 0.5 * u):
                if dx or dy:
                    r = cheap(box[0] + dx, box[1] + dy)
                    if r and r[0] > best[0]:
                        best = r
        refined.append(best)
    if not arrow:
        s, box = max(refined, key=lambda t: t[0])
        return (box, s) if with_score else box
    best, best_s = None, -math.inf
    avoid = [o for o in blocked if o not in mine and not contains(o, target)]   # a row box around the target
    for s, box in refined:   # price the arrow that would really be drawn: content crossed x length
        path = route_arrow(cmap.edges, box, target, u, avoid, style if curve else "straight", tip, lines)
        L = path_length(path)
        if L > 2.8 * u:
            body = [p for p in resample(path, max(4, int(L / (0.4 * u)))) if math.dist(p, path[-1]) > 0.5 * u][1:]
            s -= 5 * cmap.along(body) * L / u + (2 if len(path) > 2 else 0) + 4 * max(0.0, L / u - 7)
            # an arrow over another mark, label or arrow reads as pointing there: near-hard
            s -= (250 if _runs_over(path, avoid, box, 0.3 * u) or path_hits(path, [], 0, lines) else 0) \
                + (10 if _runs_over(path, mine, box, 0) else 0)
        if s > best_s:
            best, best_s = box, s
    return (best, best_s) if with_score else best


def place_badge(g, rad, pos, W, H, avoid, edges=None, target=None):
    """Badge just outside g: the free spot that covers the least page content wins; an explicit
    `badge` position gets a head start, and without one top-left wins only a near tie. Inside corners are allowed for
    big targets (menus, cards) where the badge cannot hide what is being pointed at."""
    o = 0.55 * rad
    spots = {
        "tl": (g[0] - o, g[1] - o), "tr": (g[2] + o, g[1] - o),
        "bl": (g[0] - o, g[3] + o), "br": (g[2] + o, g[3] + o),
        "l": (g[0] - rad - 2, (g[1] + g[3]) / 2), "r": (g[2] + rad + 2, (g[1] + g[3]) / 2),
        "t": ((g[0] + g[2]) / 2, g[1] - rad - 2), "b": ((g[0] + g[2]) / 2, g[3] + rad + 2),
    }
    if target and (target[2] - target[0]) > 6 * rad and (target[3] - target[1]) > 3 * rad:
        spots.update({"itl": (g[0] + rad + 2, g[1] + rad + 2), "itr": (g[2] - rad - 2, g[1] + rad + 2)})
    best, best_score = None, -1e9
    for k, (bx, by) in spots.items():
        b = [bx - rad, by - rad, bx + rad, by + rad]
        if not inside(b, W, H, 2):
            continue
        if any(intersects(b, a) for a in avoid if not (k.startswith("i") and a == target)):
            continue
        score = -(busy(edges, b) if edges else 0) + (25 if k == pos else 0) - (10 if k.startswith("i") else 0)
        if pos is None and edges:   # also half of what is right around it: a badge wedged between
            n = grow(b, 0.6 * rad)  # two buttons reads as numbering the neighbour
            score += (3 if k == "tl" else 0) - 0.5 * busy(edges, [max(0, n[0]), max(0, n[1]), min(W, n[2]),
                                                                  min(H, n[3])])
        if score > best_score:
            best, best_score = b, score
    if best:
        return best, True
    # target fills the frame: clamp inside and accept some overlap
    bx, by = spots.get(pos or "tl", spots["tl"])
    bx, by = min(max(bx, rad + 2), W - rad - 2), min(max(by, rad + 2), H - rad - 2)
    return [bx - rad, by - rad, bx + rad, by + rad], False


# ---------- magnify ----------

LOUPE_ZOOM = 2.0      # default zoom; the loupe radius is also capped at min(4 u, 18% of the frame)


def _loupe_spot(cmap, sample, size, avoid, u, lines):
    """The calmest free spot for a loupe: off page content, preferring margins and empty bands,
    with a connector that can avoid content like the label arrows do."""
    W, H = cmap.size
    lw, lh = size
    cx, cy = (sample[0] + sample[2]) / 2, (sample[1] + sample[3]) / 2
    ref = max(lw, lh)
    spots = []
    for f in (0.0, 0.25, 0.6, 1.0, 1.6, 2.4, 3.4):
        dist = 1.5 * u + f * ref
        for k in range(24):
            a = k * math.pi / 12
            ex = cx + math.cos(a) * ((sample[2] - sample[0]) / 2 + dist + lw / 2)
            ey = cy + math.sin(a) * ((sample[3] - sample[1]) / 2 + dist + lh / 2)
            spots.append((ex - lw / 2, ey - lh / 2))
    step = max(u, min(lw, lh) / 3)
    spots += [(6 + i * step, 6 + j * step) for i in range(int((W - lw - 12) / step) + 1)
              for j in range(int((H - lh - 12) / step) + 1)]
    keep_off = grow(sample, u)
    scored = []
    for x, y in spots:
        box = [x, y, x + lw, y + lh]
        if not inside(box, W, H, 6) or intersects(box, keep_off) or any(intersects(box, o) for o in avoid):
            continue
        if lines and _hits_lines(box, lines, 0.3 * u):
            continue
        p1, p2 = edge_point(sample, x + lw / 2, y + lh / 2), edge_point(box, cx, cy)
        s = -60 * cmap.cover(box) - 20 * cmap.cover(grow(box, u)) - 0.4 * gap(box, sample) / u \
            - 10 * cmap.along(_arrow_body(p1, p2, u))
        scored.append((s, box))
    scored.sort(key=lambda t: -t[0])
    best, best_s = None, -math.inf
    for s, box in scored[:6]:
        path = route_arrow(cmap.edges, box, sample, u, avoid, "skitch", 0, lines)
        L = path_length(path)
        if L > 2 * u:
            s -= 25 * cmap.along(resample(path, max(4, int(L / (0.4 * u))))[1:-1])
            if path_hits(path, avoid, 0.3 * u, lines):
                s -= 60
        if s > best_s:
            best, best_s = box, s
    return best


def place_loupe(i, m, r, point, W, H, u, cmap, avoid, relaxed, placed, warnings, lines=()):
    """(loupe box, sample box, curved connector path or None for a straight one). `point` (spec
    `at`: the loupe center, in source px like the rect, already shifted by the crop) wins;
    else the calmest free spot. The default zoom (2) is capped so the loupe radius stays under
    min(4 u, 18% of the frame); any zoom shrinks (an explicit one with a warning) until it fits."""
    shape = m.get("shape", "circle")
    sample = loupe_sizes(r, shape, 1, u)[0]
    sw, sh = sample[2] - sample[0], sample[3] - sample[1]
    fit = min(0.6 * W / sw, 0.6 * H / sh)
    if fit < 1.2:
        raise RenderError(f"mark #{i} (magnify): the source rect is too large to magnify in this frame; "
                          "magnify a smaller region or widen the crop")
    if "zoom" in m:
        want = float(m["zoom"])
    else:
        want = min(LOUPE_ZOOM, max(1.2, 2 * min(4 * u, 0.18 * min(W, H)) / max(sw, sh)))
    zooms = [min(want, fit)]
    while not point and zooms[-1] > 1.5:
        zooms.append(max(1.5, zooms[-1] * 0.85))
    box = None
    for obst in ((avoid,) if point else (avoid, relaxed)):
        for zoom in zooms:
            _, lw, lh = loupe_sizes(r, shape, zoom, u)
            if point:
                box = clamp_box([point[0] - lw / 2, point[1] - lh / 2, point[0] + lw / 2, point[1] + lh / 2], W, H)
            else:
                box = _loupe_spot(cmap, sample, (lw, lh), obst + placed, u, lines)
            if box:
                break
        if box:
            break
    if box is None:
        raise RenderError(f"mark #{i} (magnify): no free spot for the loupe. Set 'at' (the loupe center, in "
                          "source units like 'rect'), lower 'zoom' or widen the crop.")
    if "zoom" in m and zoom < want - 0.05:
        warnings.append(f"mark #{i} (magnify): zoom lowered from {want:g} to {zoom:.1f} to fit the frame")
    if point and intersects(box, sample):
        warnings.append(f"mark #{i} (magnify): the loupe covers its own source region; move 'at'")
    x0, y0 = round(box[0]), round(box[1])
    box = [x0, y0, x0 + lw, y0 + lh]
    path = None if intersects(box, sample) else route_arrow(cmap.edges, box, sample, u, avoid + placed, "skitch", 0,
                                                             lines)
    return box, sample, (path if path and len(path) > 2 else None)
