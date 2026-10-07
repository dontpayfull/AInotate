"""Obstacle-aware arrows and content-aware placement: curve geometry, routing around page content
and other marks, arrow_style, the content map, where labels and loupes land, and the rendered result
on synthetic dense pages."""
import math

import pytest
from PIL import Image, ImageDraw, ImageFilter

from ainotate.geometry import (clip_path, quad_bezier, resample, segment_hits_rect,
                              skitch_arrow_points, skitch_curve_points)
from ainotate.marks import overlap_warnings, path_busy, path_hits, route_arrow
from ainotate.render import render
from ainotate.spec import SpecError, validate

U = 16
LABEL = [40, 400, 160, 430]
TARGET = [440, 60, 520, 90]


def page(text_band=True, size=(600, 500)):
    """White page; optionally a block of text-like lines across the straight route from LABEL
    to TARGET, with free margins left and right of it."""
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    if text_band:
        for y in range(150, 340, 14):
            for x in range(180, 420, 9):
                d.rectangle([x, y, x + 5, y + 7], fill="black")
    return img


def edges_of(img):
    return img.convert("L").filter(ImageFilter.FIND_EDGES)


# ---------- geometry ----------

def test_quad_bezier_and_resample_ends():
    pts = quad_bezier((0, 0), (50, 100), (100, 0), n=20)
    assert pts[0] == (0, 0) and pts[-1] == (100, 0) and len(pts) == 21
    even = resample(pts, 10)
    steps = [math.dist(a, b) for a, b in zip(even, even[1:])]
    assert max(steps) - min(steps) < 0.5
    assert even[0] == pts[0] and math.dist(even[-1], pts[-1]) < 1e-6


def test_clip_path_runs_edge_to_edge():
    start, end = [0, 0, 40, 20], [200, 100, 260, 140]
    pts = clip_path(quad_bezier((20, 10), (150, -40), (230, 120)), start, end)
    assert pts and abs(pts[0][0] - 40) < 1e-6 or abs(pts[0][1]) < 1e-6
    x, y = pts[-1]
    assert 200 - 1e-6 <= x <= 260 + 1e-6 and 100 - 1e-6 <= y <= 140 + 1e-6
    assert min(abs(x - 200), abs(x - 260), abs(y - 100), abs(y - 140)) < 1e-6
    # a path that comes back into the label box is rejected
    assert clip_path([(20, 10), (80, 10), (20, 15), (230, 120)], start, end) is None


def test_segment_hits_rect():
    r = [10, 10, 20, 20]
    assert segment_hits_rect((0, 0), (30, 30), r)
    assert segment_hits_rect((15, 0), (15, 12), r)
    assert not segment_hits_rect((0, 0), (30, 5), r)
    assert not segment_hits_rect((0, 25), (30, 25), r)


def test_curve_outline_matches_straight_head():
    """On a straight path the curved outline has the same head and tip as the straight one."""
    p1, p2 = (10, 10), (210, 110)
    a, b = skitch_arrow_points(p1, p2, U), skitch_curve_points([p1, p2], U)
    assert len(b) == 2 * 25 + 3
    for k in (17, 18, 19):                    # head corner, tip, head corner
        assert math.dist(a[k], b[25 + k - 17]) < 1e-6


def test_curve_outline_tapers_and_follows_curve():
    path = quad_bezier((0, 200), (150, 0), (300, 200))
    pts = skitch_curve_points(path, U)
    tail = math.dist(pts[0], pts[-1])
    neck = math.dist(pts[24], pts[-25])
    assert tail < neck < 2 * U
    assert pts[26] == path[-1]
    assert min(y for _, y in pts) < 120         # the body bows up with the curve


# ---------- routing ----------

