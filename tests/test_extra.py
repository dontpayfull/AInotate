"""Skill install, the permission owner in doctor, and desktop UI element targets (matching is
pure; the Accessibility walk itself needs a real Mac and is not run here)."""
import sys
import zipfile

import pytest

from ainotate import cli, skill_install
from ainotate.capture import ax


# ---------- install-skill ----------

@pytest.fixture
def homes(tmp_path, monkeypatch):
    t = {"claude": tmp_path / "claude", "agents": tmp_path / "agents"}
    monkeypatch.setattr(skill_install, "TARGETS", t)
    return t


def test_install_copies_the_skill_everywhere(homes):
    done = skill_install.install()
    assert [w for _, w in done] == ["installed", "installed"]
    for root in homes.values():
        assert (root / "ainotate" / "SKILL.md").read_text().startswith("---\nname: ainotate")
        assert (root / "ainotate" / "references" / "spec.md").is_file()


def test_install_updates_its_own_copy_but_not_a_stranger(homes):
    skill_install.install(["claude"])
    assert skill_install.install(["claude"])[0][1] == "updated"
    other = homes["agents"] / "ainotate"
    other.mkdir(parents=True)
    (other / "SKILL.md").write_text("---\nname: something-else\n---\n")
    with pytest.raises(skill_install.SkillError, match="not the AInotate skill"):
        skill_install.install(["agents"])
    assert skill_install.install(["agents"], force=True)[0][1] == "updated"


def test_install_leaves_a_link_to_this_source_alone(homes):
    homes["claude"].mkdir()
    (homes["claude"] / "ainotate").symlink_to(skill_install.source())
    assert skill_install.install(["claude"])[0][1] == "already this install"


def test_install_never_deletes_its_own_source(homes, tmp_path, monkeypatch):
    """A checkout whose skill/ is a link to the installed folder: installing must not touch it."""
    real = homes["agents"] / "ainotate"
    real.parent.mkdir(parents=True)
    import shutil
    shutil.copytree(skill_install.source(), real)
    link = tmp_path / "checkout-skill"
    link.symlink_to(real)
    monkeypatch.setattr(skill_install, "source", lambda: link)
    assert skill_install.install(["agents"])[0][1] == "already this install"
    assert (real / "SKILL.md").is_file()


def test_a_link_to_another_checkout_is_left_alone(homes, tmp_path):
    other = tmp_path / "other" / "skill"
    other.mkdir(parents=True)
    (other / "SKILL.md").write_text("---\nname: ainotate\n---\n")
    homes["claude"].mkdir()
    (homes["claude"] / "ainotate").symlink_to(other)
    assert skill_install.install(["claude"])[0][1].startswith("left alone")
    assert (homes["claude"] / "ainotate").is_symlink()


@pytest.mark.parametrize("head", ["---\nname: ainotate-custom\n---\n", "# name: ainotate\n", "name: ainotate\n"])
def test_only_the_exact_skill_name_counts_as_ours(homes, head):
    d = homes["claude"] / "ainotate"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(head)
    with pytest.raises(skill_install.SkillError):
        skill_install.install(["claude"])
    assert (d / "SKILL.md").read_text() == head


def test_a_failed_copy_keeps_the_old_skill(homes, monkeypatch):
    skill_install.install(["claude"])
    before = (homes["claude"] / "ainotate" / "SKILL.md").read_text()

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(skill_install.shutil, "copytree", boom)
    with pytest.raises(OSError):
        skill_install.install(["claude"])
    assert (homes["claude"] / "ainotate" / "SKILL.md").read_text() == before


def test_zip_has_one_top_level_folder(tmp_path):
    z = skill_install.make_zip(tmp_path / "new folder")       # created, file inside it
    assert z.name == "ainotate-skill.zip" and z.parent.name == "new folder"
    names = zipfile.ZipFile(z).namelist()
    assert "ainotate/SKILL.md" in names and all(n.startswith("ainotate/") for n in names)
    assert not any(n.endswith(".zip") for n in names)


