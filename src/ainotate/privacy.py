"""Automatic privacy redaction: find secrets and personal data on a page or in a screenshot.

Two detectors, one finding format::

    {"kind": "email", "x": 10, "y": 20, "w": 120, "h": 16, "units": "css" | "image",
     "source": "dom" | "ocr", "preview": "a***@d***.com"}

- `DOM_SCAN_JS`: a JavaScript function `(opts) => findings` for Playwright `page.evaluate(js, opts)`
  (see `scan_page`) or BrowserOS neo `evaluate({code: neo_code(opts)})`. CSS px, viewport-relative.
- `scan_text_items(items)`: OCR words `{"text","x","y","w","h","conf","level"}` in image px.

Both use the same regexes (`PATTERNS`, shared with the JS as JSON) and the same validators.
A preview never holds the full value. Kinds: see `KINDS`.

Guarantees: overlapping matches are UNIONED (merged span, most severe kind per `SEVERITY`), so a
custom or weaker pattern never shrinks a built-in match. Allow lists apply before the union and to
classified DOM fields too (password inputs always redacted). OCR findings cover whole OCR items,
never a slice of a word. No truncation: the DOM scan reads all text (chunked) and returns every
finding; there is no cap and no `truncated` flag. `name_field` / `address_field` come ONLY from DOM
form-field classification (type, autocomplete, name/id/label); OCR and free text never yield them.
Truncated emails ("andrei@do…", "andrei@dontpay...", "andr…@gmail.com") count as `email`.
For messages: `mask_text(s, policy=None)` and `sanitize_url(url)` (no userinfo/query/fragment).
"""
from __future__ import annotations

import base64
import json
import math
import re
from collections import Counter
from urllib.parse import unquote, urlsplit, urlunsplit

from .privacy_js import _CONTENT_BOX, _DOM, _JS, build_dom_scan_js  # noqa: F401
from .spec import SpecError

KINDS = ("password_field", "api_key", "jwt", "token", "credit_card", "iban", "email", "phone",
         "ip", "name_field", "address_field", "custom", "unknown_frame")
# unknown_frame = a cross-origin iframe nobody could read: reported, not blacked out by default
# name_field / address_field = DOM form-field classification only (never OCR or free text)
AUTO_KINDS = frozenset(KINDS) - {"unknown_frame"}
# most severe first: when overlapping matches merge, the merged finding takes the earliest kind
SEVERITY = ("password_field", "api_key", "jwt", "token", "credit_card", "iban", "email", "phone",
            "ip", "custom", "name_field", "address_field", "unknown_frame")
_TRUNC = re.compile(r"\u2026|\.{2,}")       # an ellipsis: "…" or "..." (truncated text)

_NB = r"(?<![\w#$€£.,/+-])"      # not glued to a word, price, order id or decimal
_NA = r"(?![\w/]|[.,]\d)"
_KEYCTX = re.compile(r"(?i)(key|token|secret|auth|signature|password|session|bearer|cookie)\W{0,12}[\w ]{0,20}$")
_DATE = re.compile(r"^\d{1,4}[./-]\d{1,2}[./-]\d{1,4}$")
_ORDERCTX = re.compile(r"(?i)(order|invoice|ref|sku|tracking|id|#|no\.?|nr\.?)\s*[:#]?\s*$")
_FILE_TLD = {"png", "jpg", "jpeg", "gif", "svg", "webp", "js", "css", "html", "json", "pdf", "txt"}
_KV_STOP = {"none", "null", "true", "false", "required", "optional", "hidden", "yes", "no", "n/a",
            "undefined", "empty", "example", "reset", "forgot", "changed", "expired", "invalid"}

