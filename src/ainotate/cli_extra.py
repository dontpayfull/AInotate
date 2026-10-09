"""CLI commands registered outside cli.py (which stays under the size limit): desktop window
capture with UI element targets, element listing, agent skill install, doctor and the MCP server."""
from __future__ import annotations

import json

from .cli import EXIT_CAPTURE, A, CliExit, _json, _mod, _print_image, command


@command("window", "capture one window by id (from `windows`); --target measures UI elements [desktop]",
         "ainotate window 4711 --target allow='{\"element\": \"Allow\", \"role\": \"button\"}'",
         [A("id"), A("--out", metavar="PATH"),
          A("--target", action="append", default=[], metavar="NAME=JSON",
            help='macOS: a UI element to measure, {"element": label, "role"?, "nth"?, "exact"?}; '
                 "prints JSON with the rects and the spec scale (repeatable)")])
def run_window(a):
    desk = _mod("window")
    path = desk.window(a.id, a.out)
    if not a.target:
        _print_image(path)
        return
    from PIL import Image
    from .capture import ax
    targets = {}
    for t in a.target:
        name, sep, raw = t.partition("=")
        if not sep or not name:
            raise CliExit(2, f"--target {t!r}: use NAME=JSON")
        targets[name] = _json(raw, f"--target {name}")
    w = desk._find(a.id)
    if not w:
        raise CliExit(EXIT_CAPTURE, f"window {a.id} is gone; run `ainotate windows`")
    with Image.open(path) as im:
        size = im.size
    print(json.dumps({"image_path": str(path), **ax.window_targets(w, size, targets)}))


@command("elements", "list the UI elements of a running app (macOS Accessibility) [desktop]",
         'ainotate elements --app "System Settings" --role button',
         [A("--app", required=True, help="app name as in the Dock"), A("--role", help="e.g. button, checkbox"),
          A("--filter", help="substring of the label"), A("--json", action="store_true")])
def run_elements(a):
    ax = _mod("elements")
    role = ax._role(a.role or "")
    els = [e for e in ax.elements(a.app) if (not role or e["role"] == role)
           and (not a.filter or a.filter.lower() in e["label"].lower())]
    if a.json:
        print(json.dumps(els))
        return
    for e in els:
        r = e["rect"]
        print(f"{e['role']}\t{e['label']}\t{r['x']:.0f},{r['y']:.0f},{r['w']:.0f},{r['h']:.0f}")


@command("install-skill", "install the agent skill for Claude Code and Codex, or zip it for an upload",
         "ainotate install-skill            # or: ainotate install-skill --zip ~/Desktop",
         [A("--target", choices=["claude", "agents", "all"], default="all",
            help="claude = ~/.claude/skills, agents = ~/.agents/skills (Codex and others)"),
          A("--zip", metavar="PATH", help="write a ZIP for Claude Desktop, Cowork or claude.ai "
                                          "(Customize > Skills > Upload) instead"),
          A("--force", action="store_true", help="replace whatever is in the way")])
def run_install_skill(a):
    from . import skill_install as si
    if a.zip:
        print(si.make_zip(a.zip))
        print("Upload it in Claude: Customize > Skills > + > Upload a skill")
        return
    for dest, what in si.install(["claude", "agents"] if a.target == "all" else [a.target], a.force):
        print(f"{what}: {dest}")


@command("doctor", "check this machine: capture, OCR, browser, output folder", "ainotate doctor --json",
         [A("--json", action="store_true", help="machine-readable output")])
def run_doctor(a):
    from .doctor import cli_doctor
    if cli_doctor(["--json"] if a.json else []):
        raise CliExit(EXIT_CAPTURE)   # a required check failed


@command("mcp", "run the MCP stdio server (Claude Desktop, any MCP client) [mcp]", "ainotate mcp")
def run_mcp(a):
    _mod("mcp").main()
