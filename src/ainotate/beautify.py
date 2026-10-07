"""Frame an annotated screenshot so it looks finished (Xnapper / CleanShot "Background tool"):
a soft gradient backdrop, padding, rounded corners, a macOS-like window shadow, optional
minimal browser/window chrome and a fixed aspect ratio. Pure: no printing, no file writes.

apply_frame(img, frame, unit) -> Image; validate_frame(frame) -> list of error strings.
All sizes scale with `unit` (the render's mark unit), so a retina capture gets a retina frame.
"""
from __future__ import annotations

import math
import os
import random
from functools import lru_cache

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter, ImageFont

from .beautify_color import (  # noqa: F401  (re-export: callers keep importing these from ainotate.beautify)
    PRESETS, _MARK_COLORS, _edge_lightness, _gam, _grain, _grain_tile, _lin, _oklab_lin, _pixels, _thumb,
    auto_hues, auto_stops, bg_stops, dominant_hue, from_oklab, gradient, luminance, oklch, shadow_color, to_oklab,
)
from .style import COLORS, LABEL_FILL, load_font  # noqa: F401

FRAME_DEFAULTS = {
    "bg": "auto",       # "auto" | preset | "#hex" | ["#hex", "#hex", ...]
    "pad": None,        # px around the window; None = 6% of the longer side
    "radius": None,     # px corner radius of the window; None = 1.0 x unit
    "shadow": True,     # True | False | 0..1 opacity of the ambient shadow
    "chrome": None,     # None | "browser" | "window"
    "url": None,        # text in the browser address pill
    "theme": "auto",    # chrome colors: "auto" (from the screenshot's top edge) | "light" | "dark"
    "aspect": None,     # None | "16:9" | "4:3" | "1:1" | "twitter" | "linkedin" | "W:H"
    "balance": False,   # shift the window so off-center content looks centered
}

ASPECTS = {"16:9": 16 / 9, "4:3": 4 / 3, "1:1": 1.0, "twitter": 16 / 9, "linkedin": 1200 / 627}

# Real browser-ish chrome colors (Safari/Chrome minimal toolbars)
CHROME = {
    "light": {"bar": "#F2F2F4", "line": "#D8D8DC", "pill": "#E3E3E8", "text": "#6E6E73"},
    "dark": {"bar": "#2B2B2E", "line": "#141416", "pill": "#3C3C40", "text": "#A1A1A6"},
}
LIGHTS = [("#FF5F57", "#E2463F"), ("#FEBC2E", "#E1A116"), ("#28C840", "#1AAB29")]
# traffic lights (x unit): radius, first center from the left edge, center-to-center step.
# macOS proportions: 12 pt lights on a 20 pt pitch, so diameter / step = 0.6
LIGHT_R, LIGHT_X0, LIGHT_STEP = 0.44, 1.4, 1.47



# ---------- validation ----------

def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _color_ok(c):
    if not isinstance(c, str) or not c:
        return False
    try:
        ImageColor.getrgb(c)
        return True
    except ValueError:
        return False


def _aspect_ratio(a):
    if not isinstance(a, str):
        return None
    if a in ASPECTS:
        return ASPECTS[a]
    if a.count(":") == 1:
        x, y = a.split(":")
        try:
            x, y = float(x), float(y)
        except ValueError:
            return None
        if x > 0 and y > 0 and math.isfinite(x) and math.isfinite(y) and math.isfinite(x / y) and x / y > 0:
            return x / y
    return None


def _check_bg(bg, errs):
    if isinstance(bg, str) and (bg in ("auto", "transparent") or bg in PRESETS):
        return
    if isinstance(bg, list):
        if not 2 <= len(bg) <= 5 or not all(_color_ok(c) for c in bg):
            errs.append("spec.frame: 'bg' gradient must be a list of 2 to 5 colors (#hex or css)")
        return
    if not _color_ok(bg):
        errs.append(f"spec.frame: bad bg {bg!r}; use auto, one of {', '.join(PRESETS)}, transparent, "
                    "#hex or [\"#hex\", \"#hex\"]")


