import asyncio
import json
import subprocess
import sys
import types

import pytest
from PIL import Image, ImageDraw

from ainotate import ocr, ocr_engines
from ainotate.style import BUNDLED_FONT


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    ocr._cache.clear()
    monkeypatch.delenv("AINOTATE_OCR_BACKEND", raising=False)


def words_line(line, text, x, y, h=14, cw=8, conf=0.9):
    """Line item + word items laid out left to right, cw px per character, one space = cw px."""
    out, cx, ws = [], x, []
    for j, t in enumerate(text.split()):
        ws.append({"text": t, "x": cx, "y": y, "w": cw * len(t), "h": h, "conf": conf, "line": line,
                   "level": "word", "word": j})
        cx += cw * (len(t) + 1)
    out.append({"text": text, "x": x, "y": y, "w": cx - cw - x, "h": h, "conf": conf, "line": line,
                "level": "line"})
    return out + ws


def items_of(*lines):
    out = []
    for i, (text, x, y) in enumerate(lines):
        out += words_line(i, text, x, y)
    return out


def fake_engine(monkeypatch, lines_fn, tile=None, name="tesseract"):
    monkeypatch.setitem(ocr._ENGINES, name, (lambda: True, lines_fn, tile))
    for other in ocr.BACKENDS:
        if other != name:
            monkeypatch.setitem(ocr._ENGINES, other, (lambda: False, None, None))


# ---------- geometry / backends ----------

def test_vision_box_to_px_flips_origin():
    assert ocr._vision_box_to_px((0.1, 0.8, 0.4, 0.05), 1000, 500) == pytest.approx((100, 75, 400, 25))
    r = types.SimpleNamespace(origin=types.SimpleNamespace(x=0, y=0), size=types.SimpleNamespace(width=1, height=0.5))
    assert ocr._vision_box_to_px(r, 200, 100) == (0, 50, 200, 50)


def test_word_spans_count_utf16():
    assert ocr._word_spans("👍 Like ș") == [("👍", 0, 2), ("Like", 3, 4), ("ș", 8, 1)]


class _Rect:
    def __init__(self, x, y, w, h):
        self.origin, self.size = types.SimpleNamespace(x=x, y=y), types.SimpleNamespace(width=w, height=h)


class _Cand:
    def __init__(self, text, word_rects):
        self.text, self.word_rects, self.ranges = text, word_rects, []

    def string(self):
        return self.text

    def confidence(self):
        return 0.5

    def boundingBoxForRange_error_(self, rng, err):
        self.ranges.append(rng)
        r = self.word_rects.get(rng)
        return (types.SimpleNamespace(boundingBox=lambda: r) if r else None), None


class _Obs:
    def __init__(self, rect, cand):
        self.rect, self.cand = rect, cand

    def boundingBox(self):
        return self.rect

    def topCandidates_(self, n):
        return [self.cand]


def install_fake_vision(monkeypatch, observations):
    class Req:
        def __init__(self):
            self.langs = None

        @classmethod
        def alloc(cls):
            return cls()

        def init(self):
            return self

        def setRecognitionLevel_(self, v): self.level = v
        def setUsesLanguageCorrection_(self, v): self.corr = v
        def setRecognitionLanguages_(self, v): self.langs = v

        def supportedRecognitionLanguagesAndReturnError_(self, e):
            return ["en-US", "ro-RO", "de-DE"], None

        def results(self):
            return observations

    class Handler:
        @classmethod
        def alloc(cls):
            return cls()

        def initWithCGImage_options_(self, cg, opts):
            return self

        def performRequests_error_(self, reqs, e):
            Handler.seen = reqs
            return True, None

    vision = types.SimpleNamespace(VNRecognizeTextRequest=Req, VNImageRequestHandler=Handler,
                                   VNRequestTextRecognitionLevelAccurate=0)
    quartz = types.SimpleNamespace(**{n: None for n in (
        "CGDataProviderCreateWithCFData", "CGImageCreate", "CGColorSpaceCreateDeviceRGB",
        "kCGImageAlphaNoneSkipLast", "kCGRenderingIntentDefault")})
    monkeypatch.setitem(sys.modules, "Vision", vision)
    monkeypatch.setitem(sys.modules, "Quartz", quartz)
    monkeypatch.setitem(sys.modules, "Foundation", types.SimpleNamespace(NSData=None))
    monkeypatch.setattr(ocr_engines, "_vsyms", {})
    monkeypatch.setattr(ocr_engines, "_vision_cgimage", lambda img: "cg")
    return Handler


