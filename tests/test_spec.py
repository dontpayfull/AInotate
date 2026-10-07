import pytest

from ainotate.spec import SpecError, validate

R = [10, 10, 100, 50]


def spec(*marks, **top):
    s = {"input": "x.png", "marks": list(marks)}
    s.update(top)
    return s


def errors_of(s):
    with pytest.raises(SpecError) as e:
        validate(s)
    return str(e.value)


def test_valid_spec_returns_marks():
    marks = [{"type": "box", "rect": R, "label": "Here"},
             {"type": "step", "n": 1, "rect": {"x": 1, "y": 2, "width": 3, "height": 4}},
             {"type": "step", "n": "A", "rect": R, "badge": "br"},
             {"type": "arrow", "rect": R, "label": "Look", "label_at": [5, 5], "color": "bad"},
             {"type": "highlight", "rect": R, "color": "#123456"},
             {"type": "spotlight", "rect": R},
             {"type": "redact", "rect": R, "fill": "black"},
             {"type": "text", "text": "Note", "at": [1, 2], "color": "info"}]
    assert validate(spec(*marks, crop="auto", dim=0.3, crop_pad=0, scale=2, name="x")) == marks


def test_not_an_object():
    assert "spec must be a JSON object" in errors_of([1, 2])


@pytest.mark.parametrize("top, msg", [
    ({"input": None}, "missing 'input'"),
    ({"scale": 0}, "'scale' must be a positive number"),
    ({"scale": True}, "'scale' must be a positive number"),
    ({"scale": float("nan")}, "'scale' must be a positive number"),
    ({"crop": [10, 10, 5, 50]}, "x2>x1"),
    ({"crop": "full"}, "rect must be [x1,y1,x2,y2] or {x,y,w,h}"),
    ({"dim": 1.5}, "'dim' must be a number from 0 to 1"),
    ({"dim": "x"}, "'dim' must be a number from 0 to 1"),
    ({"crop_pad": -1}, "'crop_pad' must be a number >= 0"),
    ({"arrow_style": "curvy"}, "'arrow_style' must be skitch, straight, curved or line"),
    ({"name": 5}, "'name' must be a string"),
    ({"marks": {}}, "'marks' must be a list"),
])
def test_top_level_errors(top, msg):
    s = spec({"type": "box", "rect": R})
    s.update(top)
    assert msg in errors_of(s)


def test_missing_marks():
    assert "missing 'marks'" in errors_of({"input": "x.png"})


@pytest.mark.parametrize("mark, msg", [
    ("box", "must be an object"),
    ({"type": "circle", "rect": R}, "unknown type 'circle'"),
    ({"rect": R}, "unknown type None"),
    ({"type": "box"}, "missing 'rect'"),
    ({"type": "redact"}, "missing 'rect'"),
    ({"type": "box", "rect": {"x": 1, "y": 1, "w": 0, "h": 5}}, "zero/negative size"),
    ({"type": "box", "rect": {"x": 1, "y": 1, "w": "5", "h": 5}}, "needs numeric x, y"),
    ({"type": "box", "rect": [10, 10, 10, 50]}, "x2>x1"),
    ({"type": "box", "rect": [10, 60, 20, 50]}, "x2>x1, y2>y1"),
    ({"type": "box", "rect": [1, 2, 3]}, "rect must be [x1,y1,x2,y2]"),
    ({"type": "step", "rect": R}, "'n' must be a step number"),
    ({"type": "step", "rect": R, "n": True}, "'n' must be a step number"),
    ({"type": "step", "rect": R, "n": ""}, "'n' must be a step number"),
    ({"type": "text", "at": [1, 2]}, "missing 'text'"),
    ({"type": "text", "text": "", "at": [1, 2]}, "missing 'text'"),
    ({"type": "text", "text": "hi"}, ".at: must be [x, y]"),
    ({"type": "text", "text": "hi", "at": [1, "2"]}, ".at: must be [x, y]"),
    ({"type": "arrow", "rect": R}, "arrow needs a 'label'"),
    ({"type": "box", "rect": R, "label": ""}, "'label' must be a non-empty string"),
    ({"type": "box", "rect": R, "label": 3}, "'label' must be a non-empty string"),
    ({"type": "highlight", "rect": R, "label": "x"}, "labels are drawn only on box/step/arrow"),
    ({"type": "redact", "rect": R, "label_at": [1, 1]}, "labels are drawn only on box/step/arrow"),
    ({"type": "box", "rect": R, "label_at": [1, 1]}, "'label_at' needs a 'label'"),
    ({"type": "box", "rect": R, "label": "x", "label_at": [1]}, ".label_at: must be [x, y]"),
    ({"type": "box", "rect": R, "pad": -2}, "'pad' must be a number >= 0"),
    ({"type": "step", "n": 1, "rect": R, "badge": "middle"}, "'badge' must be one of"),
    ({"type": "box", "rect": R, "color": "blurple"}, "bad color 'blurple'"),
    ({"type": "box", "rect": R, "color": ""}, "bad color ''"),
    ({"type": "redact", "rect": R, "fill": None}, "bad fill None"),
    ({"type": "redact", "rect": R, "fill": "nope"}, "bad fill 'nope'"),
])
def test_mark_errors(mark, msg):
    assert msg in errors_of(spec(mark))


def test_every_problem_is_listed():
    msg = errors_of({"input": 1, "scale": -1, "marks": [{"type": "box"}, {"type": "x"}]})
    assert msg.startswith("invalid spec:")
    for part in ("missing 'input'", "'scale'", "mark #0 (box): missing 'rect'", "mark #1: unknown type"):
        assert part in msg


def test_n_on_a_non_step_mark_warns():
    from ainotate.spec import spec_warnings
    w = spec_warnings([{"type": "box", "rect": R, "n": 2, "label": "Here"},
                       {"type": "arrow", "rect": R, "n": 3, "label": "x"},
                       {"type": "step", "rect": R, "n": 1}])
    assert w == ["mark #0 (box): n is only used by step marks; use \"type\": \"step\" for a numbered badge",
                 "mark #1 (arrow): n is only used by step marks; use \"type\": \"step\" for a numbered badge"]
    assert validate(spec({"type": "box", "rect": R, "n": 2}))      # still valid: a warning, not an error


def test_crop_modes():
    for crop in ("auto", "tight"):
        validate(spec({"type": "box", "rect": R}, crop=crop))
    assert '"auto" / "tight"' in errors_of(spec({"type": "box", "rect": R}, crop="loose"))
