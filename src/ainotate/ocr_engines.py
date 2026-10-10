"""OCR engine backends (Apple Vision, Windows.Media.Ocr, Tesseract), tiling and merge. Re-exported by ocr."""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading

from PIL import Image

DEFAULT_LANGS = ("ro-RO", "en-US")
BACKENDS = ("vision", "winrt", "tesseract")
TIE_EPS = 0.02            # candidates within this of the best score are "equally good"
PART_PENALTY = 0.97       # query matches only part of a longer OCR line
ESTIMATED_PENALTY = 0.96  # box cut out of a word by character count (e.g. "Keywords" in "Store-Keywords")
EXIT_OK, EXIT_USAGE, EXIT_OCR, EXIT_AMBIGUOUS, EXIT_NOT_FOUND = 0, 2, 4, 5, 6

INSTALL = {
    "darwin": "macOS:   pip install pyobjc-framework-Vision   (Apple Vision, built into macOS 10.15+)\n"
              "         or: brew install tesseract tesseract-lang",
    "win32": "Windows: pip install winrt-runtime winrt-Windows.Foundation winrt-Windows.Foundation.Collections "
             "winrt-Windows.Globalization winrt-Windows.Graphics.Imaging winrt-Windows.Media.Ocr "
             "winrt-Windows.Storage.Streams\n"
             "         plus an OCR language (admin PowerShell): "
             "Add-WindowsCapability -Online -Name \"Language.OCR~~~en-US~0.0.1.0\"\n"
             "         or: winget install UB-Mannheim.TesseractOCR",
    "linux": "Linux:   sudo apt install tesseract-ocr tesseract-ocr-ron   "
             "(Fedora: sudo dnf install tesseract tesseract-langpack-ron)",
}


class OcrError(Exception):
    """No OCR engine, engine failure, or bad arguments."""


def mask(text):
    """`text` with secrets and personal data masked (privacy.mask_text): OCR text read off a
    screenshot can hold an email or a token, and error messages must never repeat it."""
    from .privacy import mask_text   # lazy: privacy is heavier than the OCR helpers
    return mask_text(str(text))


def _masked_items(items):
    return [dict(it, text=mask(it["text"])) if isinstance(it, dict) and "text" in it else it
            for it in items or ()]


class TextNotFound(OcrError):
    """No OCR match. Message and `near[].text` are masked (privacy.mask_text)."""

    def __init__(self, msg, near=()):
        super().__init__(mask(msg))
        self.near = _masked_items(near)


class AmbiguousText(OcrError):
    """Several equally good matches. Message and `candidates[].text` are masked."""

    def __init__(self, msg, candidates=()):
        super().__init__(mask(msg))
        self.candidates = _masked_items(candidates)


def _os_key():
    return sys.platform if sys.platform in ("darwin", "win32") else "linux"


def install_hint():
    here = _os_key()
    return "\n".join([INSTALL[here]] + [v for k, v in INSTALL.items() if k != here])


# ---------- geometry helpers ----------

def _union(boxes):
    x1 = min(b[0] for b in boxes); y1 = min(b[1] for b in boxes)
    return (x1, y1, max(b[0] + b[2] for b in boxes) - x1, max(b[1] + b[3] for b in boxes) - y1)


def _overlap(a, b):
    """Intersection area over the smaller box's area (boxes are x, y, w, h)."""
    iw = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    ih = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    return iw * ih / max(1e-9, min(a[2] * a[3], b[2] * b[3]))


def _word_spans(text):
    """[(word, utf16_start, utf16_len)]: NSString ranges count UTF-16 units, not code points."""
    u16 = lambda s: len(s.encode("utf-16-le")) // 2
    return [(m.group(), u16(text[:m.start()]), u16(m.group())) for m in re.finditer(r"\S+", text)]


def _split_by_chars(box, text, parts):
    """Boxes for consecutive substrings `parts` of `text`, proportional to character count."""
    x, y, w, h = box
    total = max(1, len(text))
    out, pos = [], 0
    for p in parts:
        i = text.find(p, pos)
        i = pos if i < 0 else i
        out.append((x + w * i / total, y, w * len(p) / total, h))
        pos = i + len(p)
    return out


def _tiles(W, H, size, overlap):
    def spans(L):
        if L <= size:
            return [(0, L)]
        n = math.ceil((L - overlap) / (size - overlap))
        step = (L - size) / (n - 1)
        return [(round(i * step), min(L, round(i * step) + size)) for i in range(n)]
    return [(x0, y0, x1, y1) for y0, y1 in spans(H) for x0, x1 in spans(W)]


