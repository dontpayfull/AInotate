"""ainotate.capture.web: pure geometry and argument parser tests (no browser launched)."""
import json
import os
import sys

import pytest
from PIL import Image

from ainotate.capture import web
from ainotate.capture.web import (PRESETS, CaptureError, TargetSpecError, frame_content_box, inside,
                                 intersect, nice_scale, offset_rect, parse_action, parse_target,
                                 parse_target_arg, plan_scroll, png_size, rect, to_capture_coords)


# ---------------------------------------------------------------- presets
def test_preset_table():
    assert {k: (p.width, p.height, p.scale, p.mobile) for k, p in PRESETS.items()} == {
        "laptop": (1280, 800, 2, False), "wide": (1600, 1000, 2, False), "phone": (390, 844, 3, True)}
    assert "iPhone" in PRESETS["phone"].user_agent
    assert PRESETS["laptop"].user_agent is None


# ---------------------------------------------------------------- target specs
def test_parse_target_forms():
    assert parse_target("Checkout") == {"text": "Checkout"}   # no nth default: must be unique
    assert parse_target({"selector": "#a", "nth": 2}) == {"selector": "#a", "nth": 2}
    assert parse_target({"role": "button", "name": "Save", "within": "form"})["within"] == "form"
    assert parse_target({"selector": "button", "text": "Save"})["text"] == "Save"
    assert parse_target({"text": "Sav", "exact": False})["exact"] is False
    assert parse_target({"text": "comments", "first": True})["first"] is True


@pytest.mark.parametrize("bad", [
    {}, {"nth": 1}, {"text": ""}, {"text": "a", "role": "button"}, {"name": "x", "text": "a"},
    {"text": "a", "nth": True}, {"text": "a", "nth": "1"}, {"text": "a", "foo": 1}, {"text": 3},
    {"text": "a", "exact": "yes"}, ["text"], 5, {"text": "a", "first": 1}, {"text": "a", "first": True, "nth": 0},
])
def test_parse_target_rejects(bad):
    with pytest.raises(TargetSpecError):
        parse_target(bad)


def test_target_spec_error_is_a_capture_error_and_value_error():
    assert issubclass(TargetSpecError, CaptureError) and issubclass(TargetSpecError, ValueError)


def test_parse_target_arg():
    assert parse_target_arg('btn={"role":"button","name":"Login"}') == (
        "btn", {"role": "button", "name": "Login"})
    assert parse_target_arg("refs=References") == ("refs", {"text": "References"})
    assert parse_target_arg('q="a=b"') == ("q", {"text": "a=b"})
    for bad in ("noequals", "=x", 'x={"text":', "x="):
        with pytest.raises(TargetSpecError):
            parse_target_arg(bad)


def test_parse_action():
    assert parse_action({"wait_ms": 500}) == {"kind": "wait_ms", "ms": 500.0}
    assert parse_action({"click": {"text": "Go"}}) == {"kind": "click", "target": {"text": "Go"}}
    a = parse_action({"fill": {"selector": "#u"}, "value": "bob", "timeout_ms": 900})
    assert (a["kind"], a["value"], a["timeout_ms"]) == ("fill", "bob", 900.0)
    assert parse_action({"scroll_to": "References"})["target"] == {"text": "References"}
    assert parse_action({"wait": {"selector": ".done"}})["kind"] == "wait"


@pytest.mark.parametrize("bad", [
    {}, {"click": {"text": "a"}, "wait": {"text": "b"}}, {"fill": {"text": "a"}},
    {"fill": {"text": "a"}, "value": 3}, {"click": {"text": "a"}, "value": "x"},
    {"wait_ms": -1}, {"wait_ms": True}, {"click": {}}, {"click": {"text": "a"}, "extra": 1}, "click",
])
def test_parse_action_rejects(bad):
    with pytest.raises(TargetSpecError):
        parse_action(bad)


def test_capture_validates_before_launching():
    with pytest.raises(TargetSpecError, match="preset"):
        web.capture("https://example.com", preset="tablet")
    with pytest.raises(TargetSpecError, match="url"):
        web.capture()
    with pytest.raises(TargetSpecError):
        web.capture("https://example.com", targets={"x": {"bogus": 1}})


