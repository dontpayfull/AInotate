import pytest
from PIL import Image

from ainotate.render import RenderError, RenderResult, render
from ainotate.spec import SpecError

CHECKOUT = [1300, 100, 1520, 150]
COUPON = [1300, 170, 1520, 220]
EMAIL = {"x": 420, "y": 230, "w": 170, "h": 14}


def spec(ui, *marks, **top):
    s = {"input": ui, "marks": list(marks)}
    s.update(top)
    return s


@pytest.mark.parametrize("mark", [
    {"type": "step", "n": 1, "rect": CHECKOUT, "label": "Click Checkout"},
    {"type": "step", "n": "2", "rect": CHECKOUT, "badge": "none"},
    {"type": "box", "rect": COUPON, "label": "Ultima versiune"},
    {"type": "box", "rect": COUPON, "label": "No arrow", "arrow": False, "box": False},
    {"type": "arrow", "rect": CHECKOUT, "label": "Here"},
    {"type": "highlight", "rect": COUPON, "color": "good"},
    {"type": "spotlight", "rect": CHECKOUT},
    {"type": "redact", "rect": EMAIL},
    {"type": "text", "text": "Note:\ntwo lines", "at": [700, 600], "color": "info"},
])
def test_every_mark_type_renders(ui, mark, capsys):
    res = render(spec(ui, mark))
    assert isinstance(res, RenderResult)
    assert res.image.mode == "RGB" and res.image.size == (1600, 1000)
    assert res.image.tobytes() != Image.open(ui).convert("RGB").tobytes()
    assert capsys.readouterr() == ("", "")       # pure: nothing printed


def test_line_arrows_dark_ui_and_auto_crop(ui_dark):
    s = spec(ui_dark, {"type": "step", "n": 1, "rect": CHECKOUT, "label": "Pay"},
             {"type": "box", "rect": COUPON, "label": "Coupon", "color": "bad"},
             arrow_style="line", crop="auto")
    img = render(s).image
    assert img.width < 1600 and img.width >= 800        # cropped, but keeps half the width


def test_scale_and_dict_rects(ui):
    s = spec(ui, {"type": "box", "rect": {"x": 650, "y": 50, "w": 110, "h": 25}, "label": "Checkout"}, scale=2)
    assert render(s).image.size == (1600, 1000)


def test_explicit_crop_and_label_at(ui):
    s = spec(ui, {"type": "box", "rect": COUPON, "label": "Forced", "label_at": [1000, 400]},
             crop=[700, 50, 1600, 700])
    assert render(s).image.size == (900, 650)


