import hashlib
import importlib.metadata
import json
import sys

import PIL
import pytest
from PIL import Image, features

from ainotate import cli
from ainotate.render import Look, render
from ainotate.spec import SpecError

EVERY = [
    {"type": "step", "n": 1, "rect": [1300, 100, 1520, 150], "label": "Checkout"},
    {"type": "box", "rect": [1300, 170, 1520, 220], "label": "Coupon", "color": "good"},
    {"type": "arrow", "rect": [280, 450, 900, 480], "label": "Row", "color": "bad"},
    {"type": "highlight", "rect": [280, 340, 900, 370]},
    {"type": "spotlight", "rect": [280, 560, 900, 600]},
    {"type": "redact", "rect": {"x": 420, "y": 230, "w": 170, "h": 14}},
    {"type": "text", "text": "A note", "at": [700, 900], "color": "info"},
    {"type": "magnify", "rect": [1300, 100, 1400, 150], "at": [1100, 800]},
    {"type": "click", "at": [1410, 125], "label": "Click", "color": "info"},
    {"type": "keys", "keys": ["Ctrl", "S"], "at": [300, 940]},
    {"type": "blur", "rect": [290, 680, 700, 700]},
    {"type": "pixelate", "rect": [290, 735, 700, 755]},
]
# sha256 of the default render of every mark type, made with the code before looks were pluggable.
# Fonts come from the system, so the pixels are pinned per platform (Linux: the contributor's run).
DEFAULT_SHA = {
    ("linux", False): "2459af01e003be489fe9675a07cea01f3109a6ed153d6b02d767dd0d11456417",
    ("linux", True): "fc93f1192da4bbcc2fb0ad05d98fa91b006776c58c0968f158ffa25769628368",
    ("darwin", False): "5a795eaadddc77d6fde353ef40765eb6dac235f7963d7d56396cccdbb181956f",
    ("darwin", True): "d0402a305915cd4f43326b0f8bee1d166a8cd16e95fe80ab736abd71523f92c6",
}


@pytest.mark.parametrize("dark", [False, True])
def test_default_look_is_unchanged(ui, ui_dark, dark):
    s = {"input": ui_dark if dark else ui, "marks": EVERY}
    img = render(s).image
    assert render(dict(s, look="default")).image.tobytes() == img.tobytes()
    if (PIL.__version__, features.version("freetype2")) != ("12.3.0", "2.14.3") \
            or (sys.platform, dark) not in DEFAULT_SHA:
        pytest.skip("pinned pixels were made with Pillow 12.3.0 and FreeType 2.14.3 on Linux and macOS")
    assert hashlib.sha256(img.tobytes()).hexdigest() == DEFAULT_SHA[(sys.platform, dark)]


class Gray(Look):
    """A test look: gray outlines and redactions, no shadow; records what it drew."""
    shadow = (0, 0, 0)
    drawn = []

    def outline(self, layer, i, g):
        Gray.drawn.append(("outline", i))
        super().outline(layer, i, g)

    def color(self, i):
        return "#777777"

    def redact_fill(self, i):
        return "#555555"


class EP:
    name, group = "gray", "ainotate.looks"

    def load(self):
        return Gray


@pytest.fixture
def gray_installed(monkeypatch):
    real = importlib.metadata.entry_points
    monkeypatch.setattr(importlib.metadata, "entry_points",
                        lambda **kw: [EP()] if kw.get("group") == "ainotate.looks" else real(**kw))
    Gray.drawn = []


def test_an_installed_look_draws_the_marks(ui, gray_installed):
    marks = [EVERY[1], EVERY[5]]
    plain = render({"input": ui, "marks": marks}).image
    img = render({"input": ui, "look": "gray", "marks": marks}).image
    assert img.tobytes() != plain.tobytes() and Gray.drawn == [("outline", 0)]
    assert img.getpixel((500, 237)) == (0x55, 0x55, 0x55)    # the redaction, in the look's color


def test_look_from_flag_env_and_config(ui, gray_installed, isolated_env, monkeypatch, tmp_path, capsys):
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps({"input": ui, "marks": [EVERY[1]]}))
    assert cli.main(["annotate", str(spec), "--draft", "--look", "gray"]) == 0 and Gray.drawn
    cfg = isolated_env / ".config" / "ainotate" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('look = "gray"\n')
    for env, used in ((None, True), ("default", False), ("gray", True)):
        Gray.drawn = []
        if env:
            monkeypatch.setenv("AINOTATE_LOOK", env)
        render({"input": ui, "marks": [EVERY[1]]})
        assert bool(Gray.drawn) == used, env
    Gray.drawn = []
    render({"input": ui, "look": "default", "marks": [EVERY[1]]})   # the spec beats env and config
    assert not Gray.drawn


@pytest.mark.parametrize("where", ["spec", "env"])
def test_unknown_look_lists_the_installed_looks(ui, gray_installed, monkeypatch, where):
    s = {"input": ui, "marks": [EVERY[1]]}
    if where == "spec":
        s["look"] = "neat"
    else:
        monkeypatch.setenv("AINOTATE_LOOK", "neat")
    with pytest.raises(SpecError) as e:
        render(s)
    assert "'neat', not an installed look; installed looks: default, gray" in str(e.value)
    assert ("AINOTATE_LOOK" if where == "env" else "'look'") in str(e.value)


@pytest.mark.parametrize("broken", ["import", "not-a-look", "init"])
def test_a_broken_look_falls_back_to_the_default_with_a_warning(ui, monkeypatch, broken):
    class Boom(Look):
        def __init__(self, ctx):
            raise RuntimeError("bad font file")

    class Bad:
        name, group = "neat", "ainotate.looks"

        def load(self):
            if broken == "import":
                raise ImportError("No module named 'neat_look'")
            return object if broken == "not-a-look" else Boom
    real = importlib.metadata.entry_points
    monkeypatch.setattr(importlib.metadata, "entry_points",
                        lambda **kw: [Bad()] if kw.get("group") == "ainotate.looks" else real(**kw))
    s = {"input": ui, "marks": [EVERY[1]]}
    res = render(dict(s, look="neat"))
    assert res.image.tobytes() == render(s).image.tobytes()
    assert any("look 'neat' could not be loaded" in w and "used the default look" in w for w in res.warnings)