def test_vision_backend_converts_line_and_word_boxes(monkeypatch, tmp_path):
    # 1000x500 image; line "Save changes" at normalized (0.1, 0.8, 0.4, 0.05) bottom-left
    cand = _Cand("Save changes", {(0, 4): _Rect(0.1, 0.8, 0.15, 0.05), (5, 7): _Rect(0.27, 0.8, 0.23, 0.05)})
    handler = install_fake_vision(monkeypatch, [_Obs(_Rect(0.1, 0.8, 0.4, 0.05), cand)])
    fake_engine(monkeypatch, ocr._vision_run, tile=1000, name="vision")
    p = tmp_path / "a.png"
    Image.new("RGB", (1000, 500), "white").save(p)
    items = ocr.read(p, langs=["ro-RO", "xx-XX", "en-US"])
    assert handler.seen[0].langs == ["ro-RO", "en-US"] and handler.seen[0].corr is True
    line, w1, w2 = items
    assert (line["level"], line["text"], line["x"], line["y"], line["w"], line["h"]) == ("line", "Save changes", 100, 75, 400, 25)
    assert (w1["text"], w1["x"], w1["y"], w1["w"], w1["h"], w1["conf"]) == ("Save", 100, 75, 150, 25, 0.5)
    assert (w2["text"], w2["x"], w2["w"], w2["word"], w2["line"]) == ("changes", 270, 230, 1, 0)
    assert cand.ranges == [(0, 4), (5, 7)]


def test_vision_missing_or_whole_line_word_boxes_are_estimated(monkeypatch):
    whole = _Rect(0.0, 0.0, 1.0, 1.0)   # old Vision: every range returns the whole line
    cand = _Cand("ab cd", {(0, 2): whole})
    install_fake_vision(monkeypatch, [_Obs(whole, cand)])
    (ln,) = ocr._vision_run(Image.new("RGB", (100, 10)), ["en-US"])
    assert [w["box"] for w in ln["words"]] == [pytest.approx((0, 0, 40, 10)), pytest.approx((60, 0, 40, 10))]


def fake_winrt(lines, supported=("en-US",)):
    rect = lambda x, y, w, h: types.SimpleNamespace(x=x, y=y, width=w, height=h)
    result = types.SimpleNamespace(lines=[
        types.SimpleNamespace(text=" ".join(t for t, _ in ws),
                              words=[types.SimpleNamespace(text=t, bounding_rect=rect(*b)) for t, b in ws])
        for ws in lines])
    calls = {}

    class Engine:
        async def recognize_async(self, bmp):
            calls["bitmap"] = bmp
            return result

    class OcrEngine:
        is_language_supported = staticmethod(lambda lang: lang.tag in supported)
        try_create_from_user_profile_languages = staticmethod(lambda: Engine())

        @staticmethod
        def try_create_from_language(lang):
            calls["lang"] = lang.tag
            return Engine()

    class Writer:
        def write_bytes(self, b): calls["bytes"] = len(b)
        def detach_buffer(self): return "buf"

    sb = types.SimpleNamespace(create_copy_from_buffer=lambda buf, fmt, w, h: ("bmp", fmt, w, h))
    return {"ocr": types.SimpleNamespace(OcrEngine=OcrEngine),
            "imaging": types.SimpleNamespace(SoftwareBitmap=sb, BitmapPixelFormat=types.SimpleNamespace(BGRA8="BGRA8")),
            "streams": types.SimpleNamespace(DataWriter=Writer),
            "glob": types.SimpleNamespace(Language=lambda t: types.SimpleNamespace(tag=t))}, calls


