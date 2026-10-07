"""ainotate.mcp_server through the MCP SDK's in-memory client (no network); one stdio test that
launches the real server process, and one real web capture marked `web`."""
import base64
import io
import json
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from PIL import Image

pytest.importorskip("mcp")
from mcp import Client  # noqa: E402

from ainotate import mcp_server  # noqa: E402
from ainotate.mcp_server import PREVIEW_MAX, mcp  # noqa: E402

pytestmark = pytest.mark.anyio

# Playwright's browser cache, resolved at import: conftest points HOME at a temp dir per test.
BROWSERS = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or (
    os.path.expanduser("~/Library/Caches/ms-playwright") if sys.platform == "darwin" else
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright") if sys.platform == "win32" else
    os.path.expanduser("~/.cache/ms-playwright"))

TOOLS = {"annotate", "preview", "capture_web", "shoot", "locate", "capture_screen", "list_windows",
         "capture_window", "clipboard_image", "grid", "zoom", "make_guide", "compare", "animate",
         "copy_to_clipboard", "doctor"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def call(name, args=None):
    async with Client(mcp) as c:
        return await c.call_tool(name, args or {})


def payload(res):
    data = json.loads(res.content[0].text)
    assert res.structured_content == data
    assert "Traceback" not in res.content[0].text
    return data


def preview_of(res):
    """The ImageContent block: JPEG, at most PREVIEW_MAX px on the long side."""
    imgs = [b for b in res.content if b.type == "image"]
    assert len(imgs) == 1, res.content
    assert imgs[0].mime_type == "image/jpeg"
    im = Image.open(io.BytesIO(base64.b64decode(imgs[0].data)))
    assert im.format == "JPEG" and max(im.size) <= PREVIEW_MAX
    return im


def no_image(res):
    assert not [b for b in res.content if b.type == "image"]


def spec_for(img, **kw):
    s = {"input": img, "scale": 1, "name": "test shot",
         "marks": [{"type": "box", "rect": [1300, 100, 1520, 150], "label": "Checkout"},
                   {"type": "redact", "rect": [290, 120, 700, 140]}]}
    s.update(kw)
    return s


# ---------------------------------------------------------------- discovery

async def test_lists_tools_prompts_and_spec_resource():
    async with Client(mcp) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        assert TOOLS <= names
        prompts = {p.name for p in (await c.list_prompts()).prompts}
        assert {"annotate-guide", "bug-ticket"} <= prompts
        uris = {str(r.uri) for r in (await c.list_resources()).resources}
        assert "ainotate://spec" in uris
        spec = (await c.read_resource("ainotate://spec")).contents[0].text
        assert "redact" in spec and "scale" in spec and "magnify" in spec
        assert spec.startswith("# AInotate spec reference\n\n## ") and "show.py" not in spec
        assert "\n## 2. " not in spec
        p = await c.get_prompt("bug-ticket", {"issue": "coupon not applied", "url": "https://x.test"})
        text = p.messages[0].content.text
        assert "coupon not applied" in text and "locate" in text and "Expected" in text
        p = await c.get_prompt("annotate-guide", {"goal": "export a report"})
        assert "make_guide" in p.messages[0].content.text and "draft=false" in p.messages[0].content.text
        tools = {t.name: t for t in (await c.list_tools()).tools}
        for name in ("annotate", "shoot", "compare", "animate", "make_guide"):
            props = tools[name].input_schema["properties"]
            assert props["draft"]["default"] is True, name
            assert "draft=false" in tools[name].description, name
        for name in ("compare", "animate", "make_guide"):
            assert "name" in tools[name].input_schema["properties"], name


# ---------------------------------------------------------------- annotate / preview

async def test_annotate_draft_returns_json_and_small_jpeg_preview(ui, out_dirs):
    res = await call("annotate", {"spec": spec_for(ui)})          # draft is the default
    assert not res.is_error
    d = payload(res)
    assert d["ok"] and d["draft"] and len(d["paths"]) == 1 and not out_dirs[0].exists()
    assert "call again with draft=false to save" in d["next"]
    assert d["legend"] == [] and d["redactions"] == []
    steps = [{"type": "step", "n": n, "rect": [1300, y, 1520, y + 50], "label": t}
             for n, y, t in ((1, 100, "Pay"), (2, 170, "Coupon"))]
    d = payload(await call("annotate", {"spec": spec_for(ui, marks=steps)}))
    assert d["legend"] == [{"n": 1, "label": "Pay"}, {"n": 2, "label": "Coupon"}]
    full = Path(d["paths"][0])
    assert full.is_file() and Image.open(full).size == (d["image"]["width"], d["image"]["height"])
    assert d["image"]["width"] == 1600            # full resolution stays on disk
    im = preview_of(res)
    assert im.size == (1024, 640)


async def test_annotate_final_saves_to_output_and_backup(ui, out_dirs):
    out, bak = out_dirs
    d = payload(await call("annotate", {"spec": json.dumps(spec_for(ui)), "draft": False}))   # JSON string accepted
    assert d["ok"] and not d["draft"]
    assert [Path(p).parent for p in d["paths"]] == [out, bak]
    assert "Deliver" in d["next"]


async def test_preview_is_a_debug_draft(ui, out_dirs):
    res = await call("preview", {"spec": spec_for(ui)})
    d = payload(res)
    assert d["ok"] and d["draft"] and "cyan" in d["next"]
    assert not any(out_dirs[0].glob("*.png"))
    preview_of(res)


async def test_invalid_spec_is_a_structured_error(ui):
    res = await call("annotate", {"spec": {"input": ui, "marks": [{"type": "bogus"}]}})
    assert res.is_error
    d = payload(res)
    assert d["ok"] is False and d["error"]["type"] == "SpecError"
    assert "bogus" in d["error"]["message"] and "ainotate://spec" in d["error"]["fix"]
    no_image(res)


async def test_missing_input_image_names_the_path(tmp_path):
    d = payload(await call("annotate", {"spec": {"input": str(tmp_path / "nope.png"), "marks": []}}))
    assert d["error"]["type"] == "SpecError" and "nope.png" in d["error"]["message"]


async def test_bad_json_string_spec():
    d = payload(await call("annotate", {"spec": "{not json"}))
    assert d["error"]["type"] == "SpecError" and "JSON" in d["error"]["message"]


# ---------------------------------------------------------------- grid / zoom

async def test_grid_preview_is_the_grid(ui):
    res = await call("grid", {"image_path": ui, "step": 100})
    d = payload(res)
    assert d["ok"] and Path(d["grid_path"]).is_file()
    assert d["image"]["width"] == 1600 and "zoom" in d["next"]
    preview_of(res)


async def test_big_image_preview_is_capped(tmp_path):
    big = tmp_path / "big.png"
    Image.new("RGBA", (3000, 2000), (10, 20, 30, 128)).save(big)
    res = await call("grid", {"image_path": str(big)})
    assert preview_of(res).size == (1024, 683)


async def test_zoom_returns_the_zoom_as_preview(ui):
    res = await call("zoom", {"image_path": ui, "x1": 1280, "y1": 90, "x2": 1540, "y2": 230})
    d = payload(res)
    assert d["ok"] and Path(d["zoom_path"]).is_file() and "source px" in d["note"]
    assert d["zoom_size"] == [780, 420]          # x3 enlargement
    preview_of(res)


async def test_zoom_empty_region_is_a_tool_error(ui):
    res = await call("zoom", {"image_path": ui, "x1": 5000, "y1": 0, "x2": 5100, "y2": 50})
    d = payload(res)
    assert res.is_error and d["error"]["type"] == "ToolError" and "1600x1000" in d["error"]["message"]


# ---------------------------------------------------------------- export

async def test_compare_animate_guide(ui, ui_dark, out_dirs, tmp_path):
    out = out_dirs[0]
    res = await call("compare", {"before": ui, "after": ui_dark, "labels": ["Înainte", "După"]})
    d = payload(res)
    assert d["ok"] and d["draft"] and Path(d["paths"][0]).is_file() and Path(d["paths"][0]).parent != out
    assert "draft=false" in d["next"]
    preview_of(res)
    d = payload(await call("compare", {"before": ui, "after": ui_dark, "name": "coupon fix", "draft": False}))
    assert Path(d["paths"][0]).parent == out and d["paths"][0].endswith(" coupon fix.png")
    for fmt, ext in (("apng", ".png"), ("gif", ".gif")):
        res = await call("animate", {"frames": [ui, ui_dark], "format": fmt, "durations_ms": 400})
        d = payload(res)
        assert d["ok"] and d["paths"][0].endswith(ext) and d["frames"] == 2 and d["draft"]
        assert preview_of(res).size == (1024, 640)                # the first frame
    d = payload(await call("animate", {"frames": [ui, ui_dark], "name": "flow", "draft": False}))
    assert Path(d["paths"][0]).parent == out and d["paths"][0].endswith(" flow.png")
    steps = [{"image": ui, "text": "Open the cart"}, {"image": ui_dark, "text": "Apply the coupon"}]
    res = await call("make_guide", {"steps": steps, "title": "Coupon guide"})
    d = payload(res)
    assert d["ok"] and set(d["files"]) == {"md", "html"} and d["steps"] == 2 and d["draft"]
    assert all(Path(p).is_file() and out not in Path(p).parents for p in d["files"].values())
    first = preview_of(res).convert("RGB")                     # preview = the first step's image
    assert first.size == (1024, 640) and first.getpixel((500, 600))[0] > 200   # light ui, not ui_dark
    d = payload(await call("make_guide", {"steps": steps, "name": "coupon", "draft": False}))
    assert all(Path(p).parent.parent == out and Path(p).parent.name.endswith(" coupon")
               for p in d["files"].values())


async def test_guide_error_is_structured(tmp_path, ui):
    d = payload(await call("make_guide", {"steps": [{"image": str(tmp_path / "x.png")}]}))
    assert d["error"]["type"] == "ExportError" and "x.png" in d["error"]["message"]
    assert "steps[0].image" in d["error"]["fix"] and "clipboard_image" in d["error"]["fix"]


async def test_fix_hints_are_specific(tmp_path, ui):
    d = payload(await call("annotate", {"spec": {"input": str(tmp_path / "gone.png"), "marks": []}}))
    assert "clipboard_image" in d["error"]["fix"] and "path" in d["error"]["fix"]
    d = payload(await call("compare", {"before": ui, "after": str(tmp_path / "gone.png")}))
    assert d["error"]["fix"].startswith("`after`") and "clipboard_image" in d["error"]["fix"]
    d = payload(await call("animate", {"frames": [ui], "format": "webm"}))
    assert "`format`" in d["error"]["fix"]
    d = payload(await call("compare", {"before": ui, "after": ui, "labels": ["one"]}))
    assert "`labels`" in d["error"]["fix"]


async def test_copy_to_clipboard(monkeypatch, ui):
    from ainotate import export
    seen = []
    monkeypatch.setattr(export, "copy_to_clipboard", lambda p: seen.append(p))
    d = payload(await call("copy_to_clipboard", {"path": ui}))
    assert d["ok"] and seen == [ui]


# ---------------------------------------------------------------- desktop (faked capture)

async def test_desktop_tools(monkeypatch, ui):
    from ainotate.capture import clipboard, desktop
    monkeypatch.setattr(desktop, "screen", lambda out_path=None, region=None, display=None: Path(ui))
    monkeypatch.setattr(desktop, "window", lambda id, out_path=None: Path(ui))
    monkeypatch.setattr(desktop, "list_windows", lambda filter=None: [
        {"id": 42, "app": "Safari", "title": "Cart", "bounds": {"x": 0, "y": 0, "w": 800, "h": 600}}])
    monkeypatch.setattr(clipboard, "grab", lambda out_path=None: Path(ui))
    for name, args in (("capture_screen", {"region": [0, 0, 800, 600]}), ("capture_window", {"id": 42}),
                       ("clipboard_image", {})):
        res = await call(name, args)
        d = payload(res)
        assert d["ok"] and d["image"] == {"path": ui, "width": 1600, "height": 1000}, name
        preview_of(res)
    d = payload(await call("list_windows", {"filter": "saf"}))
    assert d["count"] == 1 and d["windows"][0]["id"] == 42


async def test_desktop_capture_error_carries_fix(monkeypatch):
    from ainotate.capture import desktop
    def boom(out_path=None, region=None, display=None):
        raise desktop.CaptureError(desktop.MAC_PERMISSION_HELP)
    monkeypatch.setattr(desktop, "screen", boom)
    res = await call("capture_screen")
    d = payload(res)
    assert res.is_error and d["error"]["type"] == "CaptureError"
    assert "Screen Recording" in d["error"]["message"] and "Claude" in d["error"]["fix"]


# ---------------------------------------------------------------- web (faked capture)

async def test_capture_web_returns_rects_scale_and_outlined_preview(monkeypatch, ui):
    from ainotate.capture import web
    got = {}

    def fake(url=None, **kw):
        got.update(kw, url=url)
        return web.WebCapture(Path(ui), 2.0, (800, 500), rects={"checkout": {"x": 650, "y": 50, "w": 110, "h": 25}},
                              missing={"coupon": "no visible element with text 'Coupon'"}, url=url)
    monkeypatch.setattr(web, "capture", fake)
    res = await call("capture_web", {"url": "https://shop.test", "targets": {
        "checkout": {"text": "Checkout"}, "coupon": {"text": "Coupon"}}, "preset": "laptop"})
    d = payload(res)
    assert d["ok"] and d["scale"] == 2.0 and d["rects"]["checkout"]["x"] == 650
    assert "coupon" in d["missing"] and "Not found: coupon" in d["suggestions"][0]
    assert d["spec_stub"] == {"input": ui, "scale": 2.0, "crop": "auto", "marks": []}
    assert got["targets"]["checkout"] == {"text": "Checkout"} and got["actions"] == []
    im = preview_of(res).convert("RGB")
    k = 1024 / 1600
    x, y = round(1300 * k), round(124 * k)      # left edge of the checkout rect, in the preview
    r, g, b = im.getpixel((x, y))
    assert r > 180 and b > 100 and g < 120      # magenta outline drawn


async def test_capture_web_errors(monkeypatch):
    from ainotate.capture import web
    d = payload(await call("capture_web", {"url": "https://x.test", "preset": "tablet"}))
    assert d["error"]["type"] == "TargetSpecError" and "tablet" in d["error"]["message"]

    def fail(url=None, **kw):
        raise web.CaptureError("net::ERR_NAME_NOT_RESOLVED at https://x.test")
    monkeypatch.setattr(web, "capture", fail)
    d = payload(await call("capture_web", {"url": "https://x.test"}))
    assert d["error"]["type"] == "CaptureError" and "cdp_url" in d["error"]["fix"]


async def test_missing_dependency_suggests_install(monkeypatch):
    from ainotate.capture import web

    def no_pw(url=None, **kw):
        raise ModuleNotFoundError("No module named 'playwright'", name="playwright")
    monkeypatch.setattr(web, "capture", no_pw)
    d = payload(await call("capture_web", {"url": "https://x.test"}))
    assert "ainotate[web]" in d["error"]["fix"] and "playwright install chromium" in d["error"]["fix"]


# ---------------------------------------------------------------- shoot / locate (lazy modules)

async def test_shoot_not_available(monkeypatch):
    monkeypatch.setitem(sys.modules, "ainotate.shoot", None)
    res = await call("shoot", {"spec": {"url": "https://x.test", "marks": []}})
    d = payload(res)
    assert res.is_error and d["error"]["type"] == "NotAvailable" and "capture_web" in d["error"]["fix"]


async def test_shoot_with_module(monkeypatch, ui):
    mod = ModuleType("ainotate.shoot")
    cap = SimpleNamespace(to_dict=lambda: {"image_path": ui, "scale": 2, "rects": {}, "missing": {}})
    seen = {}

    def fake(spec, draft=False):
        seen["draft"] = draft
        return SimpleNamespace(paths=[Path(ui)], warnings=["w1"], capture=cap,
                               legend=[{"n": 1, "label": "Pay"}], redactions=["email x1: j***@e***.com"])
    mod.shoot = fake
    monkeypatch.setitem(sys.modules, "ainotate.shoot", mod)
    res = await call("shoot", {"spec": {"url": "https://x.test", "marks": []}})
    d = payload(res)
    assert d["ok"] and d["paths"] == [ui] and d["warnings"] == ["w1"] and d["capture"]["scale"] == 2
    assert seen["draft"] is True and d["draft"] and "draft=false" in d["next"]
    assert d["legend"] == [{"n": 1, "label": "Pay"}] and d["redactions"] == ["email x1: j***@e***.com"]
    preview_of(res)


async def test_locate_not_available(monkeypatch, ui):
    monkeypatch.setitem(sys.modules, "ainotate.ocr", None)
    monkeypatch.delattr("ainotate.ocr", raising=False)
    d = payload(await call("locate", {"image_path": ui, "text": "Checkout"}))
    assert d["ok"] is False and "update AInotate" in d["error"]["fix"]


def _ocr_backend():
    try:
        from ainotate import ocr
        return ocr.available_backends()
    except Exception:
        return []


@pytest.mark.skipif(not _ocr_backend(), reason="no OCR engine on this machine")
async def test_locate_real_ocr(tmp_path):
    from PIL import ImageDraw

    from ainotate.style import load_font
    img = Image.new("RGB", (1200, 500), "white")
    d = ImageDraw.Draw(img)
    f = load_font(40)
    d.text((100, 100), "Checkout", fill="black", font=f)
    d.text((100, 300), "Apply coupon", fill="black", font=f)
    d.text((700, 300), "Apply coupon", fill="black", font=f)
    p = tmp_path / "ocr.png"
    img.save(p)
    res = await call("locate", {"image_path": str(p), "text": "Checkout"})
    data = payload(res)
    assert data["ok"] and not data["ambiguous"]
    x1, y1, x2, y2 = data["matches"][0]["rect"]
    assert 90 <= x1 <= 110 and 95 <= y1 <= 125 and x2 > 250
    preview_of(res)
    data = payload(await call("locate", {"image_path": str(p), "text": "Apply coupon"}))
    assert data["ambiguous"] and len(data["matches"]) == 2 and "nth" in data["suggestions"][0]
    data = payload(await call("locate", {"image_path": str(p), "text": "Apply coupon", "nth": 1}))
    assert not data["ambiguous"] and data["matches"][0]["rect"][0] > 600
    res = await call("locate", {"image_path": str(p), "text": "Zebra unicorn"})
    data = payload(res)
    assert res.is_error and data["error"]["type"] == "TextNotFound" and data["error"]["near"]
    res = await call("locate", {"image_path": str(p), "text": "Checkout", "within": [600, 250, 1200, 450]})
    data = payload(res)
    assert data["error"]["type"] == "TextNotFound" and "1 match(es) outside the region" in data["error"]["message"]


async def test_error_payloads_are_masked(monkeypatch, ui):
    from ainotate import ocr
    from ainotate.capture import web

    err = web.CaptureError("login failed for jane.doe@example.com with token sk-live-ABCDEF0123456789abcdef")
    monkeypatch.setattr(web, "capture", lambda url=None, **kw: (_ for _ in ()).throw(err))
    text = (await call("capture_web", {"url": "https://x.test"})).content[0].text
    assert "jane.doe@example.com" not in text and "ABCDEF0123456789abcdef" not in text
    secret = "mary.major@example.org"
    raw = ocr.OcrError(f"near {secret}")    # carries unmasked hints, as a 3rd-party error might
    raw.near, raw.candidates = [{"text": secret, "score": 0.4}], [{"text": secret, "rect": [1, 2, 3, 4]}]
    monkeypatch.setattr(ocr, "read", lambda *a, **k: (_ for _ in ()).throw(raw))
    res = await call("locate", {"image_path": ui, "text": "Pay"})
    d = payload(res)
    assert secret not in res.content[0].text and d["error"]["near"][0]["text"] != secret
    assert d["error"]["candidates"][0]["rect"] == [1, 2, 3, 4]


async def test_concurrent_calls_never_touch_stdout(monkeypatch, ui):
    """Regression: each call swapped the global sys.stdout from its thread (racy under concurrency)."""
    import anyio

    from ainotate import render as r
    real = r.render
    original = sys.stdout
    seen = []

    def noisy(spec, debug=False):
        seen.append(sys.stdout is original)
        print("stray print from a tool")
        return real(spec, debug=debug)
    monkeypatch.setattr(r, "render", noisy)
    async with Client(mcp) as c:
        async with anyio.create_task_group() as tg:
            for _ in range(6):
                tg.start_soon(c.call_tool, "annotate", {"spec": spec_for(ui)})
    assert len(seen) == 6 and all(seen) and sys.stdout is original


def test_guard_stdout_sends_prints_to_stderr_but_keeps_the_wire(monkeypatch):
    wire, err = io.TextIOWrapper(io.BytesIO()), io.StringIO()
    monkeypatch.setattr(sys, "stdout", wire), monkeypatch.setattr(sys, "stderr", err)
    mcp_server.guard_stdout(), mcp_server.guard_stdout()      # once: a second call does not wrap again
    print("stray")
    assert err.getvalue() == "stray\n" and sys.stdout.buffer is wire.buffer
    assert isinstance(sys.stdout, mcp_server._StrayToStderr) and sys.stdout._wire is wire


async def test_stdio_concurrent_calls_with_stray_prints(ui):
    """Real server process, tools that print, concurrent calls: every response still parses."""
    import anyio
    from mcp import StdioServerParameters
    src = str(Path(mcp_server.__file__).resolve().parents[1])
    env = dict(os.environ, PYTHONPATH=src + os.pathsep + os.environ.get("PYTHONPATH", ""))
    boot = ("import ainotate.render as r, ainotate.mcp_server as m\nreal = r.render\n"
            "r.render = lambda spec, debug=False: print('STRAY ' * 40) or real(spec, debug=debug)\nm.main()\n")
    params = StdioServerParameters(command=sys.executable, args=["-c", boot], env=env)
    results = []
    async with Client(params) as c:
        async def one():
            results.append(await c.call_tool("annotate", {"spec": spec_for(ui)}))
        async with anyio.create_task_group() as tg:
            for _ in range(5):
                tg.start_soon(one)
    assert len(results) == 5 and all(payload(r)["ok"] for r in results)


# ---------------------------------------------------------------- doctor

async def test_doctor(monkeypatch):
    from ainotate import doctor
    from ainotate.doctor import Check
    monkeypatch.setattr(doctor, "run_checks", lambda: [
        Check("python", True, "3.12"), Check("ocr", False, "none", "brew install tesseract", required=False)])
    d = payload(await call("doctor"))
    assert d["ok"] and len(d["checks"]) == 2 and d["fixes"] == ["ocr: brew install tesseract"]


# ---------------------------------------------------------------- real process over stdio

async def test_stdio_server_process(ui):
    from mcp import StdioServerParameters
    src = str(Path(mcp_server.__file__).resolve().parents[1])
    env = dict(os.environ, PYTHONPATH=src + os.pathsep + os.environ.get("PYTHONPATH", ""))
    params = StdioServerParameters(command=sys.executable, args=["-m", "ainotate.mcp_server"], env=env)
    async with Client(params) as c:
        assert TOOLS <= {t.name for t in (await c.list_tools()).tools}
        res = await c.call_tool("annotate", {"spec": spec_for(ui)})
        assert payload(res)["ok"]
        preview_of(res)


# ---------------------------------------------------------------- real browser

@pytest.mark.web
async def test_capture_web_hacker_news(monkeypatch):
    pytest.importorskip("playwright")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", BROWSERS)
    res = await call("capture_web", {"url": "https://news.ycombinator.com",
                                     "targets": {"logo": {"text": "Hacker News"},
                                                 "login": {"text": "login"}}})
    d = payload(res)
    if not d["ok"] and "Executable doesn't exist" in d["error"]["message"]:
        pytest.skip("Chromium not installed")
    assert d["ok"], d
    assert d["scale"] == 2 and d["image"]["width"] == 2560
    assert {"logo", "login"} <= set(d["rects"]) and not d["missing"]
    preview_of(res)
