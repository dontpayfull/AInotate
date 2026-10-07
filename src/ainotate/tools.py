"""Helpers for locating things on an image: grid(), zoom(), info()."""
from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw

from .spec import open_image
from .style import load_font

GRID_COLOR = (255, 0, 80)


class ToolError(Exception):
    """Bad arguments for a tool (exit code 2)."""


@dataclass
class ToolResult:
    path: Path
    size: tuple                 # (w, h) of the written image
    note: str = ""              # what the CLI prints after the path
    warnings: list[str] = field(default_factory=list)


def info(image) -> tuple:
    """Real pixel size (w, h) of an image file."""
    return open_image(image).size


def grid(image, step: int = 100, out_path=None) -> ToolResult:
    """The image with a coordinate grid every `step` px, labels in image px."""
    if step <= 0:
        raise ToolError("--step must be a positive number of pixels")
    img = open_image(image).convert("RGB")
    d = ImageDraw.Draw(img)
    f = load_font(max(12, step // 6))
    for x in range(0, img.width, step):
        d.line([(x, 0), (x, img.height)], fill=GRID_COLOR, width=1)
        d.text((x + 3, 3), str(x), fill=GRID_COLOR, font=f)
    for y in range(0, img.height, step):
        d.line([(0, y), (img.width, y)], fill=GRID_COLOR, width=1)
        d.text((3, y + 3), str(y), fill=GRID_COLOR, font=f)
    out = Path(out_path) if out_path else Path(tempfile.mkdtemp(prefix="ainotate-grid-")) / "grid.png"
    img.save(out)
    return ToolResult(out, img.size, str(img.size))


def zoom(image, x1, y1, x2, y2, step: int = 20, out_path=None) -> ToolResult:
    """Crop a region, enlarge it until it is ~1000px wide, and draw a fine grid whose labels are
    SOURCE pixel coordinates: read exact rects off it instead of estimating from a preview."""
    img = open_image(image).convert("RGB")
    x1, y1, x2, y2 = [int(v) for v in (x1, y1, x2, y2)]
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(img.width, x2), min(img.height, y2)
    if x2 - x1 < 10 or y2 - y1 < 10:
        raise ToolError(f"zoom region is empty inside the {img.width}x{img.height} image")
    if step <= 0:
        raise ToolError("--step must be a positive number of pixels")
    k = max(1, min(8, 1000 // (x2 - x1)))
    z = img.crop((x1, y1, x2, y2)).resize(((x2 - x1) * k, (y2 - y1) * k), Image.NEAREST)
    d = ImageDraw.Draw(z)
    f = load_font(13)
    for x in range(x1 - x1 % step + step, x2, step):
        d.line([((x - x1) * k, 0), ((x - x1) * k, z.height)], fill=GRID_COLOR, width=1)
        d.text(((x - x1) * k + 2, 2), str(x), fill=GRID_COLOR, font=f)
    for y in range(y1 - y1 % step + step, y2, step):
        d.line([(0, (y - y1) * k), (z.width, (y - y1) * k)], fill=GRID_COLOR, width=1)
        d.text((2, (y - y1) * k + 2), str(y), fill=GRID_COLOR, font=f)
    out = Path(out_path) if out_path else Path(tempfile.mkdtemp(prefix="ainotate-zoom-")) / "zoom.png"
    z.save(out)
    warnings = []
    if x2 - x1 > 600:
        warnings.append("region wider than 600px stays small in the preview; zoom a narrower area "
                        "around the element for exact rects")
    return ToolResult(out, z.size, f"region {x1},{y1}-{x2},{y2} shown x{k}; grid labels are source px", warnings)
