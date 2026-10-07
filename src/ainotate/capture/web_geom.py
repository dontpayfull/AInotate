"""Pure helpers (no browser): presets, errors, target/action parsing, rect and scroll math."""
from __future__ import annotations

import difflib
import json
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

IPHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")


@dataclass(frozen=True)
class Preset:
    width: int
    height: int
    scale: float
    mobile: bool = False
    user_agent: str | None = None


PRESETS: dict[str, Preset] = {
    "laptop": Preset(1280, 800, 2),
    "wide": Preset(1600, 1000, 2),
    "phone": Preset(390, 844, 3, True, IPHONE_UA),
}


class CaptureError(Exception):
    """The page could not be captured (browser, network, CDP, screenshot)."""


class TargetSpecError(CaptureError, ValueError):
    """A target or action dict is malformed (a usage error, not a page problem)."""


def _ambiguous_base():
    try:
        from ..spec import AmbiguousText
    except ImportError:   # spec needs Pillow; capture alone must still import
        class AmbiguousText(ValueError):
            def __init__(self, msg, candidates=()):
                super().__init__(msg)
                self.candidates = list(candidates)
    return AmbiguousText


class AmbiguousTarget(_ambiguous_base()):
    """A text/selector target has several visible matches and no `nth` / `"first": true`
    (CLI exit 5, like OCR). `.candidates` = up to 8 `{"text" (masked), "rect" (CSS px, viewport),
    "path"}` dicts; `.name` = the target name."""

    def __init__(self, msg, candidates=(), name=""):
        super().__init__(msg, candidates)
        self.name = name


# ---------------------------------------------------------------- specs (pure)
_TARGET_KEYS = {"text", "selector", "role", "name", "within", "nth", "exact", "fit", "first"}


def parse_target(t: Any) -> dict[str, Any]:
    """Validate and normalize a target dict; a bare string means {"text": s}.
    No `nth` default: a text/selector target without `nth` (or `"first": true`) must be unique."""
    if isinstance(t, str):
        t = {"text": t}
    if not isinstance(t, dict):
        raise TargetSpecError(f"target must be an object, got {type(t).__name__}")
    bad = set(t) - _TARGET_KEYS
    if bad:
        raise TargetSpecError(f"unknown target key(s) {sorted(bad)}; allowed: {sorted(_TARGET_KEYS)}")
    kinds = [k for k in ("text", "selector", "role") if k in t]
    if not kinds:
        raise TargetSpecError(f"target needs 'text', 'selector' or 'role': {t}")
    if "role" in t and len(kinds) > 1:
        raise TargetSpecError(f"'role' cannot be combined with 'text'/'selector': {t}")
    if "name" in t and "role" not in t:
        raise TargetSpecError(f"'name' only works with 'role': {t}")
    for k in ("text", "selector", "role", "name", "within"):
        if k in t and (not isinstance(t[k], str) or not t[k].strip()):
            raise TargetSpecError(f"target '{k}' must be a non-empty string: {t}")
    if "nth" in t and (isinstance(t["nth"], bool) or not isinstance(t["nth"], int)):
        raise TargetSpecError(f"target 'nth' must be an integer: {t}")
    for k in ("exact", "first"):
        if k in t and not isinstance(t[k], bool):
            raise TargetSpecError(f"target '{k}' must be true/false: {t}")
    if t.get("first") and "nth" in t:
        raise TargetSpecError(f"target: use 'nth' or 'first', not both: {t}")
    if "fit" in t and t["fit"] not in ("auto", "text", "element"):
        raise TargetSpecError(f"target 'fit' must be \"auto\", \"text\" or \"element\": {t}")
    return dict(t)


def parse_target_arg(arg: str) -> tuple[str, dict[str, Any]]:
    """CLI form `name=JSON` (or `name=plain text`, read as {"text": ...})."""
    name, sep, raw = arg.partition("=")
    if not sep or not name.strip():
        raise TargetSpecError(f"--target must look like name=JSON, got {arg!r}")
    raw = raw.strip()
    try:
        value = json.loads(raw) if raw[:1] in "{[\"" else raw
    except json.JSONDecodeError as e:
        raise TargetSpecError(f"--target {name}: invalid JSON ({e.msg})") from None
    return name.strip(), parse_target(value)


