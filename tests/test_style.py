"""Visual style rules: palette contrast, frame backdrop analysis, shadow tint, chrome proportions."""
import math

from PIL import Image, ImageColor, ImageDraw

from ainotate import beautify
from ainotate.beautify import apply_frame
from ainotate.beautify_color import bg_stops, shadow_color, to_oklab
from ainotate.style import COLORS, LABEL_FILL

U = 20


def contrast_with_white(hex_color):
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(v) for v in ImageColor.getrgb(hex_color))
    return 1.05 / (0.2126 * r + 0.7152 * g + 0.0722 * b + 0.05)


def lch(color):
    L, a, b = to_oklab(ImageColor.getrgb(color) if isinstance(color, str) else color)
    return L, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360


def test_every_palette_color_has_a_readable_label_fill():
    for name, vivid in COLORS.items():
        fill = LABEL_FILL[vivid]
        assert contrast_with_white(fill) >= 3.0, name   # WCAG large/bold text
        (Lv, _, hv), (Lf, _, hf) = lch(vivid), lch(fill)
        assert Lf < Lv, name                             # deeper than the outline color
        assert abs((hv - hf + 180) % 360 - 180) < 20, name   # same hue family


def test_source_image_drives_the_auto_backdrop():
    clean = Image.new("RGB", (600, 400), (245, 245, 247))
    marked = clean.copy()
    ImageDraw.Draw(marked).rectangle([40, 40, 560, 360], fill=(36, 160, 70))   # a huge green "mark"
    opts = {"pad": 60, "shadow": False}
    corner = (5, 5)
    want = apply_frame(clean, opts, U).getpixel(corner)
    assert apply_frame(marked, opts, U, source=clean).getpixel(corner) == want
    assert apply_frame(marked, opts, U).getpixel(corner) != want


def test_source_drives_the_chrome_theme():
    light, dark = Image.new("RGB", (800, 300), "white"), Image.new("RGB", (800, 300), (20, 20, 22))
    opts = {"chrome": "browser", "pad": 0, "shadow": False}
    bar = apply_frame(dark, opts, U, source=light).getpixel((400, 3))
    assert sum(bar[:3]) / 3 > 200


def test_shadow_is_a_deep_shade_of_the_backdrop():
    for preset in ("ocean", "sunset", "mint", "white", "graphite"):
        stops = bg_stops(preset, None)
        L, C, h = lch(shadow_color(stops))
        darkest = min(s[0] for s in stops)
        assert L <= min(0.25, darkest), preset    # 0.24 target, + sRGB clipping
    _, C, h = lch(shadow_color(bg_stops("ocean", None)))
    assert C > 0.02 and 200 < h < 300            # blue backdrop, blue shadow (not gray)


def test_transparent_backdrop_keeps_a_black_shadow():
    out = apply_frame(Image.new("RGB", (400, 300), "white"), {"bg": "transparent", "pad": 60}, U)
    assert out.getpixel((200, 360 + U // 2))[:3] == (0, 0, 0)


def test_traffic_lights_keep_macos_proportions():
    assert math.isclose(2 * beautify.LIGHT_R / beautify.LIGHT_STEP, 0.6, abs_tol=0.02)
    assert beautify.LIGHT_X0 > beautify.LIGHT_R + 0.8   # breathing room from the window edge