def test_clean_diagonal_gets_a_gentle_bend():
    """Nothing in the way: a long diagonal arrow bows slightly (hand-drawn), never detours."""
    e = edges_of(page(text_band=False))
    straight = route_arrow(e, LABEL, TARGET, U, style="straight")
    path = route_arrow(e, LABEL, TARGET, U)
    assert len(path) > 2
    (x1, y1), (x2, y2) = straight
    L = math.dist(straight[0], straight[1])
    bow = max(abs((y2 - y1) * x - (x2 - x1) * y + x2 * y1 - y2 * x1) / L for x, y in path)
    assert 0.03 * L < bow < 0.15 * L


def test_clean_short_or_level_arrow_stays_straight():
    e = edges_of(page(text_band=False))
    assert len(route_arrow(e, [40, 60, 160, 90], TARGET, U)) == 2          # level with the target
    assert len(route_arrow(e, [400, 130, 470, 150], TARGET, U)) == 2       # short


def test_dirty_straight_curves_around_text():
    e = edges_of(page())
    straight = route_arrow(e, LABEL, TARGET, U, style="straight")
    curved = route_arrow(e, LABEL, TARGET, U)
    assert len(straight) == 2 and len(curved) > 2
    assert path_busy(e, curved, U)[0] < 0.5 * path_busy(e, straight, U)[0]
    assert math.dist(curved[-1], straight[-1]) < 4 * U       # still lands on the target


def test_curve_never_crosses_other_marks():
    e = edges_of(page())
    free = route_arrow(e, LABEL, TARGET, U)
    # block the side the free curve chose: the arrow must bow the other way or stay straight
    xs = [x for x, _ in free]
    side = [min(xs) - 10, 100, 175, 360] if sum(xs) / len(xs) < 300 else [425, 100, max(xs) + 10, 360]
    path = route_arrow(e, LABEL, TARGET, U, avoid=[side])
    assert len(path) == 2 or not path_hits(path, [side], 0)


