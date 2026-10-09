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
    """True only for a folder whose SKILL.md frontmatter says exactly `name: ainotate`."""
    try:
        lines = (path / "SKILL.md").read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    if not lines or lines[0].strip() != "---":
        return False
    for line in lines[1:40]:
        if line.strip() == "---":
            return False
        if line.strip() == f"name: {NAME}":
            return True
    return False


def _related(a: Path, b: Path) -> bool:
    """The same folder, or one inside the other (after resolving links)."""
    a, b = a.resolve(), b.resolve()
    return a == b or a in b.parents or b in a.parents


def _replace(src: Path, dest: Path) -> None:
    """Copy src to dest without ever leaving dest missing: copy beside it first, then swap."""
    tag = f".{NAME}.{os.getpid()}"
    new, old = dest.with_name(dest.name + tag + ".new"), dest.with_name(dest.name + tag + ".old")
    junk = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", "*.zip")

    def ignore(d, names):   # never follow a link out of the skill folder
        return set(junk(d, names)) | {n for n in names if (Path(d) / n).is_symlink()}
    try:
        shutil.copytree(src, new, ignore=ignore)
        if not (new / "SKILL.md").is_file():
            raise SkillError(f"copy of the skill to {new} is incomplete")
        had = dest.exists() or dest.is_symlink()
        if had:
            dest.rename(old)
        try:
            new.rename(dest)
        except OSError:
            if had:
                old.rename(dest)        # put the previous skill back
            raise
        if had:
            if old.is_symlink() or old.is_file():
                old.unlink()
            else:
                shutil.rmtree(old, ignore_errors=True)
    finally:
        if new.exists():
            shutil.rmtree(new, ignore_errors=True)


def install(targets=("claude", "agents"), force=False) -> list[tuple[Path, str]]:
    """Copy the skill into each target's skills folder as `ainotate`. Returns [(path, what happened)].
    An existing copy of this skill is updated; a link is left alone (it is someone's checkout)
    unless force; anything else in the way needs force. Never touches the source itself."""
    src = source()
    done = []
    for t in targets:
        if t not in TARGETS:
            raise SkillError(f"unknown target {t!r}; use {', '.join(TARGETS)}")
        dest = TARGETS[t].expanduser() / NAME
        if (dest.exists() or dest.is_symlink()) and _related(src, dest):
            done.append((dest, "already this install"))
            continue
        if dest.is_symlink() and not force:
            done.append((dest, f"left alone: a link to {os.readlink(dest)} (--force replaces it)"))
            continue
        if dest.exists() and not (force or _ours(dest)):
            raise SkillError(f"{dest} exists and is not the AInotate skill; move it or pass --force")
        what = "updated" if dest.exists() or dest.is_symlink() else "installed"
        dest.parent.mkdir(parents=True, exist_ok=True)
        _replace(src, dest)
        done.append((dest, what))
    return done


def make_zip(out: str | os.PathLike) -> Path:
    """The skill as a ZIP with one top-level `ainotate/` folder, ready for an upload. `out` is a
    folder (created if needed) or a path ending in .zip; never inside the skill itself."""
    src, out = source(), Path(out).expanduser()
    if out.suffix.lower() != ".zip":
        out = out / f"{NAME}-skill.zip"
    where = out.parent.resolve()
    if src.resolve() == where or src.resolve() in where.parents:
        raise SkillError(f"{out} is inside the skill folder {src}; write the ZIP somewhere else")
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(src.rglob("*")):
            if f.is_file() and not f.is_symlink() and "__pycache__" not in f.parts and f.name != ".DS_Store" \
                    and f.suffix != ".zip":
                z.write(f, Path(NAME) / f.relative_to(src))
    return out