def _merge(cands):
    """cands: [(priority, line)] with line boxes in image px. Lower priority wins duplicates
    (0 = whole inside a tile, 1 = full-image pass, 2 = cut by a tile edge); longer text next."""
    kept = []
    for _, ln in sorted(cands, key=lambda c: (c[0], -len(c[1]["text"]))):
        if all(_overlap(ln["box"], k["box"]) < 0.4 for k in kept):
            kept.append(ln)
    return kept


def _shift(ln, dx, dy, k=1.0):
    mv = lambda b: (b[0] * k + dx, b[1] * k + dy, b[2] * k, b[3] * k)
    return {**ln, "box": mv(ln["box"]), "words": [{**w, "box": mv(w["box"])} for w in ln["words"]]}


def _fix_words(line):
    """Fill missing word boxes, and replace ones that are really the whole line (older Vision)."""
    words = line["words"]
    est = _split_by_chars(line["box"], line["text"], [w["text"] for w in words])
    for w, e in zip(words, est):
        b = w.get("box")
        if b is None or (len(words) > 1 and b[2] > 0.9 * line["box"][2]
                         and len(w["text"]) < 0.7 * len(line["text"])):
            w["box"] = e
    return line


# ---------- backends: each returns [{"text", "conf", "box": (x,y,w,h) px, "words": [{"text","box"}]}] ----------

def _vision_box_to_px(r, W, H):
    """Vision normalized rect (origin bottom-left) -> (x, y, w, h) px with origin top-left."""
    if hasattr(r, "origin"):
        r = (r.origin.x, r.origin.y, r.size.width, r.size.height)
    nx, ny, nw, nh = (float(v) for v in r)
    return (nx * W, (1.0 - ny - nh) * H, nw * W, nh * H)


_vsyms, _vlock = {}, threading.Lock()


def _vision_syms():
    """Resolve pyobjc's lazy symbols once, under a lock: resolving them from several threads races."""
    with _vlock:
        if not _vsyms:
            import Quartz
            import Vision
            from Foundation import NSData
            for name in ("CGDataProviderCreateWithCFData", "CGImageCreate", "CGColorSpaceCreateDeviceRGB",
                         "kCGImageAlphaNoneSkipLast", "kCGRenderingIntentDefault"):
                _vsyms[name] = getattr(Quartz, name)
            for name in ("VNRecognizeTextRequest", "VNImageRequestHandler",
                         "VNRequestTextRecognitionLevelAccurate"):
                _vsyms[name] = getattr(Vision, name)
            _vsyms["NSData"] = NSData
        return _vsyms


def _vision_cgimage(img):
    q = _vision_syms()
    rgba = img.convert("RGBA")
    raw = rgba.tobytes()
    provider = q["CGDataProviderCreateWithCFData"](q["NSData"].dataWithBytes_length_(raw, len(raw)))
    return q["CGImageCreate"](rgba.width, rgba.height, 8, 32, 4 * rgba.width, q["CGColorSpaceCreateDeviceRGB"](),
                              q["kCGImageAlphaNoneSkipLast"], provider, None, False,
                              q["kCGRenderingIntentDefault"])


def _vision_run(img, langs):
    v = _vision_syms()
    req = v["VNRecognizeTextRequest"].alloc().init()
    req.setRecognitionLevel_(v["VNRequestTextRecognitionLevelAccurate"])
    req.setUsesLanguageCorrection_(True)
    supported, _ = req.supportedRecognitionLanguagesAndReturnError_(None)
    supported = [str(s) for s in (supported or [])]
    use = [l for l in langs if l in supported] or [s for s in supported if s.startswith("en")][:1]
    if use:
        req.setRecognitionLanguages_(use)
    handler = v["VNImageRequestHandler"].alloc().initWithCGImage_options_(_vision_cgimage(img), None)
    ok, err = handler.performRequests_error_([req], None)
    if not ok:
        raise OcrError(f"Apple Vision failed: {err}")
    W, H = img.size
    lines = []
    for obs in req.results() or []:
        top = obs.topCandidates_(1)
        if not top:
            continue
        c = top[0]
        text = str(c.string())
        words = []
        for word, start, n in _word_spans(text):
            box = None
            try:
                r, _ = c.boundingBoxForRange_error_((start, n), None)
                box = _vision_box_to_px(r.boundingBox(), W, H) if r is not None else None
            except Exception:
                pass
            words.append({"text": word, "box": box})
        lines.append(_fix_words({"text": text, "conf": float(c.confidence()),
                                 "box": _vision_box_to_px(obs.boundingBox(), W, H), "words": words}))
    return lines


