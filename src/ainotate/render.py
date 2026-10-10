"""render(spec) -> RenderResult: the annotated image plus warnings. Pure: no printing, no exits,
no file writes.

Marks (coordinates in spec units; `scale` converts them to image pixels):

  type        needs                  options
  step        rect, n                label, label_at, arrow, box, badge, pad, color
  box         rect                   label, label_at, arrow, box, pad, color
  arrow       rect, label            label_at, color
  highlight   rect                   color
  spotlight   rect                   (spec dim)
  redact      rect                   fill, pad (default outset ~2 px). Solid: the only mark for secrets
  text        text, at (top-left)    color
  magnify     rect (source)          at (loupe center in source units like rect, before the crop; else a
                                     calm free spot), zoom 1.2-8 (2; the default loupe radius stays
                                     under min(4 u, 18% of the frame)), shape circle|rounded, color
  click       at or rect (center)    label, label_at, arrow, color: ripple rings + pointer
  keys        keys, at or rect       at = top-left; with rect the keycaps sit beside it like a label
  blur        rect                   radius. Decorative only: refused with "sensitive": true
  pixelate    rect                   block. Decorative only: blur can be reversed, use redact

spec "crop": "auto" frames the marks plus crop_pad and keeps a minimum of context (half the width,
45% of the height); "tight" frames only the marks plus crop_pad (privacy-sensitive images);
[x1,y1,x2,y2] / {x,y,w,h} in spec units.

Marks may use "target": {"text": "Save", "nth"?, "within"?, "exact"?} instead of "rect" (found by
OCR on the input). spec "privacy": "auto" adds solid redactions for secrets OCR reads (default "off"
here); spec "frame" puts the finished image on a backdrop (beautify.apply_frame, last step).

spec "look": the mark style, "default" (built in) or the name of an installed look package (entry
point group "ainotate.looks", e.g. "neat"); without it AINOTATE_LOOK, then `look` in config.toml.

Warnings: more than 6 marks, labels over 4 words, labels that overlap each other or cover another
mark's target, arrows that cross, a magnifier that had to shrink its zoom, a look that is not
installed or failed (the default look is used then; the warning lists the installed looks).

Layout (crop, labels, badges, loupes) lives in placement.py.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from types import SimpleNamespace

from PIL import Image, ImageDraw, ImageFilter, ImageStat

from .geometry import clamp_box, contains, grow, norm_rect, overlap_ratio, path_length, skitch_arrow_points, skitch_curve_points
from .marks import (aa_draw, click_geometry, deemphasize, draw_click, draw_keys, draw_loupe, keys_size,
                    overlap_warnings, route_arrow)
from .label_plan import Job, plan_labels
from .placement import RenderError, ContentMap, auto_crop, place_badge, place_label, place_loupe  # noqa: F401
from .looks import Look, LookFailed, fallback, installed_name, pick  # noqa: F401  (Look: base class)
from .spec import DECORATIVE, LABELED, open_image, spec_warnings, validate
from .style import LABEL_FILL, SHADOW, color_of, font_for, load_font, mark_unit, pill_size, rgba, text_color_on

__all__ = ["RenderError", "RenderResult", "render"]


@dataclass
class RenderResult:
    image: Image.Image                                     # RGB; RGBA with a transparent frame
    warnings: list[str] = field(default_factory=list)
    redactions: list[str] = field(default_factory=list)    # privacy.summary() lines, never values


# ---------- drawing ----------

def draw_outline(layer, g, color, width, radius):
    aa_draw(layer, grow(g, width), color,
            lambda d, T, S: d.rounded_rectangle(T(g), radius=radius * S, outline=color, width=width * S))


def draw_badge(layer, b, color, text, font, ring):
    color = LABEL_FILL.get(str(color).upper(), color)   # same readable fill as the labels
    fg = text_color_on(color)
    aa_draw(layer, b, color, lambda d, T, S: d.ellipse(T(b), fill=color, outline=fg, width=ring * S))
    ImageDraw.Draw(layer).text(((b[0] + b[2]) / 2, (b[1] + b[3]) / 2), text, fill=fg, font=font, anchor="mm")


def draw_pill(layer, box, text, font, fill, u):
    fill = LABEL_FILL.get(str(fill).upper(), fill)
    fg = text_color_on(fill)
    aa_draw(layer, box, fill, lambda d, T, S: d.rounded_rectangle(T(box), radius=int(0.6 * u) * S, fill=fill))
    ImageDraw.Draw(layer).multiline_text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), text,
                                         fill=fg, font=font, anchor="mm", align="center", spacing=4)


def draw_arrow(d, p1, p2, color, width, u):
    d.line([p1, p2], fill=color, width=width)
    ang = math.atan2(p2[1] - p1[1], p2[0] - p1[0])
    L = 1.6 * u
    d.polygon([p2,
               (p2[0] - L * math.cos(ang - 0.45), p2[1] - L * math.sin(ang - 0.45)),
               (p2[0] - L * math.cos(ang + 0.45), p2[1] - L * math.sin(ang + 0.45))],
              fill=color)


def draw_arrow_skitch(layer, gray, p1, p2, color, u, path=None):
    """Draw the arrow 4x on a local layer and downsample for smooth edges. A thin white rim
    is added only on dark backgrounds, where a colored arrow would otherwise sink in."""
    if math.dist(p1, p2) < 1:
        return
    S = 4
    pts = skitch_curve_points(path, u) if path and len(path) > 2 else skitch_arrow_points(p1, p2, u)
    m = u
    x0 = int(max(0, min(x for x, _ in pts) - m)); y0 = int(max(0, min(y for _, y in pts) - m))
    x1 = int(min(layer.width, max(x for x, _ in pts) + m)); y1 = int(min(layer.height, max(y for _, y in pts) + m))
    if x1 <= x0 or y1 <= y0:
        return
    local = [((x - x0) * S, (y - y0) * S) for x, y in pts]
    hi = Image.new("RGBA", ((x1 - x0) * S, (y1 - y0) * S), rgba(color, 0))
    hd = ImageDraw.Draw(hi)
    if ImageStat.Stat(gray.crop((x0, y0, x1, y1))).mean[0] < 90:
        hd.polygon(local, fill="white", outline="white", width=max(2, int(0.14 * u * S)))
    hd.polygon(local, fill=color)
    layer.alpha_composite(hi.resize((x1 - x0, y1 - y0), Image.LANCZOS), dest=(x0, y0))


# ---------- render ----------

def _su(vals, scale):
    """Image px -> spec units for messages (ints when they are whole)."""
    out = [v / scale for v in vals]
    return [int(round(v)) if abs(v - round(v)) < 0.05 else round(v, 1) for v in out]


def _outside_crop(i, m, r, crop, scale):
    """Error for a mark that lies mostly outside an explicit crop: where it is, in spec units, and
    the usual cause on live pages (a banner or notice that moved the layout between runs)."""
    W, H = crop[2] - crop[0], crop[3] - crop[1]
    cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
    where = [w for w, bad in (("above", cy < 0), ("below", cy > H), ("left of", cx < 0), ("right of", cx > W)) if bad]
    rect = _su([r[0] + crop[0], r[1] + crop[1], r[2] + crop[0], r[3] + crop[1]], scale)
    return (f"mark #{i} ({m['type']}): rect {rect} lies mostly outside the crop {_su(crop, scale)}"
            f"{' (' + ' and '.join(where) + ' it)' if where else ''}; both in spec units. A live page may have "
            "moved between runs (an app banner or cookie notice pushes content down): use \"crop\": \"auto\" or "
            "widen the crop.")


def render(spec: dict, debug: bool = False) -> RenderResult:
    """Validate and draw a spec. SpecError for an invalid spec or unreadable input,
    RenderError for a valid spec that cannot be drawn. When an installed look is in use and
    anything fails (the look, or AInotate on a value the look gave it), the render is redone from
    the spec with the default look, with a warning; an error the default look also hits is raised."""
    try:
        return _render(spec, debug)
    except LookFailed as e:
        warning = str(e)
    except Exception as e:  # noqa: BLE001 - only with an installed look, see below
        name = installed_name(spec)
        if name is None:
            raise
        warning = fallback(name, "failed while drawing", e)
    res = _render(dict(spec, look="default"), debug)
    res.warnings.insert(0, warning)
    return res


def _render(spec: dict, debug: bool) -> RenderResult:
    from .shoot import prepare_image_spec   # text targets (OCR) and auto privacy -> rects
    n_user = len(validate(spec))
    spec, redactions = prepare_image_spec(spec)
    marks = validate(spec)
    auto = [bool(m.get("auto")) for m in marks]   # automatic redactions: never crop, place or fail on them
    warnings = spec_warnings(marks[:n_user])
    img = open_image(spec["input"]).convert("RGBA")
    scale = float(spec.get("scale", 1))
    full = [0, 0, img.width, img.height]
    rects = [norm_rect(m["rect"], scale) if "rect" in m else None for m in marks]
    for i, r in enumerate(rects):
        if r and not auto[i] and overlap_ratio(r, full) < 0.5:
            raise RenderError(f"mark #{i} ({marks[i]['type']}): rect {[round(v) for v in r]} lies mostly outside "
                              f"the {img.width}x{img.height} image. Check 'scale' (DOM px vs image px).")
    # free points: text anchors and forced label positions, with the text they carry
    pills = [([v * scale for v in m["at"]], m["text"]) for m in marks if m["type"] == "text"]
    pills += [([v * scale for v in m["label_at"]], m["label"]) for m in marks if m.get("label_at")]
    for (x, y), text in pills:
        if not (0 <= x < img.width and 0 <= y < img.height):
            raise RenderError(f"position {[round(x), round(y)]} for {text!r} is outside the "
                              f"{img.width}x{img.height} image")
    # `at` of click / keys / magnify (click point, keycaps top-left, loupe center)
    points = {i: [v * scale for v in m["at"]] for i, m in enumerate(marks)
              if m["type"] in ("click", "keys", "magnify") and "at" in m}
    for i, (x, y) in points.items():
        if not (0 <= x < img.width and 0 <= y < img.height):
            raise RenderError(f"mark #{i} ({marks[i]['type']}): 'at' {[round(x), round(y)]} is outside the "
                              f"{img.width}x{img.height} image")

    # crop first so sizes scale with what the reader actually sees
    crop = spec.get("crop")
    if crop in ("auto", "tight"):
        crop = auto_crop(spec, img, [None if a else r for r, a in zip(rects, auto)], pills + [
            (p, " ".join(marks[i].get("keys", [])) or " ") for i, p in points.items()], scale, crop == "auto")
    elif crop:
        crop = norm_rect(crop, scale)
        for (x, y), text in pills:
            if not (crop[0] <= x < crop[2] and crop[1] <= y < crop[3]):
                raise RenderError(f"position {[round(x), round(y)]} for {text!r} is outside the crop "
                                  f"{[round(v) for v in crop]}")
        for i, (x, y) in points.items():
            if not (crop[0] <= x < crop[2] and crop[1] <= y < crop[3]):
                what = "'at' (the loupe center, source units)" if marks[i]["type"] == "magnify" else "'at'"
                raise RenderError(f"mark #{i} ({marks[i]['type']}): {what} {_su([x, y], scale)} is outside "
                                  f"the crop {_su(crop, scale)}")
    if crop:
        crop = [int(max(0, crop[0])), int(max(0, crop[1])),
                int(min(img.width, crop[2])), int(min(img.height, crop[3]))]
        if crop[2] - crop[0] < 10 or crop[3] - crop[1] < 10:
            raise RenderError(f"crop {crop} is empty after clipping to the {img.width}x{img.height} image")
        img = img.crop(crop)
        shift = lambda b: [b[0] - crop[0], b[1] - crop[1], b[2] - crop[0], b[3] - crop[1]]
        rects = [shift(r) if r else None for r in rects]
        for i, r in enumerate(rects):
            if r and not auto[i] and overlap_ratio(r, [0, 0, img.width, img.height]) < 0.5:
                raise RenderError(_outside_crop(i, marks[i], r, crop, scale))
    ox, oy = (crop[0], crop[1]) if crop else (0, 0)
    points = {i: [x - ox, y - oy] for i, (x, y) in points.items()}
    page = img.convert("RGB")   # the unmarked page: the frame's auto backdrop reads its colors

    W, H = img.size
    u = mark_unit(W, H, scale)
    style = spec.get("arrow_style", "skitch")
    lk = pick(spec, SimpleNamespace(page=page, marks=marks, rects=rects, points=points,
                                    offset=(ox, oy), scale=scale, u=u), warnings)
    stroke = lk.stroke
    base_pad = 0.7 * u

    def pad_for(m, r):
        """Outline gap, tight for small elements (an 11px link); `pad` overrides (spec units)."""
        if "pad" in m:
            return m["pad"] * scale
        return max(stroke + 2, min(base_pad, 0.35 * min(r[2] - r[0], r[3] - r[1])))

    outlines = [grow(r, pad_for(m, r)) if r else None for m, r in zip(marks, rects)]

    # 1) area effects under everything: decorative blur / pixelate, dim, tints, then redaction
    for m, r in zip(marks, rects):
        if m["type"] in DECORATIVE:
            key, default = ("radius", 0.5 * u) if m["type"] == "blur" else ("block", 0.9 * u)
            img = deemphasize(img, r, m["type"], m[key] * scale if key in m else default, u)
    over = Image.new("RGBA", img.size, (0, 0, 0, 0))
    spots = [g for m, g in zip(marks, outlines) if m["type"] == "spotlight"]
    if spots:
        dim = Image.new("RGBA", img.size, (0, 0, 0, int(255 * spec.get("dim", lk.dim))))
        mask = Image.new("L", img.size, 255)
        md = ImageDraw.Draw(mask)
        for g in spots:
            md.rounded_rectangle(g, radius=lk.spot_radius, fill=0)
        over.paste(dim, (0, 0), mask)
    for i, (m, r) in enumerate(zip(marks, rects)):
        if m["type"] == "highlight":
            lk.highlight(over, img, i, r)
    img = Image.alpha_composite(img, over)
    d = ImageDraw.Draw(img)

    for i, (m, r) in enumerate(zip(marks, rects)):
        if m["type"] == "redact":       # solid, never blur: blur can be reversed
            out = m["pad"] * scale if "pad" in m else max(2, 2 * scale)   # default outset: safer
            d.rectangle(grow(r, out), fill=rgba(color_of(m.get("fill", lk.redact_fill(i))), 255))

    # 2) shapes; collect obstacles so labels avoid them
    # placement reads page content (edges = text, lines, icons) before any mark covers it
    gray = img.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    cmap = ContentMap(edges, u)   # what labels and loupes must not cover, built once
    # every mark below goes on its own layer, so one soft shadow can be cast under all of them
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    obstacles, placed, dbg, badges = [], [], [], []
    targets = [r for r, a in zip(rects, auto) if r and not a]   # highlight/spotlight/redact/arrow too
    for i, (m, r, g) in enumerate(zip(marks, rects, outlines)):
        if m["type"] in ("box", "step"):
            if m.get("box", True):
                lk.outline(layer, i, g)
            obstacles.append(g)

    # forced label positions are known up front: reserve them so no badge or auto label lands there
    forced = {}
    for i, m in enumerate(marks):
        if m["type"] in LABELED and m.get("label_at"):
            lx, ly = [v * scale for v in m["label_at"]]
            pw, ph = lk.label_size(m["label"])
            forced[i] = clamp_box([lx - ox, ly - oy, lx - ox + pw, ly - oy + ph], W, H)
            if forced[i]:
                placed.append(forced[i])

    # click / keys-at / magnify have fixed or early positions: lay them out before labels and badges
    own, hard, labels, arrows = {}, [], [], []   # own: box a mark's label and overlap checks refer to
    leaders = []    # loupe connectors (labels avoid them like arrows)
    key_fonts = {i: lk.keys_font(i, "".join(m["keys"])) for i, m in enumerate(marks) if m["type"] == "keys"}
    for i, (m, r) in enumerate(zip(marks, rects)):
        if m["type"] == "click":
            x, y = points.get(i) or ((r[0] + r[2]) / 2, (r[1] + r[3]) / 2)
            draw_click(layer, x, y, lk.color(i), u)
            own[i] = click_geometry(x, y, u)[2]
            obstacles.append(own[i])
            hard.append(own[i])
        elif m["type"] == "keys" and i in points:
            kw, kh = keys_size(key_fonts[i], m["keys"], u)
            x, y = points[i]
            kb = clamp_box([x, y, x + kw, y + kh], W, H)
            if kb is None:
                raise RenderError(f"mark #{i} (keys): the keycaps are wider than the frame; widen the crop")
            draw_keys(layer, kb, m["keys"], key_fonts[i], u)
            own[i] = kb
            placed.append(kb)
            labels.append((i, kb))
    for i, (m, r) in enumerate(zip(marks, rects)):
        if m["type"] == "magnify":
            avoid = [o for o in obstacles + targets if o != r]
            box, sample, path = place_loupe(i, m, r, points.get(i), W, H, u, cmap, avoid,
                                            [o for o in targets + hard if o != r], placed, warnings, leaders)
            draw_loupe(layer, img, sample, box, m.get("shape", "circle"), lk.color(i), u, path, lk.loupe_line)
            if path:
                leaders.append(path)
            own[i] = box
            obstacles.append(box)
            hard.append(box)
            dbg.append(box)

    # badges first: small and tied to their target, so every label can keep off all of them
    badge_of = {}
    for i, (m, r, g) in enumerate(zip(marks, rects, outlines)):
        if m["type"] == "step" and m.get("badge") != "none":
            # may touch its own outline, never the target itself or other marks
            avoid = [o for o in obstacles if o != g] + placed + targets
            if W < 2.5 * u + 4 or H < 2.5 * u + 4:
                raise RenderError(f"mark #{i}: frame {W}x{H} is too small for a step badge; widen the crop")
            b, clean = place_badge(g, 1.25 * u, m.get("badge"), W, H, avoid, edges, r)
            if not clean:
                warnings.append(f"mark #{i}: badge overlaps the target (target touches the frame edge); "
                                "widen the crop")
            badges.append((b, i, str(m["n"])))   # drawn last, above arrows and labels
            badge_of[i] = b
            placed.append(b)

    # automatic labels, planned together (label_plan.py): forced ones are already in `placed`
    jobs = []
    for i, (m, r, g) in enumerate(zip(marks, rects, outlines)):
        if m["type"] in LABELED and m.get("label") and not m.get("label_at"):
            target = own.get(i) or (g if m["type"] != "arrow" else r)
            near = 5 if m["type"] == "arrow" else (3.5 if m.get("arrow", True) else 2)
            jobs.append(Job(i, target, lk.label_size(m["label"]),
                            [o for o in obstacles + targets if o != target and o != r],
                            [o for o in targets + hard if o != target and o != r], near,
                            dict(arrow=m.get("arrow", True), curve=style in ("skitch", "curved"), style=style,
                                 tip=stroke), [badge_of[i]] if i in badge_of else []))
    route = lambda box, j, avoid, lines: route_arrow(edges, box, j.target, u, avoid, style, stroke, lines)
    planned, _ = plan_labels(jobs, place_label, route, cmap, u, placed, leaders)
    for i, lb in planned.items():
        forced[i] = lb
        placed.append(lb)
        if cmap.text(lb) > 0.12:
            warnings.append(f"mark #{i}: no free room for its label, so it covers page text; widen the crop, "
                            "shorten the label or set label_at")

    for i, (m, r, g) in enumerate(zip(marks, rects, outlines)):
        t = m["type"]
        if t in LABELED and m.get("label"):
            target = own.get(i) or (g if t != "arrow" else r)
            lb = forced.get(i)
            if lb is None and m.get("label_at"):
                raise RenderError(f"mark #{i} ({t}): label {m['label']!r} is larger than the frame; widen the "
                                  "crop or shorten the label")
            if lb is None:
                raise RenderError(f"mark #{i} ({t}): no room for label {m['label']!r}. Widen the crop, shorten "
                                  "the label, or set label_at.")
            if m.get("arrow", True):
                mine = [badge_of[i]] if i in badge_of else []
                avoid = [o for o in obstacles + targets + placed
                         if o != target and o != r and o not in mine and not contains(o, target)]
                path = route_arrow(edges, lb, target, u, avoid, style, stroke, [a[3] for a in arrows] + leaders)
                if path_length(path) > 2.8 * u:   # room for a body behind the head
                    arrows.append((i, path[0], path[-1], path))
                    lk.arrow(layer, gray, i, path, style, badge_of.get(i))
            lk.label(layer, img, i, lb, m["label"])
            dbg.append(lb)
            labels.append((i, lb))
        if t == "keys" and i not in points:   # beside its rect, like a label without an arrow
            size = keys_size(key_fonts[i], m["keys"], u)
            kw = dict(near=1.5, arrow=False, lines=[a[3] for a in arrows] + leaders)
            kb = place_label(cmap, r, size, [o for o in obstacles + targets if o != r], placed, u, **kw) or \
                place_label(cmap, r, size, [o for o in targets + hard if o != r], placed, u, **kw)
            if kb is None:
                raise RenderError(f"mark #{i} (keys): no room for the keycaps next to the rect; widen the crop "
                                  "or set 'at'")
            draw_keys(layer, kb, m["keys"], key_fonts[i], u)
            own[i] = kb
            placed.append(kb)
            dbg.append(kb)
            labels.append((i, kb))
        if t == "text":
            x, y = [v * scale for v in m["at"]]
            pw, ph = lk.label_size(m["text"])
            tb = clamp_box([x - ox, y - oy, x - ox + pw, y - oy + ph], W, H)
            if tb is None:
                raise RenderError(f"mark #{i} (text): note {m['text']!r} is larger than the frame; widen the "
                                  "crop or shorten it")
            lk.label(layer, img, i, tb, m["text"])
            placed.append(tb)
            dbg.append(tb)
            labels.append((i, tb))

    for b, i, n in badges:
        lk.badge(layer, img, i, b, n)

    # Skitch-style soft drop shadow under all marks, then the marks themselves
    opacity, dy, blur = lk.shadow
    alpha = layer.getchannel("A").filter(ImageFilter.GaussianBlur(blur * u))
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    shadow.paste(Image.new("RGBA", img.size, (0, 0, 0, 255)), (0, round(dy * u)),
                 alpha.point(lambda v: int(v * opacity)))
    img = Image.alpha_composite(Image.alpha_composite(img, shadow), layer)
    d = ImageDraw.Draw(img)

    warnings += overlap_warnings(marks, rects, own, labels, arrows)
    if debug:
        for r in rects:
            if r:
                d.rectangle(r, outline=(0, 255, 255, 255), width=2)
        for b in dbg:
            d.rectangle(b, outline=(255, 0, 255, 255), width=2)
    if spec.get("frame") not in (None, False):
        from .beautify import apply_frame
        return RenderResult(apply_frame(img.convert("RGB"), spec["frame"], u, source=page), warnings, redactions)
    return RenderResult(img.convert("RGB"), warnings, redactions)
