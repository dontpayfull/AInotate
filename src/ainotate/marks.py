"""Drawing for the newer marks: magnify (loupe), click, keys, blur / pixelate. render.py does the
layout; everything here draws at 4x on local tiles (aa_draw) so edges stay smooth."""
from __future__ import annotations

import math
import weakref

from PIL import Image, ImageDraw, ImageFilter

from .geometry import (clip_path, contains, edge_point, grow, in_rect, intersects, path_length,
                       quad_bezier, resample, segment_hits_rect, segments_cross)
from .style import KEYCAP, LABEL_FILL, rgba, text_size


def aa_draw(layer, box, color, paint, S=4):
    """Draw a shape at S x on a local tile and downsample: Pillow's own rounded rectangles and
    ellipses have stair-stepped edges. The tile's transparent pixels carry the shape's color so
    the downsampling does not darken the edges."""
    x0, y0 = max(0, int(box[0]) - 3), max(0, int(box[1]) - 3)
    x1, y1 = min(layer.width, int(box[2]) + 4), min(layer.height, int(box[3]) + 4)
    if x1 <= x0 or y1 <= y0:
        return
    tile = Image.new("RGBA", ((x1 - x0) * S, (y1 - y0) * S), rgba(color, 0))
    paint(ImageDraw.Draw(tile), lambda b: [(b[0] - x0) * S, (b[1] - y0) * S, (b[2] - x0) * S, (b[3] - y0) * S], S)
    layer.alpha_composite(tile.resize((x1 - x0, y1 - y0), Image.LANCZOS), dest=(x0, y0))


def deep(color):
    """The darker label shade of a palette color (white text and glyphs stay readable on it)."""
    return LABEL_FILL.get(str(color).upper(), color)


def _aa_mask(size, paint, S=4):
    big = Image.new("L", (size[0] * S, size[1] * S), 0)
    paint(ImageDraw.Draw(big), S)
    return big.resize(size, Image.LANCZOS)


# ---------- blur / pixelate (decorative only: never for secrets) ----------

def deemphasize(img, r, kind, amount, u):
    """Blur or pixelate rect r of img in place, with softly rounded corners."""
    box = [max(0, int(r[0])), max(0, int(r[1])), min(img.width, int(math.ceil(r[2]))),
           min(img.height, int(math.ceil(r[3])))]
    w, h = box[2] - box[0], box[3] - box[1]
    if w < 2 or h < 2:
        return img
    if kind == "blur":
        m = int(2 * amount) + 2                 # sample around the rect: no dark seams at its edge
        outer = [max(0, box[0] - m), max(0, box[1] - m), min(img.width, box[2] + m), min(img.height, box[3] + m)]
        done = img.crop(outer).filter(ImageFilter.GaussianBlur(amount))
        done = done.crop((box[0] - outer[0], box[1] - outer[1], box[0] - outer[0] + w, box[1] - outer[1] + h))
    else:
        b = max(2, int(round(amount)))         # exact b x b cells from the rect's corner
        done = img.crop(box)
        cells = ImageDraw.Draw(done)
        for y in range(0, h, b):
            for x in range(0, w, b):
                cell = (x, y, min(x + b, w), min(y + b, h))
                cells.rectangle([cell[0], cell[1], cell[2] - 1, cell[3] - 1],
                                fill=done.crop(cell).resize((1, 1), Image.BOX).getpixel((0, 0)))
    rad = min(0.4 * u, w / 2, h / 2)
    mask = _aa_mask((w, h), lambda d, S: d.rounded_rectangle([0, 0, w * S - 1, h * S - 1], radius=rad * S, fill=255))
    img.paste(done, box[:2], mask)
    return img


# ---------- magnify ----------

def loupe_sizes(r, shape, zoom, u):
    """(sample box, loupe width, loupe height) for source rect r at a zoom factor."""
    pad = 0.3 * u
    if shape == "circle":   # the circle must hold the whole rect: its diameter is the diagonal
        side = math.hypot(r[2] - r[0], r[3] - r[1]) + 2 * pad
        cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
        sample = [cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2]
    else:
        sample = grow(r, pad)
    return sample, round((sample[2] - sample[0]) * zoom), round((sample[3] - sample[1]) * zoom)


def _on_circle(c, rad, p):
    """The point of the circle (center c, radius rad) toward p."""
    d = math.dist(c, p) or 1
    return c[0] + (p[0] - c[0]) * rad / d, c[1] + (p[1] - c[1]) * rad / d