def _winrt_modules():
    for root in ("winrt.windows", "winsdk.windows"):
        try:
            mods = [importlib.import_module(f"{root}.{m}") for m in
                    ("media.ocr", "graphics.imaging", "storage.streams", "globalization")]
        except ImportError:
            continue
        return dict(zip(("ocr", "imaging", "streams", "glob"), mods))
    raise ImportError("winrt")


def _winrt_engine(m, langs):
    Ocr = m["ocr"].OcrEngine
    for tag in langs:
        for t in dict.fromkeys((tag, tag.split("-")[0])):
            lang = m["glob"].Language(t)
            if Ocr.is_language_supported(lang):
                eng = Ocr.try_create_from_language(lang)
                if eng is not None:
                    return eng
    eng = Ocr.try_create_from_user_profile_languages()
    if eng is None:
        raise OcrError("Windows OCR has no installed recognizer language. " + INSTALL["win32"])
    return eng


def _winrt_lines(result):
    lines = []
    for ln in result.lines:
        words = [{"text": str(w.text), "box": (float(w.bounding_rect.x), float(w.bounding_rect.y),
                                               float(w.bounding_rect.width), float(w.bounding_rect.height))}
                 for w in ln.words]
        if words:   # Windows reports no confidence
            lines.append({"text": str(ln.text), "conf": None, "box": _union([w["box"] for w in words]),
                          "words": words})
    return lines


def _run_coro(coro_fn):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_fn())
    from concurrent.futures import ThreadPoolExecutor   # called from async code (MCP server)
    with ThreadPoolExecutor(1) as ex:
        return ex.submit(lambda: asyncio.run(coro_fn())).result()


def _winrt_run(img, langs):
    m = _winrt_modules()
    engine = _winrt_engine(m, langs)
    r, g, b, a = img.convert("RGBA").split()
    writer = m["streams"].DataWriter()
    writer.write_bytes(Image.merge("RGBA", (b, g, r, a)).tobytes())
    imaging = m["imaging"]
    bmp = imaging.SoftwareBitmap.create_copy_from_buffer(
        writer.detach_buffer(), imaging.BitmapPixelFormat.BGRA8, img.width, img.height)

    async def go():
        return await engine.recognize_async(bmp)
    return _winrt_lines(_run_coro(go))


def _tesseract_cmd():
    try:
        import pytesseract
        cmd = pytesseract.pytesseract.tesseract_cmd
        if os.path.isfile(cmd) or shutil.which(cmd):
            return shutil.which(cmd) or cmd
    except ImportError:
        pass
    cmd = shutil.which("tesseract")
    win = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    return cmd or (win if sys.platform == "win32" and os.path.isfile(win) else None)


_TESS_CODES = {"ro": "ron", "en": "eng", "fr": "fra", "de": "deu", "it": "ita", "es": "spa", "pt": "por",
               "nl": "nld", "pl": "pol", "cs": "ces", "sv": "swe", "da": "dan", "fi": "fin", "tr": "tur"}
_tess_langs_cache = {}


def _tesseract_langs(cmd, langs):
    if cmd not in _tess_langs_cache:
        try:
            out = subprocess.run([cmd, "--list-langs"], capture_output=True, text=True, timeout=10).stdout
        except Exception:
            out = ""
        _tess_langs_cache[cmd] = {l.strip() for l in out.splitlines()[1:] if l.strip()}
    have = _tess_langs_cache[cmd]
    want = [_TESS_CODES.get(l.split("-")[0].lower(), l) for l in langs]
    return "+".join(dict.fromkeys(l for l in want if l in have)) or ("eng" if "eng" in have else "")


def _group_words(words):
    """Words -> lines by geometry: one row per vertical band, split where the gap is over ~1.2 text
    heights. Sparse-text tesseract (--psm 11) puts every word in its own block; lines are rebuilt."""
    rows = []
    for w in sorted(words, key=lambda w: w["box"][1] + w["box"][3] / 2):
        y, h = w["box"][1], w["box"][3]
        for ry, rh, row in rows:
            if min(y + h, ry + rh) - max(y, ry) > 0.5 * min(h, rh):
                row.append(w)
                break
        else:
            rows.append((y, h, [w]))
    lines = []
    for _, _, row in rows:
        row.sort(key=lambda w: w["box"][0])
        cur = [row[0]]
        for w in row[1:]:
            last = cur[-1]["box"]
            if w["box"][0] - (last[0] + last[2]) > 1.2 * max(w["box"][3], last[3]):
                lines.append(cur)
                cur = []
            cur.append(w)
        lines.append(cur)
    return [{"text": " ".join(w["text"] for w in r), "box": _union([w["box"] for w in r]),
             "conf": sum(w["conf"] for w in r) / len(r),
             "words": [{"text": w["text"], "box": w["box"]} for w in r]} for r in lines]


