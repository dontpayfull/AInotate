"""`ainotate install-skill`: copy the bundled agent skill where agents look for skills, or zip it
for apps that take an upload (Claude Desktop, Cowork, claude.ai: Customize > Skills).

The wheel carries the skill as `ainotate/skill` (pyproject force-include); a source checkout
uses the repository's `skill/` folder.
"""
from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path

NAME = "ainotate"
TARGETS = {   # where each kind of agent reads personal skills
    "claude": Path("~/.claude/skills"),    # Claude Code
    "agents": Path("~/.agents/skills"),    # Codex and other agents that read SKILL.md
}


class SkillError(OSError):
    """The skill cannot be found or installed (exit 1)."""


def source() -> Path:
    here = Path(__file__).resolve().parent
    for p in (here / "skill", here.parents[1] / "skill"):
        if (p / "SKILL.md").is_file():
            return p
    raise SkillError("the agent skill is not bundled in this install; reinstall: pipx install --force 'ainotate[all]'")


def _ours(path: Path) -> bool:
    try:
        head = (path / "SKILL.md").read_text(encoding="utf-8")[:400]
    except OSError:
        return False
    return f"name: {NAME}" in head


def install(targets=("claude", "agents"), force=False) -> list[tuple[Path, str]]:
    """Copy the skill into each target's skills folder as `ainotate`. Returns [(path, what happened)].
    An existing `ainotate` skill is updated; a link to this same source is left alone; anything
    else in the way needs force."""
    src = source()
    done = []
    for t in targets:
        if t not in TARGETS:
            raise SkillError(f"unknown target {t!r}; use {', '.join(TARGETS)}")
        dest = TARGETS[t].expanduser() / NAME
        if dest.is_symlink() and dest.resolve() == src.resolve():
            done.append((dest, "already linked to this install"))
            continue
        if dest.exists() or dest.is_symlink():
            if not (force or _ours(dest)):
                raise SkillError(f"{dest} exists and is not the AInotate skill; move it or pass --force")
            if dest.is_symlink() or dest.is_file():
                dest.unlink()
            else:
                shutil.rmtree(dest)
            what = "updated"
        else:
            what = "installed"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
        done.append((dest, what))
    return done


def make_zip(out: str | os.PathLike) -> Path:
    """The skill as a ZIP with one top-level `ainotate/` folder, ready for an upload."""
    src, out = source(), Path(out).expanduser()
    if out.is_dir():
        out = out / f"{NAME}-skill.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(src.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts and f.name != ".DS_Store":
                z.write(f, Path(NAME) / f.relative_to(src))
    return out