def parse_action(a: Any) -> dict[str, Any]:
    if not isinstance(a, dict):
        raise TargetSpecError(f"action must be an object, got {a!r}")
    kinds = [k for k in ("click", "fill", "wait", "scroll_to", "wait_ms") if k in a]
    if len(kinds) != 1:
        raise TargetSpecError(f"action needs exactly one of click/fill/wait/scroll_to/wait_ms: {a}")
    kind = kinds[0]
    extra = set(a) - {kind, "value", "timeout_ms"}
    if extra or ("value" in a and kind != "fill"):
        raise TargetSpecError(f"unexpected key(s) in action {a}")
    if kind == "wait_ms":
        ms = a["wait_ms"]
        if isinstance(ms, bool) or not isinstance(ms, (int, float)) or ms < 0:
            raise TargetSpecError(f"wait_ms must be a non-negative number: {a}")
        return {"kind": kind, "ms": float(ms)}
    if kind == "fill" and not isinstance(a.get("value"), str):
        raise TargetSpecError(f"fill needs a string 'value': {a}")
    out = {"kind": kind, "target": parse_target(a[kind])}
    if kind == "fill":
        out["value"] = a["value"]
    if "timeout_ms" in a:
        out["timeout_ms"] = float(a["timeout_ms"])
    return out


def describe(t: dict[str, Any]) -> str:
    core = (f"role={t['role']}" + (f" name={t['name']!r}" if "name" in t else "") if "role" in t
            else " ".join(f"{k}={t[k]!r}" for k in ("selector", "text") if k in t))
    return core + (f" within {t['within']!r}" if "within" in t else "") + (
        f" nth={t['nth']}" if "nth" in t else "") + (" exact" if t.get("exact") else "")


def needs_unique(t: dict[str, Any]) -> bool:
    """Text and selector targets must match one visible element unless `nth` or `first` picks one.
    Role targets keep their exact-name-first rule."""
    return "role" not in t and "nth" not in t and not t.get("first")


def closest_texts(query: str, texts: list[str], n: int = 5) -> list[str]:
    """The visible texts most like `query` (case-insensitive, containment first), best first."""
    q = " ".join(query.split()).lower()
    seen, scored = set(), []
    for raw in texts:
        s = " ".join(str(raw).split())
        k = s.lower()
        if not s or k in seen:
            continue
        seen.add(k)
        ratio = difflib.SequenceMatcher(None, q, k[:max(len(q) * 3, 40)]).ratio()
        words = set(re.findall(r"\w+", q))
        overlap = len(words & set(re.findall(r"\w+", k))) / max(1, len(words))
        scored.append((max(ratio, 0.5 + overlap / 2 if overlap else 0), -len(s), s))
    scored.sort(reverse=True)
    return [s for score, _, s in scored[:n] if score >= 0.35]


# ---------------------------------------------------------------- rect math (pure)
def rect(x: float, y: float, w: float, h: float) -> dict[str, float]:
    return {"x": round(x, 1), "y": round(y, 1), "w": round(w, 1), "h": round(h, 1)}


def offset_rect(r: dict, dx: float, dy: float) -> dict[str, float]:
    return rect(r["x"] + dx, r["y"] + dy, r["w"], r["h"])


def intersect(a: dict, b: dict) -> dict[str, float] | None:
    x1, y1 = max(a["x"], b["x"]), max(a["y"], b["y"])
    x2, y2 = min(a["x"] + a["w"], b["x"] + b["w"]), min(a["y"] + a["h"], b["y"] + b["h"])
    return rect(x1, y1, x2 - x1, y2 - y1) if x2 - x1 >= 1 and y2 - y1 >= 1 else None