# (kind, validator, flags, source). Written in the regex subset Python `re` and JS share; a
# capture group 1, when present, is the part to redact. Order = priority on overlaps.
PATTERNS = [
    ("jwt", "jwt", "", r"(?<![\w-])eyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}"),
    ("api_key", "any", "", r"(?<![\w-])(?:sk-(?:proj-|ant-(?:api\d\d-)?|svcacct-|or-v1-)?[\w-]{20,}"
     r"|(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{10,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_\w{30,}"
     r"|xox[bpasrec]-[A-Za-z0-9-]{10,}|(?:AKIA|ASIA)[0-9A-Z]{16}|AIza[\w-]{35}|glpat-[\w-]{20,}"
     r"|hf_[A-Za-z0-9]{30,}|npm_[A-Za-z0-9]{36}|shp(?:at|ss|ca|pa)_[a-fA-F0-9]{32}"
     r"|SG\.[\w-]{16,}\.[\w-]{16,}|xai-[A-Za-z0-9]{40,})(?![\w-])"),
    ("token", "bearer", "i", r"(?<![A-Za-z])(?:Bearer|Basic)\s+([\w.~+/-]{16,}=*)"),
    ("token", "kv", "i", r"(?<![A-Za-z0-9])(?:password|passwd|pwd|passcode|client[_ -]?secret|secret"
     r"|api[_ -]?key|apikey|access[_ -]?key|access[_ -]?token|auth[_ -]?token|refresh[_ -]?token"
     r"|token|private[_ -]?key)[\"']?\s*[:=]\s*[\"']?([^\s\"',;&<>]{4,})"),
    ("email", "email", "", r"(?<![\w.%+-])[A-Za-z0-9][\w.%+-]*@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
     r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*\.[A-Za-z]{2,24}(?![\w-])"),
    # truncated in a narrow column / sidebar: "andrei@do…", "andrei@dontpay...", "andr…@gmail.com"
    ("email", "email", "", r"(?<![\w.%+-])(?:[A-Za-z0-9][\w.%+-]*@[A-Za-z0-9.-]*(?:\u2026|\.\.)[\w.-]*"
     r"|(?:[A-Za-z0-9][\w.%+-]*)?\u2026@[A-Za-z0-9][A-Za-z0-9.-]*)"),
    ("credit_card", "card", "", _NB + r"(?:\d[ -]?){12,18}\d" + _NA),
    ("iban", "iban", "i", r"(?<![A-Za-z0-9])[A-Za-z]{2}\d{2}(?:[A-Za-z0-9]{11,30}|(?: [A-Za-z0-9]{4}){2,7}(?: [A-Za-z0-9]{1,3})?)(?![A-Za-z0-9])"),
    ("phone", "phone", "", r"(?<![\w#$€£.,/+-])\+\d{1,3}(?:[ .-]?\(?\d{1,4}\)?){2,5}" + _NA),
    ("phone", "phone", "", _NB + r"\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}" + _NA),
    ("phone", "phone", "", _NB + r"0[1-9]\d{1,2}[ .-]?\d{3}[ .-]?\d{3,4}" + _NA),
    ("phone", "phone", "i", r"(?<![A-Za-z])(?:tel|phone|mobile|mob|fax|telefon)\.?\s*[:.]?\s*(\+?\d[\d ()./-]{5,18}\d)"),
    ("ip", "ip", "", r"(?<![\w.])(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?![\w]|\.\d)"),
    ("token", "entropy", "", r"(?<![\w+/=-])[\w+/-]{24,}={0,2}(?![\w+/=-])"),
]


# ------------------------------------------------------------------ validators (mirrored in JS)
def _entropy(s):
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in Counter(s).values()) if n else 0.0


def _cls(ch):
    return "d" if ch.isdigit() else "u" if ch.isupper() else "l" if ch.islower() else ""


def _mod97(s):
    r = 0
    for ch in s:
        for d in (str(int(ch, 36))):
            r = (r * 10 + int(d)) % 97
    return r


def _v_any(t, ms, s, e, v, k):
    return k


