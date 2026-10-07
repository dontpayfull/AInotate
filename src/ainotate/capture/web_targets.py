"""Target resolution, candidate locator finding, text fitting and layout arrangement for web capture."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from .web_cdp import _first_line, _pw
from .web_geom import (_Ambiguous, _mask_text, closest_texts, describe, frame_content_box,
                       inside, intersect, needs_unique, offset_rect, plan_scroll, rect,
                       to_capture_coords, TargetSpecError)
from .web_js import (_DESCRIBE_ALL, _FRAME_BOX, _MEASURE_ALL, _MEASURE_ONE,
                     _SCROLL_INTO_VIEW, _STATE, _TEXT_BOX, _TOP_INSET, _VISIBLE_TEXTS)

MAX_CANDIDATES, MAX_NEAR = 8, 5


@dataclass
class _Cand:
    frame: Any
    handle: Any
    spec: dict | None = None   # the target dict (drives `fit`)


def _frame_geom(frame, cache: dict):
    """(origin_x, origin_y, clip rect | None, visible) of a frame in main-viewport CSS px."""
    if frame in cache:
        return cache[frame]
    if frame.parent_frame is None:
        geom = (0.0, 0.0, None, True)
    else:
        px, py, pclip, pvis = _frame_geom(frame.parent_frame, cache)
        try:
            fe = frame.frame_element()
            raw = fe.evaluate(_FRAME_BOX)
        except Exception:
            geom = (0.0, 0.0, None, False)
        else:
            box = offset_rect(frame_content_box(raw), px, py)
            clip = intersect(box, pclip) if pclip else box
            geom = (box["x"], box["y"], clip, bool(raw["visible"] and pvis and clip))
    cache[frame] = geom
    return geom


def _locators(frame, t: dict, loose: bool):
    root = frame.locator(t["within"]) if "within" in t else frame
    if "role" in t:
        if "name" not in t:
            return root.get_by_role(t["role"])
        return root.get_by_role(t["role"], name=t["name"], exact=not loose)
    if "selector" in t and "text" in t:
        pat = re.compile(r"^\s*" + re.escape(t["text"]) + r"\s*$")
        return root.locator(t["selector"], has_text=t["text"] if loose else pat)
    if "selector" in t:
        return root.locator(t["selector"])
    # text alone: Playwright's substring match (case-insensitive, whitespace-normalized) by default
    return root.get_by_text(t["text"], exact=bool(t.get("exact")))


def _candidates(vis, cache: dict) -> list[dict]:
    """Up to MAX_CANDIDATES visible matches: masked text, rect (CSS px, viewport) and a short DOM path."""
    out, info = [], {}
    for frame, loc, i, it in vis[:MAX_CANDIDATES]:
        if id(loc) not in info:
            try:
                info[id(loc)] = loc.evaluate_all(_DESCRIBE_ALL)
            except Exception:
                info[id(loc)] = []
        d = info[id(loc)][i] if i < len(info[id(loc)]) else {"text": "", "path": ""}
        ox, oy = _frame_geom(frame, cache)[:2]
        out.append({"text": _mask_text(d["text"])[:80], "rect": offset_rect(it, ox, oy), "path": _mask_text(d["path"])[:80]})
    return out


def _passes(t: dict) -> list[bool]:
    """Exact first, then loose, for role+name and selector+text (unless exact:true)."""
    if (("role" in t and "name" in t) or ("selector" in t and "text" in t)) and not t.get("exact"):
        return [False, True]
    return [False]


def _resolve(page, t: dict, unique: bool = False) -> tuple[_Cand | None, str]:
    """First visible match in document order (main frame, then frames), honoring nth / first.
    With `unique` (and a text/selector target without nth/first), several visible matches raise
    _Ambiguous instead of picking one."""
    _, PWError, _ = _pw()
    hidden: list[str] = []
    found = 0
    for loose in _passes(t):
        vis: list[tuple[Any, Any, int, dict]] = []
        cache: dict = {}
        for frame in page.frames:
            if frame.is_detached() or not _frame_geom(frame, cache)[3]:
                continue
            loc = _locators(frame, t, loose)
            try:
                items = loc.evaluate_all(_MEASURE_ALL)
            except PWError as e:
                msg = _first_line(e)
                if frame.parent_frame is None and ("selector" in msg.lower() or "parse" in msg.lower()):
                    raise TargetSpecError(f"bad selector in target {describe(t)}: {msg}") from None
                continue
            for i, it in enumerate(items):
                (vis.append((frame, loc, i, it)) if it["visible"] else hidden.append(it["reason"]))
        found = len(vis)
        if len(vis) > 1 and unique and needs_unique(t):
            raise _Ambiguous(len(vis), _candidates(vis, cache))
        if vis:
            n = t.get("nth", 0)
            if -len(vis) <= n < len(vis):
                frame, loc, i, _ = vis[n]
                h = loc.nth(i).element_handle(timeout=2000)
                return _Cand(frame, h, t), ""
    if found:
        return None, f"only {found} visible match(es), nth={t.get('nth', 0)} is out of range"
    if hidden:
        return None, f"{len(hidden)} match(es) but none visible ({hidden[0]})"
    if "text" in t and "selector" not in t:
        return None, ("no element contains this text exactly (exact:true; remove it for a substring match)"
                      if t.get("exact") else
                      "no element matches (substring match by default; set exact:true for exact)")
    return None, "no element matches"


def _near_texts(page, t: dict) -> list[str]:
    """Closest visible texts (masked) for a missing text / role-name target."""
    q = t.get("text") or t.get("name")
    if not q:
        return []
    texts: list[str] = []
    cache: dict = {}
    for frame in page.frames:
        try:
            if not frame.is_detached() and _frame_geom(frame, cache)[3]:
                texts += frame.evaluate(_VISIBLE_TEXTS)
        except Exception:
            continue
    return [_mask_text(x) for x in closest_texts(q, texts, MAX_NEAR)]


def _find(page, t: dict, wait_ms: float, unique: bool = False) -> tuple[_Cand | None, str, list[str]]:
    """(candidate, why not found, closest visible texts). Raises _Ambiguous (see _resolve)."""
    deadline = time.monotonic() + wait_ms / 1000
    while True:
        cand, why = _resolve(page, t, unique)
        if cand:
            return cand, "", []
        if time.monotonic() >= deadline:
            near = _near_texts(page, t) if why.startswith("no element") else []
            if near:
                why += "; closest visible texts: " + ", ".join(repr(x) for x in near)
            return None, why, near
        page.wait_for_timeout(250)


def _fit_text(c: _Cand, m: dict, ox: float, oy: float) -> None:
    """Text targets (and named roles) on a much wider element: shrink the rect to the text itself."""
    t = c.spec or {}
    fit = t.get("fit", "auto")
    if fit == "element" or not m["visible"] or "selector" in t and "text" not in t:
        return
    if "role" in t and "name" not in t and fit == "auto":
        return
    try:
        tb = c.handle.evaluate(_TEXT_BOX, {"text": t.get("text") or t.get("name"), "fit": fit})
    except Exception:
        return
    if not tb:
        return
    tr = intersect(offset_rect(tb, ox, oy), m["rect"])
    if tr and (fit == "text" or tr["w"] < 0.6 * m["rect"]["w"]):
        m["rect"] = tr


def _measure(page, cands: dict[str, _Cand]) -> dict[str, dict]:
    cache: dict = {}
    out = {}
    for name, c in cands.items():
        try:
            m = c.handle.evaluate(_MEASURE_ONE)
        except Exception as e:
            out[name] = {"error": f"element went away ({_first_line(e)})"}
            continue
        ox, oy, clip, fvis = _frame_geom(c.frame, cache)
        m["rect"] = offset_rect(m, ox, oy)
        m["clip"] = clip
        _fit_text(c, m, ox, oy)
        out[name] = m
    return out


def _in_view(m: dict, view: dict) -> bool:
    if "error" in m or not m["visible"]:
        return False
    r = m["rect"]
    vbox = rect(0, 0, view["w"], view["y"] + view["h"]) if m["fixed"] else view
    return inside(r, vbox) and (m["clip"] is None or inside(r, m["clip"]))


def _arrange(page, cands: dict[str, _Cand], full_page: bool) -> None:
    """Scroll so every target is visible in one viewport (if possible)."""
    if full_page:
        page.evaluate("() => window.scrollTo({left: 0, top: 0, behavior: 'instant'})")
        for c in cands.values():   # targets inside iframes / inner scrollers still need their own scroll
            m = _measure(page, {"_": c})["_"]
            if "error" not in m and (m["inner"] or (m["clip"] and not inside(m["rect"], m["clip"]))):
                c.handle.evaluate(_SCROLL_INTO_VIEW)
        page.evaluate("() => window.scrollTo({left: 0, top: 0, behavior: 'instant'})")
        return
    for attempt in range(4):
        st = page.evaluate(_STATE)
        inset = page.evaluate(_TOP_INSET)
        view = rect(0, inset, st["vw"], st["vh"] - inset)
        ms = _measure(page, cands)
        off = [n for n, m in ms.items() if not _in_view(m, view) and "error" not in m and m["visible"]]
        if not off:
            return
        if attempt == 0:
            for n in reversed(off):          # first target ends up centered
                cands[n].handle.evaluate(_SCROLL_INTO_VIEW)
        else:
            names = [n for n, m in ms.items() if "error" not in m and m["visible"] and not m["fixed"]]
            spans = [(ms[n]["rect"]["y"] + st["sy"], ms[n]["rect"]["y"] + ms[n]["rect"]["h"] + st["sy"])
                     for n in names]
            s = plan_scroll(spans, inset, st["vh"], st["dh"] - st["vh"])
            if abs(s - st["sy"]) < 1:
                return
            page.evaluate(f"() => window.scrollTo({{left: scrollX, top: {s}, behavior: 'instant'}})")
        page.wait_for_timeout(300)


def _final_rects(ms: dict, st: dict, inset: float, full_page: bool):
    rects, missing, warns = {}, {}, []
    view = rect(0, 0, st["dw"], st["dh"]) if full_page else rect(0, inset, st["vw"], st["vh"] - inset)
    fully_viewport = rect(0, 0, st["vw"], st["vh"])
    for name, m in ms.items():
        if "error" in m:
            missing[name] = m["error"]
            continue
        if not m["visible"]:
            missing[name] = f"not visible after scrolling ({m['reason']})"
            continue
        r = m["rect"]
        if full_page:
            rects[name] = to_capture_coords(r, (st["sx"], st["sy"]), True)
            continue
        if _in_view(m, view):
            rects[name] = to_capture_coords(r, (0, 0), False)
            continue
        vis = intersect(r, m["clip"] or fully_viewport)
        vis = vis and intersect(vis, fully_viewport)
        if vis and (r["h"] > st["vh"] - inset or r["w"] > st["vw"]):
            rects[name] = vis
            warns.append(f"{name}: element is larger than the viewport; rect clipped to the visible part")
        elif vis and inside(r, fully_viewport) and r["y"] < inset:
            rects[name] = to_capture_coords(r, (0, 0), False)
            warns.append(f"{name}: partly under a sticky header")
        else:
            missing[name] = ""
    for name in [n for n, why in missing.items() if not why]:
        missing[name] = ("off-screen: cannot share one viewport with " + ", ".join(rects)
                         + "; capture it separately or use full_page") if rects else \
            "off-screen: could not be scrolled into the viewport"
    return rects, missing, warns
