import subprocess
import sys
from types import SimpleNamespace

import pytest
from PIL import Image

from ainotate.capture import clipboard, desktop, desktop_win
from ainotate.capture.desktop import CaptureError


def fake_run_factory(calls, returncode=0, color=(10, 20, 30), write=True):
    def fake_run(cmd, timeout=20):
        calls.append(cmd)
        if write:
            Image.new("RGB", (120, 90), color).save(cmd[-1], "PNG")
        return SimpleNamespace(returncode=returncode, stdout="", stderr="")
    return fake_run


def test_mac_screen_command(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(desktop, "_system", lambda: "Darwin")
    monkeypatch.setattr(desktop, "_run", fake_run_factory(calls))
    p = desktop.screen(tmp_path / "a.png", region="1,2,300,200", display=2)
    assert calls[0] == ["screencapture", "-x", "-D", "2", "-R", "1,2,300,200", str(p)]


def test_mac_blank_capture_explains_permission(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop, "_system", lambda: "Darwin")
    monkeypatch.setattr(desktop, "_run", fake_run_factory([], color=(0, 0, 0)))
    with pytest.raises(CaptureError, match="Screen Recording"):
        desktop.screen(tmp_path / "a.png")


def test_mac_window_command(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(desktop, "_system", lambda: "Darwin")
    monkeypatch.setattr(desktop, "_run", fake_run_factory(calls))
    desktop.window("42", tmp_path / "w.png")
    assert calls[0][:5] == ["screencapture", "-x", "-o", "-l", "42"]


def test_window_bad_id(monkeypatch):
    monkeypatch.setattr(desktop, "_system", lambda: "Darwin")
    with pytest.raises(CaptureError, match="invalid window id"):
        desktop.window("abc")


def test_bad_region():
    with pytest.raises(CaptureError):
        desktop.screen(region="1,2,0,5")
    with pytest.raises(CaptureError):
        desktop.screen(region="a,b")


def _virtual_screen(monkeypatch, origin, size=(400, 300)):
    """Fake Pillow grab of a virtual desktop whose top-left is `origin`."""
    import PIL.ImageGrab as ig
    img = Image.new("RGB", size, (5, 5, 5))
    img.putpixel((0, 0), (200, 0, 0))            # first pixel = virtual origin
    seen = {}
    monkeypatch.setattr(ig, "grab", lambda bbox=None, all_screens=False, **k:
                        seen.update(bbox=bbox, all=all_screens) or img.copy())
    monkeypatch.setattr(desktop, "_system", lambda: "Windows")
    monkeypatch.setattr(desktop, "_win_dpi_aware", lambda: seen.setdefault("dpi", True))
    monkeypatch.setattr(desktop, "_win_virtual_origin", lambda: origin)
    return seen


def test_windows_screen_uses_all_screens(monkeypatch, tmp_path):
    seen = _virtual_screen(monkeypatch, (0, 0))
    p = desktop.screen(tmp_path / "w.png", region=(10, 20, 100, 50))
    assert seen == {"bbox": None, "all": True, "dpi": True}
    assert Image.open(p).size == (100, 50)


def test_windows_negative_virtual_origin(monkeypatch, tmp_path):
    _virtual_screen(monkeypatch, (-100, -50))
    # region starting exactly at the (negative) virtual origin keeps the marker pixel
    p = desktop.screen(tmp_path / "n.png", region=(-100, -50, 60, 40))
    im = Image.open(p)
    assert im.size == (60, 40) and im.getpixel((0, 0)) == (200, 0, 0)
    with pytest.raises(CaptureError, match="outside"):
        desktop.screen(tmp_path / "o.png", region=(-900, -900, 10, 10))


def test_windows_dpi_called_once(monkeypatch):
    calls = []
    fake = SimpleNamespace(
        shcore=SimpleNamespace(SetProcessDpiAwareness=lambda v: calls.append(v)),
        user32=SimpleNamespace(SetProcessDPIAware=lambda: calls.append("legacy")))
    monkeypatch.setattr(desktop.ctypes, "windll", fake, raising=False)
    monkeypatch.setattr(desktop, "_dpi_done", False)
    desktop._win_dpi_aware()
    desktop._win_dpi_aware()
    assert calls == [2]


def test_windows_window_falls_back_to_rect(monkeypatch, tmp_path):
    import PIL.ImageGrab as ig
    seen = {}
    monkeypatch.setattr(ig, "grab", lambda bbox=None, all_screens=False, **k: Image.new("RGB", (400, 300), (1, 2, 3)))
    monkeypatch.setattr(desktop, "_win_virtual_origin", lambda: (0, 0))
    monkeypatch.setattr(desktop, "_system", lambda: "Windows")
    monkeypatch.setattr(desktop, "_win_dpi_aware", lambda: None)
    monkeypatch.setattr(desktop, "_win_print_window", lambda h, o: False)
    monkeypatch.setattr(desktop, "list_windows", lambda f=None: [
        {"id": 7, "app": "x", "title": "t", "bounds": {"x": 5, "y": 6, "w": 200, "h": 100}}])
    p = desktop.window(7, tmp_path / "x.png")
    assert Image.open(p).size == (200, 100)


def test_linux_list_windows_unsupported(monkeypatch):
    monkeypatch.setattr(desktop, "_system", lambda: "Linux")
    monkeypatch.setattr(desktop.shutil, "which", lambda n: None)
    with pytest.raises(CaptureError, match="unsupported"):
        desktop.list_windows()


def test_parse_wmctrl_and_filter(monkeypatch):
    out = ("0x0400003  0 10 20 800 600 term.XTerm host My Terminal\n"
           "0x0200001 -1 0 0 1920 1080 desktop.Desktop host Desktop\n"
           "0x0500001  0 0 0 50 20 tiny.X host tiny\n")
    wins = desktop._parse_wmctrl(out)
    assert wins == [{"id": "0x0400003", "app": "XTerm", "title": "My Terminal",
                     "bounds": {"x": 10, "y": 20, "w": 800, "h": 600}}]
    monkeypatch.setattr(desktop, "_system", lambda: "Linux")
    monkeypatch.setattr(desktop, "_linux_windows", lambda: wins)
    assert desktop.list_windows("terminal") == wins
    assert desktop.list_windows("nope") == []


def test_linux_window_prefers_import(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(desktop, "_system", lambda: "Linux")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(desktop, "list_windows", lambda f=None: [
        {"id": "0x1", "app": "a", "title": "t", "bounds": {"x": 0, "y": 0, "w": 200, "h": 100}}])
    monkeypatch.setattr(desktop.shutil, "which", lambda n: "/usr/bin/import")
    monkeypatch.setattr(desktop, "_run", fake_run_factory(calls))
    desktop.window("0x1", tmp_path / "l.png")
    assert calls[0][:3] == ["import", "-window", "0x1"]


def test_linux_screen_grim_on_wayland(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(desktop, "_system", lambda: "Linux")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(desktop.shutil, "which", lambda n: "/usr/bin/grim")
    monkeypatch.setattr(desktop, "_run", fake_run_factory(calls))
    desktop.screen(tmp_path / "g.png", region=(1, 2, 3, 4))
    assert calls[0][:3] == ["grim", "-g", "1,2 3x4"]


def test_linux_screen_no_backend(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop, "_system", lambda: "Linux")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setitem(sys.modules, "mss", None)
    with pytest.raises(CaptureError, match="mss"):
        desktop.screen(tmp_path / "x.png")


def test_clipboard_no_image(monkeypatch, tmp_path):
    import PIL.ImageGrab as ig
    monkeypatch.setattr(clipboard.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(ig, "grabclipboard", lambda: None)
    with pytest.raises(CaptureError, match="No image"):
        clipboard.grab(tmp_path / "c.png")


def test_clipboard_image_and_file_list(monkeypatch, tmp_path):
    import PIL.ImageGrab as ig
    monkeypatch.setattr(clipboard.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ig, "grabclipboard", lambda: Image.new("RGB", (4, 4), (9, 9, 9)))
    assert Image.open(clipboard.grab(tmp_path / "c.png")).size == (4, 4)
    src = tmp_path / "s.jpg"
    Image.new("RGB", (6, 5), (1, 1, 1)).save(src)
    monkeypatch.setattr(ig, "grabclipboard", lambda: [str(tmp_path / "x.txt"), str(src)])
    assert Image.open(clipboard.grab(tmp_path / "d.png")).size == (6, 5)
    monkeypatch.setattr(ig, "grabclipboard", lambda: [str(tmp_path / "x.txt")])
    with pytest.raises(CaptureError, match="none is an image"):
        clipboard.grab(tmp_path / "e.png")


def test_clipboard_linux_commands(monkeypatch, tmp_path):
    png = b"\x89PNG\r\n" + b"0" * 10
    seen = []
    monkeypatch.setattr(clipboard.platform, "system", lambda: "Linux")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(clipboard.shutil, "which", lambda n: "/x" if n == "xclip" else None)
    monkeypatch.setattr(clipboard.subprocess, "run",
                        lambda cmd, **k: seen.append(cmd) or SimpleNamespace(returncode=0, stdout=png))
    p = clipboard.grab(tmp_path / "l.png")
    assert seen[0] == ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"]
    assert p.read_bytes() == png
    monkeypatch.setattr(clipboard.subprocess, "run",
                        lambda cmd, **k: SimpleNamespace(returncode=1, stdout=b""))
    with pytest.raises(CaptureError, match="No image"):
        clipboard.grab(tmp_path / "m.png")
    monkeypatch.setattr(clipboard.shutil, "which", lambda n: None)
    with pytest.raises(CaptureError, match="xclip"):
        clipboard.grab(tmp_path / "n.png")


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS live capture")
def test_mac_live_screen_and_windows(tmp_path):
    try:
        wins = desktop.list_windows()
    except CaptureError:
        pytest.skip("no Quartz")
    assert all({"id", "app", "title", "bounds"} <= set(w) for w in wins)
    try:
        p = desktop.screen(tmp_path / "s.png")
    except CaptureError as e:
        pytest.skip(str(e))
    extrema = Image.open(p).convert("RGB").getextrema()
    assert any(lo != hi for lo, hi in extrema)


# --------------------------------------------------------------- Windows ctypes hardening

class FakeWin:
    """Stand-in for user32/gdi32/kernel32/dwmapi: every function is a MagicMock, so the
    code under test can set argtypes/restype and we can inspect calls and cleanup."""

    def __init__(self, w=300, h=200, fail=None, dwm=None):
        from unittest.mock import MagicMock
        self.fail = fail
        self.log = []
        M = MagicMock

        def rec(name, ret):
            m = M(name=name, side_effect=lambda *a: (self.log.append(name), None if fail == name else ret)[1])
            return m

        self.user32 = SimpleNamespace(
            IsIconic=rec("IsIconic", 0), GetWindowDC=rec("GetWindowDC", 0x11),
            ReleaseDC=rec("ReleaseDC", 1), PrintWindow=rec("PrintWindow", 1))
        self.user32.GetWindowRect = M(side_effect=self._rect(0, 0, w, h))
        self.gdi32 = SimpleNamespace(
            CreateCompatibleDC=rec("CreateCompatibleDC", 0x22),
            CreateCompatibleBitmap=rec("CreateCompatibleBitmap", 0x33),
            SelectObject=rec("SelectObject", 0x44), DeleteObject=rec("DeleteObject", 1),
            DeleteDC=rec("DeleteDC", 1))
        import ctypes

        def dibits(mem, bmp, a, hh, buf, bih, c):
            self.log.append("GetDIBits")
            if fail == "GetDIBits":
                return 0
            ctypes.memset(buf, 120, len(buf))
            return hh
        self.gdi32.GetDIBits = M(side_effect=dibits)
        self.kernel32 = SimpleNamespace()
        self.dwmapi = None
        if dwm:
            self.dwmapi = SimpleNamespace(DwmGetWindowAttribute=M(side_effect=self._dwm(*dwm)))

    def _rect(self, l, t, r, b):
        def f(hwnd, ref):
            self.log.append("GetWindowRect")
            rc = ref._obj
            rc.left, rc.top, rc.right, rc.bottom = l, t, r, b
            return 1
        return f

    def _dwm(self, l, t, r, b):
        def f(hwnd, attr, ref, size):
            assert attr == 9  # DWMWA_EXTENDED_FRAME_BOUNDS
            rc = ref._obj
            rc.left, rc.top, rc.right, rc.bottom = l, t, r, b
            return 0
        return f


def _use_fake(monkeypatch, fake):
    monkeypatch.setattr(desktop, "_win_libs", lambda: fake)
    monkeypatch.setattr(desktop, "_system", lambda: "Windows")


@pytest.mark.parametrize("fail", ["GetWindowDC", "CreateCompatibleDC", "CreateCompatibleBitmap",
                                  "SelectObject", "PrintWindow", "GetDIBits"])
def test_print_window_failure_paths_clean_up(monkeypatch, tmp_path, fail):
    fake = FakeWin(fail=fail)
    _use_fake(monkeypatch, fake)
    assert desktop._win_print_window(5, tmp_path / "p.png") is False
    created = {"CreateCompatibleDC": "DeleteDC", "CreateCompatibleBitmap": "DeleteObject",
               "GetWindowDC": "ReleaseDC"}
    order = ["GetWindowDC", "CreateCompatibleDC", "CreateCompatibleBitmap"]
    for made in order[:order.index(fail)] if fail in order else order:
        assert created[made] in fake.log, (fail, fake.log)
    if fail == "GetWindowDC":
        assert "ReleaseDC" not in fake.log and "DeleteDC" not in fake.log
    assert not (tmp_path / "p.png").exists()


def test_print_window_success_releases_everything_and_crops_shadow(monkeypatch, tmp_path):
    fake = FakeWin(w=300, h=200, dwm=(8, 0, 292, 192))   # invisible 8px shadow left/right/bottom
    _use_fake(monkeypatch, fake)
    assert desktop._win_print_window(5, tmp_path / "p.png") is True
    assert Image.open(tmp_path / "p.png").size == (284, 192)
    for name in ("DeleteObject", "DeleteDC", "ReleaseDC"):
        assert name in fake.log
    fake.user32.ReleaseDC.assert_called_once_with(5, 0x11)
    fake.gdi32.DeleteObject.assert_called_once_with(0x33)
    fake.gdi32.DeleteDC.assert_called_once_with(0x22)


def test_print_window_falls_back_to_getwindowrect_without_dwm(monkeypatch, tmp_path):
    _use_fake(monkeypatch, FakeWin(w=300, h=200, dwm=None))
    assert desktop._win_print_window(5, tmp_path / "p.png") is True
    assert Image.open(tmp_path / "p.png").size == (300, 200)


def test_win_rects_dwm_failure_uses_window_rect(monkeypatch):
    fake = FakeWin(w=300, h=200, dwm=(0, 0, 0, 0))       # degenerate DWM answer
    full, vis = desktop._win_rects(fake, 5)
    assert full == vis == (0, 0, 300, 200)


def test_win_libs_sets_argtypes_and_restype(monkeypatch):
    """Every WinAPI function we call gets explicit argtypes and restype (64-bit handles)."""
    import ctypes
    from unittest.mock import MagicMock
    libs = {n: MagicMock(name=n) for n in ("user32", "gdi32", "kernel32", "dwmapi")}
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(**libs), raising=False)
    monkeypatch.setattr(desktop_win, "_win_libs_cache", None)
    got = desktop._win_libs()
    monkeypatch.setattr(desktop_win, "_win_libs_cache", None)
    handle_fns = [("user32", "GetWindowDC"), ("gdi32", "CreateCompatibleDC"),
                  ("gdi32", "CreateCompatibleBitmap"), ("gdi32", "SelectObject"),
                  ("user32", "PrintWindow"), ("gdi32", "GetDIBits"), ("gdi32", "DeleteObject"),
                  ("gdi32", "DeleteDC"), ("user32", "ReleaseDC"), ("user32", "GetWindowRect"),
                  ("user32", "IsWindowVisible"), ("user32", "IsIconic"),
                  ("dwmapi", "DwmGetWindowAttribute"), ("kernel32", "OpenProcess"),
                  ("kernel32", "CloseHandle"), ("kernel32", "QueryFullProcessImageNameW")]
    for lib, fn in handle_fns:
        f = getattr(getattr(got, lib), fn)
        assert isinstance(f.argtypes, list) and f.argtypes, (lib, fn)
        assert f.restype is not None, (lib, fn)
    assert got.user32.GetWindowDC.restype is ctypes.c_void_p
    assert got.gdi32.CreateCompatibleBitmap.argtypes[0] is ctypes.c_void_p
    assert got.gdi32.SelectObject.restype is ctypes.c_void_p


def test_dpi_prefers_per_monitor_v2(monkeypatch):
    import ctypes
    calls = []
    u = SimpleNamespace(SetProcessDpiAwarenessContext=lambda v: calls.append(("ctx", v.value)) or 1,
                        SetProcessDPIAware=lambda: calls.append("legacy"))
    sh = SimpleNamespace(SetProcessDpiAwareness=lambda v: calls.append(("shcore", v)))
    monkeypatch.setattr(desktop.ctypes, "windll", SimpleNamespace(user32=u, shcore=sh), raising=False)
    monkeypatch.setattr(desktop, "_dpi_done", False)
    desktop._win_dpi_aware()
    assert len(calls) == 1 and calls[0][0] == "ctx"
