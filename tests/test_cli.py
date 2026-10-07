import json
import os
import subprocess
import sys

import pytest

from ainotate import cli


def run(*args, stdin=None):
    p = subprocess.run([sys.executable, "-m", "ainotate", *args], input=stdin,
                       capture_output=True, text=True, env=os.environ.copy())
    return p.returncode, p.stdout, p.stderr


def write(tmp_path, spec, name="spec.json"):
    p = tmp_path / name
    p.write_text(json.dumps(spec) if not isinstance(spec, str) else spec)
    return str(p)


def good(ui):
    return {"input": ui, "name": "cart bug", "marks": [
        {"type": "step", "n": 1, "rect": [1300, 100, 1520, 150], "label": "Click"}]}


def test_annotate_saves_two_paths(ui, tmp_path, out_dirs):
    code, out, err = run("annotate", write(tmp_path, good(ui)))
    assert code == 0, err
    lines = out.splitlines()
    assert len(lines) == 2 and all(os.path.isfile(p) for p in lines)
    assert [os.path.dirname(p) for p in lines] == [str(d) for d in out_dirs]
    assert lines[0].endswith(" cart bug.png")


@pytest.mark.parametrize("flag", ["--draft", "--no-save", "--debug"])
def test_draft_flags_write_one_temp_path(ui, tmp_path, out_dirs, flag):
    code, out, _ = run("annotate", write(tmp_path, good(ui)), flag)
    assert code == 0 and len(out.splitlines()) == 1
    assert not out_dirs[0].exists()


def test_annotate_from_stdin(ui, out_dirs):
    code, out, _ = run("annotate", "-", "--draft", stdin=json.dumps(good(ui)))
    assert code == 0 and out.strip().endswith(".png")


def test_annotate_from_inline_json(ui):
    code, out, err = run("annotate", json.dumps(good(ui)), "--draft")
    assert code == 0 and out.strip().endswith(".png")


def test_render_warnings_go_to_stderr(ui, tmp_path, out_dirs):
    spec = {"input": ui, "marks": [{"type": "step", "n": 1, "rect": [0, 0, 1600, 1000]}]}
    code, out, err = run("annotate", write(tmp_path, spec), "--draft")
    assert code == 0 and err.startswith("WARN: mark #0: badge overlaps") and "WARN" not in out


@pytest.mark.parametrize("spec, code, msg", [
    ("{not json", 2, "ERROR: spec is not valid JSON"),
    ({"marks": []}, 2, "ERROR: invalid spec:\n  spec: missing 'input'"),
    ([], 2, "ERROR: spec must be a JSON object"),
    ({"input": "/nope/x.png", "marks": []}, 2, "ERROR: image not found"),
])
def test_spec_errors_exit_2(tmp_path, spec, code, msg):
    c, out, err = run("annotate", write(tmp_path, spec))
    assert (c, out) == (code, "") and err.startswith(msg)


def test_missing_spec_file_exit_2(tmp_path):
    c, _, err = run("annotate", str(tmp_path / "none.json"))
    assert c == 2 and "spec file not found" in err


def test_render_error_exit_3(ui, tmp_path):
    spec = {"input": ui, "marks": [{"type": "box", "rect": [1500, 900, 3000, 2000]}]}
    c, out, err = run("annotate", write(tmp_path, spec))
    assert c == 3 and out == "" and "lies mostly outside" in err


def test_usage_errors_exit_2(ui):
    assert run()[0] == 2
    assert run("annotate")[0] == 2
    assert run("grid", ui, "--step", "0")[0] == 2
    assert run("zoom", ui, "0", "0", "5", "5")[0] == 2
    assert run("info", "/nope.png")[0] == 2
    assert run("neo-latest", "--size", "huge")[0] == 2


def test_tools_output(ui):
    c, out, _ = run("info", ui)
    assert (c, out) == (0, "(1600, 1000)\n")
    c, out, _ = run("grid", ui)
    assert c == 0 and out.rstrip().endswith("grid.png (1600, 1000)")
    c, out, err = run("zoom", ui, "1300", "100", "1520", "220")
    assert c == 0 and "zoom.png region 1300,100-1520,220 shown x4" in out and err == ""


def test_neo_latest_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("AINOTATE_NEO_DIR", str(tmp_path))
    c, _, err = run("neo-latest")
    assert c == 4 and "no neo screenshots found" in err


def test_lazy_command_module_missing(monkeypatch, capsys):
    monkeypatch.setitem(cli.LAZY, "shoot", ("ainotate._not_written_yet", "web"))
    assert cli.main(["shoot", "x.json"]) == 4
    assert "`ainotate shoot` is not available" in capsys.readouterr().err


def test_lazy_command_dependency_missing(monkeypatch, capsys):
    def boom(name):
        raise ModuleNotFoundError("No module named 'playwright'", name="playwright")
    monkeypatch.setattr(cli.importlib, "import_module", boom)
    assert cli.main(["capture", "https://example.com"]) == 4
    assert "this command needs: pip install 'ainotate[web]'" in capsys.readouterr().err


def test_lazy_command_runtime_errors(monkeypatch, capsys, tmp_path):
    from types import SimpleNamespace

    from ainotate.render import RenderError
    from ainotate.spec import AmbiguousText, SpecError, TargetNotFound

    def raiser(exc):
        def shoot(spec, draft=False):
            raise exc
        return SimpleNamespace(shoot=shoot)
    spec = tmp_path / "s.json"
    spec.write_text('{"url": "https://x.test", "marks": []}')
    for exc, code in ((SpecError("bad"), 2), (RenderError("no room"), 3), (RuntimeError("no browser"), 4),
                      (AmbiguousText("two"), 5), (TargetNotFound("gone"), 6)):
        monkeypatch.setattr(cli.importlib, "import_module", lambda name, m=raiser(exc): m)
        assert cli.main(["shoot", str(spec)]) == code
    assert "ERROR: no browser" in capsys.readouterr().err


