"""Joint label planning (label_plan.py) and the default badge spot."""
from PIL import Image, ImageDraw, ImageFilter

from ainotate.label_plan import Job, plan_labels
from ainotate.placement import place_badge

GOOD, OK, FAR = [0, 0, 10, 10], [20, 0, 30, 10], [90, 90, 99, 99]


def fake_place(table):
    """place_label stand-in: each job (by target) takes its best spot not yet placed."""
    def place(cmap, target, size, obstacles, placed, u, **kw):
        for box, s in table[tuple(target)]:
            if box not in placed:
                return box, s
        return None
    return place


def job(i, target):
    return Job(i, target, (10, 10), [], [], 3.5, {"arrow": False})


def test_mark_order_kept_when_every_label_is_clean():
    a, b = [0, 50, 5, 55], [50, 50, 55, 55]
    table = {tuple(a): [(GOOD, -1)], tuple(b): [(OK, -2)]}
    boxes, scores = plan_labels([job(0, a), job(1, b)], fake_place(table), None, None, 10, [], [])
    assert boxes == {0: GOOD, 1: OK} and scores == {0: -1, 1: -2}


def test_greedy_trap_is_avoided():
    """Label 0 is fine almost anywhere, label 1 only at GOOD: mark order would give label 1 a far
    spot; the planner places label 1 first."""
    a, b = [0, 50, 5, 55], [50, 50, 55, 55]
    table = {tuple(a): [(GOOD, -1), (OK, -3)], tuple(b): [(GOOD, -2), (FAR, -150)]}
    boxes, _ = plan_labels([job(0, a), job(1, b)], fake_place(table), None, None, 10, [], [])
    assert boxes == {0: OK, 1: GOOD}


def test_label_that_fits_nowhere_is_left_out():
    a, b = [0, 50, 5, 55], [50, 50, 55, 55]
    table = {tuple(a): [(GOOD, -1)], tuple(b): [(GOOD, -1)]}
    boxes, _ = plan_labels([job(0, a), job(1, b)], fake_place(table), None, None, 10, [], [])
    assert len(boxes) == 1


def test_default_badge_avoids_text_at_top_left():
    img = Image.new("L", (400, 200), 255)
    d = ImageDraw.Draw(img)
    for x in range(40, 150, 8):                       # text right above-left of the target
        d.rectangle([x, 40, x + 4, 52], fill=0)
    edges = img.filter(ImageFilter.FIND_EDGES)
    g = [120, 70, 260, 110]
    b, clean = place_badge(g, 14, None, 400, 200, [], edges, g)
    assert clean and not (b[0] < 150 and b[1] < 60)  # not over the text
    b, _ = place_badge(g, 14, "tl", 400, 200, [], edges, g)
    assert b[0] < g[0] and b[1] < g[1]                # an explicit spot still wins


def test_label_over_text_is_reported(tmp_path):
    from ainotate.render import render
    img = Image.new("RGB", (420, 160), "white")
    d = ImageDraw.Draw(img)
    for y in range(4, 160, 12):                       # text everywhere: no free room
        for x in range(4, 420, 8):
            d.rectangle([x, y, x + 4, y + 6], fill="black")
    p = tmp_path / "dense.png"
    img.save(p)
    res = render({"input": str(p), "marks": [{"type": "box", "rect": [180, 60, 240, 90], "label": "Here"}]})
    assert any("covers page text" in w for w in res.warnings)
