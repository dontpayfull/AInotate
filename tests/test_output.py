import re
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from ainotate import output
from ainotate.output import load_config, save, slug

IMG = Image.new("RGB", (40, 30), (10, 200, 30))


def test_defaults_without_config(isolated_env):
    cfg = load_config()
    assert cfg.output_dir == isolated_env / "Pictures" / "AInotate"
    assert cfg.backup_dir is None and cfg.prefix == "AInotate"


def test_config_file_then_env_override(isolated_env, monkeypatch, tmp_path):
    conf = isolated_env / ".config" / "ainotate"
    conf.mkdir(parents=True)
    (conf / "config.toml").write_text('output_dir = "~/Shots"\nbackup_dir = "~/Shots/Backup"\nprefix = "Shot"\n')
    cfg = load_config()
    assert (cfg.output_dir, cfg.backup_dir, cfg.prefix) == (
        isolated_env / "Shots", isolated_env / "Shots" / "Backup", "Shot")
    monkeypatch.setenv("AINOTATE_OUTPUT_DIR", str(tmp_path / "envout"))
    monkeypatch.setenv("AINOTATE_PREFIX", "Env")
    cfg = load_config()
    assert (cfg.output_dir, cfg.backup_dir, cfg.prefix) == (tmp_path / "envout", isolated_env / "Shots" / "Backup", "Env")


def test_bad_config_file(isolated_env):
    conf = isolated_env / ".config" / "ainotate"
    conf.mkdir(parents=True)
    (conf / "config.toml").write_text("output_dir = \n")
    with pytest.raises(output.OutputError):
        load_config()


def test_save_with_backup(out_dirs):
    out, bak = out_dirs
    paths = save(IMG, "checkout bug")
    assert [p.parent for p in paths] == [out, bak]
    assert re.fullmatch(r"AInotate \d{4}-\d\d-\d\d at \d\d\.\d\d\.\d\d checkout bug\.png", paths[0].name)
    assert paths[0].name == paths[1].name and paths[0].read_bytes() == paths[1].read_bytes()
    assert Image.open(paths[0]).size == (40, 30)


def test_save_without_backup(tmp_path, monkeypatch):
    monkeypatch.setenv("AINOTATE_OUTPUT_DIR", str(tmp_path / "o"))
    monkeypatch.setenv("AINOTATE_PREFIX", "Pfx")
    paths = save(IMG)
    assert len(paths) == 1 and paths[0].name.startswith("Pfx ") and not paths[0].name.endswith(" .png")


class FrozenDT(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 7, 12, 0, 0)


def test_never_overwrites_and_suffix_is_shared(out_dirs, monkeypatch):
    out, bak = out_dirs
    monkeypatch.setattr(output, "datetime", FrozenDT)
    warns = []
    first = save(IMG, "x", on_warning=warns.append)
    second = save(Image.new("RGB", (5, 5)), "x", on_warning=warns.append)
    assert first[0].name == "AInotate 2026-10-07 at 12.00.00 x.png"
    assert second[0].name == second[1].name == "AInotate 2026-10-07 at 12.00.00 x (2).png"
    assert Image.open(first[0]).size == (40, 30)          # the first file is untouched
    assert len(warns) == 1 and "saved as (2)" in warns[0]
    # a name taken only in the backup folder is skipped too
    (bak / "AInotate 2026-10-07 at 12.00.00 x (3).png").write_bytes(b"old")
    third = save(IMG, "x", on_warning=warns.append)
    assert third[0].name.endswith(" x (4).png")
    assert (bak / "AInotate 2026-10-07 at 12.00.00 x (3).png").read_bytes() == b"old"


def test_warning_goes_to_stderr_by_default(out_dirs, monkeypatch, capsys):
    monkeypatch.setattr(output, "datetime", FrozenDT)
    save(IMG, "y")
    save(IMG, "y")
    err = capsys.readouterr().err
    assert err.startswith("WARN: AInotate 2026-10-07 at 12.00.00 y.png already existed; saved as (2)")


def test_draft_goes_to_temp(out_dirs):
    out, bak = out_dirs
    paths = save(IMG, "draft", draft=True)
    assert len(paths) == 1 and paths[0].is_file() and not out.exists()


def test_slug_blocks_paths():
    assert slug("../../etc/passwd") == "------etc-passwd"
    assert slug("Coș / plată: ok") == "Coș - plată- ok"
    assert len(slug("a" * 100)) == 60 and slug(None) == ""
    assert Path(slug("a/b")).name == "a-b"


@pytest.mark.parametrize("prefix", ["../up", "a/b", "a\\b", "..", ".", "bad\x07bell", "new\nline"])
def test_bad_prefix_is_a_config_error(out_dirs, monkeypatch, prefix):
    monkeypatch.setenv("AINOTATE_PREFIX", prefix)
    with pytest.raises(output.OutputError, match="single file-name component"):
        save(IMG, "x")
    assert not out_dirs[0].exists() or not any(out_dirs[0].iterdir())


def test_bad_prefix_in_config_file(isolated_env):
    conf = isolated_env / ".config" / "ainotate"
    conf.mkdir(parents=True)
    (conf / "config.toml").write_text('prefix = "../../escape"\n')
    with pytest.raises(output.OutputError, match="prefix"):
        load_config()


def test_good_prefixes_pass(monkeypatch):
    for p in ("Shot", "My.Shots v2", "Captură"):
        monkeypatch.setenv("AINOTATE_PREFIX", p)
        assert load_config().prefix == p


def test_backup_name_is_reserved_too(out_dirs, monkeypatch):
    """Both destinations are created exclusively: a backup file that appears between the
    check and the copy is never overwritten, and no half-reserved main file is left behind."""
    out, bak = out_dirs
    monkeypatch.setattr(output, "datetime", FrozenDT)
    bak.mkdir(parents=True)
    taken = bak / "AInotate 2026-10-07 at 12.00.00 x.png"
    taken.write_bytes(b"someone else")
    real_open = open
    opened = []

    def spy(path, mode="r", *a, **kw):
        opened.append((Path(path).parent.name, mode))
        return real_open(path, mode, *a, **kw)
    monkeypatch.setattr("builtins.open", spy)
    paths = save(IMG, "x")
    monkeypatch.setattr("builtins.open", real_open)
    assert [p.name for p in paths] == ["AInotate 2026-10-07 at 12.00.00 x (2).png"] * 2
    assert taken.read_bytes() == b"someone else"
    assert not (out / "AInotate 2026-10-07 at 12.00.00 x.png").exists()      # released, not leaked
    assert ("bak", "x+b") in opened and all(m == "x+b" for d, m in opened if d in ("out", "bak"))


def test_concurrent_saves_never_share_a_name(out_dirs, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    out, bak = out_dirs
    monkeypatch.setattr(output, "datetime", FrozenDT)
    imgs = [Image.new("RGB", (10 + i, 10), (i, 0, 0)) for i in range(12)]
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(lambda im: save(im, "race", on_warning=lambda m: None), imgs))
    names = [r[0].name for r in results]
    assert len(set(names)) == 12
    for (o, b), im in zip(results, imgs):
        assert o.read_bytes() == b.read_bytes() and Image.open(o).size == im.size
    assert len(list(out.iterdir())) == len(list(bak.iterdir())) == 12


def test_failed_write_leaves_nothing(out_dirs, monkeypatch):
    out, bak = out_dirs

    class Broken:
        def save(self, *a, **k):
            raise OSError("disk full")
    with pytest.raises(OSError):
        save(Broken(), "x")
    assert not any(out.iterdir()) and not any(bak.iterdir())