def test_winrt_backend_boxes_region_and_language(monkeypatch, tmp_path):
    mods, calls = fake_winrt([[("Salvează", (10, 5, 60, 12)), ("tot", (75, 5, 20, 12))]], supported=("ro",))
    monkeypatch.setattr(ocr_engines, "_winrt_modules", lambda: mods)
    fake_engine(monkeypatch, ocr._winrt_run, tile=2000, name="winrt")
    p = tmp_path / "w.png"
    Image.new("RGB", (400, 300), "white").save(p)
    items = ocr.read(p, region=[100, 50, 300, 250])
    assert calls["lang"] == "ro" and calls["bitmap"] == ("bmp", "BGRA8", 200, 200) and calls["bytes"] == 200 * 200 * 4
    line, a, b = items
    assert (line["x"], line["y"], line["w"], line["h"], line["conf"]) == (110, 55, 85, 12, None)
    assert (a["text"], a["x"], a["y"], a["w"]) == ("Salvează", 110, 55, 60)
    assert (b["x"], b["w"]) == (175, 20)


def test_winrt_works_inside_a_running_event_loop(monkeypatch):
    mods, _ = fake_winrt([[("OK", (1, 2, 3, 4))]])
    monkeypatch.setattr(ocr_engines, "_winrt_modules", lambda: mods)

    async def main():   # e.g. called from the MCP server
        return ocr._winrt_run(Image.new("RGB", (20, 20)), ["en-US"])
    (ln,) = asyncio.run(main())
    assert ln["box"] == (1, 2, 3, 4) and ln["conf"] is None


def test_winrt_without_language_explains(monkeypatch):
    mods, _ = fake_winrt([], supported=())
    mods["ocr"].OcrEngine.try_create_from_user_profile_languages = staticmethod(lambda: None)
    with pytest.raises(ocr.OcrError, match="Language.OCR"):
        ocr._winrt_engine(mods, ["ro-RO"])


TSV = "\n".join([
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
    "1\t1\t0\t0\t0\t0\t0\t0\t800\t400\t-1\t",
    "5\t1\t1\t1\t1\t1\t100\t40\t80\t28\t96.5\tStatus",
    "5\t1\t2\t1\t1\t1\t190\t42\t100\t26\t90.0\tupdated",   # psm 11: own block, same row
    "5\t1\t3\t1\t1\t1\t700\t40\t60\t28\t80.0\tSource",    # far right: separate line
    "5\t1\t4\t1\t1\t1\t100\t200\t60\t28\t-1\t ",
])


def test_parse_tesseract_tsv_regroups_rows_and_unscales():
    lines = ocr._parse_tesseract_tsv(TSV, k=2)
    assert [l["text"] for l in lines] == ["Status updated", "Source"]
    assert lines[0]["box"] == (50, 20, 95, 14) and lines[0]["conf"] == pytest.approx(0.9325)
    assert lines[0]["words"][1]["box"] == (95, 21, 50, 13)


def test_tesseract_run_args_and_upscale(monkeypatch):
    calls = []

    def run(args, **kw):
        calls.append(args)
        if "--list-langs" in args:
            return subprocess.CompletedProcess(args, 0, "List of available languages (3):\neng\nosd\nron\n", "")
        assert Image.open(args[1]).size == (200, 100)   # small image upscaled x2
        return subprocess.CompletedProcess(args, 0, TSV, "")
    monkeypatch.setattr(ocr.subprocess, "run", run)
    monkeypatch.setattr(ocr_engines, "_tesseract_cmd", lambda: "/x/tesseract")
    monkeypatch.setattr(ocr_engines, "_tess_langs_cache", {})
    lines = ocr._tesseract_run(Image.new("RGB", (100, 50)), ["ro-RO", "en-US", "ja-JP"])
    assert calls[-1][-2:] == ["-l", "ron+eng"] and "tessedit_create_tsv=1" in calls[-1]
    assert lines[0]["box"] == (50, 20, 95, 14)


def test_tiles_cover_with_overlap():
    t = ocr._tiles(3610, 887, 1000, 250)
    assert t[0][0] == 0 and t[-1][2] == 3610 and all(b[1] == 0 and b[3] == 887 for b in t)
    assert all(a[2] - b[0] >= 250 for a, b in zip(t, t[1:]))
    assert ocr._tiles(800, 600, 1000, 250) == [(0, 0, 800, 600)]


def test_merge_prefers_whole_tile_lines_then_full_pass():
    ln = lambda t, b: {"text": t, "box": b, "words": [], "conf": 1}
    kept = ocr._merge([(2, ln("Sav", (990, 10, 10, 10))), (1, ln("Save all", (985, 10, 60, 10))),
                       (0, ln("Save", (985, 10, 40, 10))), (0, ln("Save", (986, 10, 40, 10))),
                       (1, ln("Big title", (0, 300, 900, 120)))])
    assert [k["text"] for k in kept] == ["Save", "Big title"]