def test_curve_does_not_cross_drawn_arrows():
    e = edges_of(page())
    free = route_arrow(e, LABEL, TARGET, U)
    mx, my = free[len(free) // 2]
    drawn = [(mx - 40, my - 40), (mx + 40, my + 40)]      # an earlier arrow across that curve
    assert path_hits(free, [], 1, lines=[drawn])
    path = route_arrow(e, LABEL, TARGET, U, lines=[drawn])
    assert len(path) == 2 or not path_hits(path, [], 1, lines=[drawn])
    assert path != free


@pytest.mark.parametrize("style, curved", [("straight", False), ("line", False), ("curved", True)])
def test_styles(style, curved):
    e = edges_of(page(text_band=False))
    assert (len(route_arrow(e, LABEL, TARGET, U, style=style)) > 2) is curved


def test_overlap_warnings_on_curved_arrows():
    marks = [{"type": "arrow"}, {"type": "arrow"}]
    a = quad_bezier((0, 0), (100, 200), (200, 0))
    b = [(100, 60), (100, 140)]              # crosses the curve's middle, not its chord
    out = overlap_warnings(marks, [None, None], {}, [], [(0, a[0], a[-1], a), (1, b[0], b[1], b)])
    assert any("arrows cross" in w for w in out)
    out = overlap_warnings(marks, [None, None], {}, [], [(0, a[0], a[-1]), (1, b[0], b[1])])
    assert not any("arrows cross" in w for w in out)


# ---------- spec and render ----------

@pytest.mark.parametrize("style", ["skitch", "straight", "curved", "line"])
def test_arrow_style_validates(style):
    validate({"input": "x.png", "arrow_style": style, "marks": [{"type": "box", "rect": [1, 1, 5, 5]}]})


def test_arrow_style_rejects_unknown():
    with pytest.raises(SpecError, match="skitch, straight, curved or line"):
        validate({"input": "x.png", "arrow_style": "bezier", "marks": [{"type": "box", "rect": [1, 1, 5, 5]}]})


def _ink_in_band(img):
    """Orange arrow pixels drawn over the text block."""
    band = img.crop((180, 150, 420, 340)).convert("RGB")
    px = band.load()
    return sum(1 for x in range(band.width) for y in range(band.height)
               if px[x, y][0] > 200 and 90 < px[x, y][1] < 190 and px[x, y][2] < 80)


def test_render_routes_around_dense_text(tmp_path):
    p = tmp_path / "dense.png"
    page().save(p)
    spec = {"input": str(p), "marks": [{"type": "box", "rect": TARGET, "label": "Here",
                                        "label_at": LABEL[:2], "color": "#FF9500"}]}
    straight = render({**spec, "arrow_style": "straight"}).image
    auto = render(spec).image
    assert _ink_in_band(straight) > 0
    assert _ink_in_band(auto) < 0.3 * _ink_in_band(straight)
    assert render({**spec, "arrow_style": "curved"}).image.size == auto.size


def test_render_clean_page_unchanged(tmp_path):
    """No content in the way and the label level with the target: skitch and straight draw the
    very same image."""
    p = tmp_path / "clean.png"
    page(text_band=False).save(p)
    spec = {"input": str(p), "marks": [{"type": "box", "rect": TARGET, "label": "Here", "label_at": [40, 60]}]}
    a, b = render(spec).image, render({**spec, "arrow_style": "straight"}).image
    assert a.tobytes() == b.tobytes()


# ---------- content map and label placement ----------

def _spy_layout(monkeypatch):
    import ainotate.render as rmod
    rec = {"labels": [], "badges": [], "outlines": [], "arrows": []}
    for name, key, pick in (("draw_pill", "labels", lambda a, k: list(a[1])),
                            ("draw_badge", "badges", lambda a, k: list(a[1])),
                            ("draw_outline", "outlines", lambda a, k: list(a[1])),
                            ("draw_arrow_skitch", "arrows", lambda a, k: (a[6] if len(a) > 6 else k.get("path"))
                             or [a[2], a[3]])):
        orig = getattr(rmod, name)

        def f(*a, _o=orig, _k=key, _p=pick, **k):
            rec[_k].append(_p(a, k))
            return _o(*a, **k)
        monkeypatch.setattr(rmod, name, f)
    return rec


def text_page(size=(900, 500), free=(), line_h=14, gap_y=10):
    """A page full of word-like text lines, except the `free` rects (margins, gutters)."""
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    for y in range(20, size[1] - 20, line_h + gap_y):
        for x in range(20, size[0] - 20, 9):
            if (x // 9) % 7 == 6:          # word gaps
                continue
            if any(f[0] - 6 <= x <= f[2] and f[1] - line_h <= y <= f[3] for f in free):
                continue
            d.rectangle([x, y, x + 5, y + line_h - 4], fill="black")
    return img


def test_content_map_covers_text_lines_not_margins():
    from ainotate.placement import ContentMap
    e = edges_of(page())
    cm = ContentMap(e, U)
    assert cm.cover([200, 170, 400, 320]) > 0.95          # the text block
    assert cm.cover([450, 360, 590, 490]) == 0            # empty page
    assert cm.cover([0, 0, 40, 40]) == 0                   # the image border is not content
    img = Image.new("RGB", (400, 100), "white")            # two words 1 u apart on one line
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, 140, 52], fill="black")
    d.rectangle([140 + U, 40, 240 + U, 52], fill="black")
    cm = ContentMap(edges_of(img), U)
    assert cm.cover([145, 42, 140 + U - 5, 50]) == 1      # the gap inside the line counts as text


def test_label_goes_to_a_clean_margin_not_onto_text(tmp_path, monkeypatch):
    """Dense page, clean left gutter far from the target: the label lands in the gutter, never on
    the text around the target (the old ring search, 12 u at most, put it on text)."""
    from ainotate.placement import ContentMap
    p = tmp_path / "dense.png"
    img = text_page(free=[(0, 0, 150, 500)])
    img.save(p)
    rec = _spy_layout(monkeypatch)
    res = render({"input": str(p), "marks": [{"type": "box", "rect": [560, 236, 620, 250], "label": "This one"}]})
    lb = rec["labels"][0]
    cm = ContentMap(edges_of(img), 18)
    assert cm.cover(lb) == 0 and lb[2] <= 155        # 20 u away: beyond the old search rings
    assert rec["arrows"], "a far label keeps its arrow"
    assert not res.warnings


