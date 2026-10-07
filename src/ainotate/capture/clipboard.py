"""Image from the clipboard, per OS."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

from .desktop import CaptureError

_IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"}


def _out(out_path) -> Path:
    if out_path:
        p = Path(out_path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    fd, name = tempfile.mkstemp(prefix="ainotate-clipboard-", suffix=".png")
    os.close(fd)
    return Path(name)


def grab(out_path=None) -> Path:
    """Save the clipboard image as PNG and return its path; CaptureError if none."""
    out = _out(out_path)
    try:
        if platform.system() in ("Darwin", "Windows"):
            _grab_pil(out)
        else:
            _grab_linux(out)
    except CaptureError:
        out.unlink(missing_ok=True) if not out_path else None
        raise
    return out


def _grab_pil(out: Path) -> None:
    try:
        from PIL import Image, ImageGrab
    except ImportError:
        raise CaptureError("Pillow is required: pip install pillow")
    try:
        data = ImageGrab.grabclipboard()
    except Exception as e:
        raise CaptureError(f"Could not read the clipboard: {e}")
    if isinstance(data, list):  # copied files
        for name in data:
            if Path(name).suffix.lower() in _IMG_EXT and Path(name).exists():
                with Image.open(name) as im:
                    im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB").save(out, "PNG")
                return
        raise CaptureError("The clipboard holds files, but none is an image.")
    if data is None:
        raise CaptureError("No image on the clipboard. Copy an image or take a screenshot to the clipboard first.")
    data.save(out, "PNG")


def _grab_linux(out: Path) -> None:
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-paste"):
        cmd = ["wl-paste", "--no-newline", "--type", "image/png"]
    elif shutil.which("xclip"):
        cmd = ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"]
    elif shutil.which("wl-paste"):
        cmd = ["wl-paste", "--no-newline", "--type", "image/png"]
    else:
        raise CaptureError("Install `wl-clipboard` (Wayland) or `xclip` (X11) to read the clipboard.")
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=15)
    except subprocess.TimeoutExpired:
        raise CaptureError(f"`{cmd[0]}` timed out.")
    if r.returncode != 0 or not r.stdout.startswith(b"\x89PNG"):
        raise CaptureError("No image on the clipboard. Copy an image or take a screenshot to the clipboard first.")
    out.write_bytes(r.stdout)


def cli_clipboard(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="ainotate clipboard")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    p = grab(a.out)
    try:
        from PIL import Image
        with Image.open(p) as im:
            print(f"{p}\t{im.width}x{im.height}")
    except Exception:
        print(p)
    return 0
