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
    assert skill_install.install(["claude"])[0][1] == "already linked to this install"


def test_zip_has_one_top_level_folder(tmp_path):
    z = skill_install.make_zip(tmp_path)
    names = zipfile.ZipFile(z).namelist()
    assert "ainotate/SKILL.md" in names and all(n.startswith("ainotate/") for n in names)


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


def test_ambiguous_and_missing():
    with pytest.raises(ax.AmbiguousText, match="nth"):
        ax.find(ELS, {"element": "Allow"})                     # two windows, no `within`
    assert ax.find(ELS, {"element": "Allow", "nth": 1})["rect"]["x"] == 2500
    with pytest.raises(ax.TargetNotFound, match="similar"):
        ax.find(ELS, {"element": "Allowance"})
    assert cli.exit_code(ax.AmbiguousText("x")) == 5 and cli.exit_code(ax.TargetNotFound("x")) == 6


def test_window_targets_are_window_relative_with_scale(monkeypatch):
    monkeypatch.setattr(ax, "elements", lambda app: ELS)
    w = {"app": "Photos", "bounds": {"x": 100, "y": 50, "w": 1000, "h": 700}}
    m = ax.window_targets(w, (2000, 1400), {"ok": {"element": "Allow", "role": "button"}})
    assert m["scale"] == 2.0 and m["rects"]["ok"] == {"x": 400.0, "y": 250.0, "w": 40.0, "h": 20.0}
