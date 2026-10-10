"""Mark looks. `Look` is the built-in look and the base class for looks from other packages:
entry point group "ainotate.looks", name -> a Look subclass. The look is picked by spec "look"
(also --look and the MCP `look` argument), then env AINOTATE_LOOK, then `look` in config.toml,
then "default". A look that is not installed, fails to load, fails to start or fails while
drawing never breaks a render: the default look is used and the render gets a warning."""
from __future__ import annotations

import copy
import math
from types import SimpleNamespace

from PIL import ImageColor, ImageDraw, ImageStat

from .style import SHADOW, color_of, font_for, load_font, pill_size, rgba


def _r():
    """The render module: its drawing helpers are looked up at call time (tests patch them there)."""
    from . import render
    return render


class Look:
    """render() draws every mark through these methods. `ctx` holds the unmarked `page`, `marks`,
    `rects` and `points` (image px, after the crop), the crop's `offset` (image px), `scale`, `u`."""
    shadow = SHADOW       # drop shadow under all marks: opacity, y-offset, blur (x u)
    dim = 0.55            # spotlight darkness when the spec sets no "dim"
    loupe_line = None     # magnifier line width (None: the built-in width)

    def __init__(self, ctx):
        self.ctx, u = ctx, ctx.u
        self.stroke = max(3, round(u / 4))
        self.spot_radius = int(u)
        self.font = load_font(int(1.35 * u))
        self.badge_font = load_font(int(1.4 * u))

    def color(self, i):
        return color_of(self.ctx.marks[i].get("color"))

    def label_size(self, text):
        return pill_size(self.font, text, self.ctx.u)

    def label(self, layer, img, i, box, text):   # labels and text notes; img: the page with tints
        _r().draw_pill(layer, box, text, self.font, self.color(i), self.ctx.u)

    def outline(self, layer, i, g):
        _r().draw_outline(layer, g, self.color(i), self.stroke, int(0.6 * self.ctx.u))

    def badge(self, layer, img, i, b, n):
        _r().draw_badge(layer, b, self.color(i), n, self.badge_font, max(2, round(0.12 * self.ctx.u)))

    def arrow(self, layer, gray, i, path, style, badge):   # badge: the mark's own step badge or None
        if style == "line":
            _r().draw_arrow(ImageDraw.Draw(layer), path[0], path[-1], self.color(i), self.stroke, self.ctx.u)
        else:
            _r().draw_arrow_skitch(layer, gray, path[0], path[-1], self.color(i), self.ctx.u, path)

    def highlight(self, over, img, i, r):   # lighter tint on dark regions (70 reads as a solid block there)
        dark = ImageStat.Stat(img.convert("L").crop([int(v) for v in r])).mean[0] < 90
        ImageDraw.Draw(over).rectangle(r, fill=rgba(self.color(i), 45 if dark else 70))

    def redact_fill(self, i):   # a look may change the color of a redaction, never its area
        return "#1F1F1F"

    def keys_font(self, i, keys):
        return font_for(keys, int(1.15 * self.ctx.u))


def installed_looks() -> dict:
    """Looks from other packages: name -> entry point."""
    from importlib.metadata import entry_points
    return {e.name: e for e in entry_points(group="ainotate.looks")}


class LookFailed(Exception):
    """An installed look raised while drawing; render() then redraws with the default look."""


def _check(ok, what, v):
    if not ok:
        raise ValueError(f"{what} is {v!r}")
    return v


def _num(v, lo=0.0, hi=math.inf):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and lo <= v <= hi


def _color(v):
    """The color as Pillow draws it ("info" -> "#007AFF"); ValueError for anything it cannot draw."""
    c = color_of(v)
    ImageColor.getrgb(c)
    return c


# what render() reads from a look, checked before use: a bad value is a failure of the look
CHECKS = {
    "stroke": lambda v: _check(isinstance(v, int) and _num(v, 1), "stroke", v),
    "spot_radius": lambda v: _check(isinstance(v, int) and _num(v), "spot_radius", v),
    "dim": lambda v: _check(_num(v, 0, 1), "dim", v),
    "loupe_line": lambda v: _check(v is None or (isinstance(v, int) and _num(v, 1)), "loupe_line", v),
    "shadow": lambda v: _check(isinstance(v, tuple) and len(v) == 3 and all(_num(x) for x in v), "shadow", v),
    "color": _color,
    "redact_fill": _color,
    "label_size": lambda v: _check(isinstance(v, tuple) and len(v) == 2 and all(_num(x, 1, 1e6) for x in v),
                                   "label_size()", v),
    "keys_font": lambda v: _check(hasattr(v, "getbbox") and hasattr(v, "getmetrics"), "keys_font()", v),
}


READS_PAGE = {"label", "badge", "highlight"}   # hooks given the page image as their second argument


class _Guarded:
    """An installed look whose errors and bad values become LookFailed (attribute reads too), so
    they are never mistaken for core errors and render() can redraw with the default look."""

    def __init__(self, look, name):
        self._look, self._name = look, name

    def _fail(self, e):
        return LookFailed(fallback(self._name, "failed while drawing", e))

    def __getattr__(self, attr):
        check = CHECKS.get(attr, lambda x: x)
        try:
            v = getattr(self._look, attr)
            if not callable(v):
                return check(v)
        except Exception as e:  # noqa: BLE001 - any failure inside a plug-in look
            raise self._fail(e) from e

        def call(*a, **k):
            if attr in READS_PAGE:   # (layer, img, ...): drawing on its own copy of the page can never
                a = (a[0], a[1].copy(), *a[2:])   # paint over a redaction
            try:
                return check(v(*a, **k))
            except Exception as e:  # noqa: BLE001
                raise self._fail(e) from e
        return call


def _private(ctx):
    """A copy of ctx for an installed look: nothing it changes reaches the render or the spec."""
    return SimpleNamespace(page=ctx.page.copy(), marks=copy.deepcopy(ctx.marks),
                           rects=copy.deepcopy(ctx.rects), points=copy.deepcopy(ctx.points),
                           offset=ctx.offset, scale=ctx.scale, u=ctx.u)


def fallback(name, what, e=None) -> str:
    why = f" ({type(e).__name__}: {e})" if e is not None else ""
    return f"look {name!r} {what}{why}, so the default look was used"


def name_of(spec):
    """The look a spec asks for: its "look", else AINOTATE_LOOK, else `look` in config.toml."""
    if "look" in spec:
        return spec["look"]
    from .output import default_look
    return default_look()


def installed_name(spec):
    """The name of the installed look this spec would use, else None (never raises)."""
    try:
        name = name_of(spec)
        return name if name != "default" and name in installed_looks() else None
    except Exception:  # noqa: BLE001
        return None


def pick(spec, ctx, warnings):
    """The look for this render; a look that cannot be used adds a warning and gives the default."""
    name = name_of(spec)
    if name == "default":
        return Look(ctx)
    found = installed_looks()
    if name not in found:
        warnings.append(fallback(name, "is not installed") + "; installed looks: "
                        + ", ".join(["default", *sorted(found)]))
        return Look(ctx)
    try:
        cls = found[name].load()
    except Exception as e:  # noqa: BLE001
        warnings.append(fallback(name, "could not be loaded", e))
        return Look(ctx)
    try:
        return _Guarded(cls(_private(ctx)), name)
    except Exception as e:  # noqa: BLE001
        warnings.append(fallback(name, "failed to start", e))
        return Look(ctx)
