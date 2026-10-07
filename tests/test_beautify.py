import math

import pytest
from PIL import Image, ImageDraw

from ainotate.beautify import (ASPECTS, CHROME, FRAME_DEFAULTS, PRESETS, apply_frame, auto_stops,
                              canvas_size, chrome_height, chrome_theme, dominant_hue, from_oklab,
                              to_oklab, validate_frame)

U = 20


def shot(size=(800, 500), color=(255, 255, 255)):
    return Image.new("RGB", size, color)


def window_box(img):
    """Bounding box of the opaque window on a transparent, shadowless frame."""
    return img.getchannel("A").point(lambda v: 255 if v > 250 else 0).getbbox()


# ---------- validation ----------

@pytest.mark.parametrize("frame", [
    None, True, False, "auto", "ocean", "transparent", "#1E293B", "white", ["#FFD9B8", "#E9A1C2"],
    {}, dict(FRAME_DEFAULTS),
    {"bg": "sunset", "pad": 0, "radius": 0, "shadow": 0.5, "chrome": "browser", "url": "example.com",
     "theme": "dark", "aspect": "4:5", "balance": True},
    {"chrome": "window", "aspect": "linkedin", "shadow": False},
])
def test_valid_frames(frame):
    assert validate_frame(frame) == []


@pytest.mark.parametrize("frame, needle", [
    ({"pad": -1}, "spec.frame: 'pad' must be a number >= 0"),
    ({"radius": "big"}, "spec.frame: 'radius' must be a number >= 0"),
    ({"pad": True}, "'pad' must be a number >= 0"),
    ({"shadow": 2}, "'shadow' must be true, false or a number from 0 to 1"),
    ({"chrome": "tabs"}, "'chrome' must be browser, window or null"),
    ({"url": "x.com"}, "'url' needs chrome 'browser'"),
    ({"chrome": "browser", "url": 5}, "'url' must be a string"),
    ({"theme": "blue"}, "'theme' must be auto, light or dark"),
    ({"aspect": "wide"}, "'aspect' must be"),
    ({"aspect": "0:9"}, "'aspect' must be"),
    ({"balance": "yes"}, "'balance' must be true or false"),
    ({"padding": 10}, "unknown key 'padding'"),
    ({"bg": "neon"}, "bad bg 'neon'"),
    ({"bg": ["#fff"]}, "list of 2 to 5 colors"),
    ({"bg": ["#fff", "nope"]}, "list of 2 to 5 colors"),
    ("not-a-color", "bad bg"),
    (42, "spec.frame: must be"),
])
def test_invalid_frames(frame, needle):
    errs = validate_frame(frame)
    assert errs and any(needle in e for e in errs), errs
    assert all(e.startswith("spec.frame: ") for e in errs)


def test_every_problem_is_listed():
    assert len(validate_frame({"pad": -1, "chrome": "x", "aspect": "y"})) == 3


def test_apply_rejects_invalid_and_passes_through_off():
    img = shot()
    with pytest.raises(ValueError, match="'pad' must be"):
        apply_frame(img, {"pad": -5}, U)
    assert apply_frame(img, None, U) is img
    assert apply_frame(img, False, U) is img


# ---------- sizes and aspect math ----------

def test_default_pad_is_six_percent_of_the_longer_side():
    out = apply_frame(shot((1000, 400)), True, U)
    assert out.mode == "RGB" and out.size == (1000 + 120, 400 + 120)


def test_explicit_pad_and_chrome_height():
    out = apply_frame(shot((600, 300)), {"pad": 40, "chrome": "window"}, U)
    assert out.size == (680, 380 + chrome_height("window", U))
    out = apply_frame(shot((600, 300)), {"pad": 40, "chrome": "browser", "url": "a.b"}, U)
    assert out.size == (680, 380 + chrome_height("browser", U))
    assert chrome_height("browser", 2 * U) > chrome_height("browser", U) > chrome_height("window", U) > 0
    assert chrome_height(None, U) == 0


