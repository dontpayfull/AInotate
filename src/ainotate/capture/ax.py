"""UI elements of a desktop app through the macOS Accessibility API: exact boxes for buttons,
toggles, tabs and fields, so marks on a window capture land on the control instead of on OCR'd
text. Needs pyobjc-framework-ApplicationServices and the Accessibility permission for the app
that started ainotate (Terminal, Claude, Cursor...). macOS only.

Boxes come in global screen points (top-left origin), the space CGWindowList reports window
bounds in; `window_targets` turns them into a window capture's coordinates.
"""
from __future__ import annotations

import re
import sys
from collections import deque

from .desktop import CaptureError

MAX_NODES = 4000      # an app's tree can be huge (web views); stop walking after this many elements
MAX_DEPTH = 30
TIMEOUT = 2.0         # seconds per Accessibility call: a hung app must not hang the walk
NEGATION = re.compile(r"(?:\bdon[’']?t|\bdo not|\bnot|\bnever|\bno)\s+$", re.I)


class Elements(list):
    """elements() result; truncated = the walk stopped at MAX_NODES before the whole tree."""
    truncated = False
LABEL_ATTRS = ("AXTitle", "AXDescription", "AXValue", "AXPlaceholderValue", "AXHelp", "AXIdentifier")
# what the user typed is content, not a label: never list it or quote it in an error
EDITABLE = {"AXTextField", "AXTextArea", "AXComboBox", "AXSearchField", "AXSecureTextField"}


class TargetNotFound(CaptureError):
    """No element matches (exit 6)."""


class AmbiguousText(CaptureError):
    """Several elements match equally well (exit 5)."""


class TargetSpecError(ValueError):
    """A malformed element target (exit 2)."""


def check_target(name, t) -> dict:
    """Validate one element target before anything is captured."""
    if not isinstance(t, dict):
        raise TargetSpecError(f"target {name!r}: must be an object like {{\"element\": \"Allow\"}}")
    unknown = set(t) - {"element", "role", "nth", "exact"}
    if unknown:
        raise TargetSpecError(f"target {name!r}: unknown keys {sorted(unknown)}; use element, role, nth, exact")
    if not isinstance(t.get("element"), str) or not t["element"].strip():
        raise TargetSpecError(f"target {name!r}: 'element' must be a non-empty label")
    if t.get("role") is not None and not isinstance(t["role"], str):
        raise TargetSpecError(f"target {name!r}: 'role' must be a string like \"button\"")
    n = t.get("nth")
    if n is not None and (isinstance(n, bool) or not isinstance(n, int) or n < 0):
        raise TargetSpecError(f"target {name!r}: 'nth' must be a whole number >= 0")
    return t


def _ax():
    if sys.platform != "darwin":
        raise CaptureError("UI elements need macOS (Accessibility API)")
    try:
        import ApplicationServices as AS
    except ImportError:
        raise CaptureError("UI elements need pyobjc-framework-ApplicationServices: "
                           "pip install 'ainotate[desktop]'")
    if not AS.AXIsProcessTrusted():
        from ..doctor import responsible_app
        raise CaptureError(
            f"Accessibility permission missing for {responsible_app() or 'the app that runs ainotate'}: "
            "System Settings > Privacy & Security > Accessibility, switch it on, then restart that app "
            "(open \"x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility\")")
    return AS


def _attr(AS, el, name):
    err, val = AS.AXUIElementCopyAttributeValue(el, name, None)
    return val if err == 0 else None


def _pid(app: str) -> tuple[int, str]:
    from AppKit import NSWorkspace
    want = app.strip().lower()
    if not want:
        raise TargetNotFound("no app name given")
    apps = [a for a in NSWorkspace.sharedWorkspace().runningApplications() if a.localizedName()]
    exact = [a for a in apps if a.localizedName().lower() == want]
    hits = exact or [a for a in apps if want in a.localizedName().lower()]
    if not hits:
        raise TargetNotFound(f"no running app named {app!r}")
    regular = [a for a in hits if a.activationPolicy() == 0]      # apps with windows, not helpers
    hits = regular or hits
    if len(hits) > 1:
        listed = ", ".join(f"{a.localizedName()} (pid {a.processIdentifier()})" for a in hits[:8])
        raise AmbiguousText(f"app {app!r} matches several running processes: {listed}; use the full name, "
                            "or capture its window with `ainotate window ID --target` (the window knows its pid)")
    return int(hits[0].processIdentifier()), hits[0].localizedName()


