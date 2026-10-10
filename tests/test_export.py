import json
from pathlib import Path
from unittest import mock

import pytest
from PIL import Image

from ainotate import export
from ainotate.export import ExportError
from conftest import make_ui


@pytest.fixture
def shots(tmp_path):
    return [str(make_ui(tmp_path / f"s{i}.png", size=(800 + 40 * i, 500))) for i in range(3)]


def test_guide_md_html(shots, tmp_path):
    steps = [{"image": shots[0], "text": "Open <Settings>"}, {"image": shots[1], "text": "Click Save", "alt": "Save btn"}]
    res = export.guide(steps, "My guide", tmp_path / "g", intro="Intro text")
    md = res["md"].read_text()
    assert "1. Open <Settings>" in md and "![Open <Settings>](images/step-01.png)" in md
    assert "![Save btn](images/step-02.png)" in md
    assert (res["md"].parent / "images" / "step-02.png").is_file()
    h = res["html"].read_text()
    assert "data:image/png;base64," in h and "prefers-color-scheme:dark" in h and "max-width:860px" in h
    assert "Open &lt;Settings&gt;" in h and "@media print" in h
    assert "src=\"images" not in h


def test_guide_errors(tmp_path):
    with pytest.raises(ExportError):
        export.guide([], "x", tmp_path)
    with pytest.raises(ExportError):
        export.guide([{"image": str(tmp_path / "nope.png")}], "x", tmp_path)
    with pytest.raises(ExportError):
        export.guide([{"image": "a"}], "x", tmp_path, formats=("doc",))


def test_guide_pdf_pillow_fallback(shots, tmp_path):
    steps = [{"image": s, "text": "Step text " * 30} for s in shots]
    with mock.patch.object(export, "_pdf_playwright", side_effect=ImportError("no pw")):
        res = export.guide(steps, "T", tmp_path / "g", formats=("pdf",), intro="hello")
    assert res.pdf_backend == "pillow" and res["pdf"].read_bytes()[:5] == b"%PDF-"
    assert res.warnings


def test_guide_pdf_playwright_used(shots, tmp_path):
    def fake(html_path, pdf_path):
        pdf_path.write_bytes(b"%PDF-fake")
    with mock.patch.object(export, "_pdf_playwright", side_effect=fake):
        res = export.guide([{"image": shots[0], "text": "a"}], "T", tmp_path / "g", formats=("pdf",))
    assert res.pdf_backend == "playwright"


def test_apng_and_gif(shots, tmp_path):
    p = export.gif(shots, tmp_path / "a.png", durations_ms=[300, 400, 500], max_width=600)
    im = Image.open(p)
    assert im.n_frames == 3 and im.width == 600
    g = export.gif(shots, tmp_path / "a.gif", format="gif", max_width=600, crossfade_ms=200)
    gi = Image.open(g)
    assert gi.n_frames == 3 + 2 * 4 and gi.format == "GIF"
    with pytest.raises(ExportError):
        export.gif(shots, tmp_path / "x.png", durations_ms=[1, 2])
    with pytest.raises(ExportError):
        export.gif([], tmp_path / "x.png")


def test_gif_default_dir(shots, out_dirs):
    p = export.apng(shots[:2])
    assert p.parent == out_dirs[0] and p.suffix == ".png"


def test_compare_layouts(tmp_path):
    a = make_ui(tmp_path / "a.png", size=(800, 1000))
    b = make_ui(tmp_path / "b.png", size=(900, 1000), dark=True)
    side = Image.open(export.compare(a, b, tmp_path / "side.png"))
    bar = side.height - 1000                                  # tag bar above the images
    assert 20 < bar < 120 and side.width > 1700               # portrait -> side
    w1 = make_ui(tmp_path / "w1.png", size=(1600, 900))
    w2 = make_ui(tmp_path / "w2.png", size=(1600, 900), dark=True)
    stack = Image.open(export.compare(w1, w2, tmp_path / "stack.png"))
    assert stack.width == 1600 and stack.height > 1900        # wide -> stack
    forced = Image.open(export.compare(w1, w2, tmp_path / "f.png", layout="side", divider=False))
    assert forced.width == 3200
    # tags live in the bar above the images: Before = graphite (never red), After = green
    rgb = side.convert("RGB")
    def tag(x0):
        return {rgb.getpixel((x, y)) for x in range(x0, x0 + 40) for y in range(5, bar - 5)}
    assert (0x3A, 0x3A, 0x3C) in tag(0) and not any(p[0] > 150 and p[1] < 80 for p in tag(0))
    assert any(p[1] > p[0] + 40 and p[1] > p[2] + 40 for p in tag(800 + 14))


