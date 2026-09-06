"""Project browser P2: headless /browse command.

Uses a minimal fake state (quacking like REPLState) so the views run without a
live session, network, or xAI account — mirrors tests/test_howto.py.
"""

import io
import shutil
import subprocess

import pytest
from rich.console import Console

from xlii.commands import dispatch_repl_command, find_repl_command
from xlii.repl_cmds import browse, register_all

register_all()


class _Project:
    def __init__(self, root):
        self.project_root = root
        self.name = root.name


class _State:
    """Quacks like the bits of REPLState that /browse touches."""

    def __init__(self, root):
        self.project = _Project(root)
        self.attached: list[str] = []

    def attach_file(self, path, once=False):
        self.attached.append(path)
        return {"name": path.rsplit("/", 1)[-1], "kind": "text"}


def _run(root, line, state=None):
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=200)
    ctx = {"console": console, "state": state if state is not None else _State(root),
           "command_scope": "code"}
    handled = browse._cmd_browse(line, ctx)
    return handled, buf.getvalue()


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True, text=True)


_needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def test_registered_code_only():
    assert find_repl_command("/browse", "code") is not None
    assert find_repl_command("/browse", "chat") is None


def test_no_project_is_graceful():
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    handled = browse._cmd_browse("/browse", {"console": console, "command_scope": "code"})
    assert handled is True
    assert "needs a project" in buf.getvalue()


def test_skeleton_renders_fingerprint_and_zones(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text("x = 1\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_m.py").write_text("\n")

    handled, out = _run(tmp_path, "/browse")
    assert handled is True
    assert "python" in out
    assert "src/" in out
    assert "tests/" in out
    # browse is the producer: the P0 profile cache is written on first view.
    assert (tmp_path / ".xlii" / "project-profile.json").is_file()


def test_tree_honors_ignore_spec(tmp_path):
    (tmp_path / "keep.py").write_text("x = 1\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("//\n")

    handled, out = _run(tmp_path, "/browse --tree")
    assert "keep.py" in out
    assert "node_modules" not in out  # pruned by the ignore spec


def test_tree_does_not_follow_symlinked_dirs(tmp_path):
    import os

    (tmp_path / "real.py").write_text("x = 1\n")
    secret = tmp_path.parent / (tmp_path.name + "_secret")
    secret.mkdir(exist_ok=True)
    (secret / "passwd.txt").write_text("leak\n")
    os.symlink(secret, tmp_path / "link")

    _, out = _run(tmp_path, "/browse --tree")
    assert "real.py" in out
    assert "passwd.txt" not in out  # out-of-tree content via the symlink is NOT disclosed
    assert "link@" in out           # symlink shown as a leaf, not descended


def test_attach_locks_in_repo_file_and_jails_escapes(tmp_path):
    (tmp_path / "in.txt").write_text("ok\n")

    state = _State(tmp_path)
    _run(tmp_path, "/browse in.txt --attach", state)
    assert state.attached and state.attached[0].endswith("in.txt")

    state2 = _State(tmp_path)
    _, out = _run(tmp_path, "/browse ../escape.txt --attach", state2)
    assert state2.attached == []
    assert "outside" in out.lower() or "refused" in out.lower()


def test_refresh_profile_writes_cache(tmp_path):
    (tmp_path / "go.mod").write_text("module example.test\n")
    handled, out = _run(tmp_path, "/browse --refresh-profile")
    assert handled is True
    assert (tmp_path / ".xlii" / "project-profile.json").is_file()
    assert "go" in out


def test_reference_action_queues_pending_input(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 2\n")
    state = _State(tmp_path)
    _run(tmp_path, "/browse a.py b.py --reference", state)
    assert getattr(state, "pending_input", "") == "a.py b.py"


def test_edit_action_opens_editor_jailed(tmp_path, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr("xlii.editor.open_for_edit", lambda p: opened.append(str(p)) or 0)
    (tmp_path / "a.py").write_text("x = 1\n")

    _run(tmp_path, "/browse a.py --edit")
    assert len(opened) == 1 and opened[0].endswith("a.py")

    opened.clear()
    _, out = _run(tmp_path, "/browse ../escape.py --edit")
    assert opened == []  # jail blocks the escape before $EDITOR is touched
    assert "outside" in out.lower() or "refused" in out.lower()


def test_actions_survive_symlinked_project_root(tmp_path):
    import os

    real = tmp_path / "real"
    real.mkdir()
    (real / "a.py").write_text("x = 1\n")
    link = tmp_path / "link"
    os.symlink(real, link)

    # project_root stored as the symlink (unresolved) — the shape ProjectConfig uses.
    state = _State(link)
    _run(link, "/browse a.py --reference", state)  # ValueError pre-fix
    assert getattr(state, "pending_input", "") == "a.py"

    handled, out = _run(link, "/browse --tree")  # tree view must not crash either
    assert handled is True and "a.py" in out


def test_apply_action_attach_from_manifest_abs_path(tmp_path):
    """The popup hands _apply_action absolute paths; they still jail + attach."""
    f = tmp_path / "f.txt"
    f.write_text("hi\n")
    state = _State(tmp_path)
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    browse._apply_action(console, {"state": state}, tmp_path, "attach", [str(f)])
    assert state.attached and state.attached[0].endswith("f.txt")


def test_dispatch_routes_browse_like_tui(tmp_path):
    """/browse resolves through dispatch_repl_command — the same path the TUI's
    _slash uses (P6 parity-by-construction), not just a direct handler call."""
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    handled = dispatch_repl_command(
        "/browse", {"console": console, "state": _State(tmp_path), "command_scope": "code"}
    )
    assert handled is True
    assert "python" in buf.getvalue()


def test_skeleton_shows_packages_for_monorepo(tmp_path):
    (tmp_path / "apps" / "web").mkdir(parents=True)
    (tmp_path / "apps" / "web" / "package.json").write_text("{}")
    (tmp_path / "services" / "api").mkdir(parents=True)
    (tmp_path / "services" / "api" / "go.mod").write_text("module example.test/api\n")

    _, out = _run(tmp_path, "/browse")
    assert "packages" in out
    assert "apps/web/" in out


@_needs_git
def test_diff_view_shows_stat(tmp_path):
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "a.txt").write_text("1\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "init")
    (tmp_path / "a.txt").write_text("1\n2\n3\n")

    _, out = _run(tmp_path, "/browse --diff")
    assert "a.txt" in out


@_needs_git
def test_changed_lists_dirty_files(tmp_path):
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "a.txt").write_text("1\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "init")
    (tmp_path / "a.txt").write_text("2\n")   # modified
    (tmp_path / "new.txt").write_text("n\n")  # untracked

    handled, out = _run(tmp_path, "/browse --changed")
    assert handled is True
    assert "a.txt" in out
    assert "new.txt" in out
