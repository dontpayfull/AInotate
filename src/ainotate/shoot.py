"""Targets -> rects for both inputs, and the one-call web flow.

- `prepare_image_spec(spec)`: an annotate spec on an image whose marks use
  `"target": {"text": "Save", "nth"?, "within"?, "exact"?}` gets them resolved by OCR (spec units,
  so `scale` is honored); `"privacy": "auto"` (default "off" for images) OCRs the image and adds
  solid redact marks. render() calls it first.
- `shoot(spec, draft=False)`: url (or cdp_url + page_url_contains) -> capture with actions ->
  web targets measured in CSS px -> privacy scan of the frozen live page right before the
  screenshot (default "auto"; the capture redoes both if the DOM changed in between) -> render
  -> save. The raw, unredacted capture is a temp file that never outlives shoot().
"""
from __future__ import annotations

import dataclasses
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .privacy import privacy_mode, scan_text_items, summary, to_redact_marks
from .spec import AmbiguousText, SpecError, TargetNotFound, TextNotFound, open_image, validate

SHOOT_KEYS = {"url", "cdp_url", "page_url_contains", "preset", "actions", "full_page", "hide_overlays",
              "timeout_ms", "browser", "user_data_dir"}
IMAGE_TARGET_KEYS = {"text", "nth", "within", "exact"}


@dataclass
class ShootResult:
    paths: list[Path]
    warnings: list[str] = field(default_factory=list)
    capture: Any = None                                    # capture.web.WebCapture, image_path=None
    legend: list[dict] = field(default_factory=list)       # [{"n", "label"}] of the step marks
    redactions: list[str] = field(default_factory=list)    # privacy.summary() lines, never values
    size: tuple = (0, 0)                                   # (w, h) of the saved image


def legend(marks) -> list[dict]:
    """Step numbers and their labels, in spec order: the text that goes under the image."""
    return [{"n": m.get("n"), "label": m.get("label", "")} for m in marks
            if isinstance(m, dict) and m.get("type") == "step"]


def _auto_marks(findings, scale):
    """Solid redact marks for privacy findings; tagged `auto` so render never crops to them."""
    return [dict(m, auto=True) for m in to_redact_marks(findings, scale=scale)]


def _redaction_lines(findings, warnings):
    unread = [f for f in findings if f["kind"] == "unknown_frame"]
    if unread:
        warnings.append(f"privacy: {len(unread)} embedded frame(s) from another site could not be read "
                        "and were NOT redacted; check them in the image")
    return summary([f for f in findings if f["kind"] != "unknown_frame"])


# ---------------------------------------------------------------- images (annotate)

def _image_target(i, m):
    t = m["target"]
    t = {"text": t} if isinstance(t, str) else t
    bad = sorted(set(t) - IMAGE_TARGET_KEYS) if isinstance(t, dict) else []
    if not isinstance(t, dict) or not isinstance(t.get("text"), str) or not t["text"].strip() or bad:
        hint = " (selector/role targets need a live page: use `ainotate shoot`)" if bad else ""
        raise SpecError(f"invalid spec:\n  mark #{i} ({m.get('type')}): an image target is "
                        f'{{"text": "...", "nth"?, "within"?, "exact"?}}{hint}')
    return t


def prepare_image_spec(spec: dict) -> tuple[dict, list[str]]:
    """Resolve OCR text targets and apply automatic privacy on an image spec.
    Returns (spec with rects and redact marks, redaction summary lines). No-op without targets
    or privacy. AmbiguousText (exit 5) / TextNotFound (exit 6) list the candidates; OcrError
    (no engine) propagates."""
    marks = spec.get("marks") if isinstance(spec, dict) else None
    if not isinstance(marks, list):
        return spec, []
    todo = [i for i, m in enumerate(marks) if isinstance(m, dict) and "target" in m and "rect" not in m]
    pol = privacy_mode({"privacy": spec.get("privacy", "off")})
    if not todo and not pol["enabled"]:
        return spec, []
    from . import ocr
    path = os.path.expanduser(spec["input"])
    open_image(path)                       # SpecError (exit 2) before any OCR
    scale = float(spec.get("scale", 1))
    try:
        items = ocr.read(path)
    except ocr.OcrError:
        if pol["enabled"] and not ocr.available_backends():   # never save an unredacted image quietly
            off = "rerun with --privacy off (\"privacy\": \"off\" in the spec) if the image holds nothing sensitive"
            if todo:   # text targets need OCR too: --privacy off alone would still fail
                why = (f", and {len(todo)} mark(s) with a text target cannot be found without one. Nothing was "
                       "saved. Fix it one of two ways:\n")
                off = ("give those marks explicit rect coordinates instead of text targets (read them off "
                       "`ainotate grid` or `ainotate zoom`) and " + off)
            else:
                why = ". Nothing was saved. Fix it one of two ways:\n"
            raise ocr.OcrError("no text reader (OCR engine) is installed, so automatic privacy cannot read "
                               "this image to hide secrets" + why + "1. Install a text reader:\n"
                               + ocr.install_hint() + "\n2. Or " + off + ".") from None
        raise
    out, problems = list(marks), []
    for i in todo:
        m, t = marks[i], _image_target(i, marks[i])
        try:
            rect = ocr.resolve(path, t, scale=scale, items=items)
        except ocr.AmbiguousText as e:
            problems.append((AmbiguousText, f"mark #{i} ({m['type']}): {e}", e.candidates))
            continue
        except ocr.TextNotFound as e:
            problems.append((TextNotFound, f"mark #{i} ({m['type']}): {e}", e.near))
            continue
        except ocr.OcrError as e:          # nth out of range, bad within
            raise SpecError(f"invalid spec:\n  mark #{i} ({m['type']}) target: {e}")
        out[i] = {k: v for k, v in m.items() if k != "target"} | {"rect": rect}
    if problems:
        cls = problems[0][0]
        raise cls("\n".join(p[1] for p in problems), [c for p in problems for c in p[2]])
    findings = scan_text_items(items, pol) if pol["enabled"] else []
    out += _auto_marks(findings, 1 / scale)
    return dict(spec, marks=out), _redaction_lines(findings, [])