@pytest.mark.parametrize("aspect", list(ASPECTS) + ["4:5", "21:9"])
@pytest.mark.parametrize("size", [(1600, 300), (400, 900), (800, 800)])
def test_aspect_pads_to_ratio_and_never_crops(aspect, size):
    a, b = (ASPECTS[aspect], 1) if aspect in ASPECTS else map(float, aspect.split(":"))
    ratio = a / b
    W, H = canvas_size(size, 50, aspect)
    assert W >= size[0] + 100 and H >= size[1] + 100
    assert abs(W / H - ratio) <= 1.5 / min(W, H)
    out = apply_frame(shot(size), {"pad": 50, "aspect": aspect, "shadow": False}, U)
    assert out.size == (W, H)


def test_named_ratios():
    assert ASPECTS["twitter"] == pytest.approx(16 / 9)
    assert ASPECTS["linkedin"] == pytest.approx(1.914, abs=0.001)


def test_window_is_centered():
    out = apply_frame(shot((400, 200)), {"bg": "transparent", "shadow": False, "pad": 30, "aspect": "1:1"}, U)
    x0, y0, x1, y1 = window_box(out)
    W, H = out.size
    assert abs(x0 - (W - x1)) <= 1 and abs(y0 - (H - y1)) <= 1


# ---------- transparency, corners, shadow ----------