# ---------------------------------------------------------------- rect math
def test_rect_helpers():
    r = rect(10, 20, 30, 40)
    assert offset_rect(r, 5, -5) == {"x": 15, "y": 15, "w": 30, "h": 40}
    assert intersect(r, rect(30, 50, 100, 100)) == {"x": 30, "y": 50, "w": 10, "h": 10}
    assert intersect(r, rect(100, 100, 5, 5)) is None
    assert inside(r, rect(0, 0, 40, 60)) and inside(rect(-0.5, 0, 10, 10), rect(0, 0, 100, 100))
    assert not inside(r, rect(0, 0, 38, 60))


def test_frame_content_box_adds_border_and_padding():
    raw = {"x": 100, "y": 600, "cl": 7, "ct": 7, "cw": 610, "ch": 410, "pl": 5, "pt": 5, "pr": 5, "pb": 5}
    assert frame_content_box(raw) == {"x": 112, "y": 612, "w": 600, "h": 400}


def test_nested_iframe_offset_composes():
    """Element in a frame inside a frame: offsets add up level by level (what _frame_geom does)."""
    outer = frame_content_box({"x": 100, "y": 600, "cl": 7, "ct": 7, "cw": 610, "ch": 410,
                               "pl": 5, "pt": 5, "pr": 5, "pb": 5})
    inner_in_outer = frame_content_box({"x": 20, "y": 90, "cl": 3, "ct": 3, "cw": 300, "ch": 200,
                                        "pl": 0, "pt": 0, "pr": 0, "pb": 0})
    inner = offset_rect(inner_in_outer, outer["x"], outer["y"])
    el = offset_rect(rect(10, 10, 50, 25), inner["x"], inner["y"])
    assert el == {"x": 145, "y": 715, "w": 50, "h": 25}


def test_capture_coords_full_page_adds_scroll():
    r = rect(40, 120, 80, 20)
    assert to_capture_coords(r, (0, 900), False) == r
    assert to_capture_coords(r, (10, 900), True) == {"x": 50, "y": 1020, "w": 80, "h": 20}


def test_plan_scroll_centers_a_union_that_fits():
    s = plan_scroll([(1000, 1040), (1200, 1230)], 0, 800, 5000)
    assert s <= 1000 and s + 800 >= 1230
    assert abs((s + 400) - (1000 + 1230) / 2) < 1


def test_plan_scroll_prefers_the_first_target_when_they_cannot_share():
    s = plan_scroll([(3000, 3030), (100, 130)], 0, 800, 10000)
    assert s <= 3000 and s + 800 >= 3030


def test_plan_scroll_keeps_targets_out_from_under_a_sticky_header():
    s = plan_scroll([(500, 520), (1150, 1180)], 120, 800, 5000)
    assert s + 120 <= 500 and s + 800 >= 1180


def test_plan_scroll_clamps_to_document():
    assert plan_scroll([(10, 30)], 0, 800, 5000) == 0
    assert plan_scroll([(1950, 1990)], 0, 800, 1200) == 1200
    assert plan_scroll([], 0, 800, 100) == 0


def _m(x, y, w, h, **kw):
    return {"rect": rect(x, y, w, h), "visible": True, "reason": "", "fixed": False, "inner": False,
            "clip": None, **kw}


def test_final_rects_reports_off_screen_targets_instead_of_wrong_rects():
    st = {"sx": 0, "sy": 0, "vw": 1280, "vh": 800, "dw": 1280, "dh": 5000}
    ms = {"a": _m(10, 100, 50, 20), "b": _m(10, 2500, 50, 20),
          "c": _m(0, 0, 0, 0, visible=False, reason="zero size"), "d": {"error": "element went away"}}
    rects, missing, warns = web._final_rects(ms, st, 0, False)
    assert rects == {"a": rect(10, 100, 50, 20)}
    assert "off-screen" in missing["b"] and "a" in missing["b"]
    assert "zero size" in missing["c"] and missing["d"] == "element went away"


def test_final_rects_full_page_and_clipping():
    st = {"sx": 0, "sy": 300, "vw": 1280, "vh": 800, "dw": 1280, "dh": 5000}
    rects, missing, _ = web._final_rects({"b": _m(10, 2500, 50, 20)}, st, 0, True)
    assert rects["b"] == {"x": 10, "y": 2800, "w": 50, "h": 20} and not missing
    st["sy"] = 0
    rects, _, warns = web._final_rects({"big": _m(0, 100, 1280, 2000)}, st, 0, False)
    assert rects["big"] == {"x": 0, "y": 100, "w": 1280, "h": 700} and warns
    # in a frame, only the part inside the frame's visible box counts
    rects, missing, _ = web._final_rects({"f": _m(100, 900, 50, 20, clip=rect(0, 0, 600, 400))}, st, 0, False)
    assert "f" in missing