def test_capture_fallback_prints_json(monkeypatch, capsys):
    from pathlib import Path
    from types import SimpleNamespace
    seen = {}

    def capture(url=None, **kw):
        seen.update(kw, url=url)
        return SimpleNamespace(image_path=Path("/tmp/c.png"), scale=2.0, viewport=(1280, 800),
                               rects={"cart": {"x": 1, "y": 2, "w": 3, "h": 4}}, missing={})
    monkeypatch.setattr(cli.importlib, "import_module", lambda name: SimpleNamespace(capture=capture))
    code = cli.main(["capture", "https://x.test", "--preset", "phone", "--target", 'cart={"text": "Cart"}',
                     "--action", '{"wait_ms": 100}'])
    assert code == 0
    assert seen["targets"] == {"cart": {"text": "Cart"}} and seen["preset"] == "phone"
    assert seen["actions"] == [{"wait_ms": 100}]
    out = json.loads(capsys.readouterr().out)
    assert out["scale"] == 2.0 and out["rects"]["cart"]["w"] == 3


def test_capture_browser_and_profile_are_passed(monkeypatch, capsys, tmp_path):
    from pathlib import Path
    from types import SimpleNamespace
    seen = {}

    def capture(url=None, **kw):
        seen.update(kw)
        return SimpleNamespace(image_path=Path("/tmp/c.png"), scale=1.0, viewport=(1, 1), rects={}, missing={})
    monkeypatch.setattr(cli.importlib, "import_module", lambda name: SimpleNamespace(capture=capture))
    assert cli.main(["capture", "https://x.test", "--browser", "firefox", "--user-data-dir", str(tmp_path)]) == 0
    assert seen["browser"] == "firefox" and seen["user_data_dir"] == str(tmp_path)
    assert cli.main(["capture", "https://x.test"]) == 0
    assert seen["browser"] == "chromium" and seen["user_data_dir"] is None
    with pytest.raises(SystemExit):
        cli.main(["capture", "https://x.test", "--browser", "netscape"])


def test_copy_reports_copied_only_on_success(ui, tmp_path, out_dirs, monkeypatch, capsys):
    from ainotate import export
    spec = write(tmp_path, good(ui))

    def fail(path):
        raise export.ExportError("osascript failed: no pasteboard")
    monkeypatch.setattr(export, "copy_to_clipboard", fail)
    assert cli.main(["annotate", spec, "--draft", "--copy", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["copied"] is False and any("--copy failed" in w for w in d["warnings"])
    monkeypatch.setattr(export, "copy_to_clipboard", lambda path: None)
    assert cli.main(["annotate", spec, "--draft", "--copy", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["copied"] is True
    assert cli.main(["annotate", spec, "--draft", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["copied"] is False


def test_shoot_debug_is_a_draft_with_the_overlay(ui, monkeypatch, capsys, tmp_path, out_dirs):
    """`shoot --debug`: draft + the render debug overlay (cyan rects, magenta label boxes)."""
    from types import SimpleNamespace

    from ainotate import render as r
    from ainotate.output import save
    calls = {}

    def shoot(spec, draft=False):
        res = r.render({"input": ui, "marks": [{"type": "step", "n": 1, "rect": [1300, 100, 1520, 150],
                                                "label": "Pay"}]})
        calls["draft"] = draft
        calls["debug_px"] = res.image.getpixel((1300, 100))
        return SimpleNamespace(paths=save(res.image, "s", draft=draft), warnings=[], legend=[], redactions=[],
                               size=res.image.size)
    monkeypatch.setattr(cli.importlib, "import_module", lambda name: SimpleNamespace(shoot=shoot))
    spec = write(tmp_path, {"url": "https://x.test", "marks": []})
    assert cli.main(["shoot", spec, "--debug"]) == 0
    assert calls["draft"] is True and not any(out_dirs[0].glob("*.png"))
    debug_px = calls["debug_px"]
    assert cli.main(["shoot", spec, "--draft"]) == 0
    assert debug_px != calls["debug_px"]            # the overlay changed the pixels
    assert r.render.__name__ == "render"             # the real renderer is back


def test_compare_labels_both_forms_and_names(ui, ui_dark, out_dirs, capsys):
    out = out_dirs[0]
    assert cli.main(["compare", ui, ui_dark, "--labels", "Before,After", "--name", "coupon fix"]) == 0
    p = capsys.readouterr().out.strip()
    assert p.startswith(str(out)) and p.endswith(" coupon fix.png")
    assert cli.main(["compare", ui, ui_dark, "--labels", "Old", "New"]) == 0
    assert capsys.readouterr().out.strip().endswith(" compare.png")
    assert cli.main(["compare", ui, ui_dark, "--labels", "Only"]) == 2
    assert "two labels" in capsys.readouterr().err
    assert cli.main(["animate", ui, ui_dark, "--name", "checkout flow", "--duration", "300"]) == 0
    a = capsys.readouterr().out.strip()
    assert a.startswith(str(out)) and a.endswith(" checkout flow.png")


def test_errors_and_warnings_are_masked(capsys):
    cli._err("could not log in as jane.doe@example.com")
    cli._warn("token sk-live-ABCDEF0123456789abcdef in the page")
    err = capsys.readouterr().err
    assert "jane.doe@example.com" not in err and "ABCDEF0123456789abcdef" not in err
    assert err.startswith("ERROR: could not log in as") and "WARN: token" in err
