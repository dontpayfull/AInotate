import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from ainotate import doctor
from ainotate.doctor import Check


def by_name(checks):
    return {c.name: c for c in checks}


def test_run_checks_shape_and_speed():
    import time
    t = time.time()
    checks = doctor.run_checks()
    assert time.time() - t < 6
    names = by_name(checks)
    assert {"python", "pillow", "font", "output dir", "playwright", "cdp", "ocr", "mcp"} <= set(names)
    assert all(isinstance(c, Check) for c in checks)
    assert names["python"].ok and names["pillow"].ok and names["font"].ok


def test_failed_checks_always_carry_a_fix_or_are_optional(monkeypatch):
    for c in doctor.run_checks():
        if not c.ok and c.required:
            assert c.fix, c


def test_output_dir_not_writable(monkeypatch, tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        monkeypatch.setenv("AINOTATE_OUTPUT_DIR", str(ro / "sub"))
        c = doctor.check_output_dir()
        import os
        if os.access(ro, os.W_OK):   # running as root
            pytest.skip("root ignores permissions")
        assert not c.ok and "chmod" in c.fix and not (ro / "sub").exists()
    finally:
        ro.chmod(0o700)


def test_output_dir_creatable_has_no_side_effect(monkeypatch, tmp_path):
    monkeypatch.setenv("AINOTATE_OUTPUT_DIR", str(tmp_path / "new" / "deep"))
    c = doctor.check_output_dir()
    assert c.ok and "will be created" in c.detail
    assert not (tmp_path / "new").exists()


def test_backup_dir_optional(monkeypatch):
    monkeypatch.delenv("AINOTATE_BACKUP_DIR", raising=False)
    monkeypatch.setattr("ainotate.output._read_config_file", lambda p: {})
    c = doctor.check_backup_dir()
    assert c.ok and not c.required


def test_font_missing_file(monkeypatch, tmp_path):
    monkeypatch.setenv("AINOTATE_FONT", str(tmp_path / "nope.ttf"))
    c = doctor.check_font()
    assert not c.ok and "AINOTATE_FONT" in c.fix


def test_cdp_from_neo_config(monkeypatch, tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"ports": {"cdp": 1}}))   # port 1: nothing listens
    monkeypatch.setattr(doctor, "NEO_CONFIG", str(cfg))
    monkeypatch.delenv("AINOTATE_CDP", raising=False)
    url, src = doctor._cdp_target()
    assert url == "http://127.0.0.1:1" and "neo" in src
    c = doctor.check_cdp()
    assert c.ok and "not reachable" in c.detail      # neo not running is not an error


def test_cdp_explicit_unreachable_fails(monkeypatch):
    monkeypatch.setenv("AINOTATE_CDP", "1")
    c = doctor.check_cdp()
    assert not c.ok and c.fix and not c.required


def test_cdp_not_configured(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor, "NEO_CONFIG", str(tmp_path / "none.json"))
    monkeypatch.delenv("AINOTATE_CDP", raising=False)
    assert doctor.check_cdp().ok


def test_screen_capture_skipped_off_mac(monkeypatch):
    monkeypatch.setattr(doctor, "_os", lambda: "Linux")
    assert "skipped" in doctor.check_screen_capture().detail


def test_screen_capture_blank_on_mac(monkeypatch):
    from PIL import Image
    monkeypatch.setattr(doctor, "_os", lambda: "Darwin")

    def fake_run(cmd, **k):
        if cmd[0] == "screencapture":   # not `ps` (the permission-owner lookup)
            Image.new("RGB", (32, 32), (0, 0, 0)).save(cmd[-1], "PNG")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(doctor.subprocess, "run", fake_run)
    c = doctor.check_screen_capture()
    assert not c.ok and "Screen" in c.fix


def test_clipboard_linux_missing(monkeypatch):
    monkeypatch.setattr(doctor, "_os", lambda: "Linux")
    monkeypatch.setattr(doctor.shutil, "which", lambda n: None)
    c = doctor.check_clipboard()
    assert not c.ok and "xclip" in c.fix


def test_playwright_timeout(monkeypatch):
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda n: object())

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("x", 1)
    monkeypatch.setattr(doctor.subprocess, "run", boom)
    c = doctor.check_playwright()
    assert not c.ok and "playwright install" in c.fix


def test_crashing_check_is_reported(monkeypatch):
    def check_bad():
        raise RuntimeError("kaput")
    monkeypatch.setattr(doctor, "CHECKS", [check_bad])
    (c,) = doctor.run_checks()
    assert not c.ok and "kaput" in c.detail


def test_cli_json_and_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "run_checks", lambda: [
        Check("a", True, "fine"), Check("b", False, "bad", "do x", required=False)])
    assert doctor.cli_doctor(["--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] and data["checks"][1] == {"name": "b", "ok": False, "detail": "bad",
                                                "fix": "do x", "required": False}
    monkeypatch.setattr(doctor, "run_checks", lambda: [Check("a", False, "bad", "fix a")])
    assert doctor.cli_doctor([]) == 1
    out = capsys.readouterr().out
    assert "[FAIL] a" in out and "fix: fix a" in out


def test_ocr_check_uses_available_backends(monkeypatch):
    from ainotate import ocr
    monkeypatch.setattr(ocr, "available_backends", lambda: [])
    c = doctor.check_ocr()
    assert not c.ok and "no OCR engine" in c.detail and c.fix == ocr.install_hint()
    assert "tesseract" in c.fix
    monkeypatch.setattr(ocr, "available_backends", lambda: ["tesseract"])
    c = doctor.check_ocr()
    assert c.ok and "tesseract" in c.detail and not c.fix
