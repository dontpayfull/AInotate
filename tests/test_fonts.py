from PIL import Image, ImageDraw

from ainotate import style


def bitmap(font, text, size=(220, 40)):
    im = Image.new("L", size, 0)
    ImageDraw.Draw(im).text((4, 4), text, font=font, fill=255)
    return im


def test_bundled_inter_is_used():
    path, idx = style.font_source()
    assert path.endswith("Inter-Bold.ttf") and idx == 0
    assert "Inter" in style.load_font(16).getname()[0]


def test_font_override(monkeypatch, tmp_path):
    monkeypatch.setenv("AINOTATE_FONT", "/some/fonts/X.ttc:1")
    assert style.font_source() == ("/some/fonts/X.ttc", 1)
    monkeypatch.setenv("AINOTATE_FONT", "C:\\Fonts\\arial.ttf")
    assert style.font_source() == ("C:\\Fonts\\arial.ttf", 0)
    monkeypatch.setenv("AINOTATE_FONT", str(tmp_path / "missing.ttf"))
    assert style.load_font(16) is not None        # falls back to Pillow's default font


def test_diacritics_have_glyphs():
    f = style.load_font(24)
    notdef = bitmap(f, "\U0010FFF0").tobytes()
    for ch in "șțăîâȘȚĂÎÂşţäöüßÄÖÜéèêëàçôûùœÉÈÇ":
        bm = bitmap(f, ch)
        assert bm.getbbox() is not None and bm.tobytes() != notdef, ch


def test_space_is_visible_at_16px():
    f = style.load_font(16)
    assert f.getlength(" ") >= 3.5
    im = bitmap(f, "Ultima versiune")
    cols = [any(im.getpixel((x, y)) > 60 for y in range(im.height)) for x in range(im.width)]
    inked = [x for x, c in enumerate(cols) if c]
    gaps, run = [], 0
    for x in range(inked[0], inked[-1]):
        if cols[x]:
            if run:
                gaps.append(run)
            run = 0
        else:
            run += 1
    assert max(gaps) >= 4 and sorted(gaps)[-2] <= 2     # one clear word gap, letters stay tight