def test_big_image_is_tiled_and_shifted(monkeypatch, tmp_path):
    seen = []

    def run(img, langs):
        seen.append(img.size)
        return [{"text": "Hi", "conf": 1.0, "box": (400, 10, 30, 12), "words": [{"text": "Hi", "box": (400, 10, 30, 12)}]}]
    fake_engine(monkeypatch, run, tile=1000)
    p = tmp_path / "big.png"
    Image.new("RGB", (3000, 400), "white").save(p)
    xs = [i["x"] for i in ocr.read(p) if i["level"] == "line"]
    assert (3000, 400) in seen and len(seen) == 5
    assert xs == [400, 1067, 1733, 2400]   # full-pass duplicate of tile 0 dropped


def test_read_region_cache_and_alpha(monkeypatch, tmp_path):
    got = []

    def run(img, langs):
        got.append((img.size, img.getpixel((0, 0)), langs))
        return [{"text": "x", "conf": 1.0, "box": (1, 2, 3, 4), "words": [{"text": "x", "box": (1, 2, 3, 4)}]}]
    fake_engine(monkeypatch, run)
    p = tmp_path / "t.png"
    Image.new("RGBA", (100, 80), (0, 0, 0, 0)).save(p)
    a = ocr.read(p, region="10,20,60,70")
    assert got == [((50, 50), (255, 255, 255), ["ro-RO", "en-US"])]   # transparent -> white
    assert (a[0]["x"], a[0]["y"]) == (11, 22)
    a[0]["x"] = 999
    assert ocr.read(p, region="10,20,60,70")[0]["x"] == 11 and len(got) == 1   # cached, copies
    with pytest.raises(ocr.OcrError, match="x2>x1"):
        ocr.read(p, region=[50, 0, 10, 10])
    with pytest.raises(ocr.OcrError, match="image not found"):
        ocr.read(tmp_path / "nope.png")


def test_no_backend_says_what_to_install(monkeypatch):
    for b in ocr.BACKENDS:
        monkeypatch.setitem(ocr._ENGINES, b, (lambda: False, None, None))
    with pytest.raises(ocr.OcrError) as e:
        ocr.pick_backend()
    msg = str(e.value)
    assert "pyobjc-framework-Vision" in msg and "winrt-Windows.Media.Ocr" in msg and "tesseract-ocr" in msg
    with pytest.raises(ocr.OcrError, match="not available"):
        ocr.pick_backend("vision")
    with pytest.raises(ocr.OcrError, match="unknown OCR backend"):
        ocr.pick_backend("easyocr")


def test_env_overrides_backend(monkeypatch):
    fake_engine(monkeypatch, None)
    monkeypatch.setitem(ocr._ENGINES, "vision", (lambda: True, None, None))
    assert ocr.pick_backend() == "vision"
    monkeypatch.setenv("AINOTATE_OCR_BACKEND", "tesseract")
    assert ocr.pick_backend() == "tesseract"


# ---------- matcher ----------

def test_norm_folds_case_and_romanian_diacritics():
    assert ocr.norm("ȘTERGE Ţară  Înapoi") == "sterge tara inapoi"
    assert ocr.score("salveaza", "Salvează") == 1.0
    assert ocr.score("Keywords", "Keyw0rds") == 0.95
    assert 0.8 < ocr.score("Keywords", "Kevwords") < 0.95


def test_locate_multiword_across_words_whole_line_wins():
    items = items_of(("Demand", 100, 10), ("Demand Max 12m", 300, 10), ("Status updated at", 100, 60))
    c = ocr.locate(None, "status UPDATED at", items=items)
    assert c[0]["text"] == "Status updated at" and c[0]["rect"] == [100, 60, 236, 74] and c[0]["score"] == 1.0
    d = ocr.locate(None, "Demand", items=items)
    assert [(x["x"], x["top"]) for x in d] == [(100, True), (300, False)]   # part of a longer label: weaker


