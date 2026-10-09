"""`ainotate doctor`: check that this machine can run every part of AInotate.

Each check returns a Check(name, ok, detail, fix); `fix` is an exact command (or
click path) for the current OS. Checks run in parallel threads, finish in a few
seconds, and leave nothing behind except temp files they delete.
"""
from __future__ import annotations

import importlib.util
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

NEO_CONFIG = "~/Library/Application Support/BrowserClaw/.browseros/config.json"
SLOW_TIMEOUT = 4.0


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""
    required: bool = True      # a failed optional check is a warning, not an error


def _os() -> str:
    return platform.system()


def _pip(extra: str = "") -> str:
    return f'"{sys.executable}" -m pip install -U ' + (f'"ainotate[{extra}]"' if extra else "pillow")


# --------------------------------------------------------------------------- checks

def check_python() -> Check:
    v = sys.version_info
    ok = v >= (3, 10)
    return Check("python", ok, f"{v.major}.{v.minor}.{v.micro} ({sys.executable})",
                 "" if ok else {"Darwin": "brew install python@3.12",
                                "Windows": "winget install Python.Python.3.12"}.get(
                                    _os(), "sudo apt install python3.12  # or your distro's package"))


def check_pillow() -> Check:
    try:
        import PIL
        ok = tuple(int(p) for p in PIL.__version__.split(".")[:2]) >= (10, 1)
        return Check("pillow", ok, PIL.__version__, "" if ok else _pip())
    except ImportError:
        return Check("pillow", False, "not installed", _pip())


def check_font() -> Check:
    try:
        from PIL import ImageFont
        from . import style
        path, index = style.font_source()
        if not Path(path).is_file():
            return Check("font", False, f"font file missing: {path}",
                         "unset AINOTATE_FONT, or reinstall: " + _pip("").replace(" pillow", " ainotate"))
        font = ImageFont.truetype(path, 24, index=index)
        tofu = bytes(font.getmask("\uffff"))
        missing = [c for c in "ăâîșțéöüñ" if bytes(font.getmask(c)) == tofu]
        if missing:
            return Check("font", False, f"{Path(path).name} lacks glyphs: {''.join(missing)}",
                         "use a font with Latin Extended coverage: set AINOTATE_FONT=/path/to/font.ttf "
                         "(or unset it to use the bundled Inter Bold)")
        return Check("font", True, f"{Path(path).name} loads, diacritics render")
    except Exception as e:
        return Check("font", False, f"cannot load the label font: {e}",
                     "unset AINOTATE_FONT, or reinstall ainotate (bundled Inter-Bold.ttf missing)")


def _writable_dir(path: Path) -> tuple[bool, str]:
    """Writable now, or creatable (nearest existing ancestor is writable). No side effects."""
    p = path
    if p.is_dir():
        return (os.access(p, os.W_OK | os.X_OK), "exists")
    while not p.exists() and p.parent != p:
        p = p.parent
    return (p.is_dir() and os.access(p, os.W_OK | os.X_OK), f"will be created under {p}")


def check_output_dir() -> Check:
    try:
        from .output import load_config
        cfg = load_config()
    except Exception as e:
        return Check("output dir", False, f"config unreadable: {e}",
                     "fix or delete ~/.config/ainotate/config.toml")
    ok, note = _writable_dir(cfg.output_dir)
    return Check("output dir", ok, f"{cfg.output_dir} ({note if ok else 'not writable'})",
                 "" if ok else f'mkdir -p {shlex.quote(str(cfg.output_dir))} && chmod u+rwx {shlex.quote(str(cfg.output_dir))}  # or set AINOTATE_OUTPUT_DIR')


def check_backup_dir() -> Check:
    try:
        from .output import load_config
        bak = load_config().backup_dir
    except Exception as e:
        return Check("backup dir", False, f"config unreadable: {e}", "", required=False)
    if bak is None:
        return Check("backup dir", True, "not configured (optional)", required=False)
    ok, note = _writable_dir(bak)
    return Check("backup dir", ok, f"{bak} ({note if ok else 'not writable'})",
                 "" if ok else f'mkdir -p {shlex.quote(str(bak))} && chmod u+rwx {shlex.quote(str(bak))}  # or fix AINOTATE_BACKUP_DIR',
                 required=False)


_PW_PROBE = (
    "from playwright.sync_api import sync_playwright\n"
    "with sync_playwright() as p:\n"
    "    b = p.chromium.launch(headless=True)\n"
    "    v = b.version\n"
    "    b.close()\n"
    "print(v)\n")


