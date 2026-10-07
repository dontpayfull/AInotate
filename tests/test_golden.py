"""Pinned automatic label positions on three reference specs, in Helvetica Neue Bold: a change
to the placer shows up here as moved boxes, and the labels must keep off page content, marks,
badges and arrows (no overlap, cover or crossing warnings). Step badges are pinned with `badge`
so only the labels are under test."""
import os

import pytest

import ainotate.render as rmod
from ainotate.render import render

HELVETICA = "/System/Library/Fonts/HelveticaNeue.ttc"

pytestmark = pytest.mark.skipif(not os.path.isfile(HELVETICA), reason="needs macOS Helvetica Neue")


def specs(ui, ui_dark):
    return {
        "steps_auto_crop": {"input": ui, "scale": 1, "crop": "auto", "marks": [
            {"type": "step", "n": 1, "rect": [1300, 100, 1520, 150], "label": "Apasă Checkout",
             "badge": "tl"},
            {"type": "step", "n": 2, "rect": [1300, 170, 1520, 220], "label": "Introdu codul", "color": "good",
             "badge": "tl"},
            {"type": "redact", "rect": {"x": 420, "y": 230, "w": 170, "h": 14}},
            {"type": "text", "text": "Ultima versiune", "at": [900, 420], "color": "info"}]},
        "dark_spotlight_dom": {"input": ui_dark, "scale": 2, "dim": 0.4, "marks": [
            {"type": "spotlight", "rect": {"x": 650, "y": 50, "w": 110, "h": 25}},
            {"type": "arrow", "rect": {"x": 650, "y": 85, "w": 110, "h": 25}, "label": "Coupon", "color": "bad"},
            {"type": "box", "rect": {"x": 140, "y": 55, "w": 300, "h": 30}, "label": "Rând greșit",
             "badge": "br", "pad": 3}]},
        "crop_forced_line": {"input": ui, "crop": [250, 80, 1580, 900], "arrow_style": "line", "marks": [
            {"type": "highlight", "rect": [280, 340, 900, 370], "color": "look"},
            {"type": "box", "rect": [280, 450, 900, 480], "label": "Forced", "label_at": [1000, 600]},
            {"type": "step", "n": "A", "rect": [1300, 100, 1520, 150], "badge": "r", "arrow": False,
             "label": "No arrow"}]},
    }


# automatic label boxes [x1, y1, x2, y2] (rounded) of the content-aware placer, in mark order
# regenerated on purpose when the placer changes
LABELS = {
    "steps_auto_crop": [[870, 93, 1069, 135], [908, 216, 1071, 259]],    # beside Checkout / Apply coupon
    "dark_spotlight_dom": [[968, 107, 1172, 186], [968, 12, 1245, 91]],  # left of the button, above the row
    "crop_forced_line": [[894, 14, 1020, 58]],                            # beside the Checkout outline
}


@pytest.mark.parametrize("name", ["steps_auto_crop", "dark_spotlight_dom", "crop_forced_line"])
def test_label_positions(ui, ui_dark, monkeypatch, name):
    monkeypatch.setenv("AINOTATE_FONT", f"{HELVETICA}:1")
    got, orig = [], rmod.place_label

    def spy(*a, **k):
        res = orig(*a, **k)
        box = res[0] if isinstance(res, tuple) else res
        got.append([round(v) for v in box] if box else None)
        return res
    monkeypatch.setattr(rmod, "place_label", spy)
    res = render(specs(ui, ui_dark)[name])
    assert got == LABELS[name]
    assert not [w for w in res.warnings if "overlap" in w or "covers" in w or "cross" in w]
