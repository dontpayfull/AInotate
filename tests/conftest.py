import pytest
from PIL import Image, ImageDraw


def make_ui(path, size=(1600, 1000), dark=False):
    """Deterministic fake app screenshot: header, sidebar, text lines, buttons."""
    W, H = size
    bg, fg, line = ((30, 30, 34), (220, 220, 220), (70, 70, 80)) if dark else \
        ((255, 255, 255), (40, 40, 40), (210, 210, 215))
    img = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 70], fill=(20, 60, 140))
    d.text((24, 26), "Acme Dashboard", fill="white")
    d.rectangle([0, 70, 240, H], fill=(245, 246, 248) if not dark else (45, 45, 50))
    for i, item in enumerate(["Overview", "Orders", "Customers", "Settings", "Billing"]):
        d.text((30, 110 + 50 * i), item, fill=fg)
    for row in range(14):
        y = 120 + row * 55
        d.text((290, y), f"Order #{1000 + row}   customer{row}@example.com   $ {row * 13}.00", fill=fg)
        d.line([(280, y + 35), (W - 40, y + 35)], fill=line)
    d.rounded_rectangle([1300, 100, 1520, 150], radius=8, fill=(0, 113, 227))
    d.text((1360, 118), "Checkout", fill="white")
    d.rounded_rectangle([1300, 170, 1520, 220], radius=8, outline=(120, 120, 120))
    d.text((1350, 188), "Apply coupon", fill=fg)
    img.save(path)
    return path


@pytest.fixture
def ui(tmp_path):
    return str(make_ui(tmp_path / "ui.png"))


@pytest.fixture
def ui_dark(tmp_path):
    return str(make_ui(tmp_path / "ui_dark.png", dark=True))


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """No test reads the user's config, writes to their folders or uses a font override."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for v in ("AINOTATE_OUTPUT_DIR", "AINOTATE_BACKUP_DIR", "AINOTATE_PREFIX", "AINOTATE_FONT", "AINOTATE_NEO_DIR",
              "AINOTATE_LOOK"):
        monkeypatch.delenv(v, raising=False)
    return home


@pytest.fixture
def out_dirs(tmp_path, monkeypatch):
    out, bak = tmp_path / "out", tmp_path / "bak"
    monkeypatch.setenv("AINOTATE_OUTPUT_DIR", str(out))
    monkeypatch.setenv("AINOTATE_BACKUP_DIR", str(bak))
    return out, bak
