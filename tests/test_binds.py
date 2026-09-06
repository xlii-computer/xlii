"""Task chrome binds — menu / F-key symlinks to /tasks run."""

from __future__ import annotations

from pathlib import Path

import pytest

from xlii import binds as B
from xlii import tasks as T
from xlii.repl_cmds import bind as cmd
from xlii.repl_cmds import register_all


register_all()


class _Console:
    def __init__(self):
        self.lines = []

    def print(self, *a, **k):
        self.lines.append(" ".join(str(x) for x in a))

    @property
    def text(self):
        return "\n".join(self.lines)


def _xli(tmp_path: Path) -> Path:
    d = tmp_path / ".xlii"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ctx(xli: Path):
    from types import SimpleNamespace

    return {
        "console": _Console(),
        "state": SimpleNamespace(project=SimpleNamespace(xli_dir=xli)),
    }


def _write_task(xli: Path, name: str, body: str = '[[step]]\nrun = "true"\n') -> None:
    T.tasks_dir(xli).mkdir(parents=True, exist_ok=True)
    (T.tasks_dir(xli) / f"{name}.toml").write_text(f'name = "{name}"\n{body}')


@pytest.fixture
def cfgdir(tmp_path, monkeypatch):
    d = tmp_path / "cfg"
    d.mkdir()
    monkeypatch.setenv("XLII_CONFIG_DIR", str(d))
    return d


def test_upsert_defaults_to_project_menu():
    rows = B.upsert_bind([], task="echo-hello")
    assert rows == [B.Bind(task="echo-hello", menu="project", origin="user")]


def test_fkey_conflict_clears_previous():
    rows = B.upsert_bind([], task="a", menu="project", fkey="f11")
    rows = B.upsert_bind(rows, task="b", fkey="f11")
    by = {b.task: b for b in rows}
    assert by["a"].fkey == "" and by["a"].menu == "project"
    assert by["b"].fkey == "f11"


def test_remove_by_task_and_fkey():
    rows = [
        B.Bind(task="a", menu="project", fkey="f11"),
        B.Bind(task="b", menu="tools"),
    ]
    kept, n = B.remove_binds(rows, "f11")
    assert n == 1 and kept[0].task == "b"
    kept, n = B.remove_binds(rows, "b")
    assert n == 1 and kept[0].task == "a"


def test_remove_by_label_case_insensitive():
    rows = [B.Bind(task="bump-note", menu="project", label="yo")]
    kept, n = B.remove_binds(rows, "YO")
    assert n == 1 and kept == []


def test_remove_bare_token_only_when_one():
    one = [B.Bind(task="bump-note", menu="project", label="yo")]
    kept, n = B.remove_binds(one, "")
    assert n == 1 and kept == []
    two = [
        B.Bind(task="a", menu="project"),
        B.Bind(task="b", menu="tools"),
    ]
    kept, n = B.remove_binds(two, "")
    assert n == 0 and len(kept) == 2


def test_user_fkey_wins_over_project(tmp_path, cfgdir):
    B.save_user_binds([B.Bind(task="user-t", fkey="f11", menu="tools")])
    xli = _xli(tmp_path)
    B.save_project_binds(xli, [B.Bind(task="proj-t", fkey="f11", menu="project",
                                      origin="project")])
    loaded = B.load_binds(xli)
    assert [b.task for b in loaded] == ["user-t", "proj-t"]
    assert B.fkey_bind(loaded, "f11").task == "user-t"
    assert not any(b.task == "proj-t" and b.fkey == "f11" for b in loaded)


def test_run_line_seeds_required_params(tmp_path):
    xli = _xli(tmp_path)
    _write_task(xli, "need", """
[params.q]
required = true
[[step]]
run = "echo {{q}}"
""")
    assert B.bind_run_line("need", xli) == "/tasks run need "
    _write_task(xli, "plain")
    assert B.bind_run_line("plain", xli) == "/tasks run plain --yes"


