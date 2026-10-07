"""Palette, label fills, shadow, fonts and the base mark unit."""
from __future__ import annotations

import math
import os
from functools import lru_cache
from pathlib import Path

from PIL import ImageColor, ImageFont

COLORS = {  # Apple system colors: vivid on light and dark UIs. Any Pillow color also accepted
    "look": "#FF9500",   # orange: look here / neutral highlight (default)
    "bad": "#FF3B30",    # red: bug, wrong, removed
    "good": "#34C759",   # green: correct, expected
    "info": "#007AFF",   # blue: secondary context
}
# label fills: same hue, deep enough for white bold text (contrast >= 3.9:1 vs 2.2:1 for the vivid ones)
LABEL_FILL = {"#FF9500": "#D45D00", "#FF3B30": "#D70015", "#34C759": "#248A3D", "#007AFF": "#0071E3"}
SHADOW = (0.30, 0.10, 0.15)   # Skitch drop shadow: opacity, y-offset and blur (all x base unit)
# macOS-style keycap: light face with a soft top-to-bottom gradient, darker lip below, dark text
KEYCAP = {"face_top": "#FFFFFF", "face_bottom": "#ECEDF0", "lip": "#B4B7BF", "border": "#C9CBD1",
          "text": "#1D1D1F"}

FONT_ENV = "AINOTATE_FONT"     # override: "path" or "path:index" (a .ttc face index)
BUNDLED_FONT = Path(__file__).parent / "fonts" / "Inter-Bold.ttf"


def color_of(name):
    return COLORS.get(name or "look", name or COLORS["look"])


def valid_color(c):
    if not isinstance(c, str) or not c:
        return False
    try:
        ImageColor.getrgb(color_of(c))
        return True
    except ValueError:
        return False


def rgba(color, alpha=255):
    return ImageColor.getrgb(color)[:3] + (alpha,)


def text_color_on(color) -> str:
    """Text color ('white' or '#1D1D1F') for high contrast against `color`."""
    try:
        rgb = ImageColor.getrgb(color_of(color))[:3]
        lum = 0.2126 * (rgb[0] / 255) ** 2.2 + 0.7152 * (rgb[1] / 255) ** 2.2 + 0.0722 * (rgb[2] / 255) ** 2.2
        return "#1D1D1F" if lum > 0.45 else "white"
    except Exception:
        return "white"


def font_source():
    """(path, face index) of the label font: $AINOTATE_FONT, else the bundled Inter Bold."""
    raw = os.environ.get(FONT_ENV, "").strip()
    if raw:
        path, sep, idx = raw.rpartition(":")
        if sep and idx.isdigit() and path:   # "C:\\x.ttf" keeps its drive colon
            return os.path.expanduser(path), int(idx)
        return os.path.expanduser(raw), 0
    return str(BUNDLED_FONT), 0


@lru_cache(maxsize=64)
def _truetype(path, index, size):
    return ImageFont.truetype(path, size, index=index)


def load_font(size):
    path, index = font_source()
    try:
        return _truetype(path, index, size)
    except OSError:
        return ImageFont.load_default(size)


def has_glyphs(font, text):
    """False when the font draws any non-space character of text as its missing-glyph box."""
    try:
        missing = font.getmask("\U0010FFFD")
        miss = (missing.size, bytes(missing))
        return all((font.getmask(ch).size, bytes(font.getmask(ch))) != miss for ch in text if not ch.isspace())
    except (AttributeError, OSError, ValueError):
        return True


def font_for(text, size):
    """Label font, or the bundled Inter when an $AINOTATE_FONT override lacks a glyph (⌘ ⇧ ⌥ ⌃)."""
    font = load_font(size)
    if has_glyphs(font, text):
        return font
    try:
        return _truetype(str(BUNDLED_FONT), 0, size)
    except OSError:
        return font


def text_size(font, text):
    lines = text.split("\n")
    widths = [font.getbbox(l)[2] - font.getbbox(l)[0] for l in lines]
    asc, desc = font.getmetrics()
    return max(widths), (asc + desc) * len(lines) + 4 * (len(lines) - 1)


def pill_size(font, text, u):
    tw, th = text_size(font, text)
    return tw + 1.4 * u, th + 0.9 * u


def mark_unit(W, H, scale):
    """Base unit: area-based so wide strips stay sane, and grown on retina/phone captures
    (scale > 1) so marks keep their size relative to the page text."""
    k = max(1.0, min(scale, 3) * 0.75)
    return k * max(12, min(22, math.sqrt(W * H) / 60))
