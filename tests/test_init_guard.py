"""Tests for the `xlii init` bulk-upload guard (xlii/cmds/project.py).

Dependency-free — run directly:  ./venv/bin/python tests/test_init_guard.py
(also discoverable by pytest if it's installed).

Pins the behaviour that stops `xlii init` from silently uploading a huge or
sensitive tree (the classic "ran it in ~" mistake):
  * _sensitive_init_target flags home / fs-root / system / personal-container dirs,
  * _count_tracked_files honours ignores, prunes ignored dirs, and is bounded,
  * a normal small project trips neither signal.
"""

import tempfile
from pathlib import Path

from xlii.cmds.project._guards import (
    _LARGE_TREE_FILE_WARN,
    _count_tracked_files,
    _sensitive_init_target,
)


def test_sensitive_targets_flagged():
    home = Path.home()
    assert _sensitive_init_target(home) is not None
    assert _sensitive_init_target(Path("/")) is not None
    assert _sensitive_init_target(Path("/tmp")) is not None
    assert _sensitive_init_target(Path("/etc")) is not None
    assert _sensitive_init_target(home / "Downloads") is not None
    assert _sensitive_init_target(home / "Documents") is not None


def test_normal_project_not_flagged():
    home = Path.home()
    # A plausible project dir under home is not a "container" folder.
    assert _sensitive_init_target(home / "code" / "myproj") is None
    assert _sensitive_init_target(home / "myproj") is None


def test_count_respects_ignores_and_is_bounded():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        # 5 tracked files at top level...
        for i in range(5):
            (root / f"src{i}.txt").write_text("x")
        # ...and a big ignored dir that must NOT be counted or descended into.
        node = root / "node_modules"
        node.mkdir()
        for i in range(100):
            (node / f"dep{i}.js").write_text("x")

        count, hit = _count_tracked_files(root, [], _LARGE_TREE_FILE_WARN)
        assert count == 5, f"expected 5 tracked, got {count}"
        assert hit is False


def test_count_stops_at_cap():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        for i in range(20):
            (root / f"f{i}.txt").write_text("x")
        count, hit = _count_tracked_files(root, [], cap=10)
        assert count == 10
        assert hit is True


def test_count_visit_budget_bounds_huge_ignored_free_tree():
    # Even with no ignores, the visit budget keeps the walk bounded.
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        for i in range(30):
            (root / f"f{i}.txt").write_text("x")
        count, hit = _count_tracked_files(root, [], cap=10_000, max_visits=10)
        assert hit is True
        assert count <= 30


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