def check_playwright() -> Check:
    if importlib.util.find_spec("playwright") is None:
        return Check("playwright", False, "package not installed (needed for `ainotate capture`)",
                     _pip("web") + " && " + f'"{sys.executable}" -m playwright install chromium',
                     required=False)
    install = f'"{sys.executable}" -m playwright install chromium'
    if _os() == "Linux":
        install = f'"{sys.executable}" -m playwright install --with-deps chromium'
    try:
        r = subprocess.run([sys.executable, "-c", _PW_PROBE], capture_output=True, text=True,
                           timeout=SLOW_TIMEOUT)
    except subprocess.TimeoutExpired:
        return Check("playwright", False, f"headless Chromium did not start within {SLOW_TIMEOUT:g}s",
                     install, required=False)
    if r.returncode != 0:
        lines = [l.strip() for l in r.stderr.splitlines() if l.strip() and not set(l.strip()) <= set("╔╗╚╝═║ ")]
        err = next((l for l in lines if "doesn't exist" in l or "Executable" in l),
                   lines[-1] if lines else "launch failed")[:160]
        if "doesn't exist" in err or "playwright install" in r.stderr:
            err = "Chromium is not downloaded"
        return Check("playwright", False, f"Chromium cannot launch: {err}", install, required=False)
    return Check("playwright", True, f"installed, headless Chromium {r.stdout.strip()} launches",
                 required=False)


def _cdp_target() -> tuple[str | None, str]:
    """(url, source): AINOTATE_CDP, else BrowserOS neo's CDP port from its config."""
    env = os.environ.get("AINOTATE_CDP", "").strip()
    if env:
        return (env if "://" in env else f"http://127.0.0.1:{env}" if env.isdigit() else f"http://{env}",
                "AINOTATE_CDP")
    cfg = Path(NEO_CONFIG).expanduser()
    if cfg.is_file():
        try:
            port = json.loads(cfg.read_text())["ports"]["cdp"]
            return f"http://127.0.0.1:{int(port)}", "BrowserOS neo config"
        except (OSError, ValueError, KeyError, TypeError):
            return None, ""
    return None, ""


def check_cdp() -> Check:
    url, source = _cdp_target()
    if not url:
        return Check("cdp", True, "not configured (optional: set AINOTATE_CDP or run BrowserOS neo)",
                     required=False)
    explicit = source == "AINOTATE_CDP"
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/json/version", timeout=1.5) as r:
            ver = json.loads(r.read().decode("utf-8", "replace")).get("Browser", "?")
        return Check("cdp", True, f"{url} reachable ({ver}) via {source}", required=False)
    except Exception as e:
        if not explicit:   # neo simply is not running right now
            return Check("cdp", True, f"{url} not reachable ({source}); neo not running, ignore "
                                      "unless you use `--cdp`", required=False)
        return Check("cdp", False, f"{url} not reachable: {e}",
                     "start the browser with --remote-debugging-port=<port> (or unset AINOTATE_CDP)",
                     required=False)


