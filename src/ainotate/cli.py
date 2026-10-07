"""ainotate command line. Output paths go to stdout (one per line), WARN/REDACTED/ERROR lines to
stderr; `--json` puts one JSON object on stdout instead.

Exit codes (the only list; `ainotate --help` prints it from EXIT_CODES):
  0 ok, 1 cannot save/export, 2 invalid spec or usage, 3 render error, 4 capture failed or a
  backend is missing, 5 a text target is ambiguous, 6 a target was not found.
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import json
import sys

from . import __version__

EXIT_CODES = [
    (0, "ok"),
    (1, "cannot save or export the result (output folder, clipboard, guide)"),
    (2, "invalid spec or usage (the message lists every problem)"),
    (3, "valid spec that cannot be drawn (no room for a label, rect outside the image)"),
    (4, "capture failed, or a backend is missing (browser, OCR engine, optional extra)"),
    (5, "a text target matches several places equally well: ask, or add nth / within"),
    (6, "a target was not found (the message gives the reason or the closest text)"),
]
EXIT_OK, EXIT_OUTPUT, EXIT_SPEC, EXIT_RENDER, EXIT_CAPTURE, EXIT_AMBIGUOUS, EXIT_NOT_FOUND = range(7)

# commands whose module needs an optional extra: imported only when the command runs
LAZY = {
    "capture": ("ainotate.capture.web", "web"), "shoot": ("ainotate.shoot", "web"),
    "screen": ("ainotate.capture.desktop", "desktop"), "windows": ("ainotate.capture.desktop", "desktop"),
    "window": ("ainotate.capture.desktop", "desktop"), "clipboard": ("ainotate.capture.clipboard", "desktop"),
    "locate": ("ainotate.ocr", "ocr"), "mcp": ("ainotate.mcp_server", "mcp"),
}
# pip distribution behind a missing top-level module
_PROVIDES = {"playwright": "web", "mcp": "mcp", "anyio": "mcp", "mss": "desktop", "Quartz": "desktop",
             "Vision": "ocr", "winrt": "ocr"}

GROUPS = [
    ("Annotate", ["annotate", "shoot"]),
    ("Capture", ["capture", "screen", "windows", "window", "clipboard", "neo-latest"]),
    ("Find things on an image", ["locate", "grid", "zoom", "info"]),
    ("Share", ["guide", "animate", "compare", "copy"]),
    ("Setup", ["doctor", "mcp"]),
]
COMMANDS: dict = {}   # name -> (summary, example, [(flags, kwargs)], run)


class CliExit(Exception):
    def __init__(self, code, msg=""):
        super().__init__(msg)
        self.code, self.msg = code, msg


def A(*flags, **kw):
    """One argparse argument, declared next to its command."""
    return flags, kw


def command(name, summary, example, args=()):
    """Register `run(args)` as `ainotate NAME`; `example` goes at the end of its --help."""
    def register(run):
        COMMANDS[name] = (summary, example, list(args), run)
        return run
    return register


def _mask(text) -> str:
    """privacy.mask_text: an error or warning can quote OCR/page text holding a secret."""
    from .privacy import mask_text
    return mask_text(str(text))


def _err(msg):
    print(f"ERROR: {_mask(msg)}", file=sys.stderr)


def _warn(msg):
    print(f"WARN: {_mask(msg)}", file=sys.stderr)


def _mod(cmd):
    modname, extra = LAZY[cmd]
    try:
        return importlib.import_module(modname)
    except ImportError as e:
        raise CliExit(EXIT_CAPTURE, _missing_dep(cmd, extra, e))


def _missing_dep(cmd, extra, e):
    name = getattr(e, "name", None) or ""
    if name.startswith("ainotate"):
        return f"`ainotate {cmd}` is not available in this install (module {name} is missing)"
    extra = _PROVIDES.get(name.split(".")[0], extra)
    return f"this command needs: pip install 'ainotate[{extra}]' (missing module: {name or e})"


def _load_spec(path):
    s = path.strip() if isinstance(path, str) else ""
    if s.startswith(("{", "[")):
        try:
            return json.loads(path)
        except json.JSONDecodeError as e:
            raise CliExit(EXIT_SPEC, f"spec is not valid JSON: {e}")
    try:
        f = sys.stdin if path == "-" else open(path, encoding="utf-8")
        with f:
            return json.load(f)
    except FileNotFoundError:
        raise CliExit(EXIT_SPEC, f"spec file not found: {path}")
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise CliExit(EXIT_SPEC, f"spec is not valid JSON: {e}")
    except OSError as e:
        raise CliExit(EXIT_SPEC, f"cannot read spec {path}: {e}")


def _json(raw, what):
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise CliExit(EXIT_SPEC, f"{what} is not valid JSON ({e})")


def _print_image(path):
    try:
        from PIL import Image
        with Image.open(path) as im:
            print(f"{path}\t{im.width}x{im.height}")
    except OSError:
        print(path)


# ---------------------------------------------------------------- annotate / shoot

def _result_args(privacy_default):
    return [A("spec", help="spec.json, inline JSON, or - for stdin"),
            A("--draft", "--no-save", dest="draft", action="store_true", help="write to a temp dir"),
            A("--privacy", choices=("auto", "off"), help=f"solid redaction (default: {privacy_default})"),
            A("--copy", action="store_true", help="also put the image on the clipboard"),
            A("--json", action="store_true", help="print JSON status on stdout"),
            A("--debug", action="store_true", help="draw target/label boxes; implies --draft")]


def _spec_with_flags(a):
    spec = _load_spec(a.spec)
    if a.privacy and isinstance(spec, dict):
        spec["privacy"] = a.privacy
    return spec


def _emit(a, paths, warnings, legend, redactions, size):
    """One result format for annotate and shoot. `copied` is true only if the copy worked."""
    copied = False
    if a.copy:
        try:
            importlib.import_module("ainotate.export").copy_to_clipboard(paths[0])
            copied = True
        except Exception as e:  # noqa: BLE001 - the image is saved; a failed copy is only a warning
            warnings.append(f"--copy failed: {e}")
    if a.json:
        print(json.dumps({"paths": [str(p) for p in paths], "warnings": [_mask(w) for w in warnings],
                          "legend": legend, "redactions": redactions, "size": list(size), "copied": copied},
                         ensure_ascii=False))
        return
    for w in warnings: _warn(w)
    for r in redactions: print(f"REDACTED: {r}", file=sys.stderr)
    for p in paths: print(p)


@command("annotate", "render a spec on an image and save it",
         "ainotate annotate spec.json --draft\n  echo '{\"input\": \"shot.png\", \"frame\": true, \"marks\": "
         "[{\"type\": \"step\", \"n\": 1,\n    \"target\": {\"text\": \"Save\"}, \"label\": \"Click\"}]}' "
         "| ainotate annotate - --json",
         _result_args("off"))
def run_annotate(a):
    from .output import save
    from .render import render
    from .shoot import legend
    spec = _spec_with_flags(a)
    res = render(spec, debug=a.debug)
    warnings = list(res.warnings)
    paths = save(res.image, spec.get("name", ""), draft=a.draft or a.debug, on_warning=warnings.append)
    _emit(a, paths, warnings, legend(spec.get("marks")), res.redactions, res.image.size)


@command("shoot", "web page + marks with targets -> capture, redact, annotate, save [web]",
         "ainotate shoot hn.json --draft\n  hn.json: {\"url\": \"https://news.ycombinator.com\", \"frame\": "
         "{\"chrome\": \"browser\"},\n    \"marks\": [{\"type\": \"step\", \"n\": 1, \"target\": {\"text\": "
         "\"login\"}, \"label\": \"Sign in\"}]}", _result_args("auto"))
def run_shoot(a):
    mod, spec = _mod("shoot"), _spec_with_flags(a)
    kw = {"draft": True, "debug": True} if a.debug else {"draft": a.draft}
    if not a.debug or "debug" in inspect.signature(mod.shoot).parameters:
        res = mod.shoot(spec, **kw)
    else:
        from . import render as r
        plain, r.render = r.render, lambda s, debug=False, _p=r.render: _p(s, debug=True)
        try: res = mod.shoot(spec, draft=True)
        finally: r.render = plain
    _emit(a, res.paths, list(res.warnings), getattr(res, "legend", []), getattr(res, "redactions", []),
          getattr(res, "size", (0, 0)))


# ---------------------------------------------------------------- capture

@command("capture", "screenshot a web page and measure targets (prints JSON) [web]",
         "ainotate capture https://news.ycombinator.com --target login='{\"text\": \"login\"}'\n"
         "  ainotate capture --cdp http://127.0.0.1:9222 --tab-contains github.com --preset wide",
         [A("url", nargs="?", help="page to open (optional with --cdp + --tab-contains)"),
          A("--preset", default="laptop", help="laptop 1280x800@2, wide 1600x1000@2, phone 390x844@3"),
          A("--target", action="append", metavar="NAME=JSON", help='repeatable: cart=\'{"text": "Cart"}\''),
          A("--action", action="append", metavar="JSON", help='repeatable: \'{"click": {"text": "Menu"}}\''),
          A("--cdp", metavar="URL", help="attach to a running Chromium (BrowserOS neo, Chrome)"),
          A("--tab-contains", "--page-url-contains", dest="tab_contains", metavar="STR"),
          A("--browser", choices=("chromium", "firefox", "webkit"), default="chromium",
            help="engine to launch (ignored with --cdp, which attaches to a running Chromium)"),
          A("--user-data-dir", metavar="DIR", help="persistent browser profile (stays logged in between runs)"),
          A("--full-page", action="store_true"), A("--no-hide-overlays", action="store_true"),
          A("--out", metavar="PATH"), A("--timeout-ms", type=int, default=30000)])
def run_capture(a):
    if not a.url and not (a.cdp and a.tab_contains):
        raise CliExit(EXIT_SPEC, "give a URL, or --cdp and --tab-contains to pick an open tab")
    targets = {}
    for item in a.target or []:
        name, sep, raw = item.partition("=")
        if not sep or not name:
            raise CliExit(EXIT_SPEC, f"--target must look like name=JSON, got {item!r}")
        targets[name] = _json(raw, f"--target {name}") if raw[:1] in "{[\"" else {"text": raw}
    actions = [_json(x, "--action") for x in a.action or []]
    c = _mod("capture").capture(a.url, cdp_url=a.cdp, page_url_contains=a.tab_contains, preset=a.preset,
                                targets=targets, actions=actions, hide_overlays=not a.no_hide_overlays,
                                full_page=a.full_page, out_path=a.out, timeout_ms=a.timeout_ms,
                                browser=a.browser, user_data_dir=a.user_data_dir)
    d = c.to_dict() if hasattr(c, "to_dict") else {
        "image_path": str(c.image_path), "scale": c.scale, "viewport": list(c.viewport),
        "rects": c.rects, "missing": c.missing, "url": getattr(c, "url", ""),
        "warnings": list(getattr(c, "warnings", []))}
    print(json.dumps(d, ensure_ascii=False))


@command("screen", "capture the screen or a region [desktop]", "ainotate screen --region 0,0,1440,900",
         [A("--region", help="x,y,w,h in screen points"), A("--display", type=int), A("--out", metavar="PATH")])
def run_screen(a):
    _print_image(_mod("screen").screen(a.out, region=a.region, display=a.display))


@command("windows", "list windows: id, app, title, bounds [desktop]", "ainotate windows chrome",
         [A("filter", nargs="?", help="substring of the app or title")])
def run_windows(a):
    for w in _mod("windows").list_windows(a.filter):
        b = w.get("bounds") or {}
        print("\t".join([str(w["id"]), w["app"], w["title"],
                         f"{b.get('x', 0)},{b.get('y', 0)},{b.get('w', 0)},{b.get('h', 0)}"]))


@command("window", "capture one window by id (from `windows`) [desktop]", "ainotate window 4711",
         [A("id"), A("--out", metavar="PATH")])
def run_window(a):
    _print_image(_mod("window").window(a.id, a.out))


@command("clipboard", "save the image on the clipboard", "ainotate clipboard --out ~/Desktop/pasted.png",
         [A("--out", metavar="PATH")])
def run_clipboard(a):
    _print_image(_mod("clipboard").grab(a.out))


@command("neo-latest", "newest BrowserOS neo auto-screenshot", "ainotate neo-latest --size 2560x1600",
         [A("--session"), A("--size", help="e.g. 2560x1600")])
def run_neo_latest(a):
    from .capture.neo import latest
    try:
        shot = latest(a.session, a.size)
    except ValueError as e:
        raise CliExit(EXIT_SPEC, str(e))
    print(shot.path, shot.size, f"{shot.age:.0f}s old")
    for w in shot.warnings:
        _warn(w)


# ---------------------------------------------------------------- find things on an image

@command("locate", "find text on an image by OCR; prints [x1,y1,x2,y2] in image px [ocr]",
         "ainotate locate shot.png \"Save changes\"\n  ainotate locate shot.png Save --nth 1 --json",
         [A("image"), A("text"), A("--all", action="store_true", help="also list weaker matches"),
          A("--json", action="store_true"),
          A("--nth", type=int, help="pick among equally good matches, 0-based reading order"),
          A("--within", help="x1,y1,x2,y2 image px"),
          A("--exact", action="store_true", help="whole words only, no fuzzy matching"),
          A("--lang", help="comma list, default ro-RO,en-US"),
          A("--backend", default="auto", choices=("auto", "vision", "winrt", "tesseract"))])
def run_locate(a):
    ocr = _mod("locate")
    items = ocr.read(a.image, a.lang.split(",") if a.lang else None, backend=a.backend)
    try:
        cands = ocr.locate(a.image, a.text, nth=a.nth, within=a.within, exact=a.exact, items=items)
    except ocr.OcrError as e:
        raise CliExit(EXIT_SPEC, str(e))
    top = [c for c in cands if c["top"]]
    show = cands if a.all else top
    if a.json:
        print(json.dumps([dict(c, text=_mask(c["text"])) for c in show], ensure_ascii=False))
    else:
        for c in show:
            print(f"[{c['x']},{c['y']},{c['x'] + c['w']},{c['y'] + c['h']}]  score={c['score']:.2f}  "
                  f"{json.dumps(_mask(c['text']), ensure_ascii=False)}" + ("" if c["top"] else "  (weaker)")
                  + ("  (box estimated from characters)" if c["estimated"] else ""))
    if not top:
        raise ocr.not_found(a.text, items, within=a.within, exact=a.exact)
    if len(top) > 1:   # equally good matches stay ambiguous with --all too; --nth picks one
        raise CliExit(EXIT_AMBIGUOUS, f"{len(top)} equally good matches for {a.text!r}: ask the user which "
                      "one, or pass --nth N (0-based, reading order) or --within x1,y1,x2,y2")
    weaker = len(cands) - len(top)
    if weaker and not a.all:
        _warn(f"{weaker} weaker match(es) not shown; --all lists them")


@command("grid", "the image with a coordinate grid", "ainotate grid shot.png --step 100",
         [A("image"), A("--step", type=int, default=100)])
def run_grid(a):
    from .tools import grid
    r = grid(a.image, a.step)
    print(r.path, r.note)


@command("zoom", "a region enlarged with a fine grid (labels in source px)",
         "ainotate zoom shot.png 1200 80 1600 260",
         [A("image")] + [A(c, type=float) for c in ("x1", "y1", "x2", "y2")] + [A("--step", type=int, default=20)])
def run_zoom(a):
    from .tools import zoom
    r = zoom(a.image, a.x1, a.y1, a.x2, a.y2, a.step)
    print(r.path, r.note)
    for w in r.warnings:
        _warn(w)


@command("info", "real pixel size of an image", "ainotate info shot.png", [A("image")])
def run_info(a):
    from .tools import info
    print(info(a.image))


# ---------------------------------------------------------------- share

@command("guide", "step-by-step guide (md, html, pdf) from annotated images",
         "ainotate guide --title \"Log in\" --step a.png \"Open the page\" --step b.png \"Sign in\"\n"
         "  ainotate guide steps.json --formats md,html,pdf",
         [A("steps_json", nargs="?", help="[{image, text}] or {title, intro, steps}"),
          A("--step", nargs=2, action="append", metavar=("IMAGE", "TEXT"), help="repeatable, in order"),
          A("--title"), A("--intro"), A("--formats", default="md,html", help="comma list of md,html,pdf"),
          A("--width", type=int, help="canvas width for every step image (default: the widest; 0 = as is)"),
          A("--name", help="folder name in the output folder (default: guide)"),
          A("--out-dir", metavar="DIR")])
def run_guide(a):
    from pathlib import Path

    from .export import guide
    meta, steps = {}, [{"image": img, "text": text} for img, text in a.step or []]
    if a.steps_json:
        data, base = _load_spec(a.steps_json), Path(a.steps_json).expanduser().parent
        meta = data if isinstance(data, dict) else {}
        listed = data.get("steps") if isinstance(data, dict) else data
        if not isinstance(listed, list):
            raise CliExit(EXIT_SPEC, "steps JSON must be a list or an object with a 'steps' list")
        steps = [dict(x, image=str(base / Path(str(x["image"])).expanduser()))
                 if isinstance(x, dict) and "image" in x else x for x in listed] + steps
    if not steps:
        raise CliExit(EXIT_SPEC, "give a steps JSON file or at least one --step IMAGE TEXT")
    res = guide(steps, a.title if a.title is not None else meta.get("title", ""), a.out_dir,
                tuple(f for f in a.formats.split(",") if f), a.intro if a.intro is not None else meta.get("intro", ""),
                name=a.name or meta.get("name", ""), width=a.width)
    for k, v in res.items():
        print(f"{k}: {v}")
    for w in getattr(res, "warnings", []):
        _warn(w)


@command("animate", "animated APNG (default) or GIF from images", "ainotate animate 1.png 2.png 3.png --format gif",
         [A("frames", nargs="+"),
          A("--format", choices=("apng", "gif"), default="apng", help="apng: full color; gif: plays everywhere"),
          A("--duration", type=int, help="ms per frame (default 1500)"),
          A("--crossfade", type=int, default=0, help="ms of crossfade between frames"),
          A("--max-width", type=int, default=1200), A("--loop", type=int, default=0, help="0 = forever"),
          A("--name", help="file name in the output folder (default: animation)"),
          A("--out", metavar="PATH", help="explicit file path instead of the output folder")])
def run_animate(a):
    from .export import gif
    print(gif(a.frames, a.out, a.duration, a.loop, a.max_width, a.crossfade, a.format, name=a.name or ""))


@command("compare", "before/after plate", "ainotate compare before.png after.png --labels Old New --name \"coupon fix\"",
         [A("before"), A("after"),
          A("--labels", nargs="+", default=["Before", "After"], metavar="LABEL",
            help="two labels: --labels Old New, or --labels Old,New"),
          A("--layout", choices=("side", "stack", "auto"), default="auto"), A("--no-divider", action="store_true"),
          A("--name", help="file name in the output folder (default: compare)"),
          A("--out", metavar="PATH", help="explicit file path instead of the output folder")])
def run_compare(a):
    from .export import compare
    labels = a.labels if len(a.labels) != 1 else a.labels[0].split(",")
    labels = [x.strip() for x in labels]
    if len(labels) != 2 or not all(labels):
        raise CliExit(EXIT_SPEC, f"--labels takes two labels (Old New, or Old,New); got {a.labels!r}")
    print(compare(a.before, a.after, a.out, tuple(labels), a.layout, not a.no_divider, name=a.name or ""))


@command("copy", "put an image on the clipboard", "ainotate copy shot.png", [A("image")])
def run_copy(a):
    from .export import copy_to_clipboard
    copy_to_clipboard(a.image)
    print(f"copied: {a.image}", file=sys.stderr)


# ---------------------------------------------------------------- setup

@command("doctor", "check this machine: capture, OCR, browser, output folder", "ainotate doctor --json",
         [A("--json", action="store_true", help="machine-readable output")])
def run_doctor(a):
    from .doctor import cli_doctor
    if cli_doctor(["--json"] if a.json else []):
        raise CliExit(EXIT_CAPTURE)   # a required check failed


@command("mcp", "run the MCP stdio server (Claude Desktop, any MCP client) [mcp]", "ainotate mcp")
def run_mcp(a):
    _mod("mcp").main()


# ---------------------------------------------------------------- argparse

def _overview():
    lines = []
    for group, names in GROUPS:
        lines += [f"{group}:"] + [f"  {n:<11} {COMMANDS[n][0]}" for n in names] + [""]
    return "\n".join(lines + ["`ainotate COMMAND --help` shows its options and an example.",
                              "[web] [desktop] [ocr] [mcp] = needs that extra: pip install 'ainotate[all]'"])


def _exit_codes():
    return "exit codes:\n" + "\n".join(f"  {c}  {d}" for c, d in EXIT_CODES)


def build_parser():
    fmt = argparse.RawDescriptionHelpFormatter
    ap = argparse.ArgumentParser(
        prog="ainotate", formatter_class=fmt, usage="ainotate COMMAND [options]",
        description="Annotated screenshots for AI agents: numbered steps, Skitch arrows, boxes, labels, "
                    "solid redaction,\nframes. Show where things are instead of describing them.\n\n" + _overview(),
        epilog="Spec units: every coordinate (rect, crop, label_at, at) is in ONE unit; `scale` converts it to "
               "image px.\nDOM/CSS rects: scale = image width / innerWidth. Grid, `info` or `locate` pixels: "
               "scale 1.\n\n" + _exit_codes())
    ap.add_argument("--version", action="version", version=f"ainotate {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="COMMAND", help=argparse.SUPPRESS,
                             prog="ainotate")
    for name, (summary, example, args, run) in COMMANDS.items():
        s = sub.add_parser(name, formatter_class=fmt, description=summary[0].upper() + summary[1:] + ".",
                           epilog=f"example:\n  {example}\n\n{_exit_codes()}")
        for flags, kw in args:
            s.add_argument(*flags, **kw)
        s.set_defaults(fn=run)
    return ap


def exit_code(e: BaseException):
    """The documented exit code for an exception; None = a bug (let it raise)."""
    if isinstance(e, CliExit):
        return e.code
    names = {c.__name__ for c in type(e).__mro__}
    for code, kinds in ((EXIT_AMBIGUOUS, {"AmbiguousText"}), (EXIT_NOT_FOUND, {"TextNotFound", "TargetNotFound"}),
                        (EXIT_SPEC, {"SpecError", "ToolError", "TargetSpecError"}), (EXIT_RENDER, {"RenderError"}),
                        (EXIT_OUTPUT, {"OutputError", "ExportError"}),
                        (EXIT_CAPTURE, {"CaptureError", "NeoError", "OcrError", "ImportError"})):
        if names & kinds:
            return code
    return EXIT_OUTPUT if isinstance(e, OSError) else None


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    a = build_parser().parse_args(argv)   # usage errors: argparse prints them and exits 2
    try:
        a.fn(a)
        return EXIT_OK
    except Exception as e:  # noqa: BLE001 - every known failure maps to a documented exit code
        code = exit_code(e)
        if code is None:
            if a.cmd not in LAZY:
                raise           # a bug in AInotate itself: keep the traceback
            code = EXIT_CAPTURE  # third-party capture/OCR failure
        if isinstance(e, ImportError) and a.cmd in LAZY:
            _err(_missing_dep(a.cmd, LAZY[a.cmd][1], e))
        elif str(e):
            _err(e)
        return code


if __name__ == "__main__":
    sys.exit(main())
