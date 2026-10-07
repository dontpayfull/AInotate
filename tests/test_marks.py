"""magnify, click, keys, blur / pixelate, spec warnings and overlap warnings."""
import pytest
from PIL import Image, ImageChops

from ainotate.geometry import segments_cross
from ainotate.marks import overlap_warnings
from ainotate.render import RenderError, render
from ainotate.spec import SpecError, spec_warnings, validate
from ainotate.style import has_glyphs, load_font

CHECKOUT = [1300, 100, 1520, 150]
COUPON = [1300, 170, 1520, 220]
EMAIL = [420, 230, 590, 244]
R = [10, 10, 100, 50]


def spec(inp, *marks, **top):
    s = {"input": inp, "marks": list(marks)}
    s.update(top)
    return s


def errors_of(*marks):
    with pytest.raises(SpecError) as e:
        validate(spec("x.png", *marks))
    return str(e.value)


# ---------- spec ----------

def test_new_marks_validate():
    marks = [{"type": "magnify", "rect": R},
             {"type": "magnify", "rect": R, "at": [300, 300], "zoom": 4, "shape": "rounded", "color": "info"},
             {"type": "click", "at": [5, 5]},
             {"type": "click", "rect": R, "label": "Click", "label_at": [200, 200]},
             {"type": "keys", "keys": ["⌘", "Shift", "K"], "at": [1, 1]},
             {"type": "keys", "keys": ["Ctrl", "C"], "rect": R},
             {"type": "blur", "rect": R, "radius": 4},
             {"type": "pixelate", "rect": R, "block": 6, "sensitive": False}]
    assert validate(spec("x.png", *marks)) == marks


@pytest.mark.parametrize("mark, msg", [
    ({"type": "magnify"}, "mark #0 (magnify): missing 'rect'"),
    ({"type": "magnify", "rect": R, "zoom": 1}, "'zoom' must be a number from 1.2 to 8"),
    ({"type": "magnify", "rect": R, "zoom": "2x"}, "'zoom' must be a number from 1.2 to 8"),
    ({"type": "magnify", "rect": R, "shape": "square"}, "'shape' must be circle or rounded"),
    ({"type": "magnify", "rect": R, "at": [1]}, "(magnify).at: must be [x, y]"),
    ({"type": "click"}, "(click): needs 'at' [x, y], a 'rect' or a 'target'"),
    ({"type": "click", "at": "here"}, "(click).at: must be [x, y]"),
    ({"type": "click", "rect": [1, 2, 3]}, "rect must be [x1,y1,x2,y2]"),
    ({"type": "keys", "at": [1, 1]}, "'keys' must be a list of 1-6 key names"),
    ({"type": "keys", "keys": [], "at": [1, 1]}, "'keys' must be a list of 1-6 key names"),
    ({"type": "keys", "keys": ["K", " "], "at": [1, 1]}, "'keys' must be a list of 1-6 key names"),
    ({"type": "keys", "keys": ["K"]}, "(keys): needs 'at' [x, y], a 'rect' or a 'target'"),
    ({"type": "keys", "keys": ["K"], "at": [1, 1], "label": "x"}, "labels are drawn only on box/step/arrow/click"),
    ({"type": "blur"}, "(blur): missing 'rect'"),
    ({"type": "blur", "rect": R, "radius": 0}, "'radius' must be a number > 0"),
    ({"type": "pixelate", "rect": R, "block": -3}, "'block' must be a number > 0"),
])
def test_new_mark_errors(mark, msg):
    assert msg in errors_of(mark)


@pytest.mark.parametrize("kind", ["blur", "pixelate"])
def test_decorative_marks_refuse_sensitive_data(kind):
    msg = errors_of({"type": kind, "rect": R, "sensitive": True})
    assert f"{kind} is decorative only" in msg
    assert "blur can be reversed" in msg and "use redact (solid)" in msg


def test_spec_warnings_marks_and_long_labels():
    marks = [{"type": "box", "rect": R, "label": "one two three four five"}] + [{"type": "box", "rect": R}] * 6
    w = spec_warnings(marks)
    assert any("7 marks; more than 6" in x for x in w)
    assert any("mark #0 (box): label 'one two three four five' has 5 words" in x for x in w)
    assert spec_warnings([{"type": "box", "rect": R, "label": "one two three four"}]) == []


def test_render_returns_spec_warnings(ui):
    res = render(spec(ui, {"type": "box", "rect": COUPON, "label": "this label is far too long"}))
    assert any("has 6 words" in w for w in res.warnings)


# ---------- render ----------

