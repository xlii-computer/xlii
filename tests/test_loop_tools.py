"""Loop test-file lock enforcement (L2)."""

from __future__ import annotations

from xlii.tools import ToolContext, t_edit_file, t_write_file
from tests.helpers import make_agent


def _ctx(tmp_path, *, lock: bool) -> ToolContext:
    ag = make_agent(tmp_path)
    return ToolContext(
        project=ag.project,
        clients=ag.clients,
        cfg=ag.cfg,
        loop_lock_tests=lock,
    )


def test_write_test_file_refused_when_locked(tmp_path):
    ctx = _ctx(tmp_path, lock=True)
    res = t_write_file(ctx, {"path": "tests/test_x.py", "content": "x = 1\n"})
    assert res.is_error
    assert "locked" in res.content.lower()


def test_write_test_file_allowed_when_unlocked(tmp_path):
    ctx = _ctx(tmp_path, lock=False)
    res = t_write_file(ctx, {"path": "tests/test_x.py", "content": "x = 1\n"})
    assert not res.is_error
    assert (tmp_path / "tests" / "test_x.py").exists()


def test_edit_test_file_refused_when_locked(tmp_path):
    path = tmp_path / "tests" / "test_y.py"
    path.parent.mkdir(parents=True)
    path.write_text("old\n")
    ctx = _ctx(tmp_path, lock=True)
    res = t_edit_file(
        ctx,
        {"path": "tests/test_y.py", "old_string": "old", "new_string": "new"},
    )
    assert res.is_error


def test_edit_src_allowed_when_locked(tmp_path):
    path = tmp_path / "src" / "main.py"
    path.parent.mkdir(parents=True)
    path.write_text("old\n")
    ctx = _ctx(tmp_path, lock=True)
    res = t_edit_file(
        ctx,
        {"path": "src/main.py", "old_string": "old", "new_string": "new"},
    )
    assert not res.is_error