def _shape(d, box, shape, radius, **kw):
    if shape == "circle":
        d.ellipse(box, **kw)
    else:
        d.rounded_rectangle(box, radius=radius, **kw)


def draw_loupe(layer, page, sample, box, shape, color, u, path=None, line=None):
    """Source outline, connector and the loupe itself (zoomed page pixels, white rim, color ring).
    `page` is the image before any mark is drawn (redactions and blur already applied). `path`: a
    curved connector from the loupe to the sample (routed around page content), else straight.
    `line`: width of the connector, source outline and color ring (default from u)."""
    lw, lh = int(box[2] - box[0]), int(box[3] - box[1])
    ring = line
    line = line or max(2, round(0.13 * u))
    rad_src = min(0.6 * u, (sample[3] - sample[1]) / 2)
    rad_lp = min(1.3 * u, min(lw, lh) / 4)
    # connector between the facing edges (drawn first: the loupe and outline cover its ends)
    sc = ((sample[0] + sample[2]) / 2, (sample[1] + sample[3]) / 2)
    lc = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    if path:
        pts = list(path)
        if shape == "circle":   # the route ends on the bounding boxes: extend it to the circles
            pts = [_on_circle(lc, lw / 2, pts[0])] + pts + [_on_circle(sc, (sample[2] - sample[0]) / 2, pts[-1])]
        ext = [min(x for x, _ in pts) - line, min(y for _, y in pts) - line,
               max(x for x, _ in pts) + line, max(y for _, y in pts) + line]
        aa_draw(layer, ext, color, lambda d, T, S: d.line(
            [tuple(T([x, y, x, y])[:2]) for x, y in pts], fill=color, width=line * S, joint="curve"))
    elif shape == "circle":
        dist = math.dist(sc, lc) or 1
        ux, uy = (lc[0] - sc[0]) / dist, (lc[1] - sc[1]) / dist
        rs, rl = (sample[2] - sample[0]) / 2, lw / 2
        p1, p2 = (sc[0] + ux * rs, sc[1] + uy * rs), (lc[0] - ux * rl, lc[1] - uy * rl)
    else:
        p1, p2 = edge_point(sample, *lc), edge_point(box, *sc)
    if not path and math.dist(p1, p2) > 2:
        ext = [min(p1[0], p2[0]) - line, min(p1[1], p2[1]) - line, max(p1[0], p2[0]) + line, max(p1[1], p2[1]) + line]
        aa_draw(layer, ext, color, lambda d, T, S: d.line(
            [tuple(T([*p1, *p1])[:2]), tuple(T([*p2, *p2])[:2])], fill=color, width=line * S))
    aa_draw(layer, grow(sample, line), color,
            lambda d, T, S: _shape(d, T(sample), shape, rad_src * S, outline=color, width=line * S))
    # zoomed content: resample the exact source box without translating edge regions
    sw, sh = max(1, int(round(sample[2] - sample[0]))), max(1, int(round(sample[3] - sample[1])))
    patch = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
    x0, y0 = int(round(sample[0])), int(round(sample[1]))
    cx0, cy0 = max(0, x0), max(0, y0)
    cx1, cy1 = min(page.width, x0 + sw), min(page.height, y0 + sh)
    if cx1 > cx0 and cy1 > cy0:
        patch.paste(page.convert("RGBA").crop((cx0, cy0, cx1, cy1)), (cx0 - x0, cy0 - y0))
    content = patch.resize((lw, lh), Image.LANCZOS)
    mask = _aa_mask((lw, lh), lambda d, S: _shape(d, [0, 0, lw * S - 1, lh * S - 1], shape, rad_lp * S, fill=255))
    content.putalpha(mask)
    layer.alpha_composite(content, dest=(int(box[0]), int(box[1])))
    rim, ring = max(3, round(0.3 * u)), ring or max(2, round(0.1 * u))
    inner = grow(box, -rim / 2)
    aa_draw(layer, box, "white", lambda d, T, S: _shape(d, T(inner), shape, (rad_lp - rim / 2) * S,
                                                        outline="white", width=rim * S))
    outer = grow(box, ring / 2)
    aa_draw(layer, grow(box, ring), color, lambda d, T, S: _shape(d, T(outer), shape, (rad_lp + ring / 2) * S,
                                                                  outline=color, width=ring * S))


# ---------- click ----------

