import hashlib
import sys
import importlib.metadata
import json

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
# sha256 of the default render of every mark type, made with the code before looks were pluggable
# (fonts differ by platform, so each platform has its own pin)
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


@pytest.mark.parametrize("where", ["spec", "env", "config"])
def test_a_look_that_is_not_installed_falls_back_with_a_warning(ui, gray_installed, isolated_env, monkeypatch, where):
    s = {"input": ui, "marks": [EVERY[1]]}
    if where == "spec":
        s["look"] = "neat"
    elif where == "env":
        monkeypatch.setenv("AINOTATE_LOOK", "neat")
    else:
        cfg = isolated_env / ".config" / "ainotate" / "config.toml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text('look = "neat"\n')
    res = render(s)
    assert res.image.tobytes() == render({"input": ui, "marks": [EVERY[1]]}).image.tobytes()
    assert res.warnings == ["look 'neat' is not installed, so the default look was used; "
                            "installed looks: default, gray"]


class Broken(Gray):
    def __init__(self, ctx):
        raise RuntimeError("no font")


class BadDraw(Gray):
    def label(self, layer, img, i, box, text):
        raise ValueError("bad label")


@pytest.mark.parametrize("case, warning", [
    ("import", "look 'gray' could not be loaded (ModuleNotFoundError: No module named 'gray_look'), "
               "so the default look was used"),
    ("init", "look 'gray' failed to start (RuntimeError: no font), so the default look was used"),
    ("draw", "look 'gray' failed while drawing (ValueError: bad label), so the default look was used"),
])
def test_a_broken_look_falls_back_with_a_warning(ui, gray_installed, monkeypatch, tmp_path, capsys, case, warning):
    def load():
        if case == "import":
            import gray_look  # noqa: F401
        return Broken if case == "init" else BadDraw
    monkeypatch.setattr(EP, "load", lambda self: load())
    s = {"input": ui, "look": "gray", "marks": [EVERY[0], EVERY[5]]}
    res = render(s)
    assert res.warnings[0] == warning
    assert res.image.tobytes() == render(dict(s, look="default")).image.tobytes()
    spec = tmp_path / "s.json"                       # the CLI prints it like any render warning
    spec.write_text(json.dumps(s))
    assert cli.main(["annotate", str(spec), "--draft"]) == 0
    assert warning in capsys.readouterr().err


def test_mcp_tools_take_a_look_and_return_its_warning(ui, gray_installed, out_dirs):
    pytest.importorskip("mcp")
    import anyio
    from mcp import Client

    from ainotate.mcp_server import mcp

    async def go():
        async with Client(mcp) as c:
            tools = {t.name: t for t in (await c.list_tools()).tools}
            for name in ("annotate", "preview", "shoot"):
                assert "installed look" in tools[name].input_schema["properties"]["look"]["description"]
            res = await c.call_tool("annotate", {"spec": {"input": ui, "marks": [EVERY[1]]}, "look": "nope"})
            return json.loads(res.content[0].text)
    d = anyio.run(go)
    assert d["ok"] and d["warnings"] == ["look 'nope' is not installed, so the default look was used; "
                                         "installed looks: default, gray"]


def test_look_must_be_a_name(ui):
    with pytest.raises(SpecError) as e:
        render({"input": ui, "look": 3, "marks": []})
    assert "'look' must be a look name" in str(e.value)


# ---------------------------------------------------------------- a look can never change redactions

REDACT = {"type": "redact", "rect": [100, 100, 300, 130]}


class Mutates(Gray):
    """Changes the geometry and spec data it is given, then fails (in __init__ or in outline)."""
    when = "init"

    def __init__(self, ctx):
        super().__init__(ctx)
        ctx.rects[0][0] += 10                       # the redaction's left edge
        ctx.marks[1].pop("label", None)
        if Mutates.when == "init":
            raise RuntimeError("bad start")

    def outline(self, layer, i, g):
        raise RuntimeError("bad outline")


class Paints(Gray):
    """Works, but paints white over the whole page image it is handed."""

    def label(self, layer, img, i, box, text):
        img.paste((255, 255, 255, 255), (0, 0, img.width, img.height))
        super().label(layer, img, i, box, text)


