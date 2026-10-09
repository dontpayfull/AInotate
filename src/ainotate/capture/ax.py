"""UI elements of a desktop app through the macOS Accessibility API: exact boxes for buttons,
toggles, tabs and fields, so marks on a window capture land on the control instead of on OCR'd
text. Needs pyobjc-framework-ApplicationServices and the Accessibility permission for the app
that started ainotate (Terminal, Claude, Cursor...). macOS only.

Boxes come in global screen points (top-left origin), the space CGWindowList reports window
bounds in; `window_targets` turns them into a window capture's coordinates.
"""
from __future__ import annotations

import sys

from .desktop import CaptureError

MAX_NODES = 4000      # an app's tree can be huge (web views); stop walking after this many elements
MAX_DEPTH = 30
LABEL_ATTRS = ("AXTitle", "AXDescription", "AXValue", "AXHelp", "AXIdentifier")


class TargetNotFound(CaptureError):
    """No element matches (exit 6)."""


class AmbiguousText(CaptureError):
    """Several elements match equally well (exit 5)."""


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
    apps = [a for a in NSWorkspace.sharedWorkspace().runningApplications() if a.localizedName()]
    exact = [a for a in apps if a.localizedName().lower() == want]
    hits = exact or [a for a in apps if want in a.localizedName().lower()]
    names = sorted({a.localizedName() for a in hits})
    if not hits:
        raise TargetNotFound(f"no running app named {app!r}")
    if len(names) > 1:
        raise AmbiguousText(f"app {app!r} matches several running apps: {', '.join(names)}; use the full name")
    return int(hits[0].processIdentifier()), hits[0].localizedName()


ATTRS = ("AXRole", *LABEL_ATTRS, "AXPosition", "AXSize", "AXChildren")


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
    return {k: v for k, v in zip(ATTRS, vals) if v is not None and not missing(v)}


def _box(AS, pos, size):
    try:
        ok1, p = AS.AXValueGetValue(pos, AS.kAXValueCGPointType, None)
        ok2, s = AS.AXValueGetValue(size, AS.kAXValueCGSizeType, None)
    except (TypeError, ValueError):
        return None
    if not (ok1 and ok2) or s.width <= 0 or s.height <= 0:
        return None
    return {"x": float(p.x), "y": float(p.y), "w": float(s.width), "h": float(s.height)}


def elements(app: str) -> list[dict]:
    """Every labeled, visible element in the app's windows: [{role, label, rect}], tree order."""
    AS = _ax()
    pid, _ = _pid(app)
    root = AS.AXUIElementCreateApplication(pid)
    queue = [(w, 0) for w in (_attr(AS, root, "AXWindows") or [])]
    out, seen = [], 0
    while queue and seen < MAX_NODES:
        el, depth = queue.pop(0)
        seen += 1
        a = _read(AS, el)
        label = next((v.strip() for k in LABEL_ATTRS for v in [a.get(k)]
                      if isinstance(v, str) and v.strip() and len(v) <= 200), "")
        rect = _box(AS, a["AXPosition"], a["AXSize"]) if "AXPosition" in a and "AXSize" in a else None
        if label and rect:
            out.append({"role": str(a.get("AXRole") or ""), "label": label, "rect": rect})
        if depth < MAX_DEPTH and "AXChildren" in a:
            queue += [(c, depth + 1) for c in a["AXChildren"]]
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
    want = str(target.get("element", "")).strip()
    if not want:
        raise CaptureError("element target needs a non-empty 'element' label")
    role = _role(str(target.get("role", "")))

    def inside(r):
        if not within:
            return True
        cx, cy = r["x"] + r["w"] / 2, r["y"] + r["h"] / 2
        return within["x"] <= cx <= within["x"] + within["w"] and within["y"] <= cy <= within["y"] + within["h"]
    pool = [e for e in els if inside(e["rect"]) and (not role or e["role"] == role)]
    exact = [e for e in pool if e["label"].lower() == want.lower()]
    hits = exact if exact or target.get("exact") else [e for e in pool if want.lower() in e["label"].lower()]
    if "nth" in target:
        n = int(target["nth"])
        if not 0 <= n < len(hits):
            raise TargetNotFound(f"element {want!r}: nth {n} but {len(hits)} match")
        return hits[n]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        near = sorted({e["label"] for e in pool if want.lower()[:3] in e["label"].lower()})[:8]
        raise TargetNotFound(f"no element labeled {want!r}" + (f" (role {role})" if role else "")
                             + (f"; similar: {', '.join(repr(n) for n in near)}" if near else
                                "; list them with `ainotate elements --app ...`"))
    listed = "; ".join(f"[{i}] {e['role']} {e['label']!r}" for i, e in enumerate(hits[:8]))
    raise AmbiguousText(f"{len(hits)} elements match {want!r}: {listed}. Add \"nth\", \"role\" or \"exact\": true")


def window_targets(window: dict, image_size: tuple[int, int], targets: dict) -> dict:
    """Rects of named element targets for a window capture: {"scale": image px per point,
    "rects": {name: {x, y, w, h} in points relative to the window}}. With "scale" in an annotate
    spec, these rects land on the right pixels."""
    b = window["bounds"]
    els = elements(window["app"])
    rects = {}
    for name, t in targets.items():
        e = find(els, t, within=b)["rect"]
        rects[name] = {"x": round(e["x"] - b["x"], 1), "y": round(e["y"] - b["y"], 1),
                       "w": round(e["w"], 1), "h": round(e["h"], 1)}
    return {"scale": round(image_size[0] / b["w"], 4) if b["w"] else 1.0, "rects": rects}
