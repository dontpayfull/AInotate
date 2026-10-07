"""Color and gradient math for beautify: OKLab conversions, auto backgrounds, gradients, grain.
Re-exported by beautify."""
from __future__ import annotations

import math
import random
from functools import lru_cache

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter

from .style import COLORS, LABEL_FILL

_MARK_COLORS = list(COLORS.values()) + list(LABEL_FILL.values())

# Diagonal gradients, top-left -> bottom-right. Soft and low-chroma so the screenshot stays the
# subject; each pair stays in one hue family (no muddy midpoints).
PRESETS = {
    "sunset": ["#FFD9B8", "#F9B5A7", "#E9A1C2"],
    "ocean": ["#B9E3F6", "#8DB8F2", "#7C8FE6"],
    "slate": ["#56647A", "#2C3546", "#1A202C"],
    "mint": ["#DDF7E8", "#A8E6CF", "#83CDC6"],
    "lavender": ["#EEE6FD", "#D2C8F6", "#B3BDF0"],
    "graphite": ["#48484C", "#2A2A2D", "#161618"],
    "white": ["#FBFBFD", "#F0F0F3", "#E4E4E9"],
}


# ---------- color (OKLab: perceptually even gradients and lightness) ----------

def _lin(c):
    c /= 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _gam(c):
    c = 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
    return c * 255


def to_oklab(rgb):
    r, g, b = (_lin(v) for v in rgb[:3])
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def _oklab_lin(L, a, b):
    l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
            -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
            -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)


def from_oklab(lab):
    return tuple(max(0, min(255, round(_gam(max(0.0, min(1.0, v)))))) for v in _oklab_lin(*lab))


def oklch(L, C, h_deg):
    """In-gamut OKLab for (L, C, h): chroma shrinks until the color fits sRGB."""
    h = math.radians(h_deg)
    fits = lambda c: all(-1e-4 <= v <= 1.0001 for v in _oklab_lin(L, c * math.cos(h), c * math.sin(h)))
    lo, hi = 0.0, C
    if not fits(C):
        for _ in range(16):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if fits(mid) else (lo, mid)
        C = lo
    return (L, C * math.cos(h), C * math.sin(h))


def luminance(img, box=None):
    """Mean perceptual lightness (OKLab L, 0..1) of an image region."""
    region = img.crop(box) if box else img
    small = region.convert("RGB").resize((max(1, min(64, region.width)), max(1, min(16, region.height))),
                                         Image.BOX)
    px = list(_pixels(small))
    return sum(to_oklab(p)[0] for p in px) / len(px)


# ---------- auto background ----------

def _pixels(im):
    """Pixel values; Pillow 12.3 deprecates getdata() in favor of get_flattened_data()."""
    return im.get_flattened_data() if hasattr(im, "get_flattened_data") else im.getdata()


def _thumb(img, side=72, resample=Image.BOX):
    w, h = img.size
    k = side / max(w, h)
    return img.convert("RGB").resize((max(4, round(w * k)), max(4, round(h * k))), resample)