@pytest.mark.parametrize("cls, when", [(Mutates, "init"), (Mutates, "draw"), (Paints, None)])
def test_a_look_never_changes_redaction_coverage_or_the_spec(ui, gray_installed, monkeypatch, cls, when):
    import copy
    Mutates.when = when
    monkeypatch.setattr(EP, "load", lambda self: cls)
    s = {"input": ui, "look": "gray", "marks": [REDACT, EVERY[1]]}
    before = copy.deepcopy(s)
    res = render(s)
    assert s == before
    assert res.image.getpixel((100, 110)) == res.image.getpixel((299, 129)) != (255, 255, 255)
    plain = render(dict(s, look="default")).image
    if cls is Mutates:   # the failed render is redone from the spec, exactly as the default look
        assert res.image.tobytes() == plain.tobytes()
        assert res.warnings[0].startswith("look 'gray' failed")
    else:                # a working look: drawn by it, on its own copy of the page
        assert not res.warnings and res.image.tobytes() != plain.tobytes()


class BadValues(Gray):
    bad = "shadow"

    @property
    def shadow(self):
        if BadValues.bad == "shadow":
            raise RuntimeError("no shadow")
        return (0, 0, 0)

    def redact_fill(self, i):
        return "not-a-color" if BadValues.bad == "redact_fill" else "#555555"

    def label_size(self, text):
        return (0, -1) if BadValues.bad == "label_size" else super().label_size(text)


@pytest.mark.parametrize("bad, why", [
    ("shadow", "RuntimeError: no shadow"),
    ("redact_fill", "ValueError: unknown color specifier: 'not-a-color'"),
    ("label_size", "ValueError: label_size() is (0, -1)"),
])
def test_bad_attributes_and_hook_results_fall_back(ui, gray_installed, monkeypatch, tmp_path, capsys, bad, why):
    BadValues.bad = bad
    monkeypatch.setattr(EP, "load", lambda self: BadValues)
    s = {"input": ui, "look": "gray", "marks": [REDACT, EVERY[1]]}
    res = render(s)
    assert res.warnings[0] == f"look 'gray' failed while drawing ({why}), so the default look was used"
    assert res.image.tobytes() == render(dict(s, look="default")).image.tobytes()
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps(s))
    assert cli.main(["annotate", str(spec), "--draft"]) == 0
    assert why in capsys.readouterr().err


# ---------------------------------------------------------------- values the core cannot use

class Odd(Gray):
    kind = "name"

    def color(self, i):
        return "info" if Odd.kind == "name" else super().color(i)

    @property
    def shadow(self):
        return (0.3, float("inf"), 0.15) if Odd.kind == "inf" else (0, 0, 0)

    def label_size(self, text):   # passes the checks, but no frame has room for it
        return (999_999, 999_999) if Odd.kind == "huge" else super().label_size(text)


def test_a_color_name_from_a_look_is_drawn_as_its_color(ui, gray_installed, monkeypatch):
    Odd.kind = "name"
    monkeypatch.setattr(EP, "load", lambda self: Odd)
    click = {"type": "click", "at": [1410, 125]}   # drawn by AInotate in the look's color(i)
    res = render({"input": ui, "look": "gray", "marks": [click]})
    assert not res.warnings and res.image.getpixel((1410, 125)) != Image.open(ui).getpixel((1410, 125))


@pytest.mark.parametrize("kind, why", [
    ("inf", "(ValueError: shadow is (0.3, inf, 0.15))"),
    ("huge", "(RenderError: mark #0 (box): no room for label 'Coupon'."),
])
def test_values_the_core_cannot_use_fall_back(ui, gray_installed, monkeypatch, kind, why):
    Odd.kind = kind
    monkeypatch.setattr(EP, "load", lambda self: Odd)
    s = {"input": ui, "look": "gray", "marks": [EVERY[1]]}
    res = render(s)
    assert res.warnings[0].startswith("look 'gray' failed while drawing " + why)
    assert res.warnings[0].endswith(", so the default look was used")
    assert res.image.tobytes() == render(dict(s, look="default")).image.tobytes()


def test_an_error_the_default_look_also_hits_is_raised(ui, gray_installed):
    from ainotate.render import RenderError
    with pytest.raises(RenderError):   # a label position outside the image is the spec's fault
        render({"input": ui, "look": "gray", "marks": [dict(EVERY[1], label_at=[5000, 10])]})
