"""Spec validation: validate(spec) -> marks, or SpecError listing every problem."""
from __future__ import annotations

import math
import os
import sys

from PIL import Image

from .style import valid_color

RECT_TYPES = {"box", "step", "arrow", "highlight", "spotlight", "redact", "magnify", "blur", "pixelate"}
POINT_TYPES = {"click", "keys"}              # need `rect` or `at`
ALL_TYPES = RECT_TYPES | POINT_TYPES | {"text"}
LABELED = {"box", "step", "arrow", "click"}
DECORATIVE = {"blur", "pixelate"}            # de-emphasis only, never for secrets
BADGE_SPOTS = ("tl", "tr", "bl", "br", "l", "r", "t", "b", "none")
MAGNIFY_SHAPES = ("circle", "rounded")
MAX_MARKS, MAX_LABEL_WORDS = 6, 4
CROP_MODES = ("auto", "tight")               # tight: marks + crop_pad only, no minimum context


class SpecError(Exception):
    """Invalid spec (or an input image that cannot be read); the message lists every problem."""


class AmbiguousText(SpecError):
    """A mark's text target matches several places equally well (CLI exit 5)."""
    def __init__(self, msg, candidates=()):
        super().__init__(msg)
        self.candidates = list(candidates)


class TargetNotFound(SpecError):
    """A mark's target is not on the page (CLI exit 6); the message carries the capture's reason."""
    def __init__(self, msg, near=()):
        super().__init__(msg)
        self.near = list(near)


class TextNotFound(TargetNotFound):
    """A mark's text target is not on the image (OCR); `near` = the closest OCR text."""


def open_image(path):
    """Open the image a spec or a tool points at; SpecError if it is missing or unreadable."""
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        hint = ("Look for it: mdfind -name '<file name>' ; or ask the user to copy it and run: "
                "ainotate clipboard") if sys.platform == "darwin" else \
            "Ask the user for the file, or to copy the image and run: ainotate clipboard"
        raise SpecError(f"image not found: {path}\n{hint}")
    try:
        return Image.open(path)
    except (OSError, Image.UnidentifiedImageError) as e:
        raise SpecError(f"not a readable image: {path} ({e})")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def check_rect(r, where, errs):
    if isinstance(r, dict):
        w, h = r.get("w", r.get("width")), r.get("h", r.get("height"))
        if not all(_num(v) for v in (r.get("x"), r.get("y"), w, h)):
            errs.append(f"{where}: rect dict needs numeric x, y, w|width, h|height")
        elif w <= 0 or h <= 0:
            errs.append(f"{where}: rect has zero/negative size (hidden element?)")
    elif isinstance(r, list) and len(r) == 4 and all(_num(v) for v in r):
        if r[2] <= r[0] or r[3] <= r[1]:
            errs.append(f"{where}: rect must be [x1,y1,x2,y2] with x2>x1, y2>y1 "
                        f"(got {r}; a DOM [x,y,w,h] list must be passed as a dict)")
    else:
        errs.append(f"{where}: rect must be [x1,y1,x2,y2] or {{x,y,w,h}}")


def check_point(p, where, errs):
    if not (isinstance(p, list) and len(p) == 2 and all(_num(v) for v in p)):
        errs.append(f"{where}: must be [x, y]")


def installed_looks() -> dict:
    """Looks from other packages: entry point group "ainotate.looks", name -> entry point."""
    from importlib.metadata import entry_points
    return {e.name: e for e in entry_points(group="ainotate.looks")}


def check_look(look, where="spec: 'look'"):
    """SpecError naming the installed looks unless `look` is "default" or one of them."""
    if look != "default" and (not isinstance(look, str) or look not in installed_looks()):
        raise SpecError(f"{where} is {look!r}, not an installed look; installed looks: "
                        + ", ".join(["default", *sorted(installed_looks())]))