@pytest.mark.parametrize("mark", [
    {"type": "magnify", "rect": COUPON},
    {"type": "magnify", "rect": COUPON, "shape": "rounded", "zoom": 2},
    {"type": "magnify", "rect": COUPON, "at": [800, 600]},
    {"type": "click", "at": [1410, 125]},
    {"type": "click", "rect": CHECKOUT, "label": "Pay", "color": "info"},
    {"type": "keys", "keys": ["⌘", "Shift", "K"], "at": [700, 600]},
    {"type": "keys", "keys": ["Ctrl", "C"], "rect": COUPON},
    {"type": "blur", "rect": EMAIL},
    {"type": "pixelate", "rect": EMAIL},
])
def test_new_marks_render(ui, ui_dark, mark, capsys):
    for inp in (ui, ui_dark):
        res = render(spec(inp, mark))
        assert res.image.mode == "RGB" and res.image.size == (1600, 1000)
        assert res.image.tobytes() != Image.open(inp).convert("RGB").tobytes()
        assert not [w for w in res.warnings if "overlap" in w or "covers" in w]
    assert capsys.readouterr() == ("", "")


def test_auto_crop_includes_new_points(ui):
    img = render(spec(ui, {"type": "click", "at": [1410, 125]},
                      {"type": "keys", "keys": ["⌘", "K"], "at": [300, 800]}, crop="auto")).image
    assert img.height > 600       # reaches from the click at y=125 down to the keycaps at y=800


def test_loupe_never_shows_redacted_text(ui):
    """The loupe samples the page after redaction: the zoomed copy is solid too."""
    src = [EMAIL[0] - 20, EMAIL[1] - 4, EMAIL[2] + 20, EMAIL[3] + 4]
    img = render(spec(ui, {"type": "redact", "rect": EMAIL},
                      {"type": "magnify", "rect": src, "at": [800, 650], "shape": "rounded", "zoom": 3})).image
    cy = 650
    row = [img.getpixel((x, cy)) for x in range(800 - 200, 800 + 200)]
    assert row.count((0x1F, 0x1F, 0x1F)) > 300     # the zoomed redaction block, nothing else


def test_loupe_magnifies(ui):
    src = [1300, 100, 1420, 150]
    img = render(spec(ui, {"type": "magnify", "rect": src, "at": [800, 600], "zoom": 3, "shape": "rounded"})).image
    blue = (0, 113, 227)
    count = sum(1 for x in range(500, 1100) for y in range(400, 800) if img.getpixel((x, y)) == blue)
    assert count > 3 * 3 * 0.6 * (120 * 50)          # the blue Checkout button, about 9x the area


def test_loupe_auto_place_avoids_marks_and_its_source(ui):
    s = spec(ui, {"type": "box", "rect": CHECKOUT, "label": "Pay"}, {"type": "magnify", "rect": COUPON})
    plain = render(s).image
    assert render(s).image.tobytes() == plain.tobytes()          # deterministic
    dbg = render(s, debug=True)
    assert not [w for w in dbg.warnings if "covers" in w or "overlap" in w]


def test_loupe_zoom_is_lowered_to_fit(ui):
    res = render(spec(ui, {"type": "magnify", "rect": [300, 300, 700, 500], "zoom": 4}))
    assert any("zoom lowered from 4 to" in w for w in res.warnings)


def test_loupe_source_too_large(ui):
    with pytest.raises(RenderError, match="too large to magnify"):
        render(spec(ui, {"type": "magnify", "rect": [100, 100, 1500, 900]}))


def test_loupe_covering_its_source_warns(ui):
    res = render(spec(ui, {"type": "magnify", "rect": COUPON, "at": [1410, 195]}))
    assert any("covers its own source" in w for w in res.warnings)


@pytest.mark.parametrize("top, mark, msg", [
    ({}, {"type": "click", "at": [1700, 10]}, "mark #0 (click): 'at' [1700, 10] is outside the 1600x1000"),
    ({}, {"type": "magnify", "rect": COUPON, "at": [-5, 10]}, "(magnify): 'at' [-5, 10] is outside"),
    ({"crop": [0, 0, 800, 600]}, {"type": "keys", "keys": ["K"], "at": [900, 10]}, "is outside the crop"),
])
def test_at_outside_frame(ui, top, mark, msg):
    with pytest.raises(RenderError, match=None) as e:
        render(spec(ui, mark, **top))
    assert msg in str(e.value)


def test_blur_and_pixelate_stay_inside_their_rect(ui):
    base = render(spec(ui)).image
    for kind in ("blur", "pixelate"):
        img = render(spec(ui, {"type": kind, "rect": EMAIL})).image
        diff = ImageChops.difference(base, img).getbbox()
        assert diff and diff[0] >= EMAIL[0] and diff[1] >= EMAIL[1] and diff[2] <= EMAIL[2] and diff[3] <= EMAIL[3]


def test_pixelate_makes_blocks(ui):
    img = render(spec(ui, {"type": "pixelate", "rect": [400, 220, 640, 260], "block": 12})).image
    block = img.crop((412, 232, 424, 244))     # one whole 12px cell
    assert len(block.getcolors()) == 1


def test_keycap_glyphs_in_bundled_font():
    assert has_glyphs(load_font(40), "⌘⇧⌥⌃ Ctrl")


