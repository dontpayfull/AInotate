"""OCR text targeting on any image: read(image) -> words + lines, locate(image, "Save") -> rects.

Backends (auto order): Apple Vision on macOS, Windows.Media.Ocr on Windows, Tesseract anywhere.
Every box is in image pixels, top-left origin, so a located rect drops into a spec with scale 1.
"""
from __future__ import annotations

import asyncio
import difflib
import importlib
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unicodedata

from PIL import Image
from .ocr_engines import *  # noqa: F401,F403  (re-export: every name stays importable from ainotate.ocr)
from .ocr_engines import (  # noqa: F401  private names callers/tests reach through ainotate.ocr
    BACKENDS, DEFAULT_LANGS, ESTIMATED_PENALTY, EXIT_AMBIGUOUS, EXIT_NOT_FOUND, EXIT_OCR, EXIT_OK,
    EXIT_USAGE, INSTALL, PART_PENALTY, TIE_EPS, AmbiguousText, OcrError, TextNotFound, _ENGINES,
    _fix_words, _group_words, _has, _merge, _ocr, _os_key, _overlap, _parse_tesseract_tsv, _shift,
    _split_by_chars, _tess_langs_cache, _tesseract_cmd, _vsyms, _tesseract_langs, _tesseract_run, _tiles,
    _union, _vision_box_to_px, _vision_cgimage, _vision_run, _vision_syms, _winrt_engine,
    _winrt_lines, _winrt_modules, _winrt_run, _word_spans, _run_coro, available_backends,
    install_hint, pick_backend,
)


def _parse_rect(r, what="region"):
    """[x1,y1,x2,y2] | {x,y,w,h} | "x1,y1,x2,y2" -> (x1, y1, x2, y2) floats."""
    if isinstance(r, str):
        r = [p for p in re.split(r"[,\s]+", r.strip()) if p]
    if isinstance(r, dict):
        try:
            x, y = float(r["x"]), float(r["y"])
            return x, y, x + float(r.get("w", r.get("width"))), y + float(r.get("h", r.get("height")))
        except (KeyError, TypeError, ValueError):
            raise OcrError(f"{what}: dict needs numeric x, y, w, h")
    try:
        x1, y1, x2, y2 = (float(v) for v in r)
    except (TypeError, ValueError):
        raise OcrError(f"{what} must be [x1,y1,x2,y2], {{x,y,w,h}} or 'x1,y1,x2,y2' (got {r!r})")
    if x2 <= x1 or y2 <= y1:
        raise OcrError(f"{what} must have x2>x1 and y2>y1 (got {[x1, y1, x2, y2]})")
    return x1, y1, x2, y2




def _reading_order(lines, get_box=lambda l: l["box"]):
    lines = sorted(lines, key=lambda l: get_box(l)[1] + get_box(l)[3] / 2)
    rows, out = [], []
    for ln in lines:
        b = get_box(ln)
        cy, h = b[1] + b[3] / 2, b[3]
        if rows and abs(cy - rows[-1][0]) < 0.5 * max(h, rows[-1][1]):
            rows[-1][2].append(ln)
        else:
            rows.append((cy, h, [ln]))
    for _, _, row in rows:
        out += sorted(row, key=lambda l: get_box(l)[0])
    return out


def _item(text, box, conf, line, level, W, H, **extra):
    r = lambda v: round(v, 3)   # 74.99999 from normalized floats is 75, not 74
    x1, y1 = max(0, math.floor(r(box[0]))), max(0, math.floor(r(box[1])))
    x2, y2 = min(W, math.ceil(r(box[0] + box[2]))), min(H, math.ceil(r(box[1] + box[3])))
    return {"text": text, "x": x1, "y": y1, "w": max(1, x2 - x1), "h": max(1, y2 - y1),
            "conf": None if conf is None else round(conf, 3), "line": line, "level": level, **extra}


_cache = {}