def test_labels_never_cover_marks_badges_or_arrows(ui, monkeypatch):
    rec = _spy_layout(monkeypatch)
    marks = [{"type": "step", "n": k + 1, "rect": [24, 104 + 50 * k, 110, 124 + 50 * k], "label": f"Item {k + 1}"}
             for k in range(4)] + [{"type": "box", "rect": [1300, 100, 1520, 150], "label": "Pay"},
                                   {"type": "arrow", "rect": [1300, 170, 1520, 220], "label": "Coupon"}]
    res = render({"input": ui, "marks": marks})
    labels = rec["labels"]
    assert len(labels) == 6
    for i, lb in enumerate(labels):
        for o in labels[:i] + labels[i + 1:] + rec["badges"] + rec["outlines"]:
            assert not (lb[0] < o[2] and lb[2] > o[0] and lb[1] < o[3] and lb[3] > o[1]), (lb, o)
        for path in rec["arrows"]:
            assert not any(lb[0] + 2 < x < lb[2] - 2 and lb[1] + 2 < y < lb[3] - 2 for x, y in path)
    assert not [w for w in res.warnings if "labels overlap" in w or "covers" in w or "cross" in w]


def test_consecutive_step_labels_share_a_side(tmp_path, monkeypatch):
    """A short list with room on both sides: the three labels stay on one side."""
    from ainotate.placement import side_of
    p = tmp_path / "list.png"
    img = Image.new("RGB", (900, 500), "white")
    d = ImageDraw.Draw(img)
    rects = [[400, 130 + 80 * k, 470, 146 + 80 * k] for k in range(3)]
    for r in rects:
        d.rectangle(r, fill="black")
    img.save(p)
    rec = _spy_layout(monkeypatch)
    render({"input": str(p), "marks": [{"type": "step", "n": k + 1, "rect": r, "label": f"Step {k + 1}"}
                                       for k, r in enumerate(rects)]})
    sides = [side_of(lb, g) for lb, g in zip(rec["labels"], rec["outlines"])]
    assert len(sides) == 3 and len(set(sides)) == 1, sides


def test_label_beside_a_flat_target_is_aligned(tmp_path, monkeypatch):
    """Only the target's own row is free left and right of it: the label sits beside it, centred
    on the line, not half a line up or down."""
    from ainotate.placement import side_of
    from ainotate.style import mark_unit
    p = tmp_path / "row.png"
    img = text_page(size=(900, 400), free=[(0, 180, 900, 216)])
    ImageDraw.Draw(img).rectangle([380, 192, 520, 204], fill="black")
    img.save(p)
    rec = _spy_layout(monkeypatch)
    target = [380, 192, 520, 204]
    render({"input": str(p), "marks": [{"type": "box", "rect": target, "label": "Row", "arrow": False}]})
    lb, g = rec["labels"][0], rec["outlines"][0]
    u = mark_unit(900, 400, 1)
    assert side_of(lb, g) in "lr"
    assert abs((lb[1] + lb[3]) / 2 - (target[1] + target[3]) / 2) < 0.3 * u


# ---------- magnify leader ----------

def test_loupe_leader_curves_around_text(tmp_path, monkeypatch):
    import ainotate.placement as pl
    p = tmp_path / "dense.png"
    page().save(p)
    spec = {"input": str(p), "marks": [{"type": "magnify", "rect": TARGET, "at": [80, 420]}]}
    auto = render(spec).image
    monkeypatch.setattr(pl, "route_arrow", lambda edges, a, b, u, *r, **k: route_arrow(edges, a, b, u, style="straight"))
    straight = render(spec).image
    assert _ink_in_band(straight) > 0
    assert _ink_in_band(auto) < 0.3 * _ink_in_band(straight)
