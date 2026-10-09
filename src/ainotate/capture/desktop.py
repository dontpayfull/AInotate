"""Desktop capture: whole screen, region, or one window, per OS.

Never moves, resizes, focuses or closes user windows.
"""
from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace  # noqa: F401

from .desktop_win import _bih, _win_libs, _win_rects, _win_windows  # noqa: F401  (re-export)


class CaptureError(Exception):
    """A capture could not be made; the message says what to do about it."""


MAC_PERMISSION_HELP = (
    "The capture came back blank. macOS Screen Recording permission is probably missing: "
    "System Settings > Privacy & Security > Screen & System Audio Recording, enable the "
    "app that runs this command (Terminal, iTerm, VS Code, Claude...), then restart that app."
)


def _system() -> str:
    return platform.system()


def _out(out_path, prefix: str) -> Path:
    if out_path:
        p = Path(out_path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    fd, name = tempfile.mkstemp(prefix=f"ainotate-{prefix}-", suffix=".png")
    os.close(fd)
    return Path(name)


def _run(cmd: list[str], timeout: int = 20) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise CaptureError(f"`{cmd[0]}` was not found; install it or put it on PATH.")
    except subprocess.TimeoutExpired:
        raise CaptureError(f"`{cmd[0]}` timed out after {timeout}s.")


def _check_not_blank(path: Path) -> None:
    """Raise if the file is missing/empty or a single flat colour (permission denied)."""
    if not path.exists() or path.stat().st_size == 0:
        raise CaptureError("No image was produced. " + (MAC_PERMISSION_HELP if _system() == "Darwin" else ""))
    try:
        from PIL import Image
        with Image.open(path) as im:
            extrema = im.convert("RGB").getextrema()
    except Exception as e:  # unreadable image
        raise CaptureError(f"Captured file is not a readable image: {e}")
    if all(lo == hi == 0 for lo, hi in extrema):
        if _system() == "Darwin":
            raise CaptureError(MAC_PERMISSION_HELP)
        raise CaptureError("The capture is completely black (protected content, or no display access).")


def _parse_region(region):
    if region is None:
        return None
    if isinstance(region, str):
        region = region.split(",")
    try:
        x, y, w, h = (int(float(v)) for v in region)
    except (TypeError, ValueError):
        raise CaptureError("region must be four numbers: x,y,w,h")
    if w <= 0 or h <= 0:
        raise CaptureError("region width and height must be positive")
    return x, y, w, h


# --------------------------------------------------------------------------- screen

def screen(out_path=None, region=None, display=None) -> Path:
    """Capture the screen (all displays on Windows, main display on macOS/Linux unless
    `display` is given). `region` = (x, y, w, h) in screen coordinates."""
    region = _parse_region(region)
    out = _out(out_path, "screen")
    try:
        sysname = _system()
        if sysname == "Darwin":
            cmd = ["screencapture", "-x"]
            if display is not None:
                cmd += ["-D", str(int(display))]
            if region:
                cmd += ["-R", "%d,%d,%d,%d" % region]
            cmd.append(str(out))
            r = _run(cmd)
            if r.returncode != 0:
                raise CaptureError("screencapture failed: " + (r.stderr.strip() or f"exit {r.returncode}"))
        elif sysname == "Windows":
            _win_dpi_aware()
            _win_grab(region, out)
        else:
            _linux_screen(region, display, out)
        _check_not_blank(out)
        return out
    except BaseException:
        if not out_path:
            out.unlink(missing_ok=True)
        raise


def _win_virtual_origin() -> tuple[int, int]:
    """Top-left of the virtual screen (negative when a monitor sits left of / above the primary)."""
    try:
        u = _win_libs().user32
        return int(u.GetSystemMetrics(76)), int(u.GetSystemMetrics(77))  # SM_X/YVIRTUALSCREEN
    except Exception:
        return 0, 0


def _win_grab(region, out: Path) -> None:
    try:
        from PIL import ImageGrab
    except ImportError:
        raise CaptureError("Pillow is required: pip install pillow")
    try:
        # Always grab the whole virtual desktop and crop ourselves: Pillow's bbox origin
        # differs between versions when monitors have negative coordinates.
        img = ImageGrab.grab(all_screens=True)
    except Exception as e:
        raise CaptureError(f"Screen grab failed: {e}")
    if region:
        x, y, w, h = region
        vx, vy = _win_virtual_origin()
        box = (x - vx, y - vy, x - vx + w, y - vy + h)
        box = (max(box[0], 0), max(box[1], 0), min(box[2], img.width), min(box[3], img.height))
        if box[2] <= box[0] or box[3] <= box[1]:
            raise CaptureError(f"region {x},{y},{w},{h} is outside the screen area")
        img = img.crop(box)
    img.convert("RGB").save(out)


def _linux_screen(region, display, out: Path) -> None:
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("grim"):
        cmd = ["grim"]
        if region:
            x, y, w, h = region
            cmd += ["-g", f"{x},{y} {w}x{h}"]
        cmd.append(str(out))
        r = _run(cmd)
        if r.returncode != 0:
            raise CaptureError("grim failed: " + r.stderr.strip())
        return
    try:
        import mss
        import mss.tools
    except ImportError:
        raise CaptureError(
            "No screen-capture backend found. Install mss (pip install mss), or grim on Wayland.")
    try:
        with mss.mss() as sct:
            if region:
                x, y, w, h = region
                mon = {"left": x, "top": y, "width": w, "height": h}
            else:
                idx = int(display) if display is not None else 0  # 0 = all monitors combined
                if idx >= len(sct.monitors):
                    raise CaptureError(f"display {idx} not found; {len(sct.monitors) - 1} display(s) available")
                mon = sct.monitors[idx]
            shot = sct.grab(mon)
            mss.tools.to_png(shot.rgb, shot.size, output=str(out))
    except CaptureError:
        raise
    except Exception as e:
        raise CaptureError(f"mss capture failed ({e}). On Wayland install grim; on X11 check $DISPLAY.")


# --------------------------------------------------------------------------- windows

def list_windows(filter=None) -> list[dict]:
    """Visible top-level windows: [{id, app, title, bounds:{x,y,w,h}}]. `filter` is a
    case-insensitive substring matched against app and title."""
    sysname = _system()
    if sysname == "Darwin":
        wins = _mac_windows()
    elif sysname == "Windows":
        wins = _win_windows()
    else:
        wins = _linux_windows()
    if filter:
        f = str(filter).lower()
        wins = [w for w in wins if f in w["app"].lower() or f in w["title"].lower()]
    return wins


def _mac_windows() -> list[dict]:
    try:
        import Quartz
    except ImportError:
        raise CaptureError("Window listing on macOS needs pyobjc-framework-Quartz: pip install pyobjc-framework-Quartz")
    infos = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID) or []
    out = []
    for w in infos:
        if w.get("kCGWindowLayer", 0) != 0:
            continue
        b = w.get("kCGWindowBounds") or {}
        if b.get("Width", 0) < 100 or b.get("Height", 0) < 50:
            continue
        out.append({
            "id": int(w["kCGWindowNumber"]),
            "app": str(w.get("kCGWindowOwnerName", "")),
            "pid": int(w.get("kCGWindowOwnerPID", 0)) or None,
            "title": str(w.get("kCGWindowName", "") or ""),
            "bounds": {"x": int(b["X"]), "y": int(b["Y"]), "w": int(b["Width"]), "h": int(b["Height"])},
        })
    return out