# ---------------------------------------------------------------- web (shoot)

def _slug(label) -> str:
    s = unicodedata.normalize("NFKD", label if isinstance(label, str) else "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:40].strip("-")


def _target_name(m: dict, i: int, taken: set) -> str:
    """Capture rect name for mark #i: its label as a slug (unique), else mark<i>."""
    base = _slug(m.get("label")) or f"mark{i}"
    name, k = base, 2
    while name in taken:
        name, k = f"{base}-{k}", k + 1
    taken.add(name)
    return name


def _split(spec: dict):
    """(capture kwargs, render spec without input/scale, {target name: mark index})."""
    if not isinstance(spec, dict):
        raise SpecError("spec must be a JSON object")
    errs = []
    for k in ("input", "scale"):
        if k in spec:
            errs.append(f"spec: shoot captures the image itself; remove {k!r} (or use `ainotate annotate`)")
    if not spec.get("url") and not (spec.get("cdp_url") and spec.get("page_url_contains")):
        errs.append("spec: missing 'url' (or 'cdp_url' + 'page_url_contains' to reuse an open tab)")
    if not isinstance(spec.get("actions", []), list):
        errs.append("spec: 'actions' must be a list")
    if errs:
        raise SpecError("invalid spec:\n  " + "\n  ".join(errs))
    from .capture.web import TargetSpecError, parse_action, parse_target
    marks, targets, index = spec.get("marks"), {}, {}
    taken = {f"mark{i}" for i in range(len(marks))} if isinstance(marks, list) else set()
    for i, m in enumerate(marks if isinstance(marks, list) else []):
        if isinstance(m, dict) and "target" in m and "rect" not in m:
            try:
                t = parse_target(m["target"])
            except TargetSpecError as e:
                errs.append(f"mark #{i} ({m.get('type')}): {e}")
                continue
            taken.discard(f"mark{i}")
            name = _target_name(m, i, taken)
            targets[name], index[name] = t, i
    for j, a in enumerate(spec.get("actions", [])):
        try:
            parse_action(a)
        except TargetSpecError as e:
            errs.append(f"action #{j + 1}: {e}")
    if errs:
        raise SpecError("invalid spec:\n  " + "\n  ".join(errs))
    cap = {k: spec[k] for k in SHOOT_KEYS if k in spec}
    rest = {k: v for k, v in spec.items() if k not in SHOOT_KEYS}
    rest.setdefault("crop", "auto")
    validate(dict(rest, input="<capture>"))   # every spec problem before a browser starts
    return cap, rest, targets, index


def _sanitize_url(url: str) -> str:
    """privacy.sanitize_url when available; otherwise drop userinfo, query and fragment and mask
    what the text detector flags in the rest."""
    from . import privacy
    fn = getattr(privacy, "sanitize_url", None)
    if fn:
        return fn(url)
    from urllib.parse import urlsplit, urlunsplit
    try:
        u = urlsplit(url)
        host = (u.hostname or "") + (f":{u.port}" if u.port else "")
    except ValueError:
        return ""
    clean = urlunsplit((u.scheme, host, u.path, "", ""))
    out, last = [], 0
    for kind, a, b in privacy.find_in_text(clean):
        out += [clean[last:a], privacy.mask(clean[a:b], kind)]
        last = b
    return "".join(out) + clean[last:]


def _frame_url(frame, page_url):
    """Browser chrome without a url shows the captured page's address, sanitized: no
    credentials, query or fragment, secrets in the path masked."""
    if isinstance(frame, dict) and frame.get("chrome") == "browser" and not frame.get("url") and page_url:
        u = _sanitize_url(page_url).split("://", 1)[-1].rstrip("/")
        return dict(frame, url=u[4:] if u.startswith("www.") else u)
    return frame


def _findings(result):
    """Findings from privacy.scan_page; a truncated scan (too many findings to list) is refused."""
    truncated = getattr(result, "truncated", False)
    if isinstance(result, dict):
        truncated, result = result.get("truncated", False), result.get("findings", [])
    result = list(result or [])
    if truncated or any(isinstance(f, dict) and (f.get("truncated") or f.get("kind") == "truncated")
                        for f in result):
        return None
    return result


def _user_mark_warnings(warnings: list[str], user_marks: list) -> list[str]:
    """render counts the automatic redactions in its "N marks" warning; count user marks only."""
    from .spec import MAX_MARKS
    out = [w for w in warnings if not re.match(r"spec: \d+ marks; more than", w)]
    if len(user_marks) > MAX_MARKS:
        out.insert(0, f"spec: {len(user_marks)} marks; more than {MAX_MARKS} is hard to read, "
                      "split it into two images")
    return out


def shoot(spec: dict, draft: bool = False, debug: bool = False) -> ShootResult:
    """Capture `url`, resolve mark targets on the live page, redact, render, save."""
    from .capture import web
    from .output import save
    from .render import render
    cap_kw, rest, targets, index = _split(spec)
    pol = privacy_mode({"privacy": rest.get("privacy", "auto")})
    full_page = bool(cap_kw.get("full_page"))

    def scan(page):   # runs on the frozen page right before the screenshot (redone if the DOM changes)
        from .privacy import scan_page
        try:
            found = _findings(scan_page(page, pol, full_page=full_page))
        except Exception as e:  # noqa: BLE001 - never save an unscanned shot silently
            raise web.CaptureError(f"privacy scan failed ({e}); set \"privacy\": \"off\" to skip it") from None
        if found is None:
            raise web.CaptureError("privacy scan was truncated (too many findings on this page); nothing "
                                   "saved. Capture a smaller area (no full_page, a narrower page) or set "
                                   "\"privacy\": \"off\" if the page holds nothing sensitive")
        return found

    def where(name):
        i = index[name]
        m = rest["marks"][i]
        return f"mark #{i} ({m.get('type')}" + (f" {m['label']!r}" if m.get("label") else "") + ")"

    c = None
    try:
        try:
            c = web.capture(cap_kw.get("url"), cdp_url=cap_kw.get("cdp_url"),
                            page_url_contains=cap_kw.get("page_url_contains"), preset=cap_kw.get("preset", "laptop"),
                            targets=targets, actions=cap_kw.get("actions") or [], full_page=full_page,
                            hide_overlays=cap_kw.get("hide_overlays", True),
                            timeout_ms=cap_kw.get("timeout_ms", 30000), browser=cap_kw.get("browser", "chromium"),
                            user_data_dir=cap_kw.get("user_data_dir"), on_page=scan if pol["enabled"] else None)
        except web.AmbiguousTarget as e:
            msg = str(e)
            for name in index:
                msg = msg.replace(f"target {name!r}:", f"{where(name)}: target")
            raise web.AmbiguousTarget(msg, e.candidates, e.name) from None
        lost = sorted((index[n], n, why) for n, why in c.missing.items() if n in index)
        if lost:
            near = getattr(c, "near", {}) or {}
            raise TargetNotFound("\n".join(f"{where(n)}: target not found: {why}" for _, n, why in lost),
                                 [x for _, n, _ in lost for x in near.get(n, [])])
        at = {i: n for n, i in index.items()}
        marks = [({k: v for k, v in m.items() if k != "target"} | {"rect": c.rects[at[i]]})
                 if at.get(i) in c.rects else m for i, m in enumerate(rest["marks"])]
        findings = c.extra or []
        warnings = list(c.warnings)
        redactions = _redaction_lines(findings, warnings)
        rspec = dict(rest, input=str(c.image_path), scale=c.scale, privacy="off",
                     marks=marks + _auto_marks(findings, 1))
        if "frame" in rspec:
            rspec["frame"] = _frame_url(rspec["frame"], c.url)
        res = render(rspec, debug=debug)
    finally:
        if c is not None and c.image_path:   # the raw capture is unredacted: never left behind
            Path(c.image_path).unlink(missing_ok=True)
    warnings += _user_mark_warnings(res.warnings, rest["marks"])
    paths = save(res.image, rest.get("name", ""), draft=draft or debug, on_warning=warnings.append)
    public = dataclasses.replace(c, image_path=None, extra=None, url=_sanitize_url(c.url))
    return ShootResult(paths, warnings, public, legend(rest["marks"]), redactions, res.image.size)