def test_compare_saves_via_output(tmp_path, out_dirs):
    a = make_ui(tmp_path / "a.png", size=(600, 800))
    p = export.compare(a, a)
    assert p.parent == out_dirs[0] and p.is_file()


def test_clipboard_mac(shots):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return mock.Mock(returncode=0, stdout="«class PNGf», 123", stderr="")
    with mock.patch.object(export.platform, "system", return_value="Darwin"), \
            mock.patch.object(export, "_run", side_effect=run):
        export.copy_to_clipboard(shots[0])
    assert calls[0][0] == "osascript" and "PNGf" in " ".join(calls[0]) and calls[1][-1] == "clipboard info"


NASTY = 'we\\ird "q" \\" & x"& y.png'   # backslashes, quotes, `"&`


def test_clipboard_mac_path_is_an_argument_never_script(tmp_path):
    """Regression: the path used to be pasted into the AppleScript source (injection)."""
    img = tmp_path / NASTY
    Image.new("RGB", (8, 8), "red").save(img, format="PNG")
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return mock.Mock(returncode=0, stdout="«class PNGf», 1", stderr="")
    with mock.patch.object(export.platform, "system", return_value="Darwin"), \
            mock.patch.object(export, "_run", side_effect=run):
        export.copy_to_clipboard(img)
    cmd = calls[0]
    scripts = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-e"]
    assert scripts[0] == "on run argv" and scripts[-1] == "end run"
    assert all(NASTY not in s and "weird" not in s and '"&' not in s for s in scripts)
    assert cmd[-1] == str(img.resolve()) and cmd[-2] == "end run"