def test_redaction_is_opaque(ui):
    r = EMAIL
    img = render(spec(ui, {"type": "redact", "rect": r})).image
    region = img.crop((r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"]))
    assert {c for _, c in region.getcolors()} == {(0x1F, 0x1F, 0x1F)}
    img = render(spec(ui, {"type": "redact", "rect": r, "fill": "bad"})).image
    region = img.crop((r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"]))
    assert {c for _, c in region.getcolors()} == {(0xFF, 0x3B, 0x30)}


def test_debug_draws_boxes(ui):
    m = {"type": "box", "rect": COUPON, "label": "x"}
    assert render(spec(ui, m), debug=True).image.tobytes() != render(spec(ui, m)).image.tobytes()


def test_badge_warning_when_target_fills_frame(ui):
    s = spec(ui, {"type": "step", "n": 1, "rect": [0, 0, 1600, 1000]})
    res = render(s)
    assert res.warnings and "badge overlaps the target" in res.warnings[0]


def test_invalid_spec_raises_spec_error(ui):
    with pytest.raises(SpecError):
        render({"input": ui})


def test_missing_image_is_spec_error(tmp_path):
    with pytest.raises(SpecError, match="image not found"):
        render({"input": str(tmp_path / "nope.png"), "marks": []})


def test_unreadable_image_is_spec_error(tmp_path):
    p = tmp_path / "x.png"
    p.write_text("not an image")
    with pytest.raises(SpecError, match="not a readable image"):
        render({"input": str(p), "marks": []})


@pytest.mark.parametrize("top, mark, msg", [
    ({}, {"type": "box", "rect": [1500, 900, 1800, 1200]}, "lies mostly outside the 1600x1000 image"),
    ({"scale": 2}, {"type": "box", "rect": CHECKOUT}, "Check 'scale'"),
    ({}, {"type": "text", "text": "hi", "at": [1700, 10]}, "is outside the 1600x1000 image"),
    ({}, {"type": "box", "rect": CHECKOUT, "label": "x", "label_at": [-5, 10]}, "is outside the 1600x1000 image"),
    ({"crop": [0, 0, 800, 600]}, {"type": "box", "rect": CHECKOUT}, "lies mostly outside the crop"),
    ({"crop": [0, 0, 800, 600]}, {"type": "text", "text": "hi", "at": [900, 10]}, "is outside the crop"),
    ({"crop": [1595, 0, 1700, 600]}, {"type": "text", "text": "hi", "at": [1596, 10]}, "is empty after clipping"),
    ({"crop": [1300, 100, 1330, 130]}, {"type": "step", "n": 1, "rect": [1302, 102, 1328, 128]},
     "too small for a step badge"),
    ({"crop": [1200, 80, 1600, 260]}, {"type": "box", "rect": CHECKOUT, "label": "x" * 80}, "no room for label"),
    ({"crop": [1200, 80, 1600, 260]}, {"type": "box", "rect": CHECKOUT, "label": "x" * 80, "label_at": [1210, 90]},
     "is larger than the frame"),
    ({"crop": [1200, 80, 1600, 260]}, {"type": "text", "text": "x" * 80, "at": [1210, 90]}, "is larger than the frame"),
])
def test_render_errors(ui, top, mark, msg):
    with pytest.raises(RenderError) as e:
        render(spec(ui, mark, **top))
    assert msg in str(e.value)


# ---------- crop ----------

def test_auto_crop_keeps_min_context_after_pill_pass(ui):
    """The second (pill-aware) pass of crop "auto" used to rebuild the frame from scratch and drop
    the minimum-context rule: a note next to a mark gave a 560 px wide crop of a 1600 px shot."""
    s = spec(ui, {"type": "box", "rect": CHECKOUT}, {"type": "text", "text": "note", "at": [1400, 120]},
             crop="auto")
    img = render(s).image
    assert img.width >= 800 and img.height >= 450


def test_crop_tight_frames_only_the_marks(ui):
    marks = [{"type": "box", "rect": CHECKOUT}, {"type": "text", "text": "note", "at": [1400, 120]}]
    auto = render(spec(ui, *marks, crop="auto")).image
    tight = render(spec(ui, *marks, crop="tight")).image
    assert tight.width < auto.width and tight.width < 800          # no half-width floor
    assert tight.width == 1600 - (CHECKOUT[0] - 260)                # marks + crop_pad, clipped right
    small = render(spec(ui, {"type": "redact", "rect": CHECKOUT}, crop="tight", crop_pad=20)).image
    assert small.size == (220 + 40, 50 + 40)


def test_tight_crop_validates():
    from ainotate.spec import validate
    validate({"input": "x.png", "crop": "tight", "marks": []})
    with pytest.raises(SpecError, match='"auto" / "tight"'):
        validate({"input": "x.png", "crop": "snug", "marks": []})


# ---------- redact pad ----------

def test_redact_honors_explicit_pad(ui):
    base = Image.open(ui).convert("RGB")
    x1, y1, x2, y2 = EMAIL["x"], EMAIL["y"], EMAIL["x"] + EMAIL["w"], EMAIL["y"] + EMAIL["h"]
    tight = render(spec(ui, {"type": "redact", "rect": EMAIL, "pad": 0})).image
    assert {c for _, c in tight.crop((x1, y1, x2, y2)).getcolors()} == {(0x1F, 0x1F, 0x1F)}
    ring = (x1 - 2, y1 - 2, x2 + 2, y1)               # just above the rect: untouched with pad 0
    assert tight.crop(ring).tobytes() == base.crop(ring).tobytes()
    default = render(spec(ui, {"type": "redact", "rect": EMAIL})).image   # default outset stays
    assert {c for _, c in default.crop(ring).getcolors()} == {(0x1F, 0x1F, 0x1F)}
    wide = render(spec(ui, {"type": "redact", "rect": EMAIL, "pad": 5})).image
    assert wide.getpixel((x1 - 4, y1 - 4)) == (0x1F, 0x1F, 0x1F)
    assert wide.getpixel((x1 - 7, y1 - 7)) != (0x1F, 0x1F, 0x1F)


# ---------- mark outside an explicit crop (live page that moved) ----------

@pytest.mark.parametrize("pad", [{"pad": 0}, {}])
def test_mark_pushed_out_of_explicit_crop_explains(tmp_path, pad):
    """Real run (wikipedia, phone preset, crop [0,0,390,420]): an app-install banner pushed the
    Language button from y=216 to y=502 between two captures; the error blamed nothing in
    particular and showed the crop in image px. `pad` was not the cause: same error without it."""
    p = tmp_path / "phone.png"
    Image.new("RGB", (1170, 2532), "white").save(p)
    s = {"input": str(p), "scale": 3, "crop": [0, 0, 390, 420],
         "marks": [{"type": "step", "n": 1, "rect": {"x": 12, "y": 495, "w": 40, "h": 30},
                    "label": "Schimbă limba", "label_at": [110, 200], **pad}]}
    with pytest.raises(RenderError) as e:
        render(s)
    msg = str(e.value)
    assert "rect [12, 495, 52, 525] lies mostly outside the crop [0, 0, 390, 420] (below it)" in msg
    assert "spec units" in msg and '"crop": "auto"' in msg
    s["marks"][0]["rect"]["y"] = 216                     # where it was without the banner
    assert render(s).image.size == (1170, 1260)