def test_locate_ties_are_all_returned_and_nth_picks_in_reading_order():
    items = items_of(("Save", 500, 300), ("Save", 40, 300), ("Save", 900, 20))
    c = ocr.locate(None, "save", items=items)
    assert len(c) == 3 and all(x["top"] for x in c)
    assert [x["x"] for x in [ocr.locate(None, "save", nth=i, items=items)[0] for i in range(3)]] == [900, 40, 500]
    with pytest.raises(ocr.OcrError, match="nth=3"):
        ocr.locate(None, "save", nth=3, items=items)
    w = ocr.locate(None, "save", within=[0, 250, 600, 400], items=items)
    assert [x["x"] for x in w] == [500, 40]


def test_locate_short_queries_are_strict_and_fuzz_has_limits():
    items = items_of(("KWS", 0, 0), ("Salvează", 0, 40), ("K W", 0, 80))
    assert [x["text"] for x in ocr.locate(None, "KW", items=items)] == ["K W"]
    assert ocr.locate(None, "Anulează", items=items) == []
    assert ocr.locate(None, "Salveza", items=items)[0]["text"] == "Salvează"


def test_locate_exact_and_punctuation_parts():
    items = items_of(("Store-Keywords app", 100, 10), ("Keywords", 100, 50), ("Keyword", 100, 90))
    c = ocr.locate(None, "Keywords", items=items)
    assert c[0]["rect"] == [100, 50, 164, 64] and c[0]["top"]
    part = [x for x in c if x["estimated"]][0]
    assert part["text"] == "Keywords" and part["x"] == 100 + 6 * 8 and part["w"] == 64 and not part["top"]
    ex = ocr.locate(None, "Keywords", exact=True, items=items)
    assert [x["y"] for x in ex] == [50]
    assert ocr.locate(None, "dashlane.com", items=items_of(("dashlane.com", 5, 5)))[0]["text"] == "dashlane.com"


def test_locate_skips_icon_bullets_and_far_gaps():
    items = items_of(("• Keywords", 100, 10))
    c = ocr.locate(None, "Keywords", items=items)
    assert c[0]["x"] == 116 and c[0]["score"] == 1.0
    far = words_line(0, "Brand", 0, 0) + [dict(words_line(0, "Volume", 600, 0)[1], word=1)]
    assert ocr.locate(None, "Brand Volume", items=far) == []


def test_resolve_converts_units_and_refuses_to_guess():
    items = items_of(("Save", 200, 100), ("Save", 600, 100), ("Cancel", 200, 300))
    assert ocr.resolve(None, {"text": "cancel"}, scale=2, items=items) == [100, 150, 124, 157]
    with pytest.raises(ocr.AmbiguousText) as e:
        ocr.resolve(None, {"text": "Save"}, items=items)
    assert len(e.value.candidates) == 2 and "nth" in str(e.value)
    assert ocr.resolve(None, {"text": "Save", "within": [250, 0, 400, 100]}, scale=2, items=items) == [300, 50, 316, 57]
    with pytest.raises(ocr.TextNotFound) as e:
        ocr.resolve(None, {"text": "Delete"}, items=items)
    assert e.value.near and "closest OCR text" in str(e.value)


def test_cli_locate_formats_and_exit_codes(monkeypatch, capsys):
    items = items_of(("Save", 200, 100), ("Save", 600, 100), ("Cancel", 200, 300), ("Cancel all", 400, 300))
    monkeypatch.setattr(ocr, "read", lambda *a, **k: items)
    assert ocr.cli_locate(["x.png", "Cancel"]) == ocr.EXIT_OK
    out, err = capsys.readouterr()
    assert out == '[200,300,248,314]  score=1.00  "Cancel"\n' and "1 weaker" in err
    assert ocr.cli_locate(["x.png", "Save"]) == ocr.EXIT_AMBIGUOUS
    out, err = capsys.readouterr()
    assert out.count("\n") == 2 and "ask the user" in err
    assert ocr.cli_locate(["x.png", "Save", "--nth", "1", "--json"]) == ocr.EXIT_OK
    (hit,) = json.loads(capsys.readouterr()[0])
    assert hit["rect"] == [600, 100, 632, 114]
    assert ocr.cli_locate(["x.png", "Cancel", "--all", "--json"]) == ocr.EXIT_OK
    assert len(json.loads(capsys.readouterr()[0])) == 2
    assert ocr.cli_locate(["x.png", "Delete"]) == ocr.EXIT_NOT_FOUND
    assert "closest OCR text" in capsys.readouterr()[1]