@pytest.mark.skipif(not Path("/usr/bin/osascript").exists(), reason="macOS only")
def test_osascript_receives_the_nasty_path_verbatim(tmp_path):
    """The same argv form, with `return` instead of touching the clipboard: osascript hands the
    path back byte for byte, so quotes and backslashes cannot break out of the script."""
    import subprocess
    img = tmp_path / NASTY
    img.write_bytes(b"x")
    r = subprocess.run(["osascript", "-e", "on run argv", "-e", "return POSIX path of (POSIX file (item 1 of argv))",
                        "-e", "end run", str(img)], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.rstrip("\n") == str(img)


def test_clipboard_mac_verify_fails(shots):
    with mock.patch.object(export.platform, "system", return_value="Darwin"), \
            mock.patch.object(export, "_run", return_value=mock.Mock(returncode=0, stdout="text", stderr="")):
        with pytest.raises(ExportError):
            export.copy_to_clipboard(shots[0])


def test_clipboard_linux_hint(shots):
    with mock.patch.object(export.platform, "system", return_value="Linux"), \
            mock.patch.object(export.shutil, "which", return_value=None):
        with pytest.raises(ExportError, match="wl-clipboard"):
            export.copy_to_clipboard(shots[0])


def test_clipboard_linux_xclip_and_windows(shots, tmp_path):
    jpg = tmp_path / "x.jpg"
    Image.open(shots[0]).convert("RGB").save(jpg)
    with mock.patch.object(export.platform, "system", return_value="Linux"), \
            mock.patch.object(export.shutil, "which", side_effect=lambda n: "/x/xclip" if n == "xclip" else None), \
            mock.patch.object(export.subprocess, "run", return_value=mock.Mock(returncode=0)) as r:
        export.copy_to_clipboard(jpg)
    assert r.call_args[0][0][0] == "xclip" and r.call_args[0][0][-1].endswith("clip.png")
    with mock.patch.object(export.platform, "system", return_value="Windows"), \
            mock.patch.object(export.shutil, "which", return_value="powershell"), \
            mock.patch.object(export, "_run", return_value=mock.Mock(returncode=0, stderr="")) as r:
        export.copy_to_clipboard(shots[0])
    assert "SetImage" in r.call_args[0][0][-1] and "-STA" in r.call_args[0][0]
    # the path travels in the environment, not inside the PowerShell source
    assert "s0.png" not in r.call_args[0][0][-1] and r.call_args[1]["env"]["AINOTATE_CLIP_PATH"].endswith("s0.png")


def test_clipboard_missing_file(tmp_path):
    with pytest.raises(ExportError):
        export.copy_to_clipboard(tmp_path / "none.png")


def test_cli(shots, tmp_path, capsys):
    from ainotate import cli
    sj = tmp_path / "steps.json"
    sj.write_text(json.dumps({"title": "T", "steps": [{"image": Path(shots[0]).name, "text": "one"}]}))
    assert cli.main(["guide", str(sj), "--out-dir", str(tmp_path / "o")]) == 0
    assert (tmp_path / "o" / "T.md").is_file()
    assert cli.main(["animate", *shots, "--out", str(tmp_path / "c.gif"), "--format", "gif"]) == 0
    assert cli.main(["compare", shots[0], shots[1], "--out", str(tmp_path / "cmp.png")]) == 0
    assert cli.main(["guide", str(tmp_path / "missing.json")]) == 2
    with mock.patch.object(export, "copy_to_clipboard") as c:
        assert cli.main(["copy", shots[0]]) == 0
    c.assert_called_once()


# ---------------------------------------------------------------- names, drafts, errors

def test_compare_and_animate_names_in_output_folder(shots, out_dirs):
    out = out_dirs[0]
    c = export.compare(shots[0], shots[1], name="coupon fix")
    assert c.parent == out and c.name.endswith(" coupon fix.png")
    a = export.gif(shots, name="checkout flow")
    assert a.parent == out and a.name.endswith(" checkout flow.png") and "animation" not in a.name
    g = export.gif(shots, format="gif", name="../evil")
    assert g.parent == out and g.name.endswith(" ---evil.gif")
    d = export.compare(shots[0], shots[1], name="draft one", draft=True)
    assert d.parent != out and d.name.endswith(" draft one.png")
    assert export.gif(shots, name="x", draft=True).parent != out
    res = export.guide([{"image": shots[0], "text": "a"}], "Title", name="my guide", draft=True)
    assert out not in res["md"].parents and res["md"].parent.name.endswith(" my guide")


def test_guide_dirs_never_collide(shots, out_dirs):
    a = export.guide([{"image": shots[0]}], "T", name="same")
    b = export.guide([{"image": shots[0]}], "T", name="same")
    assert a["md"].parent != b["md"].parent and a["md"].parent.parent == out_dirs[0]


def test_export_errors_name_the_bad_input(shots, tmp_path):
    cases = [
        (lambda: export.compare(shots[0], tmp_path / "gone.png"), "after"),
        (lambda: export.compare(tmp_path / "gone.png", shots[0]), "before"),
        (lambda: export.gif([shots[0], tmp_path / "gone.png"]), "frames[1]"),
        (lambda: export.gif(shots, format="webm"), "format"),
        (lambda: export.guide([{"image": shots[0]}, {"image": str(tmp_path / "gone.png")}], "x", tmp_path),
         "steps[1].image"),
        (lambda: export.compare(shots[0], shots[1], labels=("Only",)), "labels"),
    ]
    for fn, what in cases:
        with pytest.raises(ExportError) as e:
            fn()
        assert e.value.input == what, (what, e.value.input)


# ---------------------------------------------------------------- guide width + Pillow PDF

def test_guide_keeps_image_widths_by_default_and_pads_on_request(tmp_path):
    wide = make_ui(tmp_path / "wide.png", size=(1000, 300))
    narrow = make_ui(tmp_path / "narrow.png", size=(600, 300))
    alpha = tmp_path / "alpha.png"
    Image.new("RGBA", (400, 200), (255, 0, 0, 255)).save(alpha)
    export.guide([{"image": wide}, {"image": narrow}, {"image": alpha}], "W", tmp_path / "d")
    assert [Image.open(p).size for p in sorted((tmp_path / "d" / "images").iterdir())] == \
        [(1000, 300), (600, 300), (400, 200)]                # default: each image as is
    res = export.guide([{"image": wide}, {"image": narrow}, {"image": alpha}], "W", tmp_path / "g",
                       width=1000)
    imgs = sorted((tmp_path / "g" / "images").iterdir())
    sizes = [Image.open(p).size for p in imgs]
    assert sizes == [(1000, 300), (1000, 300), (1000, 200)]
    n = Image.open(imgs[1]).convert("RGB")
    assert n.getpixel((10, 150)) == (255, 255, 255) and n.getpixel((990, 150)) == (255, 255, 255)
    assert n.getpixel((210, 5)) == n.getpixel((790, 5)) == (20, 60, 140)   # header bar, centered at x 200..800
    assert n.getpixel((190, 5)) == n.getpixel((810, 5)) == (255, 255, 255)
    a = Image.open(imgs[2])
    assert a.mode == "RGBA" and a.getpixel((5, 5))[3] == 0 and a.getpixel((500, 100)) == (255, 0, 0, 255)
    assert Image.open(narrow).size == (600, 300)            # the source file is untouched
    assert "images/step-02.png" in res["md"].read_text()
    # explicit smaller width: wider images scale down; width=0 keeps them as they are
    export.guide([{"image": wide}, {"image": narrow}], "W", tmp_path / "h", width=800)
    assert [Image.open(p).size for p in sorted((tmp_path / "h" / "images").iterdir())] == [(800, 240), (800, 300)]
    export.guide([{"image": wide}, {"image": narrow}], "W", tmp_path / "k", width=0)
    assert [Image.open(p).size[0] for p in sorted((tmp_path / "k" / "images").iterdir())] == [1000, 600]
    with pytest.raises(ExportError) as e:
        export.guide([{"image": wide}], "W", tmp_path / "z", width=-5)
    assert e.value.input == "width"


def test_pillow_pdf_long_text_never_squashes_the_image(tmp_path, monkeypatch):
    """Regression: a long step text left box_h <= 0 (or a sliver) for the screenshot."""
    from ainotate import export_html
    shot = make_ui(tmp_path / "s.png", size=(1600, 1000))
    pasted = []
    real_paste = Image.Image.paste

    def spy(self, im, box=None, mask=None):
        if isinstance(im, Image.Image) and self.size == (1240, 1754):
            pasted.append(im.size)
        return real_paste(self, im, box, mask)
    monkeypatch.setattr(Image.Image, "paste", spy)
    pages = []
    real_save = Image.Image.save

    def save_spy(self, fp, format=None, **kw):
        if format == "PDF":
            pages.append(1 + len(kw.get("append_images", [])))
        return real_save(self, fp, format, **kw)
    monkeypatch.setattr(Image.Image, "save", save_spy)
    long = "A very long instruction that keeps going and going. " * 120
    export_html._pdf_pillow("Title", "", [{"image": shot, "text": long}, {"image": shot, "text": "short"}],
                            tmp_path / "x.pdf")
    assert (tmp_path / "x.pdf").read_bytes()[:5] == b"%PDF-"
    assert len(pasted) == 2 and all(h >= export_html.MIN_IMAGE_H for _, h in pasted)
    assert pages[0] >= 3        # text spilled over a page, image on its own page, step 2


def test_guide_step_title_shows_bold_summary_above_text(tmp_path):
    from PIL import Image
    from ainotate.export import guide
    img = tmp_path / "a.png"
    Image.new("RGB", (40, 30), "white").save(img)
    res = guide([{"image": str(img), "title": "Deploy a model", "text": "Pick one and press Deploy."}],
                "T", tmp_path / "out", formats=("md", "html"))
    h = res["html"].read_text()
    assert '<span class="t">Deploy a model</span>' in h and '<div class="say"><p>Pick one and press Deploy.</p></div>' in h
    md = res["md"].read_text()
    assert "1. **Deploy a model**" in md and "   Pick one and press Deploy." in md


def test_guide_step_text_supports_bold_code_and_sublists(tmp_path):
    from PIL import Image
    from ainotate.export import guide
    img = tmp_path / "a.png"
    Image.new("RGB", (40, 30), "white").save(img)
    text = "Run `ainotate doctor` first.\n- **Mac:** use brew\n- Linux: <apt>\nDone."
    res = guide([{"image": str(img), "title": "Install", "text": text}], "T", tmp_path / "out",
                formats=("md", "html", "pdf"))
    h = res["html"].read_text()
    assert "<code>ainotate doctor</code>" in h
    assert "<ul><li><strong>Mac:</strong> use brew</li><li>Linux: &lt;apt&gt;</li></ul>" in h
    md = res["md"].read_text()
    assert "   - **Mac:** use brew" in md and "&lt;apt&gt;</li></ul><p>Done.</p>" in h and "apt>\n\n   Done." in md
    assert res["pdf"].stat().st_size > 0