def test_nice_scale():
    assert nice_scale(2560, 1280, 2) == 2.0
    assert nice_scale(1170, 980, 3) == round(1170 / 980, 4)   # phone page without meta viewport
    assert nice_scale(1280, 1280, 1) == 1.0


def test_png_size(tmp_path):
    p = tmp_path / "a.png"
    Image.new("RGB", (37, 21)).save(p)
    assert png_size(p) == (37, 21)
    (tmp_path / "b.png").write_bytes(b"not a png at all, sorry")
    with pytest.raises(CaptureError):
        png_size(tmp_path / "b.png")


def test_cli_usage_errors_exit_2(capsys):
    for argv in (["https://example.com", "--target", "noequals"],
                 ["https://example.com", "--action", "{bad"],
                 ["https://example.com", "--action", '{"jump": 1}'],
                 ["--cdp", "http://127.0.0.1:9222"]):
        with pytest.raises(SystemExit) as e:
            web.cli_capture(argv)
        assert e.value.code == 2


def test_needs_unique_and_closest_texts():
    from ainotate.capture.web_geom import closest_texts, needs_unique
    assert needs_unique({"text": "a"}) and needs_unique({"selector": "a"})
    assert not needs_unique({"text": "a", "nth": 0}) and not needs_unique({"text": "a", "first": True})
    assert not needs_unique({"role": "link", "name": "a"})          # role+name stays exact-first
    near = closest_texts("Refund", ["Refunds", "Orders", "refund policy", "Help", "Refunds"])
    assert near[:2] == ["refund policy", "Refunds"] or set(near[:2]) == {"refund policy", "Refunds"}
    assert "Help" not in near and len(near) <= 5


def test_capture_temp_file_is_closed_and_removed_on_failure(monkeypatch, tmp_path):
    import tempfile
    made = []
    real = tempfile.mkstemp

    def mkstemp(**kw):
        fd, path = real(dir=tmp_path, **kw)
        made.append((fd, path))
        return fd, path
    monkeypatch.setattr(tempfile, "mkstemp", mkstemp)

    class PW:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class PWError(Exception):
        pass
    monkeypatch.setattr(web, "_pw", lambda: (PW, PWError, TimeoutError))

    def boom(pw, job):
        with pytest.raises(OSError):
            os.fstat(made[0][0])          # the mkstemp handle is already closed (Windows locking)
        raise CaptureError("nope")
    monkeypatch.setattr(web, "_run_with_retries", boom)
    with pytest.raises(CaptureError, match="nope"):
        web.capture("https://example.com")
    assert made and not os.path.exists(made[0][1])


def test_stalled_load_is_retried_on_a_fresh_page(monkeypatch):
    calls = []

    def run(pw, job, args, notes=None):
        calls.append(list(args))
        if len(calls) < 3:
            raise web._StalledLoad("never finished loading")
        return "ok"
    monkeypatch.setattr(web, "_run", run)
    job = {"cdp_url": None, "browser": "chromium"}
    assert web._run_with_retries(None, job) == "ok"
    assert calls == [[], ["--disable-http2"], ["--disable-http2"]]
    calls.clear()
    monkeypatch.setattr(web, "_run", lambda *a, **k: (calls.append(1), (_ for _ in ()).throw(web._StalledLoad("x")))[1])
    with pytest.raises(web._StalledLoad):
        web._run_with_retries(None, {"cdp_url": "http://x", "browser": "chromium"})
    assert len(calls) == 2                   # attach / other browsers: one fresh retry too


def test_ambiguity_message_lists_candidates():
    e = web._Ambiguous(11, [{"text": f"{k} comments", "rect": rect(10, 20 * k, 70, 12), "path": "td.subline > a"}
                            for k in range(8)])
    msg = web._ambiguity_message("target 'c':", {"text": "comments"}, e)
    assert "11 visible matches" in msg and '"nth"' in msg and '"first": true' in msg
    assert msg.count("td.subline > a") == 8 and "and 3 more" in msg