def _v_jwt(t, ms, s, e, v, k):
    head, payload = v.split(".")[:2]
    try:
        h = base64.urlsafe_b64decode(head + "=" * (-len(head) % 4)).decode("utf-8", "replace")
    except (ValueError, TypeError):
        h = ""
    return k if ('"alg"' in h or '"typ"' in h or payload.startswith("eyJ")) else None


def _v_bearer(t, ms, s, e, v, k):
    return k if re.search(r"\d", v) and re.search(r"[A-Za-z]", v) else None


def _v_kv(t, ms, s, e, v, k):
    if v.lower().strip(".") in _KV_STOP or re.fullmatch(r"[*•●·.xX]+", v) \
            or re.fullmatch(r"[<{$\[(].*", v):
        return None
    return "password_field" if re.match(r"(?i)pass|pwd", t[ms:s]) else k


def _v_email(t, ms, s, e, v, k):
    tld = v.rsplit(".", 1)[-1].lower()
    return None if tld in _FILE_TLD else k


def _v_card(t, ms, s, e, v, k):
    d = re.sub(r"\D", "", v)
    groups = re.split(r"[ -]", v)
    if not 13 <= len(d) <= 19 or d[0] not in "23456" or len(set(d)) < 2:
        return None
    if len(groups) > 1 and (len(set(re.findall(r"[ -]", v))) > 1 or min(map(len, groups)) < 3):
        return None
    tot = 0
    for i, ch in enumerate(reversed(d)):
        x = int(ch) * (2 if i % 2 else 1)
        tot += x - 9 if x > 9 else x
    return k if tot % 10 == 0 else None


def _v_iban(t, ms, s, e, v, k):
    c = v.replace(" ", "")
    return k if 15 <= len(c) <= 34 and _mod97(c[4:] + c[:4]) == 1 else None


def _v_phone(t, ms, s, e, v, k):
    d = re.sub(r"\D", "", v)
    if not 7 <= len(d) <= 15 or len(set(d)) < 3 or _DATE.match(v.strip()):
        return None
    return None if _ORDERCTX.search(t[max(0, ms - 16):ms]) else k


def _v_ip(t, ms, s, e, v, k):
    if v in ("0.0.0.0", "127.0.0.1") or v.startswith("255.") or re.search(r"(?i)\b(version|ver\.?|v)\s*$", t[max(0, s - 9):s]):
        return None
    return k


def _v_entropy(t, ms, s, e, v, k):
    core = v.rstrip("=")
    if not (re.search(r"[A-Za-z]", core) and re.search(r"\d", core)):
        return None
    if re.fullmatch(r"[0-9a-fA-F-]+", core):       # hashes, UUIDs: only next to a secret keyword
        return k if _KEYCTX.search(t[max(0, s - 40):s]) else None
    wordy = sum(len(x) for x in re.findall(r"[A-Z]?[a-z]{3,}|[A-Z]{3,}", core))   # slugs, camelCase
    if wordy > 0.5 * len(core) or _entropy(core) < 3.5:
        return None
    cl = [c for c in map(_cls, core) if c]
    sw = sum(a != b for a, b in zip(cl, cl[1:])) / max(1, len(cl) - 1)
    return k if sw >= 0.3 else None


_VALIDATORS = {"any": _v_any, "jwt": _v_jwt, "bearer": _v_bearer, "kv": _v_kv, "email": _v_email,
               "card": _v_card, "iban": _v_iban, "phone": _v_phone, "ip": _v_ip, "entropy": _v_entropy}
_PREFIX = re.compile(r"^(sk-(?:proj-|ant-)?|[sprk]k_(?:live|test)_|gh[pousr]_|github_pat_|xox[a-z]-|AKIA|ASIA|AIza|glpat-|hf_|npm_|shp[a-z]{2}_|SG\.|xai-|eyJ)")