def test_bind_command_writes_user_file(tmp_path, cfgdir):
    xli = _xli(tmp_path)
    _write_task(xli, "echo-hello")
    ctx = _ctx(xli)
    cmd._cmd_bind("/bind echo-hello fkey=f12 menu=tools", ctx)
    assert "bound" in ctx["console"].text
    loaded = B.load_binds(xli)
    assert loaded[0].task == "echo-hello"
    assert loaded[0].fkey == "f12"
    assert loaded[0].menu == "tools"


def test_bind_rm_label_and_bare(tmp_path, cfgdir):
    xli = _xli(tmp_path)
    _write_task(xli, "bump-note")
    ctx = _ctx(xli)
    cmd._cmd_bind("/bind bump-note label=yo", ctx)
    assert B.load_binds(xli)[0].label == "yo"
    ctx = _ctx(xli)
    cmd._cmd_bind("/bind rm yo", ctx)
    assert B.load_binds(xli) == []
    cmd._cmd_bind("/bind bump-note label=yo", _ctx(xli))
    ctx = _ctx(xli)
    cmd._cmd_bind("/bind rm", ctx)
    assert "removed" in ctx["console"].text
    assert B.load_binds(xli) == []


def test_bind_unknown_task_refused(tmp_path, cfgdir):
    xli = _xli(tmp_path)
    ctx = _ctx(xli)
    cmd._cmd_bind("/bind nope", ctx)
    assert "no saved task" in ctx["console"].text
    assert B.load_binds(xli) == []


def test_bind_refuses_save_when_user_binds_corrupt(tmp_path, cfgdir):
    """Corrupt binds.toml must not be overwritten by a save."""
    xli = _xli(tmp_path)
    _write_task(xli, "echo-hello")
    path = B.user_binds_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[[bind]]\ntask = "keep-me"\nmenu = "project"\n[[bind]]\nBAD')
    ctx = _ctx(xli)
    cmd._cmd_bind("/bind echo-hello fkey=f12", ctx)
    assert "corrupt" in ctx["console"].text.lower()
    assert "keep-me" in path.read_text()
    assert B.load_binds(xli) == []


def test_bind_rm_refuses_save_when_user_binds_corrupt(tmp_path, cfgdir):
    path = B.user_binds_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[[bind]]\ntask = "keep-me"\nmenu = "project"\n[[bind]]\nBAD')
    ctx = _ctx(_xli(tmp_path))
    cmd._cmd_bind("/bind rm keep-me", ctx)
    assert "corrupt" in ctx["console"].text.lower()
    assert "keep-me" in path.read_text()


def test_fkey_hints_overlay():
    hints = B.fkey_hints([B.Bind(task="echo-hello", fkey="f11", label="hi")])
    assert ("F11", "hi") in hints
    assert hints[0] == ("F1", "help")


def test_save_user_binds_escapes_label_quotes(tmp_path, cfgdir):
    """A label containing double quotes must not corrupt binds.toml — an
    unescaped quote used to make tomllib reject the file and load_binds()
    returned [] (all chrome binds silently lost)."""
    label = 'say "go"'
    B.save_user_binds([B.Bind(task="plain", menu="project", label=label)])
    loaded = B.load_binds()
    assert len(loaded) == 1
    assert loaded[0].label == label


def test_chrome_rows_shape(tmp_path, cfgdir):
    xli = _xli(tmp_path)
    _write_task(xli, "plain")
    B.save_user_binds([B.Bind(task="plain", menu="project")])
    rows = B.chrome_rows(xli)
    assert rows[0]["task"] == "plain"
    assert rows[0]["line"] == "/tasks run plain --yes"
    assert rows[0]["menu"] == "project"


def test_slash_gate_allows_bind_in_chat(tmp_path):
    from types import SimpleNamespace

    from xlii.commands import dispatch_repl_command

    ctx = {
        "state": None,
        "agent": SimpleNamespace(session=SimpleNamespace()),
        "persona": object(),
        "command_scope": "chat",
        "console": _Console(),
    }
    assert dispatch_repl_command("/bind list", ctx) is True
    assert "not available in the chat REPL" not in ctx["console"].text
