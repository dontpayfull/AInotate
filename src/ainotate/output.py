"""Where annotated images go: config + save(image, name, draft) -> list[Path]. Never overwrites."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

try:
    import tomllib
except ModuleNotFoundError:   # Python 3.10
    import tomli as tomllib

DEFAULT_OUTPUT_DIR = "~/Pictures/AInotate"
DEFAULT_PREFIX = "AInotate"
CONFIG_PATH = Path("~/.config/ainotate/config.toml")


class OutputError(Exception):
    """The image could not be saved (no free file name, unreadable config)."""


@dataclass
class Config:
    output_dir: Path
    backup_dir: Optional[Path]
    prefix: str


def _read_config_file(path: Path) -> dict:
    path = path.expanduser()
    if not path.is_file():
        return {}
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise OutputError(f"cannot read {path}: {e}")
    return data


def load_config() -> Config:
    """env (AINOTATE_OUTPUT_DIR, AINOTATE_BACKUP_DIR, AINOTATE_PREFIX) > config.toml > defaults."""
    file = _read_config_file(CONFIG_PATH)

    def pick(env, key, default):
        v = os.environ.get(env)
        if v is not None and v != "":
            return v
        v = file.get(key)
        return v if isinstance(v, str) and v != "" else default

    out = pick("AINOTATE_OUTPUT_DIR", "output_dir", DEFAULT_OUTPUT_DIR)
    bak = pick("AINOTATE_BACKUP_DIR", "backup_dir", None)
    prefix = safe_component(pick("AINOTATE_PREFIX", "prefix", DEFAULT_PREFIX), "prefix (AINOTATE_PREFIX / config.toml)")
    return Config(Path(out).expanduser(), Path(bak).expanduser() if bak else None, prefix)


def default_look() -> str:
    """The look for a spec without "look": env AINOTATE_LOOK > config.toml `look` > "default"."""
    from .spec import SpecError, check_look
    look = os.environ.get("AINOTATE_LOOK") or None
    where = "AINOTATE_LOOK"
    if look is None:
        try:
            look = _read_config_file(CONFIG_PATH).get("look") or "default"
        except OutputError as e:
            raise SpecError(str(e))
        where = f"`look` in {CONFIG_PATH}"
    check_look(look, where)
    return look


def safe_component(value: str, what: str = "name") -> str:
    """`value` if it is one plain file-name component: no path separators, no '..', no control
    characters, not '.'; else OutputError (a bad prefix must not write outside the folder)."""
    v = str(value)
    bad = []
    if any(sep in v for sep in ("/", "\\", os.sep, os.altsep or "/")):
        bad.append("a path separator")
    if ".." in v or v.strip() in (".", ""):
        bad.append("'..' or an empty/dot name")
    if any(ord(c) < 32 or ord(c) == 127 for c in v):
        bad.append("a control character")
    if bad:
        raise OutputError(f"{what} {v!r} must be a single file-name component: it contains {', '.join(bad)}")
    return v


def slug(name) -> str:
    """File-name-safe version of a spec name (no path separators, max 60 chars)."""
    return "".join(ch if ch.isalnum() or ch in "-_ " else "-" for ch in str(name or ""))[:60].strip()


def _warn_stderr(msg: str) -> None:
    print(f"WARN: {msg}", file=sys.stderr)


def save(image, name: str = "", draft: bool = False,
         on_warning: Optional[Callable[[str], None]] = None) -> list[Path]:
    """Save a PNG. draft: a temp dir, one path. Otherwise
    `<output_dir>/<prefix> <YYYY-MM-DD at HH.MM.SS> <name>.png` plus an identical copy in
    `<backup_dir>` when configured. Same name in the same second gets " (2)", shared with the
    backup; `on_warning` (default: stderr) hears about it."""
    warn = on_warning or _warn_stderr
    cfg = load_config()
    stamp = datetime.now().strftime("%Y-%m-%d at %H.%M.%S")
    name = slug(name)
    base = f"{cfg.prefix} {stamp} {name}" if name else f"{cfg.prefix} {stamp}"
    if draft:
        out = Path(tempfile.mkdtemp(prefix="ainotate-")) / (base + ".png")
        image.save(out, optimize=True)
        return [out]
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    if cfg.backup_dir:
        cfg.backup_dir.mkdir(parents=True, exist_ok=True)
    for n in range(1, 1000):            # never overwrite: same name in the same second gets " (2)"
        fname = safe_component(base + (f" ({n})" if n > 1 else "") + ".png", "file name")
        bak_ok = cfg.backup_dir and cfg.backup_dir.resolve() != cfg.output_dir.resolve()
        dests = [cfg.output_dir / fname] + ([cfg.backup_dir / fname] if bak_ok else [])
        files = _reserve(dests)
        if files is None:               # one of the names is taken: try the next suffix
            continue
        try:
            image.save(files[0], format="PNG", optimize=True)
            files[0].flush()
            files[0].seek(0)
            for f in files[1:]:
                shutil.copyfileobj(files[0], f)
            for f in files:
                f.close()
        except BaseException:
            _release(files, dests)
            raise
        if n > 1:
            warn(f"{base}.png already existed; saved as ({n}). Delete the older one if it was a draft.")
        return dests
    raise OutputError(f"could not find a free file name for {base!r}")


def _reserve(paths: list[Path]):
    """Open every path with exclusive create (all or none); None if any already exists."""
    files = []
    for p in paths:
        try:
            files.append(open(p, "x+b"))
        except FileExistsError:
            _release(files, paths)
            return None
        except BaseException:
            _release(files, paths)
            raise
    return files


def _release(files, paths) -> None:
    """Close and delete the files this call created (never someone else's)."""
    for f in files:
        try:
            f.close()
        except BaseException:
            pass
    for p in paths[:len(files)]:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
