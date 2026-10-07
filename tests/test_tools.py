import os
import time

import pytest
from PIL import Image

from ainotate.capture import neo
from ainotate.spec import SpecError
from ainotate.tools import ToolError, grid, info, zoom


def test_info(ui):
    assert info(ui) == (1600, 1000)


def test_grid(ui):
    r = grid(ui, 200)
    assert r.path.is_file() and r.note == "(1600, 1000)"
    img = Image.open(r.path).convert("RGB")
    assert img.size == (1600, 1000) and img.getpixel((400, 500)) == (255, 0, 80)


def test_zoom(ui):
    r = zoom(ui, 1300, 100, 1520, 220, step=20)
    assert r.note == "region 1300,100-1520,220 shown x4; grid labels are source px"
    assert Image.open(r.path).size == (880, 480) and r.warnings == []
    wide = zoom(ui, 0, 0, 900, 300)
    assert "wider than 600px" in wide.warnings[0] and "x1;" in wide.note


@pytest.mark.parametrize("call, msg", [
    (lambda p: grid(p, 0), "--step must be a positive"),
    (lambda p: zoom(p, 0, 0, 5, 100), "zoom region is empty"),
    (lambda p: zoom(p, 1595, 0, 1700, 100), "zoom region is empty"),
    (lambda p: zoom(p, 0, 0, 100, 100, step=0), "--step must be a positive"),
])
def test_tool_errors(ui, call, msg):
    with pytest.raises(ToolError, match=msg):
        call(ui)


def test_missing_image(tmp_path):
    with pytest.raises(SpecError, match="image not found"):
        info(str(tmp_path / "missing.png"))


def _shot(path, size, age):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size).save(path)
    t = time.time() - age
    os.utime(path, (t, t))


def test_neo_latest(tmp_path, monkeypatch):
    root = tmp_path / "neo"
    monkeypatch.setenv("AINOTATE_NEO_DIR", str(root))
    sid = "c2Vzcw"    # base64("sess") without padding
    _shot(root / f"s-{sid}-1" / "a.png", (2560, 1600), 5)
    _shot(root / "s-other" / "b.png", (800, 600), 1)
    (root / "s-other" / "broken.png").write_bytes(b"\x89PNG half")
    assert neo.latest().size == (800, 600)
    assert neo.latest(size="2560x1600").path.name == "a.png"
    assert neo.latest(session="sess").path.name == "a.png"
    _shot(root / "s-old" / "c.png", (100, 100), 600)
    old = neo.latest(size="100x100")
    assert old.warnings and "600s old" in old.warnings[0]
    with pytest.raises(neo.NeoError, match="of size 1x1"):
        neo.latest(size="1x1")
    with pytest.raises(ValueError, match="--size must look like"):
        neo.latest(size="big")