def test_transparent_bg_gives_rgba_with_soft_shadow():
    out = apply_frame(shot((400, 300)), {"bg": "transparent", "pad": 60}, U)
    assert out.mode == "RGBA" and out.size == (520, 420)
    assert out.getpixel((0, 0))[3] == 0                       # canvas corner: clear
    assert out.getpixel((260, 210)) == (255, 255, 255, 255)   # screenshot: opaque
    below = out.getpixel((260, 360 + U // 2))                 # just under the window
    assert 0 < below[3] < 200 and below[:3] == (0, 0, 0)      # soft black shadow, no fringe color
    flat = apply_frame(shot((400, 300)), {"bg": "transparent", "pad": 60, "shadow": False}, U)
    assert flat.getpixel((260, 360 + U // 2))[3] == 0


def test_rounded_corners_are_anti_aliased():
    out = apply_frame(shot((400, 300), (0, 0, 0)), {"bg": "transparent", "pad": 20, "radius": 30,
                                                     "shadow": False}, U)
    assert out.getpixel((20, 20))[3] == 0                     # cut corner
    corner = out.crop((20, 20, 50, 50)).getchannel("A")
    partial = {v for v in corner.tobytes() if 0 < v < 255}
    assert len(partial) >= 5                                   # smooth ramp, not a staircase
    square = apply_frame(shot((400, 300), (0, 0, 0)), {"bg": "transparent", "pad": 20, "radius": 0,
                                                        "shadow": False}, U)
    assert square.getpixel((20, 20))[3] == 255


def test_screenshot_pixels_survive_inside_the_window():
    img = shot((300, 200))
    ImageDraw.Draw(img).rectangle([100, 80, 200, 120], fill=(12, 200, 99))
    out = apply_frame(img, {"bg": "ocean", "pad": 25}, U)
    assert out.getpixel((25 + 150, 25 + 100)) == (12, 200, 99)


def test_rgb_output_without_transparency():
    for bg in ["auto", "#FF00AA", ["#000000", "#FFFFFF"], *PRESETS]:
        out = apply_frame(shot((200, 120)), {"bg": bg}, U)
        assert out.mode == "RGB"


# ---------- chrome ----------

def test_chrome_theme_follows_top_edge():
    assert chrome_theme(shot(color=(250, 250, 250))) == "light"
    assert chrome_theme(shot(color=(28, 28, 32))) == "dark"
    split = shot(color=(255, 255, 255))
    ImageDraw.Draw(split).rectangle([0, 0, 800, 15], fill=(20, 20, 20))   # dark header, light page
    assert chrome_theme(split) == "dark"
    assert chrome_theme(shot(color=(28, 28, 32)), "light") == "light"


@pytest.mark.parametrize("color, theme", [((255, 255, 255), "light"), ((25, 25, 28), "dark")])
def test_chrome_bar_uses_theme_colors(color, theme):
    out = apply_frame(shot((900, 400), color), {"chrome": "browser", "url": "example.com", "pad": 0,
                                                "radius": 0, "shadow": False}, U)
    bar = Image.new("RGB", (1, 1), CHROME[theme]["bar"]).getpixel((0, 0))
    px = out.getpixel((900 - 30, chrome_height("browser", U) // 2))   # right end of the bar: plain bar
    assert max(abs(a - b) for a, b in zip(px, bar)) <= 2
    light = out.getpixel((round(1.55 * U), round((chrome_height("browser", U) - 1) / 2)))
    assert light[0] > 200 and light[1] < 140                           # red traffic light


def test_long_url_and_tiny_shot_do_not_break():
    out = apply_frame(shot((900, 300)), {"chrome": "browser", "url": "x" * 400}, U)
    assert out.size[0] > 900
    out = apply_frame(shot((90, 40)), {"chrome": "browser", "url": "example.com"}, U)
    assert out.size[1] > 40 + chrome_height("browser", U)


# ---------- auto background ----------

def test_auto_bg_is_light_behind_light_shots_and_deeper_behind_dark_ones():
    light = apply_frame(shot(color=(255, 255, 255)), {"pad": 60}, U)
    dark = apply_frame(shot(color=(24, 24, 28)), {"pad": 60}, U)
    L = lambda im: to_oklab(im.getpixel((im.width - 5, im.height - 5)))[0]
    assert L(light) > 0.6 > L(dark) > 0.25


def test_auto_bg_follows_the_screenshot_hue():
    img = shot((800, 500))
    ImageDraw.Draw(img).rectangle([0, 0, 800, 200], fill=(30, 90, 220))   # big blue brand header
    hue, _ = dominant_hue(img)
    assert 240 < hue < 280
    mid = from_oklab(auto_stops(img)[1])
    assert mid[2] > mid[0]                                    # bluish backdrop


def test_auto_bg_ignores_annotation_colors():
    img = shot((800, 500))
    d = ImageDraw.Draw(img)
    d.rectangle([100, 100, 500, 300], fill="#FF9500")         # an orange label fill (a mark)
    d.rectangle([520, 100, 700, 300], fill="#0071E3")
    assert dominant_hue(img)[0] is None


def test_gradient_is_smooth_and_not_flat():
    out = apply_frame(shot((600, 400)), {"bg": "ocean", "shadow": False}, U)
    a, b = out.getpixel((2, 2)), out.getpixel((out.width - 3, out.height - 3))
    assert sum(abs(x - y) for x, y in zip(a, b)) > 60


# ---------- balance, determinism ----------

def test_balance_moves_window_toward_the_empty_side():
    img = shot((600, 300))
    ImageDraw.Draw(img).rectangle([10, 100, 200, 200], fill=(0, 0, 0))     # content hugs the left
    opts = {"bg": "transparent", "shadow": False, "pad": 100}
    plain = window_box(apply_frame(img, opts, U))
    bal = window_box(apply_frame(img, dict(opts, balance=True), U))
    assert bal[0] > plain[0] and bal[0] - plain[0] <= 60       # shifted right, at most 0.6 x pad
    assert bal[2] - bal[0] == plain[2] - plain[0]


def test_deterministic(ui):
    img = Image.open(ui).convert("RGB")
    f = {"chrome": "browser", "url": "acme.test/orders", "aspect": "16:9", "balance": True}
    assert apply_frame(img, f, U).tobytes() == apply_frame(img, f, U).tobytes()
    assert apply_frame(img, True, U).tobytes() == apply_frame(img, "auto", U).tobytes()


def test_sizes_scale_with_unit():
    a = apply_frame(shot((800, 400)), {"chrome": "window", "pad": 0}, 12)
    b = apply_frame(shot((800, 400)), {"chrome": "window", "pad": 0}, 30)
    assert b.height - a.height == chrome_height("window", 30) - chrome_height("window", 12)
    assert math.isclose(chrome_height("window", 30) / chrome_height("window", 12), 2.5, rel_tol=0.1)
