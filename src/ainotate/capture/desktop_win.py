"""Windows-only ctypes plumbing for desktop capture (typed user32/gdi32 bindings, window enumeration).
Imported by desktop, which re-exports these names."""
from __future__ import annotations

import ctypes
from pathlib import Path
from types import SimpleNamespace

_DWMWA_EXTENDED_FRAME_BOUNDS = 9

_win_libs_cache = None


def _win_libs():
    """user32/gdi32/kernel32/dwmapi with explicit argtypes/restype on every call we make.

    Without them ctypes assumes 32-bit int and truncates 64-bit HWND/HDC/HBITMAP handles.
    """
    global _win_libs_cache
    if _win_libs_cache is not None:
        return _win_libs_cache
    from ctypes import wintypes
    V, I, U, B = ctypes.c_void_p, ctypes.c_int, wintypes.UINT, wintypes.BOOL
    user32, gdi32, kernel32 = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.kernel32
    try:
        dwmapi = ctypes.windll.dwmapi
    except Exception:
        dwmapi = None

    def sig(fn, args, res):
        fn.argtypes = args
        fn.restype = res

    sig(user32.IsWindowVisible, [V], B)
    sig(user32.IsIconic, [V], B)
    sig(user32.GetWindowTextLengthW, [V], I)
    sig(user32.GetWindowTextW, [V, wintypes.LPWSTR, I], I)
    sig(user32.GetWindowRect, [V, ctypes.POINTER(wintypes.RECT)], B)
    sig(user32.GetWindowThreadProcessId, [V, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD)
    sig(user32.GetWindowDC, [V], V)
    sig(user32.ReleaseDC, [V, V], I)
    sig(user32.PrintWindow, [V, V, U], B)
    sig(user32.GetSystemMetrics, [I], I)
    sig(gdi32.CreateCompatibleDC, [V], V)
    sig(gdi32.CreateCompatibleBitmap, [V, I, I], V)
    sig(gdi32.SelectObject, [V, V], V)
    sig(gdi32.GetDIBits, [V, V, U, U, V, ctypes.POINTER(_bih()), U], I)
    sig(gdi32.DeleteObject, [V], B)
    sig(gdi32.DeleteDC, [V], B)
    sig(kernel32.OpenProcess, [wintypes.DWORD, B, wintypes.DWORD], V)
    sig(kernel32.QueryFullProcessImageNameW,
        [V, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)], B)
    sig(kernel32.CloseHandle, [V], B)
    if dwmapi is not None:
        sig(dwmapi.DwmGetWindowAttribute, [V, wintypes.DWORD, V, wintypes.DWORD], ctypes.c_long)
    _win_libs_cache = SimpleNamespace(user32=user32, gdi32=gdi32, kernel32=kernel32, dwmapi=dwmapi)
    return _win_libs_cache


_BIH_CLS = None


def _bih():
    """BITMAPINFOHEADER (built lazily: wintypes types exist on every OS, but keep it local)."""
    global _BIH_CLS
    if _BIH_CLS is None:
        from ctypes import wintypes

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                        ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]
        _BIH_CLS = BITMAPINFOHEADER
    return _BIH_CLS


def _win_rects(libs, hwnd):
    """(window_rect, visible_rect) as (l, t, r, b) tuples, or (None, None) if the window is gone.

    visible_rect comes from DWMWA_EXTENDED_FRAME_BOUNDS (no invisible resize shadow);
    it falls back to the GetWindowRect rectangle."""
    from ctypes import wintypes
    rc = wintypes.RECT()
    if not libs.user32.GetWindowRect(hwnd, ctypes.byref(rc)):
        return None, None
    full = (rc.left, rc.top, rc.right, rc.bottom)
    vis = full
    if libs.dwmapi is not None:
        try:
            fr = wintypes.RECT()
            hr = libs.dwmapi.DwmGetWindowAttribute(
                hwnd, _DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(fr), ctypes.sizeof(fr))
            if hr == 0 and fr.right > fr.left and fr.bottom > fr.top:
                vis = (fr.left, fr.top, fr.right, fr.bottom)
        except Exception:
            pass
    return full, vis


def _win_windows() -> list[dict]:
    from . import desktop   # lazy (desktop imports this module); keeps desktop._win_dpi_aware patchable
    desktop._win_dpi_aware()
    from ctypes import wintypes
    libs = _win_libs()
    user32, kernel32 = libs.user32, libs.kernel32
    out: list[dict] = []
    cb_type = ctypes.WINFUNCTYPE(wintypes.BOOL, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumWindows.argtypes = [cb_type, ctypes.c_void_p]
    user32.EnumWindows.restype = wintypes.BOOL

    def proc_name(hwnd) -> str:
        h = None
        try:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            h = kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
            if not h:
                return ""
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return Path(buf.value).stem
        except Exception:
            pass
        finally:
            if h:
                kernel32.CloseHandle(h)
        return ""

    def cb(hwnd, _):
        try:
            hwnd = hwnd or 0
            if not user32.IsWindowVisible(hwnd):
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            if n == 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            _full, vis = _win_rects(libs, hwnd)
            if vis is None:
                return True
            w, h = vis[2] - vis[0], vis[3] - vis[1]
            if w < 100 or h < 50 or user32.IsIconic(hwnd):
                return True
            out.append({"id": int(hwnd), "app": proc_name(hwnd), "title": buf.value,
                        "bounds": {"x": vis[0], "y": vis[1], "w": w, "h": h}})
        except Exception:
            pass  # one odd window must not abort the enumeration
        return True

    user32.EnumWindows(cb_type(cb), 0)
    return out