# macOS arrow pointer, tip at (0, 0), height 1
CURSOR = [(0, 0), (0, 0.86), (0.21, 0.68), (0.35, 1.0), (0.49, 0.94), (0.36, 0.63), (0.62, 0.63)]


def click_geometry(x, y, u):
    """(ring radii, cursor height, bounding box) of a click indicator at (x, y)."""
    radii = (0.75 * u, 1.3 * u, 1.85 * u)
    ch = 2.2 * u
    R = radii[-1] + 0.2 * u
    return radii, ch, [x - R, y - R, x + max(R, 0.62 * ch + 0.3 * u), y + max(R, ch + 0.3 * u)]


def draw_click(layer, x, y, color, u):
    radii, ch, bbox = click_geometry(x, y, u)
    w = max(2, round(0.2 * u))
    core = [x - 0.5 * u, y - 0.5 * u, x + 0.5 * u, y + 0.5 * u]
    aa_draw(layer, core, color, lambda d, T, S: d.ellipse(T(core), fill=rgba(color, 110)))
    for rad, a in zip(radii, (235, 150, 75)):
        c = [x - rad, y - rad, x + rad, y + rad]
        aa_draw(layer, grow(c, w), color, lambda d, T, S, c=c, a=a: d.ellipse(T(c), outline=rgba(color, a), width=w * S))
    pts = [(x + px * ch, y + py * ch) for px, py in CURSOR]
    edge = max(2, round(0.13 * u))
    fill = deep(color)

    def cursor(d, T, S):
        loc = [tuple(T([px, py, px, py])[:2]) for px, py in pts]
        d.line(loc + [loc[0], loc[1]], fill="white", width=2 * edge * S, joint="curve")
        d.polygon(loc, fill=fill)
    aa_draw(layer, bbox, fill, cursor)


# ---------- keys ----------

def keys_layout(font, keys, u):
    """(widths, height, gap) of the keycaps for keys."""
    th = text_size(font, "Kg")[1]
    h = th + 1.0 * u
    widths = [max(h, text_size(font, k)[0] + 1.1 * u) for k in keys]
    return widths, h, 0.35 * u


def keys_size(font, keys, u):
    widths, h, g = keys_layout(font, keys, u)
    return sum(widths) + g * (len(widths) - 1), h


def draw_keys(layer, box, keys, font, u):
    """macOS-style keycaps in a row from the top-left of box: light face, darker lip, dark text."""
    widths, h, g = keys_layout(font, keys, u)
    x, y = box[0], box[1]
    lip, bw, rad = max(2, round(0.16 * u)), max(1, 0.07 * u), 0.38 * u
    dtext = ImageDraw.Draw(layer)
    for k, kw in zip(keys, widths):
        cap = [x, y, x + kw, y + h]
        face = [x + bw, y + bw, x + kw - bw, y + h - lip]
        x0, y0 = int(cap[0]) - 2, int(cap[1]) - 2
        S = 4
        tw, th = (int(cap[2]) + 3 - x0), (int(cap[3]) + 3 - y0)
        T = lambda b: [(b[0] - x0) * S, (b[1] - y0) * S, (b[2] - x0) * S, (b[3] - y0) * S]
        tile = Image.new("RGBA", (tw * S, th * S), rgba(KEYCAP["lip"], 0))
        d = ImageDraw.Draw(tile)
        d.rounded_rectangle(T(cap), radius=rad * S, fill=KEYCAP["lip"], outline=KEYCAP["border"], width=max(1, S))
        # face: vertical gradient clipped to a rounded rect
        fb = [int(v) for v in T(face)]
        grad = Image.linear_gradient("L").resize((1, 256)).resize((max(1, fb[2] - fb[0]), max(1, fb[3] - fb[1])))
        top, bot = Image.new("RGBA", grad.size, KEYCAP["face_top"]), Image.new("RGBA", grad.size, KEYCAP["face_bottom"])
        fmask = Image.new("L", grad.size, 0)
        ImageDraw.Draw(fmask).rounded_rectangle([0, 0, grad.width - 1, grad.height - 1], radius=(rad - bw) * S, fill=255)
        tile.paste(Image.composite(bot, top, grad), (fb[0], fb[1]), fmask)
        layer.alpha_composite(tile.resize((tw, th), Image.LANCZOS), dest=(x0, y0))
        dtext.text(((face[0] + face[2]) / 2, (face[1] + face[3]) / 2), k, fill=KEYCAP["text"], font=font, anchor="mm")
        x += kw + g