def validate(spec):
    """Return the marks of a valid spec; raise SpecError with every problem otherwise."""
    errs = []
    if not isinstance(spec, dict):
        raise SpecError("spec must be a JSON object")
    if not isinstance(spec.get("input"), str):
        errs.append("spec: missing 'input' (image path)")
    if not (_num(spec.get("scale", 1)) and spec.get("scale", 1) > 0):
        errs.append("spec: 'scale' must be a positive number")
    crop = spec.get("crop")
    if isinstance(crop, str) and crop not in CROP_MODES:
        errs.append(f"spec.crop: rect must be [x1,y1,x2,y2] or {{x,y,w,h}}, or \"auto\" / \"tight\" (got {crop!r})")
    elif crop is not None and not isinstance(crop, str):
        check_rect(crop, "spec.crop", errs)
    if "dim" in spec and not (_num(spec["dim"]) and 0 <= spec["dim"] <= 1):
        errs.append("spec: 'dim' must be a number from 0 to 1")
    if "crop_pad" in spec and not (_num(spec["crop_pad"]) and spec["crop_pad"] >= 0):
        errs.append("spec: 'crop_pad' must be a number >= 0")
    if spec.get("arrow_style", "skitch") not in ("skitch", "straight", "curved", "line"):
        errs.append("spec: 'arrow_style' must be skitch, straight, curved or line")
    if "look" in spec:
        try:
            check_look(spec["look"])
        except SpecError as e:
            errs.append(str(e))
    if "name" in spec and not isinstance(spec["name"], str):
        errs.append("spec: 'name' must be a string")
    if "marks" not in spec:
        errs.append("spec: missing 'marks' (a typo here would save the raw, unredacted shot)")
    marks = spec.get("marks", [])
    if not isinstance(marks, list):
        errs.append("spec: 'marks' must be a list")
        marks = []
    for i, m in enumerate(marks):
        where = f"mark #{i}"
        if not isinstance(m, dict):
            errs.append(f"{where}: must be an object")
            continue
        t = m.get("type")
        if not isinstance(t, str) or t not in ALL_TYPES:
            errs.append(f"{where}: unknown type {t!r}; use one of {sorted(ALL_TYPES)}")
            continue
        where = f"mark #{i} ({t})"
        if "target" in m and not (isinstance(m["target"], dict) or (isinstance(m["target"], str) and m["target"])):
            errs.append(f"{where}: 'target' must be an object like {{\"text\": \"Save\"}}")
        if t in RECT_TYPES:
            if "rect" not in m and "target" not in m:
                errs.append(f"{where}: missing 'rect' (or a 'target' to find it)")
            elif "rect" in m:
                check_rect(m["rect"], where, errs)
        n = m.get("n")
        if t == "step" and not ((isinstance(n, int) and not isinstance(n, bool)) or (isinstance(n, str) and n)):
            errs.append(f"{where}: 'n' must be a step number or a short non-empty string")
        if t == "text":
            if not isinstance(m.get("text"), str) or not m["text"]:
                errs.append(f"{where}: missing 'text'")
            check_point(m.get("at"), f"{where}.at", errs)
        if t in POINT_TYPES:
            if "rect" not in m and "at" not in m and "target" not in m:
                errs.append(f"{where}: needs 'at' [x, y], a 'rect' or a 'target'")
            if "rect" in m:
                check_rect(m["rect"], where, errs)
        if t in POINT_TYPES | {"magnify"} and "at" in m:
            check_point(m["at"], f"{where}.at", errs)
        if t == "magnify":
            if "zoom" in m and not (_num(m["zoom"]) and 1.2 <= m["zoom"] <= 8):
                errs.append(f"{where}: 'zoom' must be a number from 1.2 to 8 (default 2)")
            if m.get("shape", "circle") not in MAGNIFY_SHAPES:
                errs.append(f"{where}: 'shape' must be circle or rounded")
        if t == "keys":
            keys = m.get("keys")
            if not (isinstance(keys, list) and keys and len(keys) <= 6
                    and all(isinstance(k, str) and k.strip() for k in keys)):
                errs.append(f"{where}: 'keys' must be a list of 1-6 key names, e.g. [\"⌘\", \"Shift\", \"K\"]")
        if t in DECORATIVE:
            if m.get("sensitive"):
                errs.append(f"{where}: {t} is decorative only and must not hide sensitive data: "
                            "blur can be reversed. For secrets use redact (solid)")
            key = "radius" if t == "blur" else "block"
            if key in m and not (_num(m[key]) and m[key] > 0):
                errs.append(f"{where}: '{key}' must be a number > 0 (spec units)")
        if t == "arrow" and not m.get("label"):
            errs.append(f"{where}: arrow needs a 'label' to point from")
        if "label" in m and not (isinstance(m["label"], str) and m["label"].strip()):
            errs.append(f"{where}: 'label' must be a non-empty string")
        if ("label" in m or "label_at" in m) and t not in LABELED:
            errs.append(f"{where}: labels are drawn only on box/step/arrow/click; use a text mark")
        if "pad" in m and not (_num(m["pad"]) and m["pad"] >= 0):
            errs.append(f"{where}: 'pad' must be a number >= 0 (spec units)")
        if "badge" in m and m["badge"] not in BADGE_SPOTS:
            errs.append(f"{where}: 'badge' must be one of tl tr bl br l r t b none")
        if "label_at" in m:
            check_point(m["label_at"], f"{where}.label_at", errs)
            if "label" not in m:
                errs.append(f"{where}: 'label_at' needs a 'label' to place")
        for key in ("color", "fill"):
            if key in m and not valid_color(m[key]):
                errs.append(f"{where}: bad {key} {m[key]!r}; use look/bad/good/info, #hex or a css color")
    if spec.get("frame") is not None:
        from .beautify import validate_frame
        errs += validate_frame(spec["frame"])
    if "privacy" in spec:
        from .privacy import privacy_mode
        try:
            privacy_mode(spec)
        except SpecError as e:
            errs += [ln.strip() for ln in str(e).splitlines()[1:]]
    if errs:
        raise SpecError("invalid spec:\n  " + "\n  ".join(errs))
    return marks


def spec_warnings(marks):
    """Style problems in a valid spec's marks that still render: too many marks, long labels,
    `n` on a mark that has no badge."""
    warns = []
    if len(marks) > MAX_MARKS:
        warns.append(f"spec: {len(marks)} marks; more than {MAX_MARKS} is hard to read, split it into two images")
    for i, m in enumerate(marks):
        words = len(m["label"].split()) if isinstance(m.get("label"), str) else 0
        if words > MAX_LABEL_WORDS:
            warns.append(f"mark #{i} ({m['type']}): label {m['label']!r} has {words} words; "
                         f"keep labels to 1-{MAX_LABEL_WORDS} words")
        if "n" in m and m.get("type") != "step":
            warns.append(f"mark #{i} ({m['type']}): n is only used by step marks; use \"type\": \"step\" "
                         "for a numbered badge")
    return warns
