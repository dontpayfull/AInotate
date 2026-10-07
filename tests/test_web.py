"""ainotate.capture.web: real browser captures marked `web`."""
import json
import os
import sys

import pytest
from PIL import Image

from ainotate.capture import web
from ainotate.capture.web import (PRESETS, CaptureError, TargetSpecError, AmbiguousTarget,
                                 inside, intersect, nice_scale, offset_rect, parse_action, parse_target,
                                 parse_target_arg, plan_scroll, png_size, rect, to_capture_coords)


# ---------------------------------------------------------------- real browser (marked web)
def _browsers_path():
    """Playwright's browser cache, resolved now: conftest later points HOME at a temp dir."""
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return os.environ["PLAYWRIGHT_BROWSERS_PATH"]
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Caches/ms-playwright")
    if sys.platform == "win32":
        return os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright")
    return os.path.expanduser("~/.cache/ms-playwright")


BROWSERS = _browsers_path()


@pytest.fixture(autouse=True)
def _real_browser_cache(monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", BROWSERS)


@pytest.fixture(scope="module")
def chromium():
    pytest.importorskip("playwright")
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", BROWSERS)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            pw.chromium.launch().close()
    except Exception as e:  # browsers not installed, sandbox, ...
        pytest.skip(f"Chromium not available: {e}")


def _pixels(cap, name):
    """Colors just inside two opposite corners of a rect, and just outside them."""
    r, s = cap.rects[name], cap.scale
    im = Image.open(cap.image_path).convert("RGB")
    x0, y0, x1, y1 = r["x"] * s, r["y"] * s, (r["x"] + r["w"]) * s, (r["y"] + r["h"]) * s
    ins = [im.getpixel((int(x0) + 2, int(y0) + 2)), im.getpixel((int(x1) - 3, int(y1) - 3))]
    out = [im.getpixel((int(x0) - 3, int(y0) + 2)), im.getpixel((int(x1) + 2, int(y1) - 3))]
    return ins, out


FRAMES_HTML = """<!doctype html><html><body style="margin:0;font:16px sans-serif">
<header style="position:fixed;top:0;left:0;right:0;height:60px;background:#222;z-index:9">Header</header>
<div style="height:1500px;padding-top:80px"><div id="top" style="margin-left:40px;width:120px;height:30px;background:#00f"></div></div>
<iframe style="margin-left:100px;border:7px solid #888;padding:5px;width:600px;height:400px"
 srcdoc='<body style="margin:0"><div style="height:50px"></div><div style="margin-left:30px;width:80px;height:40px;background:#f00">A</div>
 <iframe style="margin-left:20px;border:3px solid #000;width:300px;height:200px" srcdoc="&lt;body style=margin:10px&gt;&lt;div id=b style=width:50px;height:25px;background:#0f0&gt;&lt;/div&gt;"></iframe></body>'></iframe>
<div style="height:1500px"></div></body></html>"""


@pytest.mark.web
@pytest.mark.parametrize("full_page", [False, True])
def test_local_nested_iframes_pixel_exact(chromium, tmp_path, full_page):
    page = tmp_path / "frames.html"
    page.write_text(FRAMES_HTML)
    cap = web.capture(page.as_uri(), targets={"a": {"text": "A", "exact": True, "fit": "element"}, "b": {"selector": "#b"}},
                      full_page=full_page, out_path=tmp_path / "shot.png")
    assert cap.scale == 2 and cap.viewport == (1280, 800) and not cap.missing
    for name, color in (("a", (255, 0, 0)), ("b", (0, 255, 0))):
        ins, out = _pixels(cap, name)
        assert all(p == color for p in ins) and all(p != color for p in out), (name, cap.rects)
    assert cap.rects["b"]["x"] == 145   # 100 + 7 + 5 (outer frame) + 20 + 3 (inner frame) + 10 (body)
    if full_page:
        assert png_size(cap.image_path)[1] > 1600 * 2


@pytest.mark.web
def test_local_targets_that_cannot_share_a_viewport(chromium, tmp_path):
    page = tmp_path / "frames.html"
    page.write_text(FRAMES_HTML)
    cap = web.capture(page.as_uri(), targets={"top": {"selector": "#top"}, "b": {"selector": "#b"}},
                      out_path=tmp_path / "shot.png")
    assert "top" in cap.rects and "off-screen" in cap.missing["b"]


FIT_HTML = """<!doctype html><body style="margin:0;font:16px sans-serif">
<ul style="margin:0;padding:0;width:800px;list-style:none"><li id="li" style="padding:10px;background:#ddd">Short row</li></ul>
<h2 id="h" style="margin:0;width:800px;border-bottom:1px solid #000">Heading</h2>
<p id="p" style="margin:0;width:200px">Fills the whole paragraph width with enough words to wrap</p>
<a id="lnk" href="#" style="display:block;width:800px">Wide link</a></body>"""


@pytest.mark.web
def test_text_fit_rules(chromium, tmp_path):
    page = tmp_path / "fit.html"
    page.write_text(FIT_HTML)
    t = {"row": {"text": "Short row"}, "row_el": {"text": "Short row", "fit": "element"},
         "h": {"role": "heading", "name": "Heading"}, "h_sel": {"selector": "h2"},
         "p": {"text": "Fills the whole paragraph", "exact": False}, "lnk": {"text": "Wide link"},
         "lnk_t": {"text": "Wide link", "fit": "text"}}
    cap = web.capture(page.as_uri(), targets=t, out_path=tmp_path / "s.png")
    r = cap.rects
    assert not cap.missing
    assert r["row"]["w"] < 100 and r["row_el"]["w"] == 800          # auto -> text box; element on request
    assert r["h"]["w"] < 100 and r["h_sel"]["w"] == 800            # a selector is always the element
    assert r["p"]["w"] > 150                                       # text fills the element: unchanged
    assert r["lnk"]["w"] == 800 and r["lnk_t"]["w"] < 100          # links keep the click area unless forced


def test_parse_target_fit():
    assert parse_target({"text": "x", "fit": "text"})["fit"] == "text"
    with pytest.raises(TargetSpecError):
        parse_target({"text": "x", "fit": "wide"})


@pytest.mark.web
def test_login_fill_click_error_banner(chromium, tmp_path):
    try:
        cap = web.capture("https://the-internet.herokuapp.com/login", out_path=tmp_path / "s.png",
                          actions=[{"fill": {"selector": "#username"}, "value": "tomsmith"},
                                   {"fill": {"selector": "#password"}, "value": "wrong"},
                                   {"click": {"role": "button", "name": "Login"}}],
                          targets={"flash": {"selector": "#flash"}, "btn": {"role": "button", "name": "Login"}})
    except Exception as e:
        pytest.skip(f"offline / network stall: {e}")
    assert not cap.missing and cap.scale == 2
    ins, _ = _pixels(cap, "flash")
    assert all(p[0] > 150 and p[1] < 60 for p in ins)   # the red error banner


AMBIG_HTML = """<!doctype html><body style="margin:0;font:16px sans-serif">
<nav><a href="#">comments</a> | <a href="#">past</a></nav>
<table><tr class="sub"><td class="subline"><a href="#">12 comments</a></td></tr>
<tr class="sub"><td class="subline"><a href="#">3 comments</a></td></tr></table>
<button>Save</button> <button>Save draft</button>
<p>Contact alice.smith@example.org for a refund</p>
<p class="hidden" style="display:none">comments</p></body>"""


@pytest.mark.web
def test_local_ambiguous_text_and_selector_targets(chromium, tmp_path):
    page = tmp_path / "amb.html"
    page.write_text(AMBIG_HTML)
    with pytest.raises(web.AmbiguousTarget) as e:
        web.capture(page.as_uri(), targets={"c": {"text": "comments"}}, out_path=tmp_path / "s.png")
    assert len(e.value.candidates) == 3 and "3 visible matches" in str(e.value)   # the hidden one does not count
    assert {c["path"] for c in e.value.candidates} >= {"nav > a", "tr.sub > td.subline > a"}
    assert all(set(c["rect"]) == {"x", "y", "w", "h"} for c in e.value.candidates)
    assert not (tmp_path / "s.png").exists() or (tmp_path / "s.png").stat().st_size == 0
    with pytest.raises(web.AmbiguousTarget):
        web.capture(page.as_uri(), targets={"s": {"selector": "td.subline a"}}, out_path=tmp_path / "s.png")
    with pytest.raises(web.AmbiguousTarget, match="action #1 click"):
        web.capture(page.as_uri(), actions=[{"click": {"text": "comments"}}], out_path=tmp_path / "s.png")
    cap = web.capture(page.as_uri(), out_path=tmp_path / "s.png",
                      targets={"n1": {"text": "comments", "nth": 1}, "first": {"text": "comments", "first": True},
                               "exact": {"text": "comments", "exact": True},
                               "save": {"role": "button", "name": "Save"}})   # role: exact name first, no error
    assert not cap.missing
    assert cap.rects["first"] == cap.rects["exact"] and cap.rects["n1"]["y"] > cap.rects["first"]["y"]
    assert cap.rects["save"]["w"] < 80


@pytest.mark.web
def test_local_missing_text_lists_closest_visible_texts_masked(chromium, tmp_path):
    page = tmp_path / "amb.html"
    page.write_text(AMBIG_HTML)
    cap = web.capture(page.as_uri(), out_path=tmp_path / "s.png",
                      targets={"r": {"text": "Contact alice.smith@example.org about refunds"},
                               "x": {"text": "Comments", "exact": True}})
    assert "substring match by default; set exact:true for exact" in cap.missing["r"]
    assert "remove it for a substring match" in cap.missing["x"]
    shown = cap.missing["r"].split("closest visible texts:")[1]
    assert cap.near["r"] and "alice.smith@example.org" not in " ".join(cap.near["r"]) + shown
    assert any("refund" in t for t in cap.near["r"])


MUTATING_HTML = """<!doctype html><body style="margin:0;font:16px sans-serif">
<style>@keyframes spin { to { transform: rotate(360deg); } } #s { width: 40px; height: 40px; background: red;
 animation: spin 1s linear infinite; }</style><div id="s"></div><p id="t">0</p><input id="i" value="focus me" autofocus>
<script>window.go = () => { let n = 0; setInterval(() => { document.getElementById('t').textContent = ++n; }, 5); };</script>
</body>"""


@pytest.mark.web
def test_local_scan_and_shot_see_the_same_frozen_state(chromium, tmp_path):
    page = tmp_path / "mut.html"
    page.write_text(MUTATING_HTML)
    calls = []

    def scan(pg):   # what the privacy scan would see
        calls.append(1)
        return pg.evaluate("document.getAnimations().map(a => a.playState)")
    cap = web.capture(page.as_uri(), out_path=tmp_path / "s.png", on_page=scan)
    assert calls == [1] and cap.extra == ["paused"]   # frozen; the screenshot itself is not a DOM change

    calls.clear()

    def scan_once_dirty(pg):   # the DOM changes during the first scan only: scan + shot are redone
        calls.append(1)
        if len(calls) == 1:
            pg.evaluate("document.getElementById('t').textContent = 'changed'")
        return len(calls)
    cap = web.capture(page.as_uri(), out_path=tmp_path / "s.png", on_page=scan_once_dirty)
    assert cap.extra == 2

    calls.clear()
    with pytest.raises(CaptureError, match="page kept changing; privacy cannot be guaranteed"):
        web.capture(page.as_uri(), out_path=tmp_path / "s.png", actions=[{"wait_ms": 1}],
                    on_page=lambda pg: (calls.append(1), pg.evaluate("go()"), pg.wait_for_timeout(30)))
    assert len(calls) == web.SHOT_ATTEMPTS
    # privacy off (no on_page): a busy page still gets its screenshot
    web.capture(page.as_uri(), out_path=tmp_path / "s.png", targets={"t": {"selector": "#t"}})


@pytest.mark.web
def test_hacker_news_tiny_links(chromium, tmp_path):
    cap = web.capture("https://news.ycombinator.com", out_path=tmp_path / "s.png",
                      targets={"new": {"text": "new", "exact": True}, "login": {"text": "login", "exact": True}})
    assert not cap.missing
    for r in cap.rects.values():
        assert r["w"] < 50 and r["h"] <= 20 and r["y"] < 40


@pytest.mark.web
def test_wikipedia_below_the_fold_heading(chromium, tmp_path):
    cap = web.capture("https://en.wikipedia.org/wiki/Screenshot", out_path=tmp_path / "s.png",
                      targets={"refs": {"role": "heading", "name": "References"}})
    r = cap.rects["refs"]
    assert 100 < r["y"] < 700 and r["h"] < 60


@pytest.mark.web
def test_w3schools_tryit_iframe(chromium, tmp_path):
    # the tryit layout only appears after the CMP consent (own throwaway browser)
    cap = web.capture("https://www.w3schools.com/html/tryit.asp?filename=tryhtml_iframe", hide_overlays=False,
                      out_path=tmp_path / "s.png",
                      actions=[{"click": {"text": "Accept"}}, {"wait": {"selector": "h2"}}],
                      targets={"h2": {"selector": "h2"},
                               "nested": {"text": "This page is displayed in an iframe", "exact": False}})
    assert not cap.missing
    assert cap.rects["nested"]["y"] > cap.rects["h2"]["y"] and cap.rects["h2"]["x"] > 400


@pytest.mark.web
def test_hacker_news_comments_is_ambiguous_until_nth(chromium, tmp_path):
    with pytest.raises(web.AmbiguousTarget) as e:
        web.capture("https://news.ycombinator.com", out_path=tmp_path / "s.png", targets={"c": {"text": "comments"}})
    assert len(e.value.candidates) == 8 and "and " in str(e.value)
    cap = web.capture("https://news.ycombinator.com", out_path=tmp_path / "s.png",
                      targets={"c": {"text": "comments", "nth": 0}})
    assert cap.rects["c"]["y"] < 40   # the top-bar link comes first in document order


@pytest.mark.web
def test_phone_preset_ro_wikipedia(chromium, tmp_path):
    cap = web.capture("https://ro.wikipedia.org/wiki/Bucure%C8%99ti", preset="phone",
                      out_path=tmp_path / "s.png", targets={"title": {"selector": "h1"}})
    assert cap.scale == 3 and cap.viewport == (390, 844)
    assert png_size(cap.image_path) == (1170, 2532)
    assert cap.rects["title"]["w"] <= 390


@pytest.mark.web
@pytest.mark.skipif(not os.environ.get("AINOTATE_TEST_CDP"), reason="set AINOTATE_TEST_CDP=http://127.0.0.1:PORT")
def test_attach_over_cdp_opens_and_closes_its_own_tab(tmp_path, capsys):
    import urllib.request
    cdp = os.environ["AINOTATE_TEST_CDP"]

    def tabs():
        with urllib.request.urlopen(cdp.rstrip("/") + "/json/list") as r:
            return sorted(t["id"] for t in json.load(r) if t["type"] == "page")
    before = tabs()
    web.cli_capture(["https://news.ycombinator.com", "--cdp", cdp, "--out", str(tmp_path / "s.png"),
                     "--target", "new=new"])
    d = json.loads(capsys.readouterr().out)
    assert d["scale"] == 2 and d["viewport"] == [1280, 800] and "new" in d["rects"]
    assert png_size(tmp_path / "s.png") == (2560, 1600)
    assert tabs() == before