def test_cli_locate_exact_duplicates_stay_ambiguous_with_all(monkeypatch, capsys):
    """Regression: --all used to turn two equally good matches into exit 0."""
    items = items_of(("Save", 200, 100), ("Save", 600, 100), ("Save as", 200, 300))
    monkeypatch.setattr(ocr, "read", lambda *a, **k: items)
    assert ocr.cli_locate(["x.png", "Save", "--all"]) == ocr.EXIT_AMBIGUOUS
    out, err = capsys.readouterr()
    assert out.count("\n") == 3 and "(weaker)" in out and "--nth" in err
    assert ocr.cli_locate(["x.png", "Save", "--all", "--nth", "0"]) == ocr.EXIT_OK


def test_within_failure_counts_matches_outside(monkeypatch, capsys):
    items = items_of(("Save", 200, 100), ("Save", 600, 100), ("Cancel", 200, 300))
    with pytest.raises(ocr.TextNotFound) as e:
        ocr.resolve(None, {"text": "Save", "within": [0, 250, 500, 400]}, items=items)
    msg = str(e.value)
    assert "found 2 match(es) outside the region" in msg and "[200, 100, 232, 114]" in msg
    assert "closest" not in msg and e.value.near == []
    monkeypatch.setattr(ocr, "read", lambda *a, **k: items)
    assert ocr.cli_locate(["x.png", "Save", "--within", "0,250,500,400"]) == ocr.EXIT_NOT_FOUND
    assert "found 2 match(es) outside the region" in capsys.readouterr()[1]
    # nothing anywhere: the closest text, said to be inside the region
    with pytest.raises(ocr.TextNotFound, match="not found inside the region; closest OCR text"):
        ocr.resolve(None, {"text": "Delete", "within": [0, 0, 900, 400]}, items=items)


def test_not_found_and_ambiguous_mask_secrets(monkeypatch, capsys):
    """OCR text quoted in errors goes through privacy.mask_text (message, near, candidates)."""
    secret = "jane.doe@example.com"
    items = items_of((secret, 100, 10), ("Order", 100, 60), ("Order", 400, 60))
    with pytest.raises(ocr.TextNotFound) as e:
        ocr.resolve(None, {"text": "Delete account"}, items=items)
    assert e.value.near and secret not in str(e.value) and all(secret not in n["text"] for n in e.value.near)
    assert "jane.doe" not in str(e.value) and "closest OCR text" in str(e.value)
    with pytest.raises(ocr.AmbiguousText) as e:
        ocr.resolve(None, {"text": "Order"}, items=items)
    assert e.value.candidates and all("text" in c for c in e.value.candidates)
    items2 = items_of(("token sk-live-ABCDEF0123456789abcdef", 100, 10), ("Order", 100, 60))
    monkeypatch.setattr(ocr, "read", lambda *a, **k: items2)
    assert ocr.cli_locate(["x.png", "token sk-live-ABCDEF0123456789abcdeX"]) in (ocr.EXIT_OK, ocr.EXIT_NOT_FOUND)
    out, err = capsys.readouterr()
    assert "ABCDEF0123456789abcdef" not in out + err


# ---------- real engine (macOS) ----------

@pytest.mark.skipif("vision" not in ocr.available_backends(), reason="Apple Vision not installed")
def test_vision_real_romanian(tmp_path):
    from PIL import ImageFont
    f = ImageFont.truetype(str(BUNDLED_FONT), 22)
    img = Image.new("RGB", (700, 200), "white")
    d = ImageDraw.Draw(img)
    d.text((30, 30), "Șterge contul definitiv", fill="black", font=f)
    d.rounded_rectangle([400, 120, 600, 170], radius=8, fill=(0, 113, 227))
    d.text((440, 132), "Salvează", fill="white", font=f)
    p = tmp_path / "ro.png"
    img.save(p)
    (save,) = [c for c in ocr.locate(p, "salveaza") if c["top"]]
    x1, y1, x2, y2 = save["rect"]
    assert 430 <= x1 <= 446 and 125 <= y1 <= 140 and 520 <= x2 <= 545 and y2 <= 165
    assert ocr.locate(p, "sterge contul")[0]["rect"][0] <= 34
