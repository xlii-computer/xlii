"""/alias — promote a saved task into a slash command (task-args P2)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xlii import tasks as T
from xlii.commands import find_repl_command, unregister_repl_command
from xlii.repl_cmds import alias as A
from xlii.repl_cmds import register_all

register_all()  # builtins present so the shadow guard has something to see


class _Console:
    def __init__(self):
        self.lines = []

    def print(self, *a, **k):
        self.lines.append(" ".join(str(x) for x in a))

    @property
    def text(self):
        return "\n".join(self.lines)


def _ctx(xli):
    return {"console": _Console(),
            "state": SimpleNamespace(project=SimpleNamespace(xli_dir=xli))}


def _xli(tmp_path):
    d = tmp_path / ".xlii"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_task(xli, name, body='[[step]]\nrun="printf hi"\n'):
    T.tasks_dir(xli).mkdir(parents=True, exist_ok=True)
    (T.tasks_dir(xli) / f"{name}.toml").write_text(f'name="{name}"\n{body}')


@pytest.fixture(autouse=True)
def _cleanup_aliases():
    yield
    # only drop project-sourced test aliases — never nuke a builtin (e.g. /git)
    for n in ("myt", "grep-explain", "ghost"):
        cmd = find_repl_command("/" + n, "code")
        if cmd is not None and cmd.source == "project":
            unregister_repl_command(n)


def test_alias_command_registered():
    assert find_repl_command("/alias", "code") is not None
    assert find_repl_command("/alias", "chat") is not None


def test_create_registers_and_persists(tmp_path):
    xli = _xli(tmp_path)
    _write_task(xli, "myt")
    A._cmd_alias("/alias myt", _ctx(xli))
    cmd = find_repl_command("/myt", "code")
    assert cmd is not None and cmd.source == "project"
    assert A._read_aliases(xli) == [{"name": "myt", "task": "myt"}]


def test_from_task_flag_form(tmp_path):
    xli = _xli(tmp_path)
    _write_task(xli, "myt")
    A._cmd_alias("/alias --from-task myt", _ctx(xli))
    assert find_repl_command("/myt", "code") is not None


def test_create_unknown_task_refused(tmp_path):
    xli = _xli(tmp_path)
    ctx = _ctx(xli)
    A._cmd_alias("/alias nope", ctx)
    assert find_repl_command("/nope", "code") is None
    assert A._read_aliases(xli) == []
    assert "no saved task" in ctx["console"].text


def test_create_refuses_builtin_shadow(tmp_path):
    xli = _xli(tmp_path)
    _write_task(xli, "git")  # task name collides with the /git builtin
    ctx = _ctx(xli)
    A._cmd_alias("/alias git", ctx)
    assert find_repl_command("/git", "code").source == "builtin"  # builtin untouched
    assert A._read_aliases(xli) == []
    assert "builtin" in ctx["console"].text


def test_rm_removes(tmp_path):
    xli = _xli(tmp_path)
    _write_task(xli, "myt")
    A._cmd_alias("/alias myt", _ctx(xli))
    A._cmd_alias("/alias rm myt", _ctx(xli))
    assert find_repl_command("/myt", "code") is None
    assert A._read_aliases(xli) == []


def test_list_shows_aliases(tmp_path):
    xli = _xli(tmp_path)
    _write_task(xli, "myt")
    A._cmd_alias("/alias myt", _ctx(xli))
    ctx = _ctx(xli)
    A._cmd_alias("/alias list", ctx)
    assert "/myt" in ctx["console"].text


def test_load_task_aliases_registers_and_skips_missing(tmp_path):
    xli = _xli(tmp_path)
    _write_task(xli, "myt")
    A._write_aliases(xli, [{"name": "myt", "task": "myt"},
                           {"name": "ghost", "task": "ghost"}])
    A.load_task_aliases(xli)
    assert find_repl_command("/myt", "code") is not None    # task exists
    assert find_repl_command("/ghost", "code") is None       # task gone → skipped


def test_handler_forwards_args_to_do_run(tmp_path, monkeypatch):
    xli = _xli(tmp_path)
    _write_task(xli, "myt")
    A._cmd_alias("/alias myt", _ctx(xli))
    calls = []
    import xlii.repl_cmds.tasks as tasks_mod
    monkeypatch.setattr(tasks_mod, "_do_run", lambda rest, ctx: calls.append(rest))
    find_repl_command("/myt", "code").handler("/myt needle scope=src/", _ctx(xli))
    assert calls == ["myt needle scope=src/"]