def test_zip_is_never_written_inside_the_skill():
    with pytest.raises(skill_install.SkillError, match="inside the skill"):
        skill_install.make_zip(skill_install.source() / "references")


def test_install_skill_command(homes, capsys):
    assert cli.main(["install-skill", "--target", "claude"]) == 0
    assert "installed:" in capsys.readouterr().out


def test_new_commands_are_listed(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    assert "install-skill" in out and "elements" in out and "doctor" in out


# ---------- doctor: who gets the permission ----------

@pytest.mark.skipif(sys.platform != "darwin", reason="macOS responsible-process API")
@pytest.mark.parametrize("comm, app", [("/Applications/Claude.app/Contents/MacOS/Claude", "Claude"),
                                       ("/Applications/Cursor.app/Contents/Frameworks/Cursor Helper.app/"
                                        "Contents/MacOS/Cursor Helper", "Cursor"),
                                       ("/opt/homebrew/bin/python3.13", "python3.13")])
def test_responsible_app_names_the_bundle(monkeypatch, comm, app):
    from ainotate import doctor
    monkeypatch.setattr(doctor, "_comm", lambda pid: comm)
    assert doctor.responsible_app() == app


# ---------- UI element matching ----------

def el(role, label, x, y, w=40, h=20):
    return {"role": role, "label": label, "rect": {"x": x, "y": y, "w": w, "h": h}}


ELS = [el("AXButton", "Allow", 500, 300), el("AXButton", "Don't Allow", 400, 300),
       el("AXStaticText", "Allow access to Photos", 300, 200, 300), el("AXButton", "Allow", 2500, 300)]
WIN = {"x": 0, "y": 0, "w": 1200, "h": 800}


def test_role_names():
    assert ax._role("button") == "AXButton" and ax._role("menu item") == "AXMenuItem"
    assert ax._role("AXCheckBox") == "AXCheckBox" and ax._role("") == ""


def test_exact_label_wins_inside_the_window():
    assert ax.find(ELS, {"element": "allow"}, within=WIN)["rect"]["x"] == 500


def test_role_and_substring():
    assert ax.find(ELS, {"element": "photos", "role": "statictext"})["rect"]["x"] == 300


@pytest.mark.parametrize("t", [[], {"element": ""}, {"element": None}, {"element": "Save", "nth": "1"},
                               {"element": "Save", "nth": 1.5}, {"element": "Save", "nth": True},
                               {"element": "Save", "colour": "red"}])
def test_malformed_targets_are_spec_errors(t):
    with pytest.raises(ax.TargetSpecError):
        ax.check_target("t", t)
    assert cli.exit_code(ax.TargetSpecError("x")) == 2


def test_null_nth_means_no_nth():
    assert ax.find(ELS, {"element": "Allow", "nth": None}, within=WIN)["rect"]["x"] == 500


def test_typed_text_is_never_a_label():
    a = {"AXRole": "AXTextField", "AXValue": "my secret", "AXPlaceholderValue": "Search"}
    assert ax._label(a) == "Search"
    assert ax._label({"AXRole": "AXStaticText", "AXValue": "Battery"}) == "Battery"


def test_ambiguous_and_missing():
    with pytest.raises(ax.AmbiguousText, match="nth"):
        ax.find(ELS, {"element": "Allow"})                     # two windows, no `within`
    assert ax.find(ELS, {"element": "Allow", "nth": 1})["rect"]["x"] == 2500
    with pytest.raises(ax.TargetNotFound, match="similar"):
        ax.find(ELS, {"element": "Allowance"})
    assert cli.exit_code(ax.AmbiguousText("x")) == 5 and cli.exit_code(ax.TargetNotFound("x")) == 6


def test_window_targets_are_window_relative_with_scale(monkeypatch):
    monkeypatch.setattr(ax, "elements", lambda app, pid=None, window=None: ELS)
    w = {"app": "Photos", "bounds": {"x": 100, "y": 50, "w": 1000, "h": 700}}
    m = ax.window_targets(w, (2000, 1400), {"ok": {"element": "Allow", "role": "button"}})
    assert m["scale"] == 2.0 and m["rects"]["ok"] == {"x": 400.0, "y": 250.0, "w": 40.0, "h": 20.0}


@pytest.mark.skipif(sys.platform != "darwin", reason="NSNull comes from pyobjc Foundation")
def test_missing_attributes_as_nsnull_are_dropped():
    from Foundation import NSNull

    class FakeAS:
        kAXValueAXErrorType = 5

        @staticmethod
        def AXUIElementCopyMultipleAttributeValues(el, attrs, opts, out):
            return 0, ["AXButton", "Save"] + [NSNull.null()] * (len(attrs) - 2)

        @staticmethod
        def AXValueGetType(v):
            raise TypeError("not an AXValue")
    a = ax._read(FakeAS, object())
    assert a == {"AXRole": "AXButton", "AXTitle": "Save"}


@pytest.mark.parametrize("label, ok", [("Allow", True), ("Allow access to Photos", True), ("Always Allow", True),
                                       ("Don't Allow", False), ("Don’t Allow", False), ("Do not allow", False),
                                       ("Disallow", False), ("Allowance", False)])
def test_a_negated_label_never_matches(label, ok):
    assert ax._word_match("Allow", label) is ok


def test_only_dont_allow_is_not_found():
    els = [el("AXButton", "Don't Allow", 400, 300), el("AXButton", "OK", 500, 300)]
    with pytest.raises(ax.TargetNotFound, match="similar: \"Don't Allow\"|similar: 'Don"):
        ax.find(els, {"element": "Allow", "role": "button"})


def test_cut_short_tree_is_reported():
    els = ax.Elements([el("AXButton", "OK", 1, 1)])
    els.truncated = True
    with pytest.raises(ax.TargetNotFound, match="cut short"):
        ax.find(els, {"element": "Save"})


def test_uneven_capture_gets_image_px_and_scale_1(monkeypatch):
    monkeypatch.setattr(ax, "elements", lambda app, pid=None, window=None: ELS)
    w = {"app": "Photos", "bounds": {"x": 100, "y": 50, "w": 1000, "h": 600}}
    m = ax.window_targets(w, (2000, 1160), {"ok": {"element": "Allow", "role": "button"}})
    assert m["scale"] == 1.0
    assert m["rects"]["ok"]["x"] == 800.0 and abs(m["rects"]["ok"]["y"] - 250 * 1160 / 600) < 0.1


def test_skill_copy_skips_links(homes, tmp_path, monkeypatch):
    import shutil
    src = tmp_path / "src"
    shutil.copytree(skill_install.source(), src)
    (tmp_path / "secret.txt").write_text("nope")
    (src / "leak.txt").symlink_to(tmp_path / "secret.txt")
    monkeypatch.setattr(skill_install, "source", lambda: src)
    skill_install.install(["claude"])
    assert not (homes["claude"] / "ainotate" / "leak.txt").exists()
    names = zipfile.ZipFile(skill_install.make_zip(tmp_path / "z")).namelist()
    assert "ainotate/leak.txt" not in names


# ---------- one version everywhere ----------

def test_every_manifest_carries_the_package_version():
    """scripts/bump.py sets these together; a stale one ships a launcher pinned to an old release."""
    import json
    import re
    from pathlib import Path

    import ainotate
    root = Path(__file__).resolve().parents[1]
    v = ainotate.__version__
    assert re.search(rf'^version = "{re.escape(v)}"', (root / "pyproject.toml").read_text(), re.M)
    for name in (".claude-plugin/plugin.json", "gemini-extension.json", "server.json", "packaging/mcpb/manifest.json"):
        text = (root / name).read_text()
        assert json.loads(text)["version"] == v, name
        assert set(re.findall(r"ainotate\[all\]==([^'\"\s]+)", text)) <= {v}, name
    for name in (".claude-plugin/plugin.json", "gemini-extension.json"):
        assert f"ainotate[all]=={v}" in (root / name).read_text(), f"{name}: unpinned uvx"
    assert (root / "skills" / "ainotate" / "SKILL.md").is_file()