def inside(r: dict, box: dict, tol: float = 1.0) -> bool:
    return (r["x"] >= box["x"] - tol and r["y"] >= box["y"] - tol
            and r["x"] + r["w"] <= box["x"] + box["w"] + tol
            and r["y"] + r["h"] <= box["y"] + box["h"] + tol)


def frame_content_box(raw: dict) -> dict[str, float]:
    """Iframe content box in the parent's viewport: border-box origin + border + padding."""
    return rect(raw["x"] + raw["cl"] + raw["pl"], raw["y"] + raw["ct"] + raw["pt"],
                raw["cw"] - raw["pl"] - raw["pr"], raw["ch"] - raw["pt"] - raw["pb"])


def to_capture_coords(r: dict, scroll: tuple[float, float], full_page: bool) -> dict[str, float]:
    """Viewport rect -> screenshot rect (a full-page shot starts at the document origin)."""
    return offset_rect(r, scroll[0], scroll[1]) if full_page else rect(r["x"], r["y"], r["w"], r["h"])


def plan_scroll(spans: list[tuple[float, float]], view_top: float, view_h: float,
                max_scroll: float, margin: float = 12.0) -> float:
    """Pick a window scrollY showing as many document spans (top, bottom) as possible.

    Earlier spans have priority; ties prefer the included ones centered in the free view
    (the part under a sticky header of height `view_top` does not count).
    """
    if not spans:
        return 0.0
    free = view_h - view_top
    cands = set()
    for t, b in spans:
        cands |= {t - view_top - margin, b + margin - view_h, (t + b) / 2 - view_top - free / 2}
    tops, bots = [t for t, _ in spans], [b for _, b in spans]
    cands.add((min(tops) + max(bots)) / 2 - view_top - free / 2)
    best, best_key = 0.0, None
    for s in cands:
        s = min(max(0.0, s), max(0.0, max_scroll))
        lo, hi = s + view_top, s + view_h
        flags = tuple(t >= lo - 1 and b <= hi + 1 for t, b in spans)
        inc = [(t, b) for (t, b), f in zip(spans, flags, strict=True) if f]
        mid = (min(t for t, _ in inc) + max(b for _, b in inc)) / 2 if inc else lo
        key = (sum(flags), flags, -abs(mid - (lo + hi) / 2))
        if best_key is None or key > best_key:
            best, best_key = s, key
    return best


def png_size(path: Path) -> tuple[int, int]:
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise CaptureError(f"screenshot is not a PNG: {path}")
    return struct.unpack(">II", head[16:24])


def nice_scale(img_w: int, css_w: float, dpr: float) -> float:
    s = img_w / css_w if css_w else dpr
    return float(dpr) if abs(s - dpr) < 0.01 else round(s, 4)


# ---------------------------------------------------------------- target errors (pure)
def _mask_text(text: str) -> str:
    """privacy.mask_text when available; otherwise mask what privacy.find_in_text flags."""
    from .. import privacy
    fn = getattr(privacy, "mask_text", None)
    if fn:
        return fn(text)
    out, last = [], 0
    for kind, a, b in privacy.find_in_text(text):
        out += [text[last:a], privacy.mask(text[a:b], kind)]
        last = b
    return "".join(out) + text[last:]


class _Ambiguous(Exception):
    """Internal: several visible matches for a target that must be unique."""

    def __init__(self, count: int, candidates: list[dict]):
        super().__init__(count)
        self.count, self.candidates = count, candidates


def _ambiguity_message(label: str, t: dict, e: _Ambiguous) -> str:
    lines = [f"{label} {describe(t)}: {e.count} visible matches; pick one with \"nth\" (0-based, "
             f"document order), narrow it with \"within\" or \"exact\": true, or set \"first\": true"]
    for k, c in enumerate(e.candidates):
        r = c["rect"]
        lines.append(f"  [{k}] {c['text']!r} at x={r['x']:g} y={r['y']:g} {r['w']:g}x{r['h']:g}"
                     + (f"  ({c['path']})" if c["path"] else ""))
    if e.count > len(e.candidates):
        lines.append(f"  ... and {e.count - len(e.candidates)} more")
    return "\n".join(lines)