ATTRS = ("AXRole", *LABEL_ATTRS, "AXPosition", "AXSize", "AXChildren")


def _null(v) -> bool:
    try:
        from Foundation import NSNull
    except ImportError:
        return False
    return isinstance(v, NSNull)


def _read(AS, el) -> dict:
    """All attributes the walk needs in one call (one round trip to the app instead of nine).
    Attributes the element lacks come back as AX error values and are dropped."""
    err, vals = AS.AXUIElementCopyMultipleAttributeValues(el, list(ATTRS), 0, None)
    if err != 0 or not vals:
        return {}

    def missing(v):
        try:
            return AS.AXValueGetType(v) == AS.kAXValueAXErrorType
        except (TypeError, ValueError):
            return False   # not an AXValue at all: a string, an array of children...
    return {k: v for k, v in zip(ATTRS, vals) if v is not None and not _null(v) and not missing(v)}


def _box(AS, pos, size):
    try:
        ok1, p = AS.AXValueGetValue(pos, AS.kAXValueCGPointType, None)
        ok2, s = AS.AXValueGetValue(size, AS.kAXValueCGSizeType, None)
    except (TypeError, ValueError):
        return None
    if not (ok1 and ok2) or s.width <= 0 or s.height <= 0:
        return None
    return {"x": float(p.x), "y": float(p.y), "w": float(s.width), "h": float(s.height)}


def _label(a: dict) -> str:
    role = str(a.get("AXRole") or "")
    for k in LABEL_ATTRS:
        v = a.get(k)
        if k == "AXValue" and role in EDITABLE:
            continue
        if isinstance(v, str) and v.strip() and len(v) <= 200:
            return v.strip()
    return ""


def _rect_of(AS, el):
    a = _read(AS, el)
    return _box(AS, a["AXPosition"], a["AXSize"]) if "AXPosition" in a and "AXSize" in a else None


def _same_box(r, b, tol=3) -> bool:
    return all(abs(r[k] - b[k]) <= tol for k in ("x", "y", "w", "h"))


def elements(app: str, pid: int | None = None, window: dict | None = None) -> Elements:
    """Every labeled, visible element in the app's windows: [{role, label, rect}], tree order.
    pid picks one instance of the app; window (bounds {x, y, w, h}) walks only the AX window at
    exactly those bounds, so a same-named button in another window cannot match. A walk cut
    short at MAX_NODES is marked `truncated`."""
    AS = _ax()
    pid = pid or _pid(app)[0]
    root = AS.AXUIElementCreateApplication(pid)
    AS.AXUIElementSetMessagingTimeout(root, TIMEOUT)
    wins = [w for w in (_attr(AS, root, "AXWindows") or []) if not _null(w)]
    if window:      # only the captured window; none matching (a sheet, a moved window) = nothing found
        wins = [w for w in wins if (r := _rect_of(AS, w)) and _same_box(r, window)]
    queue = deque((w, 0) for w in wins)
    out, queued = Elements(), len(queue)
    while queue:
        el, depth = queue.popleft()
        a = _read(AS, el)
        label = _label(a)
        rect = _box(AS, a["AXPosition"], a["AXSize"]) if label and "AXPosition" in a and "AXSize" in a else None
        if label and rect:
            out.append({"role": str(a.get("AXRole") or ""), "label": label, "rect": rect})
        kids = a.get("AXChildren")
        if depth < MAX_DEPTH and kids is not None and not isinstance(kids, (str, bytes)):
            try:
                for c in kids:
                    if queued >= MAX_NODES:
                        out.truncated = True
                        break
                    if not _null(c):
                        queue.append((c, depth + 1))
                        queued += 1
            except TypeError:
                pass        # not an array after all
    return out