def validate_frame(frame) -> list[str]:
    """Every problem with a `frame` value, as spec.py-style messages (empty list = valid)."""
    if frame is None or isinstance(frame, bool):
        return []
    errs: list[str] = []
    if isinstance(frame, (str, list)):   # shorthand: just the background
        _check_bg(frame, errs)
        return errs
    if not isinstance(frame, dict):
        return ["spec.frame: must be true, a background (name, color, [colors]) or an object"]
    for k in frame:
        if k not in FRAME_DEFAULTS:
            errs.append(f"spec.frame: unknown key {k!r}; use {', '.join(FRAME_DEFAULTS)}")
    if "bg" in frame:
        _check_bg(frame["bg"], errs)
    for k in ("pad", "radius"):
        if frame.get(k) is not None and not (_num(frame[k]) and frame[k] >= 0):
            errs.append(f"spec.frame: '{k}' must be a number >= 0")
    s = frame.get("shadow", True)
    if not (isinstance(s, bool) or (_num(s) and 0 <= s <= 1)):
        errs.append("spec.frame: 'shadow' must be true, false or a number from 0 to 1")
    if frame.get("chrome") not in (None, "browser", "window"):
        errs.append("spec.frame: 'chrome' must be browser, window or null")
    if frame.get("url") is not None:
        if not isinstance(frame["url"], str):
            errs.append("spec.frame: 'url' must be a string")
        elif frame.get("chrome") != "browser":
            errs.append("spec.frame: 'url' needs chrome 'browser'")
    if frame.get("theme", "auto") not in ("auto", "light", "dark"):
        errs.append("spec.frame: 'theme' must be auto, light or dark")
    if frame.get("aspect") is not None and _aspect_ratio(frame["aspect"]) is None:
        errs.append("spec.frame: 'aspect' must be 16:9, 4:3, 1:1, twitter, linkedin or W:H")
    if not isinstance(frame.get("balance", False), bool):
        errs.append("spec.frame: 'balance' must be true or false")
    return errs


def normalize_frame(frame) -> dict | None:
    """Frame value -> full options dict (None when framing is off). ValueError if invalid."""
    if frame is None or frame is False:
        return None
    errs = validate_frame(frame)
    if errs:
        raise ValueError("invalid frame:\n  " + "\n  ".join(errs))
    opts = dict(FRAME_DEFAULTS)
    if isinstance(frame, (str, list)):
        opts["bg"] = frame
    elif isinstance(frame, dict):
        opts.update(frame)
    return opts


# ---------- window: chrome bar, corners, hairline ----------

def _corner(r, S=4):
    tile = Image.new("L", (r * S, r * S), 0)
    ImageDraw.Draw(tile).ellipse([0, 0, 2 * r * S - 1, 2 * r * S - 1], fill=255)
    return tile.resize((r, r), Image.LANCZOS)