_dpi_done = False


def _win_dpi_aware() -> None:
    """Make coordinates physical pixels: per-monitor v2, then per-monitor, then system
    aware (idempotent, best effort)."""
    global _dpi_done
    if _dpi_done:
        return
    _dpi_done = True
    try:
        user32 = ctypes.windll.user32
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext.restype = ctypes.c_int
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
            return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor (Win 8.1+)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _linux_windows() -> list[dict]:
    if not shutil.which("wmctrl"):
        raise CaptureError(
            "Window listing is unsupported here: install `wmctrl` (X11 only). "
            "On Wayland use `ainotate screen --region x,y,w,h` instead.")
    r = _run(["wmctrl", "-lGx"])
    if r.returncode != 0:
        raise CaptureError("wmctrl failed: " + (r.stderr.strip() or "no X11 display?"))
    return _parse_wmctrl(r.stdout)


def _parse_wmctrl(text: str) -> list[dict]:
    """Lines of `wmctrl -lGx`: id desktop x y w h class host title..."""
    out = []
    for line in text.splitlines():
        p = line.split(None, 8)
        if len(p) < 8:
            continue
        try:
            x, y, w, h = (int(v) for v in p[2:6])
        except ValueError:
            continue
        if p[1] == "-1" or w < 100 or h < 50:  # desktop / panels
            continue
        cls = p[6].split(".")[-1]
        out.append({"id": p[0], "app": cls, "title": p[8] if len(p) > 8 else "",
                    "bounds": {"x": x, "y": y, "w": w, "h": h}})
    return out


def _find(id) -> dict | None:
    for w in list_windows():
        if str(w["id"]).lower() == str(id).lower():
            return w
    return None