def mask(value, kind):
    """Short preview that never holds the full value."""
    v = value.strip()
    d = re.sub(r"\D", "", v)
    if kind == "email" and "@" in v:
        local, _, dom = v.partition("@")
        if _TRUNC.search(v) or "." not in dom:
            return f"{local[:1]}***@{dom[:1] if re.match('[A-Za-z0-9]', dom) else ''}***\u2026"
        name, _, tld = dom.rpartition(".")
        return f"{local[:1]}***@{name[:1]}***.{tld}"
    if kind in ("api_key", "token", "jwt", "custom"):
        p = _PREFIX.match(v)
        return f"{p.group(1) if p else v[:2] if len(v) >= 12 else ''}***({len(v)} chars)"
    if kind == "credit_card":
        return f"**** {d[-4:]}"
    if kind == "iban":
        return f"{v[:2]}** ****{v.replace(' ', '')[-2:]}"
    if kind == "phone":
        return f"{'+' if v.startswith('+') else ''}*** **{d[-2:]}"
    if kind == "ip":
        return v.split(".")[0] + ".***.***.***"
    return "***" if kind == "password_field" or len(v) < 4 else f"{v[:1]}***"


def _allowed(v, allow):
    lv = v.strip().lower()
    if any(lv == a or (a.startswith("@") and lv.endswith(a)) for a in allow):
        return True
    if not _TRUNC.search(lv):
        return False
    # truncated ("support@comp…"): allowed only if it can be an exactly allowed value; a domain
    # allow ("@company.com") proves nothing about a cut-off domain
    rx = ".*".join(re.escape(x) for x in _TRUNC.split(lv))
    return any(not a.startswith("@") and re.fullmatch(rx, a, re.S) for a in allow)


def _compiled(custom=()):
    pats = [("custom", "any", "", c) for c in custom] + PATTERNS
    return [(k, vn, re.compile(src, re.ASCII | (re.I if "i" in fl else 0))) for k, vn, fl, src in pats]


_DEFAULT = _compiled()


def find_in_text(text, allow=(), custom=(), kinds=None):
    """Spans [(kind, start, end)] of sensitive values in one string (non-overlapping, sorted).
    Allowed values and unwanted kinds are dropped first; overlapping matches are then UNIONED
    (merged span, most severe kind), so no pattern can shrink another's match."""
    text = text.replace("\u00a0", " ")
    allow = [a.lower() for a in allow]
    cands = []
    for kind, vn, rx in (_compiled(custom) if custom else _DEFAULT):
        for m in rx.finditer(text):
            s, e = m.span(1) if rx.groups and m.group(1) is not None else m.span()
            k = _VALIDATORS[vn](text, m.start(), s, e, text[s:e], kind) if e > s else None
            if k and (kinds is None or k in kinds) and not _allowed(text[s:e], allow):
                cands.append((s, e, k))
    out = []
    for s, e, k in sorted(cands):
        if out and s < out[-1][2]:
            k0, s0, e0 = out[-1]
            out[-1] = (min(k0, k, key=_sev), s0, max(e0, e))
        else:
            out.append((k, s, e))
    return out


def _sev(kind):
    return SEVERITY.index(kind) if kind in SEVERITY else len(SEVERITY)