# ---------- overlap checks ----------

COVERABLE = {"box", "step", "arrow", "highlight", "spotlight", "magnify", "click"}


def overlap_warnings(marks, rects, own, labels, arrows):
    """Labels that overlap each other or cover another mark's target, and arrows that cross."""
    name = lambda i: f"mark #{i} ({marks[i]['type']})"
    out = []
    for a, (i, A) in enumerate(labels):
        for j, B in labels[a + 1:]:
            if intersects(A, B):
                out.append(f"{name(i)} and {name(j)}: labels overlap; move one with label_at")
    goals = [(j, b) for j, m in enumerate(marks) if m["type"] in COVERABLE
             for b in {id(x): x for x in (rects[j], own.get(j)) if x}.values()]
    for i, lb in labels:
        mine = own.get(i) or rects[i]
        hit = sorted({j for j, b in goals if j != i and intersects(lb, b) and not (mine and contains(b, mine))})
        out += [f"{name(i)}: its label covers the target of {name(j)}; move it with label_at" for j in hit]
    # arrows: (i, p1, p2) or (i, p1, p2, path) for a curved one
    lines = [(a[0], resample(a[3], 16) if len(a) > 3 and len(a[3]) > 2 else [a[1], a[2]]) for a in arrows]
    for a, (i, P) in enumerate(lines):
        for j, Q in lines[a + 1:]:
            if any(segments_cross(p1, p2, q1, q2) for p1, p2 in zip(P, P[1:]) for q1, q2 in zip(Q, Q[1:])):
                out.append(f"{name(i)} and {name(j)}: arrows cross; move one label with label_at")
    return out


# ---------- arrow routing ----------

ARROW_STYLES = ("skitch", "straight", "curved", "line")
DIRTY = 8          # mean edge strength along a straight arrow above which it counts as crossing text
# quadratic control points: (where along the chord, sideways offset as a share of its length)
BENDS = [(t, k) for k in (0.2, -0.2, 0.32, -0.32, 0.45, -0.45, 0.6, -0.6) for t in (0.5, 0.35, 0.65)]
_grid = {}


