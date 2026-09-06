"""Tests for `xlii ls` / `xlii cat` — client #1 of the addressing + VFS layer."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from xlii.addressing import Address
from xlii.cmds.vfs import _same_vfs_target, cmd_cat, cmd_ls


def _write_project_config(root: Path) -> None:
    xlii = root / ".xlii"
    xlii.mkdir()
    (xlii / "project.json").write_text(
        json.dumps(
            {
                "name": "proj",
                "root": str(root.resolve()),
                "collection_id": "c1",
                "created_at": "now",
            }
        )
    )


def test_ls_dir_ok(tmp_path):
    (tmp_path / "a").write_text("x")
    (tmp_path / "d").mkdir()
    assert cmd_ls(Namespace(address=f"file://{tmp_path}")) == 0


def test_ls_unsupported_scheme_returns_1():
    assert cmd_ls(Namespace(address="nope://x")) == 1  # no provider for this scheme


def test_ls_no_scheme_returns_1():
    assert cmd_ls(Namespace(address="bare_name_no_scheme")) == 1


def test_cat_writes_bytes(tmp_path, capfdbinary):
    (tmp_path / "f").write_text("hello")
    rc = cmd_cat(Namespace(address=f"file://{tmp_path}/f"))
    assert rc == 0
    assert capfdbinary.readouterr().out == b"hello"


def test_cat_directory_errors(tmp_path):
    assert cmd_cat(Namespace(address=f"file://{tmp_path}")) == 1


def test_cp_file_to_file(tmp_path):
    from xlii.cmds.vfs import cmd_cp

    (tmp_path / "src").write_text("hello")
    rc = cmd_cp(Namespace(src=f"file://{tmp_path}/src", dst=f"file://{tmp_path}/dst", force=False))
    assert rc == 0
    assert (tmp_path / "dst").read_bytes() == b"hello"


def test_cp_refuses_overwrite_without_force(tmp_path):
    from xlii.cmds.vfs import cmd_cp

    (tmp_path / "src").write_text("a")
    (tmp_path / "dst").write_text("EXISTING")
    a = Namespace(src=f"file://{tmp_path}/src", dst=f"file://{tmp_path}/dst", force=False)
    assert cmd_cp(a) == 1
    assert (tmp_path / "dst").read_text() == "EXISTING"  # untouched
    b = Namespace(src=f"file://{tmp_path}/src", dst=f"file://{tmp_path}/dst", force=True)
    assert cmd_cp(b) == 0
    assert (tmp_path / "dst").read_bytes() == b"a"


def test_cp_unwritable_dst_returns_1(tmp_path):
    from xlii.cmds.vfs import cmd_cp

    (tmp_path / "src").write_text("x")
    assert cmd_cp(Namespace(src=f"file://{tmp_path}/src", dst="conv://./t.md", force=True)) == 1


def test_cp_cross_root_conv_to_file(tmp_path):
    import os

    from xlii.cmds.vfs import cmd_cp

    turns = tmp_path / ".xlii" / "turns"
    turns.mkdir(parents=True)
    (turns / "t.md").write_text("# turn")
    out = tmp_path / "backup.md"
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        rc = cmd_cp(Namespace(src="conv://./t.md", dst=f"file://{out}", force=False))
        assert rc == 0
        assert out.read_bytes() == b"# turn"
    finally:
        os.chdir(cwd)


def test_mkdir_and_rm(tmp_path):
    from xlii.cmds.vfs import cmd_mkdir, cmd_rm

    d = f"file://{tmp_path}/d"
    assert cmd_mkdir(Namespace(address=d)) == 0
    assert (tmp_path / "d").is_dir()
    # rm refuses without --yes
    assert cmd_rm(Namespace(address=d, yes=False, recursive=False)) == 1
    assert (tmp_path / "d").is_dir()
    assert cmd_rm(Namespace(address=d, yes=True, recursive=False)) == 0
    assert not (tmp_path / "d").exists()


def test_mv_file(tmp_path):
    from xlii.cmds.vfs import cmd_mv

    (tmp_path / "src").write_text("hi")
    rc = cmd_mv(Namespace(src=f"file://{tmp_path}/src", dst=f"file://{tmp_path}/dst", force=False))
    assert rc == 0
    assert not (tmp_path / "src").exists()
    assert (tmp_path / "dst").read_bytes() == b"hi"


def test_mv_same_file_with_force_does_not_delete_source(tmp_path):
    from xlii.cmds.vfs import cmd_mv

    src = tmp_path / "src"
    src.write_text("keep")
    rc = cmd_mv(Namespace(src=f"file://{src}", dst=f"file://{src}", force=True))
    assert rc == 0
    assert src.read_text() == "keep"


def test_mv_same_project_file_alias_with_force_does_not_delete_source(tmp_path, monkeypatch):
    from xlii.cmds.vfs import cmd_mv

    _write_project_config(tmp_path)
    src = tmp_path / "src"
    src.write_text("keep")
    monkeypatch.chdir(tmp_path)

    rc = cmd_mv(Namespace(src="project://./src", dst=f"file://{src}", force=True))

    assert rc == 0
    assert src.read_text() == "keep"


def test_same_vfs_target_ignores_anchor_and_query_for_non_local_providers():
    src = Address.parse("wiki://page?view=one#section-a")
    dst = Address.parse("wiki://page?view=two#section-b")
    assert _same_vfs_target(src, dst) is True


def test_mv_from_readonly_scheme_refused(tmp_path):
    from xlii.cmds.vfs import cmd_mv

    # conv:// is read-only — can't move *from* it (cp is the way out)
    assert cmd_mv(Namespace(src="conv://./x.md", dst=f"file://{tmp_path}/out", force=False)) == 1


def test_stat_ok(tmp_path):
    from xlii.cmds.vfs import cmd_stat

    (tmp_path / "f").write_text("xy")
    assert cmd_stat(Namespace(address=f"file://{tmp_path}/f")) == 0