# ------------------------------------------------------------------ text detector (OCR words)
def _segments(words):
    """Group OCR words into reading-order runs: same text line, no wide column gap."""
    lines = []
    for w in sorted(words, key=lambda w: (w["y"] + w["h"] / 2, w["x"])):
        cy = w["y"] + w["h"] / 2
        for ln in lines:
            if abs(ln["cy"] - cy) < 0.5 * min(ln["h"], w["h"]):
                ln["words"].append(w)
                break
        else:
            lines.append({"cy": cy, "h": w["h"], "words": [w]})
    for ln in lines:
        ws = sorted(ln["words"], key=lambda w: w["x"])
        hs = sorted(w["h"] for w in ws)
        gap = 1.5 * hs[len(hs) // 2]
        seg = [ws[0]]
        for a, b in zip(ws, ws[1:]):
            if b["x"] - (a["x"] + a["w"]) > gap:
                yield seg
                seg = []
            seg.append(b)
        yield seg


def _item_rect(it):
    """The WHOLE OCR item. Glyph widths vary, so a character-count slice of a word can miss
    the match by a lot; a partly matched word is blacked out entirely."""
    return it["x"], it["y"], it["x"] + it["w"], it["y"] + it["h"]


def _word_level(items):
    good = [it for it in items if isinstance(it, dict) and str(it.get("text", "")).strip()
            and all(isinstance(it.get(k), (int, float)) for k in ("x", "y", "w", "h"))]
    for lv in (("word", 5), ("line", 4)):
        pick = [it for it in good if it.get("level") in lv]
        if pick:
            return pick
    return good


def scan_text_items(items, policy=None):
    """Findings (image px) in OCR output: list of {"text","x","y","w","h","conf","level"}.
    `policy` = privacy_mode(...) result (allow list, extra patterns, kinds); None = auto.
    Each finding covers the whole OCR words it touches. Never reports name_field/address_field
    (those need a DOM form field)."""
    pol = policy or privacy_mode({})
    if not pol["enabled"]:
        return []
    out = []
    for seg in _segments(_word_level(items)):
        text, spans = "", []
        for it in seg:
            text += " " if text else ""
            spans.append((len(text), len(text) + len(it["text"]), it))
            text += it["text"]
        for kind, s, e in find_in_text(text, pol["allow"], pol["patterns"], pol["kinds"]):
            hit = [(a, b, it) for a, b, it in spans if a < e and b > s]
            if not hit: continue
            boxes = [_item_rect(it) for _, _, it in hit]
            x1, y1 = min(b[0] for b in boxes), min(b[1] for b in boxes)
            x2, y2 = max(b[2] for b in boxes), max(b[3] for b in boxes)
            confs = [it["conf"] for _, _, it in hit if isinstance(it.get("conf"), (int, float))]
            f = {"kind": kind, "x": round(x1, 1), "y": round(y1, 1), "w": round(x2 - x1, 1),
                 "h": round(y2 - y1, 1), "units": "image", "source": "ocr", "preview": mask(text[s:e], kind)}
            if confs: f["conf"] = min(confs)
            out.append(f)
    return out


# ------------------------------------------------------------------ marks, summary, policy
def to_redact_marks(findings, scale=1, pad=2):
    """Spec redact marks. `scale` = spec units per finding unit (DOM CSS px into a CSS-px spec: 1;
    OCR image px into a CSS-px spec with scale 2: 0.5). `pad` is in spec units.
    "unknown_frame" findings (an unreadable cross-origin iframe, e.g. a video embed) are skipped:
    relabel one as "custom" to black it out."""
    marks = []
    for f in findings:
        if f["kind"] == "unknown_frame":
            continue
        x1, y1 = max(0, f["x"] * scale - pad), max(0, f["y"] * scale - pad)
        x2, y2 = (f["x"] + f["w"]) * scale + pad, (f["y"] + f["h"]) * scale + pad
        if x2 > x1 and y2 > y1:
            marks.append({"type": "redact", "rect": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
                          "note": f["kind"]})
    return marks


def summary(findings):
    """Human lines like 'email x2: a***@d***.com, j***@e***.org' (previews only, never values)."""
    by = {}
    for f in findings:
        by.setdefault(f["kind"], []).append(f.get("preview", ""))
    lines = []
    for k in sorted(by, key=lambda k: KINDS.index(k) if k in KINDS else len(KINDS)):
        prev = list(dict.fromkeys(p for p in by[k] if p and p != "***"))
        more = f" (+{len(prev) - 3} more)" if len(prev) > 3 else ""
        tail = f": {', '.join(prev[:3])}{more}" if prev else ""
        lines.append(f"{k.replace('_', ' ')} x{len(by[k])}{tail}")
    return lines


def privacy_mode(spec):
    """Normalize spec["privacy"]: "auto" (default, tickets/guides/chat), "off", or
    {"kinds": [...], "allow": ["support@company.com", "@company.com"], "patterns": ["regex"]}.
    Returns {"enabled", "kinds" (frozenset), "allow" (lowercased), "patterns"}; SpecError if bad."""
    p = spec.get("privacy", "auto") if isinstance(spec, dict) else "auto"
    base = {"enabled": True, "kinds": AUTO_KINDS, "allow": [], "patterns": []}
    if p in ("auto", None, True):
        return base
    if p in ("off", False):
        return dict(base, enabled=False, kinds=frozenset())
    if not isinstance(p, dict):
        raise SpecError(f"invalid spec:\n  spec.privacy must be \"auto\", \"off\" or an object, got {p!r}")
    errs = [f"spec.privacy: unknown key {k!r}" for k in p if k not in ("kinds", "allow", "patterns")]
    kinds, allow, pats = p.get("kinds", sorted(AUTO_KINDS)), p.get("allow", []), p.get("patterns", [])
    for name, v in (("kinds", kinds), ("allow", allow), ("patterns", pats)):
        if not (isinstance(v, list) and all(isinstance(x, str) and x for x in v)):
            errs.append(f"spec.privacy.{name} must be a list of non-empty strings")
    if not errs:
        errs += [f"spec.privacy.kinds: unknown kind {k!r}; use {', '.join(KINDS)}" for k in kinds if k not in KINDS]
        for rx in pats:
            try:
                re.compile(rx, re.ASCII)
            except re.error as e:
                errs.append(f"spec.privacy.patterns: bad regex {rx!r} ({e})")
    if errs:
        raise SpecError("invalid spec:\n  " + "\n  ".join(errs))
    kinds = set(kinds) | ({"custom"} if pats else set())
    return {"enabled": True, "kinds": frozenset(kinds), "allow": [a.lower() for a in allow], "patterns": list(pats)}


def filter_findings(findings, policy):
    """Keep the findings a policy wants redacted (kinds only; allow lists apply at scan time)."""
    return [f for f in findings if policy["enabled"] and f["kind"] in policy["kinds"]]


# ------------------------------------------------------------------ text helpers for messages
def mask_text(s: str, policy=None) -> str:
    """`s` with every sensitive match replaced by its masked preview ("a***@d***.com",
    "sk-proj-***(40 chars)"), for error messages, candidate lists and logs.
    `policy` (a privacy_mode() result) supplies allow list, extra patterns and kinds; a
    disabled ("off") policy still masks with the auto kinds: "off" governs the image only."""
    s = s if isinstance(s, str) else str(s)
    pol = policy or privacy_mode({})
    kinds = pol["kinds"] if pol.get("enabled") else AUTO_KINDS
    out, last = [], 0
    for kind, a, b in find_in_text(s, pol.get("allow", ()), pol.get("patterns", ()), kinds):
        out += [s[last:a], mask(s[a:b], kind)]
        last = b
    return "".join(out) + s[last:]


def _tokenish(seg):
    """A path segment that looks like a credential: long, letters + digits, hex/UUID or random."""
    core = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", seg)            # foo.png -> foo
    if len(core) < 16 or not re.fullmatch(r"[\w~+=.-]+", core):
        return False
    if not (re.search(r"\d", core) and re.search(r"[A-Za-z]", core)):
        return False
    if re.fullmatch(r"[0-9a-fA-F-]+", core):                    # hashes, UUIDs: reset/verify links
        return True
    return _v_entropy(core, 0, 0, len(core), core, "token") is not None


_OPAQUE = ("data", "javascript", "vbscript", "blob", "mailto", "tel", "sms", "about")


def sanitize_url(url: str) -> str:
    """A URL safe to print: no userinfo, no query, no fragment, no ;params, and every path
    segment that holds a secret or personal value (email, key, token, card, hash/UUID-like
    id) replaced by its masked preview. Scheme, host and port stay. data:/javascript: URLs
    keep only the scheme."""
    u = (url if isinstance(url, str) else str(url)).strip()
    m = re.match(r"(?i)^([a-z][a-z0-9+.-]*):(?!//)", u)
    if m and m.group(1).lower() in _OPAQUE:
        sch, rest = m.group(1).lower(), u[m.end():]
        if sch == "blob":
            return "blob:" + sanitize_url(rest)
        if sch in ("data", "javascript", "vbscript"):
            return f"{sch}:\u2026" if rest else f"{sch}:"
        return f"{sch}:" + mask_text(unquote(re.split(r"[?#]", rest)[0]))
    absolute = re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", u)
    rel = not absolute and u.startswith("/") and not u.startswith("//")
    parts = urlsplit(u if absolute or u.startswith("//") or rel else "//" + u)
    host = parts.netloc.rpartition("@")[2]
    segs = []
    for seg in parts.path.split("/"):
        seg = seg.split(";", 1)[0]
        dec = unquote(seg)
        if find_in_text(dec):
            seg = mask_text(dec)
        elif _tokenish(dec):
            seg = mask(dec, "token")
        segs.append(seg)
    out = urlunsplit((parts.scheme, host, "/".join(segs), "", ""))
    return out[2:] if not absolute and not u.startswith("//") and out.startswith("//") else out


# ------------------------------------------------------------------ DOM detector (JavaScript)
DOM_SCAN_JS = build_dom_scan_js(PATTERNS, _KV_STOP, _FILE_TLD, SEVERITY)


def dom_options(policy=None, full_page=False, offset=None, clip=None):
    """JSON-able opts for DOM_SCAN_JS from a privacy_mode() policy."""
    pol = policy or privacy_mode({})
    o = {"allow": pol["allow"], "patterns": pol["patterns"], "kinds": sorted(pol["kinds"] | {"unknown_frame"}),
         "fullPage": full_page}
    if offset:
        o["offset"] = list(offset)
    if clip:
        o["clip"] = list(clip)
    return o


def neo_code(policy=None, full_page=False):
    """Code for BrowserOS neo `p.evaluate({code})` (a function body that returns the findings)."""
    return f"return ({DOM_SCAN_JS})({json.dumps(dom_options(policy, full_page))});"


def scan_page(page, policy=None, full_page=False):
    """Run the DOM detector in a Playwright page, then inside cross-origin frames (which the page
    script cannot read) through Playwright's own frame access. CSS px, relative to the viewport.
    Frames that stay unreadable come back as "unknown_frame" (see to_redact_marks)."""
    pol = policy or privacy_mode({})
    if not pol["enabled"]:
        return []
    opts = dom_options(pol, full_page)
    found = page.evaluate(DOM_SCAN_JS, opts)
    frames = [f for f in found if f["kind"] == "unknown_frame"]
    for fr in page.frames[1:]:
        try:
            el = fr.frame_element()
            box = el.bounding_box()
            if not box or fr.parent_frame != page.main_frame:
                continue
            match = [f for f in frames if abs(f["x"] - box["x"]) < 3 and abs(f["y"] - box["y"]) < 3]
            if not match:
                continue
            dx, dy, cw, ch = el.evaluate(_CONTENT_BOX)      # skip the iframe's border + padding
            x, y = box["x"] + dx, box["y"] + dy
            clip = [max(x, 0), max(y, 0), x + cw, y + ch]
            found += fr.evaluate(DOM_SCAN_JS, dict(opts, offset=[x, y], clip=clip, fullPage=False))
            found = [f for f in found if f not in match]
        except Exception:   # frame detached / navigated mid-scan: keep the unknown_frame finding
            continue
    return [f for f in found if f["kind"] in pol["kinds"] | {"unknown_frame"}]