def _content_grid(edges, u):
    """Edge strength averaged over ~u-sized cells (a box blur over u/3 cells): one lookup per
    probe instead of a crop, so label placement can price dozens of curves per spot."""
    c = max(2, int(u // 3))
    hit = _grid.get("g")
    if not hit or hit[0]() is not edges or hit[1] != c:
        g = edges.reduce(c).filter(ImageFilter.BoxBlur(1))
        hit = _grid["g"] = (weakref.ref(edges), c, g)
    return hit[2], c


def _probe(grid, c, pts):
    W, H = grid.size
    return [grid.getpixel((min(W - 1, max(0, int(x / c))), min(H - 1, max(0, int(y / c))))) for x, y in pts]


def path_busy(edges, pts, u):
    """(mean page content under the body of an arrow path, its length in u). The last 0.35 u at
    the tip (the target's own content) and the first 0.3 u at the label do not count."""
    grid, c = _content_grid(edges, u)
    L = path_length(pts)
    n = max(4, int(L / (0.4 * u)))
    probe = [p for k, p in enumerate(resample(pts, n)) if 0.3 * u <= L * k / n <= L - 0.35 * u]
    if not probe:
        return 0.0, L / u
    vals = _probe(grid, c, probe)
    return sum(vals) / len(vals), L / u


def path_hits(pts, avoid, pad, lines=()):
    """True when the path runs over one of the `avoid` boxes (other marks, labels, badges) or
    crosses one of `lines` (arrows already drawn). A box around the start (the arrow leaves
    from inside it) is ignored."""
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    box = [min(xs), min(ys), max(xs), max(ys)]
    for o in avoid:
        g = grow(o, pad)
        if intersects(box, g) and not in_rect(pts[0], g) and \
                any(segment_hits_rect(p, q, g) for p, q in zip(pts, pts[1:])):
            return True
    coarse = resample(pts, 16)
    return any(segments_cross(p1, p2, q1, q2) for Q in lines for q1, q2 in zip(Q, Q[1:])
               for p1, p2 in zip(coarse, coarse[1:]))


def _curves(lb, end, size, u, bends):
    """Candidate curved paths from label box lb to box end, clipped to the boxes' edges."""
    lc = ((lb[0] + lb[2]) / 2, (lb[1] + lb[3]) / 2)
    tc = ((end[0] + end[2]) / 2, (end[1] + end[3]) / 2)
    D = math.dist(lc, tc) or 1
    nx, ny = -(tc[1] - lc[1]) / D, (tc[0] - lc[0]) / D
    W, H = size
    for t, k in bends:
        ctrl = (lc[0] + (tc[0] - lc[0]) * t + nx * k * D, lc[1] + (tc[1] - lc[1]) * t + ny * k * D)
        pts = clip_path(quad_bezier(lc, ctrl, tc), lb, end)
        if not pts or path_length(pts) < 2.8 * u:
            continue
        if not all(2 <= x <= W - 2 and 2 <= y <= H - 2 for x, y in pts):
            continue
        yield k, pts


def _score(busy, length, k):
    """Crossed content (mean x length), plus length and bend: a curve must earn its detour."""
    return busy * length + 1.2 * length + 8 * abs(k) * length


def _side_entry(pts, end, u):
    """True when a curve reaches a small flat target (a word in a row of links) from the side: its
    head would then lie on the neighbouring word, not under or over the target."""
    if len(pts) < 3 or (end[2] - end[0]) < 2 * (end[3] - end[1]) or end[3] - end[1] > 2 * u:
        return False
    (x0, y0), (x1, y1) = pts[max(0, len(pts) - 4)], pts[-1]
    return abs(y1 - y0) < 0.6 * abs(x1 - x0)


def route_arrow(edges, lb, target, u, avoid=(), style="skitch", tip_pad=0, lines=()):
    """Arrow path from label box lb to target: [p1, p2] for a straight arrow (the default when
    it runs over little page content and no other mark), else the gentle curve that crosses the
    least content (bend left/right, small/large) without running over another mark, label or badge.
    style: skitch = straight or curved as needed, curved = always a gentle curve, straight/line =
    never curved. lines: paths of arrows already drawn, which a curve must not cross."""
    end = grow(target, tip_pad)
    lc = ((lb[0] + lb[2]) / 2, (lb[1] + lb[3]) / 2)
    tc = ((end[0] + end[2]) / 2, (end[1] + end[3]) / 2)
    straight = [edge_point(lb, *tc), edge_point(end, *lc)]
    if style in ("straight", "line"):
        return straight
    pad = 0.3 * u
    sb, sl = path_busy(edges, straight, u)
    blocked = path_hits(straight, avoid, pad, lines)
    if style == "skitch" and sb <= DIRTY and not blocked:
        return _gentle(edges, lb, end, u, avoid, lines, straight, sb) or straight
    bends = BENDS if style == "skitch" else [b for b in BENDS if abs(b[1]) < 0.4]
    ranked = sorted(((_score(b, L, k), n, b, pts) for n, (k, pts) in enumerate(_curves(lb, end, edges.size, u, bends))
                     for b, L in (path_busy(edges, pts, u),) if not _side_entry(pts, end, u)), key=lambda c: c[:2])
    best_score, bb, best = next(((sc, b, pts) for sc, _, b, pts in ranked if not path_hits(pts, avoid, 0.5 * u, lines)),
                                (math.inf, 0, None))
    if best is None or style == "curved":
        return best or straight
    # worth it only when it crosses clearly less (running over another mark is a fault, but not
    # one worth a detour through text)
    if best_score > 0.7 * (_score(sb, sl, 0) + (40 if blocked else 0)) or (bb > DIRTY and bb > 0.6 * sb):
        return (not blocked and _gentle(edges, lb, end, u, avoid, lines, straight, sb)) or straight
    return best


def _gentle(edges, lb, end, u, avoid, lines, straight, sb):
    """A clean, long, diagonal arrow gets a slight hand-drawn bend (Skitch-style), on the side
    that crosses less content, as long as the bend crosses no more than the straight line."""
    (x1, y1), (x2, y2) = straight
    dx, dy = abs(x2 - x1), abs(y2 - y1)
    if math.hypot(dx, dy) < 4 * u or min(dx, dy) < 0.3 * max(dx, dy):
        return None
    best = None
    for k, pts in _curves(lb, end, edges.size, u, [(0.5, 0.16), (0.5, -0.16)]):
        if _side_entry(pts, end, u):
            continue
        b, _ = path_busy(edges, pts, u)
        if b <= sb + 2 and not path_hits(pts, avoid, 0.5 * u, lines) and (best is None or b < best[0]):
            best = (b, pts)
    return best and best[1]