def read(image_path, langs=None, region=None, *, backend="auto"):
    """OCR an image. Returns line items, each followed by its word items:
    {"text", "x", "y", "w", "h" (image px), "conf" 0..1 (None: engine gives none), "line", "level",
    and "word" (index in the line) on words}. `region` limits OCR to part of the image."""
    from .spec import SpecError, open_image
    langs = [langs] if isinstance(langs, str) else list(langs or DEFAULT_LANGS)
    orig_b = backend
    backend = pick_backend(backend)
    path = os.path.abspath(os.path.expanduser(str(image_path)))
    reg = _parse_rect(region) if region is not None else None
    try:
        st = os.stat(path)
        key = (path, st.st_mtime_ns, st.st_size, tuple(langs), reg, backend)
    except OSError:
        key = None
    if key in _cache:
        return [dict(i) for i in _cache[key]]
    try:
        img = open_image(path)
        img.load()
    except SpecError as e:
        raise OcrError(str(e))
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, "white")
        bg.paste(img, mask=img.getchannel("A"))
        img = bg
    else:
        img = img.convert("RGB")
    W, H = img.size
    ox = oy = 0
    if reg:
        x1, y1, x2, y2 = max(0, int(reg[0])), max(0, int(reg[1])), min(W, math.ceil(reg[2])), min(H, math.ceil(reg[3]))
        if x2 - x1 < 4 or y2 - y1 < 4:
            raise OcrError(f"region {list(reg)} is empty inside the {W}x{H} image")
        img, ox, oy = img.crop((x1, y1, x2, y2)), x1, y1
    try:
        lines = [_shift(ln, ox, oy) for ln in _ocr(img, backend, langs)]
    except OcrError:
        from .ocr_engines import available_backends
        alt = [b for b in available_backends() if b != backend]
        if orig_b in (None, "auto") and alt:
            lines = [_shift(ln, ox, oy) for ln in _ocr(img, alt[0], langs)]
        else:
            raise
    items = []
    for i, ln in enumerate(_reading_order(lines)):
        items.append(_item(ln["text"], ln["box"], ln["conf"], i, "line", W, H))
        items += [_item(w["text"], w["box"], ln["conf"], i, "word", W, H, word=j)
                  for j, w in enumerate(ln["words"])]
    if key:
        if len(_cache) > 16:
            _cache.clear()
        _cache[key] = items
    return [dict(i) for i in items]


# ---------- matcher ----------

_PUNCT = r"[\-/_|:.,;•·…\\()\[\]{}<>\"'`!?*+=~^&@#$%]+"
_LOOSE = str.maketrans({"0": "o", "1": "l", "i": "l", "|": "l", "5": "s"})


def norm(s):
    """Casefold, strip diacritics (ș ş ţ ț ă î â -> s s t t a i a), unify quotes and spaces."""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    return " ".join(s.replace("\u2019", "'").replace("ı", "i").replace("ł", "l").split())


def _key(s):
    return re.sub(r"[\W_]+", "", norm(s))


def _loose(k):
    return k.translate(_LOOSE).replace("rn", "m").replace("vv", "w")


def score(query, text):
    """1.0 same text (ignoring case, diacritics, punctuation, spaces); 0.95 same up to OCR
    look-alikes (0/o, 1/l/i, rn/m, vv/w); below that a scaled similarity ratio."""
    q, c = _key(query), _key(text)
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    lq, lc = _loose(q), _loose(c)
    if lq == lc:
        return 0.95
    return 0.94 * difflib.SequenceMatcher(None, lq, lc, autojunk=False).ratio()


def _min_score(query):
    n = len(_key(query))
    return 0.95 if n <= 3 else 0.94 * (0.82 if n < 10 else 0.78)


def _atoms(words):
    """Words split at inner punctuation ("Store-Keywords" -> Store, Keywords) with estimated boxes."""
    out = []
    for wi, w in enumerate(words):
        parts = [p for p in re.split(_PUNCT, w["text"]) if p]
        if not _key(w["text"]):   # bullets, icons read as "•": not part of any phrase
            continue
        parts = parts or [w["text"]]
        boxes = [(w["x"], w["y"], w["w"], w["h"])] if len(parts) == 1 else \
            _split_by_chars((w["x"], w["y"], w["w"], w["h"]), w["text"], parts)
        out += [(p, b, wi, len(parts)) for p, b in zip(parts, boxes)]
    return out