def window(id, out_path=None) -> Path:
    """Capture one window by id from `list_windows()`.

    Windows/Linux fallback grabs the window's screen rectangle, so anything covering it
    appears in the image; raise the window yourself first if needed (this tool never does).
    """
    sysname = _system()
    if sysname in ("Darwin", "Windows"):
        try:
            wid = int(id)
        except (TypeError, ValueError):
            raise CaptureError(f"invalid window id {id!r}; use an id from `ainotate windows`")
    out = _out(out_path, "window")
    try:
        if sysname == "Darwin":
            r = _run(["screencapture", "-x", "-o", "-l", str(wid), str(out)])
            if r.returncode != 0 or not out.exists() or out.stat().st_size == 0:
                raise CaptureError(
                    f"Could not capture window {wid} (closed, minimized or on another Space?). "
                    + (r.stderr.strip() or "Run `ainotate windows` for current ids."))
        elif sysname == "Windows":
            _win_dpi_aware()
            if not _win_print_window(wid, out):
                w = _find(wid)
                if not w:
                    raise CaptureError(f"Window {id} not found or not visible; run `ainotate windows`.")
                b = w["bounds"]
                _win_grab((b["x"], b["y"], b["w"], b["h"]), out)
        else:
            w = _find(id)
            if not w:
                raise CaptureError(f"Window {id} not found; run `ainotate windows`.")
            if shutil.which("import") and not os.environ.get("WAYLAND_DISPLAY"):
                r = _run(["import", "-window", str(id), str(out)])
                if r.returncode != 0:
                    raise CaptureError("ImageMagick import failed: " + r.stderr.strip())
            else:
                b = w["bounds"]
                _linux_screen((b["x"], b["y"], b["w"], b["h"]), None, out)
        _check_not_blank(out)
        return out
    except BaseException:
        if not out_path:
            out.unlink(missing_ok=True)
        raise


def _win_print_window(hwnd: int, out: Path) -> bool:
    """Try PrintWindow (PW_RENDERFULLCONTENT) so covered windows render. Best effort:
    returns False (caller falls back to a screen grab) on any failure. GDI objects are
    always released."""
    hdc = mem = bmp = old = None
    libs = None
    try:
        from PIL import Image
        libs = _win_libs()
        user32, gdi32 = libs.user32, libs.gdi32
        full, vis = _win_rects(libs, hwnd)
        if full is None or user32.IsIconic(hwnd):
            return False
        w, h = full[2] - full[0], full[3] - full[1]
        if w <= 0 or h <= 0:
            return False
        hdc = user32.GetWindowDC(hwnd)
        if not hdc:
            return False
        mem = gdi32.CreateCompatibleDC(hdc)
        if not mem:
            return False
        bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
        if not bmp:
            return False
        old = gdi32.SelectObject(mem, bmp)
        if not old:
            return False
        if not user32.PrintWindow(hwnd, mem, 2):
            return False
        BIH = _bih()
        bih = BIH(ctypes.sizeof(BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        # SelectObject'ed bitmaps must be deselected before GetDIBits
        gdi32.SelectObject(mem, old)
        old = None
        if gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bih), 0) != h:
            return False
        img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1)
        if vis != full:  # drop the invisible shadow border
            box = (vis[0] - full[0], vis[1] - full[1], vis[2] - full[0], vis[3] - full[1])
            if 0 <= box[0] < box[2] <= w and 0 <= box[1] < box[3] <= h:
                img = img.crop(box)
        img.convert("RGB").save(out)
        try:
            _check_not_blank(out)
        except CaptureError:
            return False
        return True
    except Exception:
        return False
    finally:
        if libs is not None:
            for fn, args in ((libs.gdi32.SelectObject, (mem, old) if (mem and old) else None),
                             (libs.gdi32.DeleteObject, (bmp,) if bmp else None),
                             (libs.gdi32.DeleteDC, (mem,) if mem else None),
                             (libs.user32.ReleaseDC, (hwnd, hdc) if hdc else None)):
                if args is None:
                    continue
                try:
                    fn(*args)
                except Exception:
                    pass


# --------------------------------------------------------------------------- CLI helpers

def _describe(path: Path) -> str:
    try:
        from PIL import Image
        with Image.open(path) as im:
            return f"{path}\t{im.width}x{im.height}"
    except Exception:
        return str(path)


def cli_screen(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="ainotate screen")
    ap.add_argument("--region", help="x,y,w,h")
    ap.add_argument("--display", type=int)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    print(_describe(screen(a.out, a.region, a.display)))
    return 0


def cli_windows(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="ainotate windows")
    ap.add_argument("filter", nargs="?")
    a = ap.parse_args(argv)
    for w in list_windows(a.filter):
        b = w["bounds"]
        print("\t".join([str(w["id"]), w["app"], w["title"], f"{b['x']},{b['y']},{b['w']},{b['h']}"]))
    return 0


def cli_window(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="ainotate window")
    ap.add_argument("id")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    print(_describe(window(a.id, a.out)))
    return 0


def cli_clipboard(argv: list[str]) -> int:
    from . import clipboard
    return clipboard.cli_clipboard(argv)