ROLES = {r[2:].lower(): r for r in (
    "AXButton", "AXCheckBox", "AXRadioButton", "AXStaticText", "AXTextField", "AXTextArea", "AXPopUpButton",
    "AXMenuButton", "AXMenuItem", "AXMenuBarItem", "AXLink", "AXImage", "AXSlider", "AXComboBox", "AXRow",
    "AXCell", "AXGroup", "AXToolbar", "AXList", "AXOutline", "AXTabGroup", "AXDisclosureTriangle",
    "AXIncrementor", "AXColorWell", "AXWindow", "AXSheet", "AXHeading")}


def _role(r: str) -> str:
    """"button", "check box", "statictext" -> AX role names; AX names pass through."""
    if not r or r.startswith("AX"):
        return r or ""
    key = r.replace("_", "").replace(" ", "").replace("-", "").lower()
    return ROLES.get(key, "AX" + key.capitalize())


def find(els: list[dict], target: dict, within: dict | None = None) -> dict:
    """The one element matching target {"element", "role"?, "exact"?, "nth"?}, inside `within`
    (a window's bounds) when given. Exact label matches win over substring ones."""
    check_target("target", target)
    want = target["element"].strip()
    role = _role(target.get("role") or "")

    def inside(r):
        if not within:
            return True
        cx, cy = r["x"] + r["w"] / 2, r["y"] + r["h"] / 2
        return within["x"] <= cx <= within["x"] + within["w"] and within["y"] <= cy <= within["y"] + within["h"]
    pool = [e for e in els if inside(e["rect"]) and (not role or e["role"] == role)]
    exact = [e for e in pool if e["label"].lower() == want.lower()]
    hits = exact if exact or target.get("exact") else [e for e in pool if _word_match(want, e["label"])]
    if target.get("nth") is not None:
        n = target["nth"]
        if not 0 <= n < len(hits):
            raise TargetNotFound(f"element {want!r}: nth {n} but {len(hits)} match")
        return hits[n]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        near = sorted({e["label"] for e in pool if want.lower()[:3] in e["label"].lower()})[:8]
        cut = (f"; the app's tree is larger than {MAX_NODES} elements and was cut short: capture a smaller "
               "window or use `ainotate locate` (OCR) instead") if getattr(els, "truncated", False) else ""
        raise TargetNotFound(f"no element labeled {want!r}" + (f" (role {role})" if role else "")
                             + (f"; similar: {', '.join(repr(n) for n in near)}" if near else
                                "; list them with `ainotate elements --app ...`") + cut)
    listed = "; ".join(f"[{i}] {e['role']} {e['label']!r}" for i, e in enumerate(hits[:8]))
    raise AmbiguousText(f"{len(hits)} elements match {want!r}: {listed}. Add \"nth\", \"role\" or \"exact\": true")


def _word_match(want: str, label: str) -> bool:
    """want as a whole word or phrase in label, not negated: "Allow" matches "Allow access to
    Photos" but never "Don't Allow" or "Disallow"."""
    for m in re.finditer(r"(?<![\w'’])" + re.escape(want) + r"(?![\w])", label, re.I):
        if not NEGATION.search(label[:m.start()]):
            return True
    return False


def window_targets(window: dict, image_size: tuple[int, int], targets: dict) -> dict:
    """Rects of named element targets for a window capture: {"scale": image px per point,
    "rects": {name: {x, y, w, h} in points relative to the window}}. With "scale" in an annotate
    spec, these rects land on the right pixels."""
    b = window["bounds"]
    if not (b.get("w") and b.get("h")):
        raise CaptureError("the window has no size; capture it again")
    sx, sy = image_size[0] / b["w"], image_size[1] / b["h"]
    uniform = abs(sx - sy) <= max(sx, sy) * 0.01
    kx, ky = (1.0, 1.0) if uniform else (sx, sy)    # uneven: hand out image px with scale 1
    els = elements(window["app"], window.get("pid"), b)
    rects = {}
    for name, t in targets.items():
        e = find(els, t, within=b)["rect"]
        rects[name] = {"x": round((e["x"] - b["x"]) * kx, 1), "y": round((e["y"] - b["y"]) * ky, 1),
                       "w": round(e["w"] * kx, 1), "h": round(e["h"] * ky, 1)}
    return {"scale": round(sx, 4) if uniform else 1.0, "rects": rects}