def locate(image_path, query, *, nth=None, within=None, exact=False, langs=None, backend="auto",
           items=None):
    """Candidates for `query`, best first: {"text", "x","y","w","h", "rect": [x1,y1,x2,y2], "score",
    "conf", "line", "top", "estimated"}. "top" marks every candidate within TIE_EPS of the best: more
    than one top = ambiguous, ask (or pass nth / within); never pick silently. `nth` (0-based, reading
    order) selects among the top ones; `within` keeps text whose centre is inside a rect;
    `exact` = whole words equal after case/diacritics folding, no fuzzing."""
    if not isinstance(query, str) or not _key(query):
        raise OcrError("query must contain letters or digits")
    if items is None:
        items = read(image_path, langs, backend=backend)
    box = _parse_rect(within, "within") if within is not None else None
    lines = {}
    for it in items:
        if it.get("level") == "word":
            cx, cy = it["x"] + it["w"] / 2, it["y"] + it["h"] / 2
            if box is None or (box[0] <= cx <= box[2] and box[1] <= cy <= box[3]):
                lines.setdefault(it["line"], []).append(it)
    conf = {it["line"]: it.get("conf") for it in items if it.get("level") == "line"}
    qn = len([p for p in re.split(_PUNCT + r"|\s+", query) if p])
    floor = 1.0 if exact else _min_score(query)
    found = []
    for li, words in lines.items():
        atoms = _atoms(words)
        lh = max(w["h"] for w in words)
        for i in range(len(atoms)):
            for j in range(i + 1, min(len(atoms), i + qn + 2) + 1):
                if j - i > 1:
                    a, b = atoms[j - 2][1], atoms[j - 1][1]
                    if b[0] - (a[0] + a[2]) > 2.5 * lh:   # too far apart to be one phrase
                        break
                span = atoms[i:j]
                text = " ".join(a[0] for a in span)
                s = score(query, text)
                if s < floor:
                    continue
                est = any(sum(1 for a in span if a[2] == wi) < n for _, _, wi, n in span)
                if exact and est:
                    continue
                if not est:   # report what OCR read ("dashlane.com"), not the split atoms
                    text = " ".join(words[wi]["text"] for wi in dict.fromkeys(a[2] for a in span))
                s *= ESTIMATED_PENALTY if est else 1.0
                s *= PART_PENALTY if (i > 0 or j < len(atoms)) else 1.0
                x, y, w, h = _union([a[1] for a in span])
                x1, y1, x2, y2 = math.floor(x), math.floor(y), math.ceil(x + w), math.ceil(y + h)
                found.append({"text": text, "x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1,
                              "rect": [x1, y1, x2, y2], "score": round(s, 3), "conf": conf.get(li),
                              "line": li, "estimated": est, "_span": (li, i, j)})
    found.sort(key=lambda c: (-c["score"], c["_span"][2] - c["_span"][1]))
    spans, out = [], []
    for c in found:   # one candidate per spot: overlapping spans on a line keep the best
        li, i, j = c.pop("_span")
        rc = (c["x"], c["y"], c["w"], c["h"])
        if not any(k == li and i < kj and ki < j for k, ki, kj in spans) and \
                all(_overlap(rc, (o["x"], o["y"], o["w"], o["h"])) < 0.5 for o in out):
            spans.append((li, i, j))
            out.append(c)
    best = out[0]["score"] if out else 0
    for c in out:
        c["top"] = c["score"] >= best - TIE_EPS
    if nth is not None:
        top = _reading_order([c for c in out if c["top"]], get_box=lambda c: (c["x"], c["y"], c["w"], c["h"]))
        if not isinstance(nth, int) or isinstance(nth, bool) or not -len(top) <= nth < len(top):
            raise OcrError(f"nth={nth!r} but {len(top)} equally good match(es) for {query!r}")
        return [top[nth]]
    return out


def near_misses(query, items, n=5):
    """Closest OCR words/lines below the match threshold, to tell the agent what the image says."""
    seen, out = set(), []
    for it in items:
        if it["text"] not in seen:
            seen.add(it["text"])
            out.append((round(score(query, it["text"]), 3), it))
    return [dict(it, score=s) for s, it in sorted(out, key=lambda t: -t[0])[:n]]


def not_found(query, items, *, within=None, exact=False) -> TextNotFound:
    """The TextNotFound for a query with no match. With `within`: how many matches the image has
    outside the region (their rects), not the exact matches dressed up as "closest" text.
    Without: the closest OCR text with scores. Texts in the message are masked."""
    if within is not None:
        outside = locate(None, query, exact=exact, items=items)
        if outside:
            rects = "; ".join(str(c["rect"]) for c in outside[:5]) + (" ..." if len(outside) > 5 else "")
            return TextNotFound(f"text {query!r} not found inside the region {[round(v) for v in _parse_rect(within, 'within')]}; "
                                f"found {len(outside)} match(es) outside the region: {rects}. Widen or "
                                "move `within`, or drop it and pick one with nth", [])
    near = near_misses(query, items)
    where = " inside the region" if within is not None else " on the image"
    return TextNotFound(f"text {query!r} not found{where}; closest OCR text: "
                        + (", ".join(f"{m['text']!r} ({m['score']:.2f})" for m in near) or "(none)"), near)


def resolve(image_path, target, *, scale=1.0, langs=None, backend="auto", items=None):
    """A spec `target` {"text", "nth"?, "within"? (spec units), "exact"?} -> [x1,y1,x2,y2] in spec
    units. Raises TextNotFound / AmbiguousText (with the candidates) instead of guessing."""
    if not isinstance(target, dict) or not isinstance(target.get("text"), str):
        raise OcrError('target must be {"text": "..."} (optional nth, within, exact)')
    within = target.get("within")
    if within is not None:
        within = [v * scale for v in _parse_rect(within, "within")]
    if items is None:
        items = read(image_path, langs, backend=backend)
    q = target["text"]
    exact = bool(target.get("exact"))
    cands = locate(image_path, q, nth=target.get("nth"), within=within, exact=exact, items=items)
    top = [c for c in cands if c["top"]]
    if not top:
        raise not_found(q, items, within=within, exact=exact)
    if len(top) > 1:
        raise AmbiguousText(f"{len(top)} equally good matches for {q!r}: "
                            + "; ".join(f"{c['rect']} {c['text']!r}" for c in top)
                            + ". Ask the user which one, or add nth (0-based, reading order) or within",
                            top)
    return [round(v / scale, 2) for v in top[0]["rect"]]


def cli_locate(argv):
    """`ainotate locate ...` (one implementation: ainotate.cli). Returns the exit code."""
    from .cli import main
    return main(["locate", *argv])