def dominant_hue(img):
    """(hue in degrees or None, colorfulness 0..1). Annotation colors are ignored: they say
    nothing about the product in the screenshot."""
    marks = [to_oklab(ImageColor.getrgb(c)) for c in _MARK_COLORS]
    bins = [0.0] * 24
    pts = []
    # NEAREST: pixels stay either page colors or exact mark colors (no blends that dodge the filter)
    thumb = _thumb(img, 120, Image.NEAREST)
    for p in _pixels(thumb):
        L, a, b = to_oklab(p)
        C = math.hypot(a, b)
        if C < 0.045 or not 0.25 < L < 0.97:
            continue
        if any(math.dist((L, a, b), m) < 0.035 for m in marks):
            continue
        h = math.degrees(math.atan2(b, a)) % 360
        bins[int(h // 15) % 24] += C
        pts.append((h, C, a, b))
    n = thumb.width * thumb.height
    total = sum(bins)
    if not pts or total / n < 0.0004:
        return None, total / n
    peak = max(range(24), key=lambda i: bins[i - 1] + bins[i] + bins[(i + 1) % 24])
    center = peak * 15 + 7.5
    sa = sum(a for h, C, a, b in pts if abs((h - center + 180) % 360 - 180) <= 30)
    sb = sum(b for h, C, a, b in pts if abs((h - center + 180) % 360 - 180) <= 30)
    return math.degrees(math.atan2(sb, sa)) % 360, total / n


def auto_stops(img):
    """A soft three-stop gradient in the screenshot's own hue family (analogous hues, never
    the complement), lighter behind light screenshots and deeper behind dark ones."""
    hue, _ = dominant_hue(img)
    chroma = 0.10
    if hue is None:            # grayscale UI: a calm blue-violet
        hue, chroma = 262, 0.06
    dark = _edge_lightness(img) <= 0.6
    Ls = (0.64, 0.52, 0.42) if dark else (0.90, 0.81, 0.73)
    chroma *= 1.15 if dark else 1.0
    return [oklch(L, chroma * f, h) for L, f, h in zip(Ls, (0.75, 1.0, 0.95), auto_hues(hue, dark))]


def auto_hues(h, dark=False):
    """Three analogous hues around h that never pass through the olive/khaki band (~75-130
    in OKLab), where soft gradients turn muddy: warm hues drift toward rose, yellow-greens
    become mint."""
    if h < 20 or h >= 330:
        return h + 25, h, h - 30
    if h < 100:                       # orange / amber -> peach, coral, rose
        h = min(h, 62)
        return (h - 15, h - 45, h - 75) if dark else (h + 8, h - 20, h - 48)
    if h < 175:                       # yellow-green / green -> mint, teal
        h = max(h, 160)
        return h - 10, h + 12, h + 38
    return h - 28, h, h + 30


def _edge_lightness(img):
    t = _thumb(img)
    w, h = t.size
    px = t.load()
    ring = [px[x, y] for x in range(w) for y in (0, 1, h - 2, h - 1)] + \
           [px[x, y] for y in range(h) for x in (0, 1, w - 2, w - 1)]
    return sum(to_oklab(p)[0] for p in ring) / len(ring)


def bg_stops(bg, img):
    if bg == "auto":
        return auto_stops(img)
    if isinstance(bg, str) and bg in PRESETS:
        bg = PRESETS[bg]
    if isinstance(bg, str):
        bg = [bg]
    return [to_oklab(ImageColor.getrgb(c)) for c in bg]


def shadow_color(stops):
    """Window shadow tint: the backdrop's deepest stop, much darker and a bit less saturated.
    A colored shadow reads as light falling on the backdrop; neutral black turns pastels gray."""
    L, a, b = min(stops, key=lambda s: s[0])
    return from_oklab((min(0.24, 0.4 * L), 0.8 * a, 0.8 * b))


# ---------- background painting ----------

@lru_cache(maxsize=4)
def _grain_tile(sigma=1.4, size=128):
    rnd = random.Random(20261007)
    tile = Image.new("L", (size, size))
    tile.putdata([max(0, min(255, round(128 + rnd.gauss(0, sigma)))) for _ in range(size * size)])
    return tile


def _grain(img):
    """Fixed, invisible film grain: kills banding in 8-bit gradients. Deterministic."""
    tile = _grain_tile()
    noise = Image.new("L", img.size)
    for y in range(0, img.height, tile.height):
        for x in range(0, img.width, tile.width):
            noise.paste(tile, (x, y))
    n3 = Image.merge("RGB", (noise, noise, noise))
    return ImageChops.add(img, n3, 1.0, -128)


def gradient(size, stops):
    """Diagonal (top-left -> bottom-right) gradient through OKLab stops, plus a faint light
    bloom in the top-left corner so a flat gradient reads as depth."""
    W, H = size
    if len(stops) == 1:
        return Image.new("RGB", size, from_oklab(stops[0]))
    lut = []
    seg = len(stops) - 1
    for i in range(512):
        t = i / 511 * seg
        k = min(seg - 1, int(t))
        f = t - k
        lab = tuple(stops[k][j] + (stops[k + 1][j] - stops[k][j]) * f for j in range(3))
        lut.append(from_oklab(lab))
    sw = max(2, min(W, 360))
    sh = max(2, round(H * sw / W))
    # the gradient runs along the diagonal, perpendicular lines share a color (CSS 135deg)
    dx, dy = W, H
    norm = dx * dx + dy * dy
    data = []
    for y in range(sh):
        Y = (y + 0.5) / sh * H
        for x in range(sw):
            X = (x + 0.5) / sw * W
            t = (X * dx + Y * dy) / norm
            data.append(lut[max(0, min(511, round(t * 511)))])
    small = Image.new("RGB", (sw, sh))
    small.putdata(data)
    img = small.resize(size, Image.BICUBIC)
    # soft bloom: a big blurred ellipse of a lighter first stop, at the top-left
    L0, a0, b0 = stops[0]
    glow = from_oklab((min(1.0, L0 + 0.12), a0 * 0.85, b0 * 0.85))
    bloom = Image.new("L", (sw, sh), 0)
    ImageDraw.Draw(bloom).ellipse([-0.35 * sw, -0.6 * sh, 0.55 * sw, 0.7 * sh], fill=255)
    bloom = bloom.filter(ImageFilter.GaussianBlur(0.18 * max(sw, sh))).resize(size, Image.BICUBIC)
    img = Image.composite(Image.new("RGB", size, glow), img, bloom.point(lambda v: int(v * 0.45)))
    return _grain(img)
