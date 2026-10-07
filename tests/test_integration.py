"""Integration: frame, OCR text targets and privacy in render/annotate, shoot() over a fake capture,
and the CLI surface (grouped help, examples, exit codes, --json)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from ainotate import cli, ocr, shoot as shoot_mod
from ainotate.capture import web
from ainotate.render import render
from ainotate.spec import AmbiguousText, SpecError, TargetNotFound, TextNotFound


def items(*lines):
    """Fake ocr.read() output: each line is a list of (text, x, y, w, h) words."""
    out = []
    for li, words in enumerate(lines):
        x1, y1 = min(w[1] for w in words), min(w[2] for w in words)
        x2, y2 = max(w[1] + w[3] for w in words), max(w[2] + w[4] for w in words)
        out.append({"text": " ".join(w[0] for w in words), "x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1,
                    "conf": 0.99, "line": li, "level": "line"})
        out += [{"text": t, "x": x, "y": y, "w": w, "h": h, "conf": 0.99, "line": li, "level": "word", "word": j}
                for j, (t, x, y, w, h) in enumerate(words)]
    return out


UI_TEXT = items([("Checkout", 1360, 118, 100, 14)], [("Apply", 1350, 188, 50, 14), ("coupon", 1405, 188, 60, 14)],
                [("Orders", 30, 160, 60, 14)], [("customer3@example.com", 420, 285, 190, 14)])


@pytest.fixture
def fake_ocr(monkeypatch):
    calls = []

    def read(path, langs=None, region=None, *, backend="auto"):
        calls.append(path)
        return [dict(i) for i in UI_TEXT]
    monkeypatch.setattr(ocr, "read", read)
    return calls


def step(target, **kw):
    return {"type": "step", "n": 1, "target": target, "label": "Pay", **kw}


# ---------------------------------------------------------------- frame

def test_frame_is_applied_last(ui):
    plain = render({"input": ui, "crop": [1200, 60, 1600, 260], "marks": []}).image
    framed = render({"input": ui, "crop": [1200, 60, 1600, 260], "marks": [],
                     "frame": {"chrome": "browser", "url": "acme.test"}}).image
    assert framed.width > plain.width and framed.height > plain.height and framed.mode == "RGB"
    clear = render({"input": ui, "marks": [], "frame": {"bg": "transparent"}}).image
    assert clear.mode == "RGBA" and clear.getpixel((0, 0))[3] == 0


@pytest.mark.parametrize("spec, msg", [
    ({"frame": {"chrome": "tablet"}}, "spec.frame: 'chrome'"),
    ({"frame": 42}, "spec.frame: must be"),
    ({"privacy": "sometimes"}, "spec.privacy must be"),
    ({"privacy": {"kinds": ["dna"]}}, "unknown kind 'dna'"),
])
def test_frame_and_privacy_are_validated(ui, spec, msg):
    with pytest.raises(SpecError, match="invalid spec") as e:
        render(dict({"input": ui, "marks": []}, **spec))
    assert msg in str(e.value)


# ---------------------------------------------------------------- OCR text targets

def test_text_target_resolves_in_spec_units(ui, fake_ocr):
    r1 = render({"input": ui, "marks": [step({"text": "Checkout"})]}, debug=True)
    assert fake_ocr and r1.redactions == []
    spec, red = shoot_mod.prepare_image_spec({"input": ui, "scale": 2, "marks": [step("Apply coupon", pad=3)]})
    m = spec["marks"][0]
    assert "target" not in m and m["pad"] == 3 and red == []
    assert m["rect"] == [675.0, 94.0, 732.5, 101.0]    # image px / 2


def test_text_target_errors(ui, fake_ocr):
    with pytest.raises(TextNotFound) as e:
        render({"input": ui, "marks": [step({"text": "Refund"})]})
    assert "mark #0 (step)" in str(e.value) and "closest OCR text" in str(e.value) and e.value.near
    with pytest.raises(SpecError, match="need a live page"):
        render({"input": ui, "marks": [step({"selector": "#pay"})]})
    with pytest.raises(SpecError, match="missing 'rect' \\(or a 'target'"):
        render({"input": ui, "marks": [{"type": "box"}]})


def test_ambiguous_text_lists_candidates(ui, monkeypatch):
    monkeypatch.setattr(ocr, "read", lambda *a, **k: items([("Save", 100, 100, 40, 14)], [("Save", 100, 300, 40, 14)]))
    with pytest.raises(AmbiguousText) as e:
        render({"input": ui, "marks": [step({"text": "Save"})]})
    assert len(e.value.candidates) == 2 and "[100, 100, 140, 114]" in str(e.value)
    res = render({"input": ui, "marks": [step({"text": "Save", "nth": 1})]})
    assert res.image.size


# ---------------------------------------------------------------- privacy on images

def test_privacy_defaults_off_on_images_and_auto_redacts(ui, fake_ocr):
    assert render({"input": ui, "marks": []}).redactions == [] and not fake_ocr
    res = render({"input": ui, "privacy": "auto", "marks": []})
    assert res.redactions and res.redactions[0].startswith("email x1")
    assert "customer3@example.com" not in " ".join(res.redactions)
    px = res.image.getpixel((500, 292))
    assert px == (31, 31, 31)          # solid, not blurred


def test_auto_redactions_never_drive_the_crop(ui, monkeypatch):
    far = items([("Checkout", 1360, 118, 100, 14)], [("x@example.com", 5, 980, 120, 14)])
    monkeypatch.setattr(ocr, "read", lambda *a, **k: far)
    spec = {"input": ui, "crop": "auto", "privacy": "auto",
            "marks": [{"type": "box", "rect": [1300, 100, 1520, 150]}]}
    with_priv = render(spec)
    without = render(dict(spec, privacy="off"))
    assert with_priv.image.size == without.image.size and with_priv.redactions


# ---------------------------------------------------------------- shoot over a fake capture

@pytest.fixture
def fake_capture(monkeypatch, ui, tmp_path):
    seen = {"raw": []}

    def capture(url=None, **kw):
        seen.update(kw, url=url)
        raw = tmp_path / f"raw-{len(seen['raw'])}.png"     # like the real temp capture: shoot owns it
        raw.write_bytes(Path(ui).read_bytes())
        seen["raw"].append(raw)
        first = next(iter(kw["targets"]), None)
        rects = {first: {"x": 650, "y": 50, "w": 110, "h": 25}} if first else {}
        missing = {n: "text='Gone': no element matches" for n in kw["targets"] if n not in rects}
        findings = []
        if kw.get("on_page"):
            findings = [{"kind": "password_field", "x": 150, "y": 150, "w": 200, "h": 20, "units": "css",
                         "source": "dom", "preview": "***"},
                        {"kind": "unknown_frame", "x": 0, "y": 400, "w": 300, "h": 100, "units": "css",
                         "source": "dom", "preview": ""}]
        if seen.get("fail"):
            raise seen["fail"]
        if seen.get("truncate") and kw.get("on_page"):
            findings = kw["on_page"](None)      # the scan callback is what checks `truncated`
        return web.WebCapture(raw, 2.0, (800, 500), rects, missing, seen.get("page_url", "https://www.acme.test/cart"),
                              ["w0"], findings, {n: ["Gonee", "Go on"] for n in missing})
    monkeypatch.setattr(web, "capture", capture)
    return seen


def test_shoot_end_to_end_with_fake_capture(fake_capture, out_dirs):
    spec = {"url": "acme.test/cart", "name": "cart", "frame": {"chrome": "browser"},
            "actions": [{"click": {"text": "Menu"}}],
            "marks": [{"type": "step", "n": 1, "target": {"text": "Checkout"}, "label": "Pay here"}]}
    res = shoot_mod.shoot(spec)
    assert fake_capture["targets"] == {"pay-here": {"text": "Checkout"}}   # named after the label
    assert callable(fake_capture["on_page"])            # privacy "auto" by default
    assert res.legend == [{"n": 1, "label": "Pay here"}]
    assert res.redactions == ["password field x1"]
    assert any("could not be read" in w for w in res.warnings) and "w0" in res.warnings
    assert len(res.paths) == 2 and res.paths[0].name.endswith(" cart.png") and res.size[0] > 0
    # the raw, unredacted capture is gone and its path is not handed to callers
    assert not fake_capture["raw"][0].exists()
    assert res.capture.image_path is None and "image_path" not in res.capture.to_dict()
    assert str(fake_capture["raw"][0]) not in json.dumps(res.capture.to_dict(), default=str)
    assert res.capture.rects == {"pay-here": {"x": 650, "y": 50, "w": 110, "h": 25}}


def test_shoot_privacy_off_and_frame_url(fake_capture, monkeypatch):
    seen = {}
    from ainotate import render as render_mod
    orig = render_mod.render

    def spy(spec, debug=False):
        seen.update(spec)
        return orig(spec, debug)
    monkeypatch.setattr(render_mod, "render", spy)
    res = shoot_mod.shoot({"url": "acme.test", "privacy": "off", "frame": {"chrome": "browser"},
                           "marks": [{"type": "box", "target": "Checkout"}]}, draft=True)
    assert fake_capture["on_page"] is None and res.redactions == []
    assert seen["frame"]["url"] == "acme.test/cart" and seen["scale"] == 2.0 and seen["crop"] == "auto"


def test_shoot_missing_target_and_spec_errors(fake_capture):
    with pytest.raises(TargetNotFound, match=r"mark #1 \(box\): target not found: text='Gone'") as e:
        shoot_mod.shoot({"url": "acme.test", "marks": [{"type": "box", "target": "Checkout"},
                                                       {"type": "box", "target": "Gone"}]}, draft=True)
    assert "capture is at" not in str(e.value) and e.value.near == ["Gonee", "Go on"]
    assert fake_capture["raw"] and not any(p.exists() for p in fake_capture["raw"])   # deleted on error too
    fake_capture.clear()
    for bad, msg in (({"input": "x.png"}, "remove 'input'"), ({"url": None}, "missing 'url'"),
                     ({"marks": [{"type": "box", "target": {"href": "/"}}]}, "unknown target key"),
                     ({"marks": [{"type": "nope"}]}, "unknown type")):
        with pytest.raises(SpecError, match=msg):
            shoot_mod.shoot(dict({"url": "acme.test", "marks": []}, **bad), draft=True)
    assert fake_capture == {}                               # no browser started for a bad spec


def test_shoot_forwards_browser_and_profile(fake_capture):
    shoot_mod.shoot({"url": "acme.test", "browser": "firefox", "user_data_dir": "~/profiles/work",
                     "marks": [{"type": "box", "target": "Checkout"}]}, draft=True)
    assert fake_capture["browser"] == "firefox" and fake_capture["user_data_dir"] == "~/profiles/work"


def test_shoot_target_names_follow_labels(fake_capture):
    marks = [{"type": "step", "n": 1, "target": "Checkout", "label": "Plătește acum"},
             {"type": "step", "n": 2, "target": {"text": "B"}, "label": "Plătește acum"},
             {"type": "box", "target": {"text": "C"}},
             {"type": "box", "target": {"text": "D"}, "label": "mark2"}]
    with pytest.raises(TargetNotFound):   # the fake only finds the first one
        shoot_mod.shoot({"url": "acme.test", "marks": marks}, draft=True)
    assert list(fake_capture["targets"]) == ["plateste-acum", "plateste-acum-2", "mark2", "mark2-2"]


def test_shoot_frame_url_is_sanitized(fake_capture, monkeypatch):
    seen = {}
    from ainotate import render as render_mod
    orig = render_mod.render
    monkeypatch.setattr(render_mod, "render", lambda spec, debug=False: (seen.update(spec), orig(spec, debug))[1])
    fake_capture["page_url"] = "https://bob:hunter2@www.acme.test/login?token=abc123#frag"
    res = shoot_mod.shoot({"url": "acme.test", "frame": {"chrome": "browser"},
                           "marks": [{"type": "box", "target": "Checkout"}]}, draft=True)
    assert seen["frame"]["url"] == "acme.test/login"
    assert "token" not in res.capture.url and "hunter2" not in res.capture.url


def test_shoot_refuses_a_truncated_privacy_scan(fake_capture, monkeypatch):
    from ainotate import privacy
    monkeypatch.setattr(privacy, "scan_page", lambda page, pol, full_page=False: {
        "findings": [{"kind": "email", "x": 1, "y": 1, "w": 5, "h": 5}], "truncated": True})
    fake_capture["truncate"] = True
    with pytest.raises(web.CaptureError, match="truncated"):
        shoot_mod.shoot({"url": "acme.test", "marks": [{"type": "box", "target": "Checkout"}]}, draft=True)
    assert shoot_mod._findings([{"kind": "email"}]) == [{"kind": "email"}]
    assert shoot_mod._findings([{"kind": "email"}, {"kind": "x", "truncated": True}]) is None
    assert shoot_mod._findings({"findings": [], "truncated": False}) == []


def test_shoot_ambiguous_target_names_the_mark(fake_capture):
    fake_capture["fail"] = web.AmbiguousTarget("target 'pay': text='Pay': 2 visible matches",
                                               [{"text": "Pay", "rect": {}, "path": "a"}], "pay")
    with pytest.raises(AmbiguousText, match=r"mark #0 \(box 'Pay'\): target text='Pay': 2 visible") as e:
        shoot_mod.shoot({"url": "acme.test", "marks": [{"type": "box", "target": "Pay", "label": "Pay"}]},
                        draft=True)
    assert e.value.candidates[0]["path"] == "a" and cli.exit_code(e.value) == 5


def test_shoot_mark_count_warning_ignores_auto_redactions(fake_capture, monkeypatch):
    from ainotate import privacy
    many = [{"kind": "email", "x": 10 + 40 * k, "y": 300, "w": 30, "h": 10, "units": "css", "source": "dom",
             "preview": "a***@b***.com"} for k in range(9)]
    monkeypatch.setattr(privacy, "scan_page", lambda page, pol, full_page=False: many)
    fake_capture["truncate"] = True    # makes the fake call the real scan callback
    res = shoot_mod.shoot({"url": "acme.test", "marks": [{"type": "box", "target": "Checkout"}]}, draft=True)
    assert res.redactions and not any("marks; more than" in w for w in res.warnings)
    assert len(shoot_mod._user_mark_warnings(["spec: 9 marks; more than 6 is hard"], [{}] * 7)) == 1


# ---------------------------------------------------------------- CLI surface

def run(*args, stdin=None):
    p = subprocess.run([sys.executable, "-m", "ainotate", *args], input=stdin, capture_output=True, text=True,
                       env=os.environ.copy())
    return p.returncode, p.stdout, p.stderr


def test_help_is_grouped_with_exit_codes():
    code, out, _ = run("--help")
    assert code == 0
    for group, names in cli.GROUPS:
        assert f"{group}:" in out and all(f"  {n} " in out for n in names)
    assert set(cli.COMMANDS) == {n for _, names in cli.GROUPS for n in names}
    assert "exit codes:" in out and all(f"  {c}  " in out for c, _ in cli.EXIT_CODES)


@pytest.mark.parametrize("name", sorted(cli.COMMANDS))
def test_every_command_help_has_an_example(name, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main([name, "--help"])
    assert e.value.code == 0
    assert "example:\n  ainotate " in capsys.readouterr().out


def test_annotate_json_and_privacy_flag(ui, tmp_path, out_dirs, fake_ocr, capsys):
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps({"input": ui, "marks": [{"type": "step", "n": 2, "label": "Pay",
                                                        "target": {"text": "Checkout"}}]}))
    assert cli.main(["annotate", str(spec), "--json", "--privacy", "auto", "--draft"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert set(d) >= {"paths", "warnings", "legend", "redactions", "size"}
    assert d["legend"] == [{"n": 2, "label": "Pay"}] and d["redactions"][0].startswith("email x1")
    assert os.path.isfile(d["paths"][0]) and d["size"] == list(Image.open(d["paths"][0]).size)
    assert cli.main(["annotate", str(spec), "--privacy", "off", "--draft"]) == 0
    assert "REDACTED" not in capsys.readouterr().err


def test_cli_text_target_exit_codes(ui, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ocr, "read", lambda *a, **k: items([("Save", 100, 100, 40, 14)], [("Save", 100, 300, 40, 14)]))
    spec = tmp_path / "s.json"
    for target, code in (({"text": "Save"}, 5), ({"text": "Refund"}, 6), ({"text": "Save", "nth": 0}, 0)):
        spec.write_text(json.dumps({"input": ui, "marks": [step(target)]}))
        assert cli.main(["annotate", str(spec), "--draft"]) == code, capsys.readouterr().err

    def no_engine(*a, **k):
        raise ocr.OcrError("no OCR engine")
    monkeypatch.setattr(ocr, "read", no_engine)
    assert cli.main(["annotate", str(spec), "--draft"]) == 4
    assert "no OCR engine" in capsys.readouterr().err