def _parse_tesseract_tsv(tsv, k=1.0):
    """tesseract TSV (word rows, level 5) -> lines; `k` = factor the image was upscaled by."""
    words = []
    for row in tsv.splitlines()[1:]:
        f = row.split("\t")
        if len(f) >= 12 and f[0] == "5" and f[11].strip():
            words.append({"text": f[11].strip(), "box": tuple(int(v) / k for v in f[6:10]),
                          "conf": max(0.0, float(f[10])) / 100})
    return _group_words(words)


def _tesseract_run(img, langs):
    cmd = _tesseract_cmd()
    if not cmd:
        raise OcrError("tesseract not found. " + install_hint())
    k = 2 if img.width * img.height < 4_000_000 else 1   # small UI text reads far better upscaled
    big = img.resize((img.width * k, img.height * k), Image.LANCZOS) if k > 1 else img
    with tempfile.TemporaryDirectory(prefix="ainotate-ocr-") as d:
        p = os.path.join(d, "in.png")
        big.save(p)
        args = [cmd, p, "stdout", "--psm", "11", "-c", "tessedit_create_tsv=1", "-c", "tessedit_create_txt=0"]
        lang = _tesseract_langs(cmd, langs)
        # one thread: OpenMP otherwise takes every core, which slows Tesseract down on most machines
        r = subprocess.run(args + (["-l", lang] if lang else []), capture_output=True, text=True,
                           encoding="utf-8", timeout=30, env={**os.environ, "OMP_THREAD_LIMIT": "1"})
    if r.returncode != 0:
        raise OcrError(f"tesseract failed: {r.stderr.strip()[:300]}")
    return _parse_tesseract_tsv(r.stdout, k)


def _has(mod):
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


# name -> (available(), run(img, langs), tile size or None)
_ENGINES = {
    "vision": (lambda: sys.platform == "darwin" and _has("Vision") and _has("Quartz"), _vision_run, 1000),
    "winrt": (lambda: sys.platform == "win32" and (_has("winrt.windows.media.ocr") or _has("winsdk")),
              _winrt_run, 2000),
    "tesseract": (lambda: _tesseract_cmd() is not None, _tesseract_run, None),
}


def available_backends():
    return [b for b in BACKENDS if _ENGINES[b][0]()]


def pick_backend(backend="auto"):
    backend = (os.environ.get("AINOTATE_OCR_BACKEND") if backend in (None, "auto") else backend) or "auto"
    if backend != "auto":
        if backend not in _ENGINES:
            raise OcrError(f"unknown OCR backend {backend!r}; use auto, {', '.join(BACKENDS)}")
        if not _ENGINES[backend][0]():
            raise OcrError(f"OCR backend {backend!r} is not available here. Install:\n{install_hint()}")
        return backend
    for b in BACKENDS:
        if _ENGINES[b][0]():
            return b
    raise OcrError("no OCR engine available. Install one:\n" + install_hint())


def _ocr(img, backend, langs):
    _, run, tile = _ENGINES[backend]
    W, H = img.size
    if not tile or max(W, H) <= tile * 4 // 3:
        return run(img, langs)
    # big image: engines downsample internally and lose small UI text, so OCR overlapping tiles,
    # plus one full pass for long lines / huge text; duplicates resolved by _merge
    from concurrent.futures import ThreadPoolExecutor
    tiles = _tiles(W, H, tile, tile // 4)
    with ThreadPoolExecutor(min(4, len(tiles) + 1)) as ex:
        full = ex.submit(run, img, langs)
        parts = list(ex.map(lambda t: run(img.crop(t), langs), tiles))
    cands = [(1, ln) for ln in full.result()]
    for (x0, y0, x1, y1), lines in zip(tiles, parts):
        inner = (x0 > 0, y0 > 0, x1 < W, y1 < H)
        for ln in lines:
            bx, by, bw, bh = ln["box"]
            cut = (inner[0] and bx < 3) or (inner[1] and by < 3) or \
                (inner[2] and bx + bw > x1 - x0 - 3) or (inner[3] and by + bh > y1 - y0 - 3)
            cands.append((2 if cut else 0, _shift(ln, x0, y0)))
    return _merge(cands)