def rounded_mask(size, r):
    """Opaque L mask with anti-aliased rounded corners (only the corners are supersampled)."""
    w, h = size
    m = Image.new("L", size, 255)
    r = int(min(r, w // 2, h // 2))
    if r <= 0:
        return m
    c = _corner(r)
    m.paste(c, (0, 0))
    m.paste(c.transpose(Image.FLIP_LEFT_RIGHT), (w - r, 0))
    m.paste(c.transpose(Image.FLIP_TOP_BOTTOM), (0, h - r))
    m.paste(c.transpose(Image.ROTATE_180), (w - r, h - r))
    return m


_UI_FONTS = ["/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/HelveticaNeue.ttc",
             "C:\\Windows\\Fonts\\segoeui.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]


def ui_font(size):
    """A regular-weight UI font for the address bar; the bundled Inter Bold as a fallback."""
    if not os.environ.get("AINOTATE_FONT"):
        for path in _UI_FONTS:
            if os.path.isfile(path):
                try:
                    return ImageFont.truetype(path, size)
                except OSError:
                    pass
    return load_font(size)


def chrome_theme(img, theme="auto"):
    """light/dark chrome: matches the top edge of the screenshot, like the real browser would."""
    if theme in ("light", "dark"):
        return theme
    band = max(2, round(img.height * 0.04))
    return "light" if luminance(img, (0, 0, img.width, band)) >= 0.55 else "dark"


def chrome_height(kind, u):
    return 0 if not kind else round((3.3 if kind == "browser" else 2.4) * u)


def draw_chrome(width, kind, theme, u, url=None, S=4):
    """The bar above the screenshot, drawn at S x and downsampled."""
    H = chrome_height(kind, u)
    pal = CHROME[theme]
    W4, H4 = width * S, H * S
    bar = Image.new("RGB", (W4, H4), pal["bar"])
    d = ImageDraw.Draw(bar)
    d.rectangle([0, H4 - max(S, round(0.07 * u * S)), W4, H4], fill=pal["line"])
    cy = (H4 - S) / 2
    rad = LIGHT_R * u * S
    x = LIGHT_X0 * u * S
    rim = 0.06 * u * S
    for fill, ring in LIGHTS:
        d.ellipse([x - rad, cy - rad, x + rad, cy + rad], fill=ring)
        d.ellipse([x - rad + rim, cy - rad + rim, x + rad - rim, cy + rad - rim], fill=fill)
        x += LIGHT_STEP * u * S
    lights_end = x - LIGHT_STEP * u * S + rad
    if kind == "browser":
        ph = 2.05 * u * S
        pw = min(max(22 * u * S, 0.42 * W4), W4 - 2 * (lights_end + 1.5 * u * S))
        if pw > 6 * u * S:
            x0 = (W4 - pw) / 2
            d.rounded_rectangle([x0, cy - ph / 2, x0 + pw, cy + ph / 2], radius=0.55 * u * S, fill=pal["pill"])
            text = (url or "").strip()
            font = ui_font(max(8, round(0.98 * u * S)))
            lock_w = 0.95 * u * S
            maxw = pw - 3 * u * S - lock_w
            while text and font.getlength(text) > maxw:
                text = text[:-2] + "…" if len(text) > 2 else ""
            tw = font.getlength(text) if text else 0
            gx = W4 / 2 - (tw + lock_w + (0.45 * u * S if text else 0)) / 2
            _lock(d, gx, cy, u * S, pal["text"])
            if text:
                d.text((gx + lock_w + 0.45 * u * S, cy), text, fill=pal["text"], font=font, anchor="lm")
    return bar.resize((width, H), Image.LANCZOS)


def _lock(d, x, cy, U, color):
    """Small padlock glyph (body + shackle), left edge at x, centered on cy."""
    bw, bh = 0.78 * U, 0.6 * U
    top = cy - bh / 2 + 0.2 * U
    d.rounded_rectangle([x, top, x + bw, top + bh], radius=0.12 * U, fill=color)
    sw = 0.5 * U
    sx = x + (bw - sw) / 2
    d.arc([sx, top - 0.5 * U, sx + sw, top + 0.35 * U], 180, 360, fill=color, width=max(1, round(0.11 * U)))
    d.line([sx + 0.055 * U, top - 0.07 * U, sx + 0.055 * U, top], fill=color, width=max(1, round(0.11 * U)))
    d.line([sx + sw - 0.055 * U, top - 0.07 * U, sx + sw - 0.055 * U, top], fill=color,
           width=max(1, round(0.11 * U)))


# ---------- layout ----------

def canvas_size(win, pad, aspect):
    """Window + padding, grown (never cropped) to the aspect ratio."""
    W, H = win[0] + 2 * pad, win[1] + 2 * pad
    r = _aspect_ratio(aspect) if aspect else None
    if r:
        if W / H < r:
            W = math.ceil(H * r)
        else:
            H = math.ceil(W / r)
    return int(W), int(H)


def content_box(img):
    """Bounding box of what differs from the screenshot's edge color (None if all flat)."""
    t = img.convert("RGB")
    corners = [t.getpixel(p) for p in ((0, 0), (t.width - 1, 0), (0, t.height - 1), (t.width - 1, t.height - 1))]
    bgc = max(set(corners), key=corners.count)
    diff = ImageChops.difference(t, Image.new("RGB", t.size, bgc)).convert("L").point(lambda v: 255 if v > 24 else 0)
    return diff.getbbox()


def balance_shift(img, top_extra, pad):
    """(dx, dy) that moves the window so its content, not its border, sits in the middle."""
    box = content_box(img)
    if not box:
        return 0, 0
    w, h = img.size
    lim = 0.6 * pad
    dx = (w - box[2] - box[0]) / 2
    dy = (h - box[3] - box[1]) / 2
    if top_extra:   # a chrome bar already weighs the top: correct half as much vertically
        dy /= 2
    return max(-lim, min(lim, dx)), max(-lim, min(lim, dy))


# ---------- main ----------

def apply_frame(img: Image.Image, frame, unit: float, source: Image.Image | None = None) -> Image.Image:
    """Put the screenshot on a backdrop. Returns RGB, or RGBA when bg is "transparent".
    frame: True (defaults), a bg name/color/[colors], or an options dict (see FRAME_DEFAULTS).
    source: the same screenshot before any mark was drawn. When given, the auto background and the
    chrome theme are read from it, so a big green label cannot turn the backdrop green."""
    opts = normalize_frame(frame)
    if opts is None:
        return img
    u = float(unit)
    shot = img.convert("RGB")
    ref = shot if source is None else source.convert("RGB")
    w, h = shot.size

    kind = opts["chrome"]
    theme = chrome_theme(ref, opts["theme"]) if kind else None
    hb = chrome_height(kind, u)
    win = Image.new("RGB", (w, h + hb))
    if kind:
        win.paste(draw_chrome(w, kind, theme, u, opts["url"]), (0, 0))
    win.paste(shot, (0, hb))
    ww, wh = win.size

    pad = round(0.06 * max(w, h)) if opts["pad"] is None else round(opts["pad"])
    radius = round(1.0 * u if opts["radius"] is None else opts["radius"])
    W, H = canvas_size((ww, wh), pad, opts["aspect"])
    x0, y0 = (W - ww) / 2, (H - wh) / 2
    if opts["balance"]:
        dx, dy = balance_shift(shot, hb, pad)
        x0, y0 = x0 + dx, y0 + dy
    x0, y0 = round(x0), round(y0)

    transparent = opts["bg"] == "transparent"
    stops = None if transparent else bg_stops(opts["bg"], ref)
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0)) if transparent else gradient((W, H), stops).convert("RGBA")

    mask = rounded_mask((ww, wh), radius)
    strength = 0.0 if opts["shadow"] is False else (0.40 if opts["shadow"] is True else float(opts["shadow"]))
    if strength > 0 and pad > 0:
        tint = (0, 0, 0) if transparent else shadow_color(stops)   # cast light: a deeper shade of the backdrop
        canvas = Image.alpha_composite(canvas, _shadow((W, H), mask, (x0, y0), strength, u, pad, tint))

    # window edge: a hairline that separates it from the backdrop, like a macOS window border
    edge_light = _edge_lightness(win) > 0.5
    t = max(1, round(u / 16))
    inner = Image.new("L", (ww, wh), 0)
    if ww > 2 * t and wh > 2 * t:
        inner.paste(rounded_mask((ww - 2 * t, wh - 2 * t), max(0, radius - t)), (t, t))
    ring = ImageChops.subtract(mask, inner)
    win_rgba = win.convert("RGBA")
    win_rgba.putalpha(mask)
    line = (0, 0, 0, 30) if edge_light else (255, 255, 255, 34)
    hair = Image.new("RGBA", (ww, wh), line[:3] + (0,))
    hair.putalpha(ring.point(lambda v: v * line[3] // 255))
    win_rgba = Image.alpha_composite(win_rgba, hair)
    canvas.alpha_composite(win_rgba, (x0, y0))
    return canvas if transparent else canvas.convert("RGB")


def _shadow(size, mask, pos, strength, u, pad, color=(0, 0, 0)):
    """Two-layer macOS-like window shadow: a wide, soft, low ambient shadow pushed down a little,
    plus a tight contact shadow that anchors the edges. Blurred at 1/4 size for speed."""
    W, H = size
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    k = 4
    layers = ((min(2.0 * u, pad / 2.6), 0.9 * u, strength), (min(0.35 * u, pad / 3), 0.12 * u, strength * 0.55))
    for blur, dy, op in layers:
        sk = k if blur > 6 else 1
        a = Image.new("L", (math.ceil(W / sk), math.ceil(H / sk)), 0)
        m = mask.resize((max(1, round(mask.width / sk)), max(1, round(mask.height / sk))), Image.BOX)
        a.paste(m, (round(pos[0] / sk), round((pos[1] + dy) / sk)))
        a = a.filter(ImageFilter.GaussianBlur(max(0.5, blur / sk)))
        if sk > 1:
            a = a.resize(size, Image.BICUBIC)
        layer = Image.new("RGBA", size, tuple(color) + (0,))
        layer.putalpha(a.point(lambda v: round(v * op)))
        out = Image.alpha_composite(out, layer)
    return out