# ---------- overlap warnings ----------

def test_forced_labels_overlapping_warn(ui):
    res = render(spec(ui, {"type": "box", "rect": CHECKOUT, "label": "One", "label_at": [900, 500]},
                      {"type": "box", "rect": COUPON, "label": "Two", "label_at": [910, 505]}))
    assert any("mark #0 (box) and mark #1 (box): labels overlap" in w for w in res.warnings)


def test_label_covering_other_target_warns(ui):
    res = render(spec(ui, {"type": "box", "rect": CHECKOUT, "label": "Pay", "label_at": [1310, 180]},
                      {"type": "step", "n": 2, "rect": COUPON}))
    assert any("mark #0 (box): its label covers the target of mark #1 (step)" in w for w in res.warnings)


def test_container_target_does_not_warn(ui):
    res = render(spec(ui, {"type": "spotlight", "rect": [1200, 60, 1580, 300]},
                      {"type": "box", "rect": CHECKOUT, "label": "Pay"}))
    assert not [w for w in res.warnings if "covers" in w]


def test_crossing_arrows_warn(ui):
    res = render(spec(ui, {"type": "arrow", "rect": [300, 300, 360, 330], "label": "A", "label_at": [900, 700]},
                      {"type": "arrow", "rect": [900, 300, 960, 330], "label": "B", "label_at": [300, 700]}))
    assert any("arrows cross" in w for w in res.warnings)


def test_overlap_warnings_unit():
    marks = [{"type": "box"}, {"type": "box"}, {"type": "text"}]
    rects = [[0, 0, 10, 10], [100, 100, 120, 120], None]
    labels = [(0, [105, 105, 150, 130]), (2, [140, 120, 200, 140])]
    arrows = [(0, (0, 0), (10, 10)), (1, (0, 10), (10, 0))]
    w = overlap_warnings(marks, rects, {}, labels, arrows)
    assert len(w) == 3
    assert segments_cross((0, 0), (10, 10), (0, 10), (10, 0))
    assert not segments_cross((0, 0), (10, 10), (10, 10), (20, 0))     # touching ends only


def test_relaxed_label_still_avoids_other_targets(ui, monkeypatch):
    """Crowded frame: the fallback placement keeps the label off the other mark's target."""
    import ainotate.render as rmod
    calls, orig = [], rmod.place_label

    def spy(*a, **k):
        res = orig(*a, **k)
        calls.append(res is not None)
        return res
    monkeypatch.setattr(rmod, "place_label", spy)
    s = spec(ui, {"type": "box", "rect": CHECKOUT, "label": "Checkout here"},
             {"type": "step", "n": 2, "rect": COUPON},
             {"type": "highlight", "rect": [1240, 90, 1290, 230]},
             crop=[1200, 80, 1600, 260])
    res = render(s)
    assert calls == [False, True]            # strict search failed, the relaxed one found a spot
    assert not [w for w in res.warnings if "covers" in w]


# ---------- magnify defaults ----------

def _loupe_box(monkeypatch, s):
    import ainotate.render as rmod
    got, orig = [], rmod.draw_loupe

    def spy(layer, page, sample, box, *a, **k):
        got.append((sample, box))
        return orig(layer, page, sample, box, *a, **k)
    monkeypatch.setattr(rmod, "draw_loupe", spy)
    res = render(s)
    return got[0], res


def test_loupe_default_zoom_is_2_and_capped(ui, monkeypatch):
    word = [1360, 118, 1420, 132]                     # small source: the default zoom 2 applies
    (sample, box), res = _loupe_box(monkeypatch, spec(ui, {"type": "magnify", "rect": word}))
    assert box[2] - box[0] == round(2 * (sample[2] - sample[0]))
    assert not res.warnings
    # a bigger source: the radius stays under min(4 u, 18% of the frame) instead of zooming 2x
    (sample, box), res = _loupe_box(monkeypatch, spec(ui, {"type": "magnify", "rect": [1300, 100, 1400, 150]}))
    from ainotate.style import mark_unit
    u = mark_unit(1600, 1000, 1)
    assert (box[2] - box[0]) / 2 <= min(4 * u, 0.18 * 1000) + 1
    assert not [w for w in res.warnings if "zoom lowered" in w]      # the default cap does not warn
    # an explicit zoom is honored as before
    (sample, box), _ = _loupe_box(monkeypatch, spec(ui, {"type": "magnify", "rect": word, "zoom": 3}))
    assert box[2] - box[0] == round(3 * (sample[2] - sample[0]))


def test_magnify_at_outside_crop_says_source_units(ui):
    with pytest.raises(RenderError) as e:
        render(spec(ui, {"type": "magnify", "rect": COUPON, "at": [100, 900]}, crop=[1000, 0, 1600, 600]))
    assert "'at' (the loupe center, source units) [100, 900] is outside the crop [1000, 0, 1600, 600]" in str(e.value)
