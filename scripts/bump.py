"""Set the release version everywhere it is written down: `python scripts/bump.py 0.1.6`.

The package, the Claude/Codex plugin, the Gemini CLI extension, the MCP Registry entry and the
Desktop extension each carry the version, and the launchers pin `ainotate[all]==<version>`
(directories reject an unpinned uvx). tests/test_extra.py fails when they disagree.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JSON_FILES = [".claude-plugin/plugin.json", "gemini-extension.json", "server.json", "packaging/mcpb/manifest.json"]
PIN = re.compile(r"ainotate\[all\](==[0-9A-Za-z.+-]+)?")


def bump(v: str) -> list[str]:
    if not re.fullmatch(r"\d+\.\d+\.\d+([a-z0-9.+-]*)", v):
        raise SystemExit(f"not a version: {v!r}")
    edits = {
        "pyproject.toml": lambda s: re.sub(r'^version = "[^"]*"', f'version = "{v}"', s, count=1, flags=re.M),
        "src/ainotate/__init__.py": lambda s: re.sub(r'__version__ = "[^"]*"', f'__version__ = "{v}"', s),
        "packaging/mcpb/pyproject.toml": lambda s: PIN.sub(f"ainotate[all]=={v}",
                                                           re.sub(r'^version = "[^"]*"', f'version = "{v}"', s, flags=re.M)),
    }
    for f in JSON_FILES:
        edits[f] = lambda s: PIN.sub(f"ainotate[all]=={v}", re.sub(r'"version"(\s*):(\s*)"[^"]*"', rf'"version"\1:\2"{v}"', s))
    changed = []
    for name, edit in edits.items():
        p = ROOT / name
        old = p.read_text(encoding="utf-8")
        new = edit(old)
        if new != old:
            p.write_text(new, encoding="utf-8")
            changed.append(name)
    return changed


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    for name in bump(sys.argv[1]):
        print("updated", name)
