"""neo-latest: the newest screenshot BrowserOS neo auto-captured (every neo call saves one)."""
from __future__ import annotations

import base64
import glob
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PIL import Image

NEO_DIR_ENV = "AINOTATE_NEO_DIR"
DEFAULT_NEO_DIR = "~/.browserclaw/screenshots"


class NeoError(Exception):
    """No matching neo screenshot (capture error, exit 4)."""


@dataclass
class NeoShot:
    path: Path
    size: tuple
    age: float                   # seconds since the file was written
    warnings: list[str] = field(default_factory=list)


def shots_dir() -> str:
    return os.path.expanduser(os.environ.get(NEO_DIR_ENV) or DEFAULT_NEO_DIR)


def parse_size(size):
    """'2560x1600' -> (2560, 1600); ValueError on anything else."""
    if not re.fullmatch(r"\d+x\d+", size.lower()):
        raise ValueError(f"--size must look like 2560x1600, got {size!r}")
    return tuple(int(v) for v in size.lower().split("x"))


def image_size(path, want=None):
    """Size of a fully decodable image; None for non-images or half-written captures."""
    try:
        with Image.open(path) as im:
            if want and im.size != want:
                return im.size          # header is enough to reject a size mismatch
            im.load()                   # truncated JPEGs pass the header but fail here
            return im.size
    except (OSError, Image.UnidentifiedImageError, SyntaxError):
        return None


def latest(session=None, size=None) -> NeoShot:
    """Newest neo screenshot, optionally of one session and/or one pixel size ('2560x1600')."""
    want = parse_size(size) if size else None
    root = shots_dir()
    if session:
        sid = base64.b64encode(session.encode()).decode().rstrip("=")
        pattern = os.path.join(glob.escape(root), f"s-{sid}*", "*.*")
    else:
        pattern = os.path.join(glob.escape(root), "*", "*.*")
    # newest first; stop at the first match (every neo call auto-captures, so filter by size)
    for p in sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True):
        got = image_size(p, want)
        if got and (want is None or got == want):
            age = datetime.now().timestamp() - os.path.getmtime(p)
            warnings = []
            if age > 60:
                warnings.append(f"newest match is {age:.0f}s old; is it really your capture?")
            return NeoShot(Path(p), got, age, warnings)
    raise NeoError(f"no neo screenshots found{' of size ' + size if size else ''}")