def _comm(pid: int) -> str:
    r = subprocess.run(["ps", "-o", "comm=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
    return r.stdout.strip()


def responsible_app() -> str | None:
    """The app macOS checks privacy permissions against when it runs ainotate (its "responsible
    process"): Terminal, Claude, Cursor... as the outermost .app bundle name, or the bare binary
    (python3.13, when started outside any app, e.g. under a detached tmux)."""
    if _os() != "Darwin":
        return None
    try:
        import ctypes
        f = ctypes.CDLL(None).responsibility_get_pid_responsible_for_pid
        f.argtypes, f.restype = [ctypes.c_int], ctypes.c_int
        comm = _comm(f(os.getpid()))
    except (AttributeError, OSError, subprocess.SubprocessError):
        return None
    if ".app/" in comm:
        return os.path.basename(comm.split(".app/")[0]) or None
    return os.path.basename(comm) or None


def check_screen_capture() -> Check:
    if _os() != "Darwin":
        return Check("screen capture", True, f"skipped (macOS permission check; {_os()} needs none)",
                     required=False)
    fd, name = tempfile.mkstemp(prefix="ainotate-doctor-", suffix=".png")
    os.close(fd)
    app = responsible_app()
    fix = ("System Settings > Privacy & Security > Screen & System Audio Recording: enable "
           + (f"{app} (macOS asks the app that started ainotate)" if app else "the app running this command")
           + ", then restart it  "
           "(open \"x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture\")")
    try:
        r = subprocess.run(["screencapture", "-x", "-R0,0,32,32", name], capture_output=True,
                           text=True, timeout=SLOW_TIMEOUT)
        if r.returncode != 0 or not os.path.getsize(name):
            return Check("screen capture", False,
                         "screencapture failed: " + (r.stderr.strip() or f"exit {r.returncode}"),
                         fix, required=False)
        from PIL import Image
        with Image.open(name) as im:
            extrema = im.convert("RGB").getextrema()
        if all(lo == hi == 0 for lo, hi in extrema):
            return Check("screen capture", False, "capture is blank: permission missing", fix,
                         required=False)
        return Check("screen capture", True, "screencapture returns real pixels"
                     + (f" (macOS permissions belong to {app})" if app else ""), required=False)
    except FileNotFoundError:
        return Check("screen capture", False, "`screencapture` not found", "", required=False)
    except subprocess.TimeoutExpired:
        return Check("screen capture", False, "screencapture timed out (permission dialog pending?)",
                     fix, required=False)
    except Exception as e:
        return Check("screen capture", False, f"cannot verify: {e}", fix, required=False)
    finally:
        Path(name).unlink(missing_ok=True)


def check_window_listing() -> Check:
    s = _os()
    if s == "Darwin":
        ok = importlib.util.find_spec("Quartz") is not None
        return Check("window listing", ok, "pyobjc Quartz " + ("available" if ok else "missing"),
                     "" if ok else f'"{sys.executable}" -m pip install pyobjc-framework-Quartz',
                     required=False)
    if s == "Windows":
        return Check("window listing", True, "ctypes user32 (built in)", required=False)
    ok = shutil.which("wmctrl") is not None
    return Check("window listing", ok, "wmctrl " + ("found" if ok else "missing (X11 only)"),
                 "" if ok else "sudo apt install wmctrl  # or: dnf/pacman -S wmctrl", required=False)


def check_ocr() -> Check:
    """Asks ainotate.ocr which engines really work here (Vision / Windows OCR / Tesseract)."""
    try:
        from . import ocr
        backends = ocr.available_backends()
    except Exception as e:
        return Check("ocr", False, f"ainotate.ocr failed to load: {e}", _ocr_fix(), required=False)
    if not backends:
        hint = getattr(ocr, "install_hint", None)
        return Check("ocr", False, "no OCR engine available (`locate` and text targets need one)",
                     hint() if callable(hint) else _ocr_fix(), required=False)
    return Check("ocr", True, "engines: " + ", ".join(backends) + f" (auto uses {backends[0]})",
                 required=False)


def _ocr_fix() -> str:
    return {"Darwin": f'"{sys.executable}" -m pip install pyobjc-framework-Vision  # or: brew install tesseract',
            "Windows": "winget install UB-Mannheim.TesseractOCR",
            }.get(_os(), "sudo apt install tesseract-ocr")


def check_clipboard() -> Check:
    s = _os()
    if s in ("Darwin", "Windows"):
        return Check("clipboard", True, "Pillow ImageGrab.grabclipboard (built in)", required=False)
    found = [t for t in ("wl-paste", "xclip") if shutil.which(t)]
    if found:
        return Check("clipboard", True, "found: " + ", ".join(found), required=False)
    return Check("clipboard", False, "neither wl-paste nor xclip found",
                 "sudo apt install wl-clipboard xclip  # Wayland: wl-clipboard, X11: xclip",
                 required=False)


def check_mcp() -> Check:
    if importlib.util.find_spec("mcp") is not None:
        try:
            from importlib.metadata import version
            return Check("mcp", True, f"mcp {version('mcp')}", required=False)
        except Exception:
            return Check("mcp", True, "mcp installed", required=False)
    return Check("mcp", False, "mcp package not installed (needed for the MCP server)",
                 _pip("mcp"), required=False)


CHECKS: list[Callable[[], Check]] = [
    check_python, check_pillow, check_font, check_output_dir, check_backup_dir,
    check_playwright, check_cdp, check_screen_capture, check_window_listing,
    check_ocr, check_clipboard, check_mcp,
]


def run_checks() -> list[Check]:
    """Run every check (in parallel threads); a crashing check becomes a failed Check."""
    def safe(fn):
        try:
            return fn()
        except Exception as e:
            return Check(fn.__name__.removeprefix("check_").replace("_", " "), False,
                         f"check crashed: {e}", "", required=False)
    with ThreadPoolExecutor(max_workers=len(CHECKS)) as ex:
        return list(ex.map(safe, CHECKS))


# --------------------------------------------------------------------------- CLI

def cli_doctor(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="ainotate doctor", description="Check this machine for AInotate.")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args(argv)
    checks = run_checks()
    failed_required = [c for c in checks if not c.ok and c.required]
    if a.json:
        print(json.dumps({"ok": not failed_required, "os": _os(),
                          "checks": [asdict(c) for c in checks]}, indent=2, ensure_ascii=False))
    else:
        w = max(len(c.name) for c in checks)
        for c in checks:
            mark = "ok  " if c.ok else ("FAIL" if c.required else "warn")
            print(f"[{mark}] {c.name.ljust(w)}  {c.detail}")
            if not c.ok and c.fix:
                print(f"       fix: {c.fix}")
        warns = sum(1 for c in checks if not c.ok and not c.required)
        print(f"\n{len(failed_required)} error(s), {warns} warning(s)")
    return 1 if failed_required else 0


if __name__ == "__main__":
    sys.exit(cli_doctor(sys.argv[1:]))
