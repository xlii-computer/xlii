"""A3 — `xlii project rm` (+ `/project rm`): remove a project's Collection(s),
registry entry, and local `.xlii/` — and NOTHING else.

The load-bearing invariant: removal never touches your source files. It also
tears down the journal Collection (Theme B coupling) and sweeps orphan journal
Collections left by older/aborted sessions (OQ3), all behind confirm + --dry-run.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

from xlii.cmds.project._collections import _orphan_journal_collections
from xlii.cmds.project import (
    _resolve_rm_target,
    _run_project_rm,
    cmd_project_rm,
)
from xlii.config import ProjectConfig
from xlii.registry import Registry
from xlii.sync import init_project
from tests.helpers import FakeConsole


# --------------------------------------------------------------------------- #
#  fake xAI Collections client (no network)
# --------------------------------------------------------------------------- #

class _FakeCollections:
    def __init__(self, cloud=None, docs=None, fail_delete=None):
        # cloud: list[(id, name)] currently in the cloud; docs: {id: n_docs}
        self._cloud = list(cloud or [])
        self._docs = dict(docs or {})
        self._fail_delete = set(fail_delete or [])
        self.deleted: list[str] = []

    def list(self, limit=500, pagination_token=None):
        cols = [SimpleNamespace(collection_id=i, collection_name=n) for i, n in self._cloud]
        return SimpleNamespace(collections=cols, pagination_token=None)

    def list_documents(self, collection_id, limit=500, pagination_token=None):
        n = self._docs.get(collection_id, 0)
        return SimpleNamespace(documents=[SimpleNamespace() for _ in range(n)],
                               pagination_token=None)

    def delete(self, collection_id):
        self.deleted.append(collection_id)
        if collection_id in self._fail_delete:
            raise RuntimeError("simulated delete failure")
        self._cloud = [(i, n) for i, n in self._cloud if i != collection_id]


def _clients(cloud=None, docs=None, fail_delete=None):
    return SimpleNamespace(xai=SimpleNamespace(collections=_FakeCollections(cloud, docs, fail_delete)))


def _make_project(root: Path, *, name="proj", cid="c1", jid=None) -> ProjectConfig:
    """A registered, synced project with an optional journal Collection id, plus a
    real source file that removal must never delete."""
    init_project(None, root, name=name, existing_collection_id=cid)
    proj = ProjectConfig.load(root)
    assert proj is not None
    if jid:
        proj.journal_collection_id = jid
        proj.save()
        proj = ProjectConfig.load(root)
    (root / "main.py").write_text("print('hello')\n")  # source — must survive
    return proj


# --------------------------------------------------------------------------- #
#  the core: full teardown, never touching source
# --------------------------------------------------------------------------- #

def test_rm_deletes_collections_and_local_but_never_source(tmp_path):
    proj = _make_project(tmp_path, name="alpha", cid="cid-alpha", jid="jid-alpha")
    clients = _clients(cloud=[("cid-alpha", "xlii/alpha"),
                              ("jid-alpha", "xlii-journal/alpha")],
                       docs={"cid-alpha": 3})
    console = FakeConsole()

    rc = _run_project_rm(clients, proj, keep_local=False, local_only=False,
                         dry_run=False, assume_yes=True, console=console)
    assert rc == 0
    # both Collections deleted
    assert set(clients.xai.collections.deleted) == {"cid-alpha", "jid-alpha"}
    # local .xlii/ gone, source file untouched
    assert not (tmp_path / ".xlii").exists()
    assert (tmp_path / "main.py").read_text() == "print('hello')\n"
    # registry entry gone
    assert Registry.load().find_by_path(tmp_path) is None


def test_rm_dry_run_changes_nothing(tmp_path):
    proj = _make_project(tmp_path, name="beta", cid="cid-beta", jid="jid-beta")
    clients = _clients(cloud=[("cid-beta", "xlii/beta"), ("jid-beta", "xlii-journal/beta")])
    console = FakeConsole()

    rc = _run_project_rm(clients, proj, keep_local=False, local_only=False,
                         dry_run=True, assume_yes=True, console=console)
    assert rc == 0
    assert clients.xai.collections.deleted == []        # nothing deleted
    assert (tmp_path / ".xlii").exists()                 # local kept
    assert Registry.load().find_by_path(tmp_path) is not None
    assert any("dry-run" in line for line in console.lines)


def test_rm_keep_local_keeps_the_xlii_tree(tmp_path):
    proj = _make_project(tmp_path, name="gamma", cid="cid-gamma")
    clients = _clients(cloud=[("cid-gamma", "xlii/gamma")])
    console = FakeConsole()

    _run_project_rm(clients, proj, keep_local=True, local_only=False,
                    dry_run=False, assume_yes=True, console=console)
    assert clients.xai.collections.deleted == ["cid-gamma"]   # cloud deleted
    assert (tmp_path / ".xlii").exists()                       # local kept
    assert Registry.load().find_by_path(tmp_path) is None      # registry pruned


def test_rm_local_only_skips_the_cloud(tmp_path):
    proj = _make_project(tmp_path, name="delta", cid="cid-delta")
    console = FakeConsole()

    # local_only → clients is None; cloud Collection is left in place.
    rc = _run_project_rm(None, proj, keep_local=False, local_only=True,
                         dry_run=False, assume_yes=True, console=console)
    assert rc == 0
    assert not (tmp_path / ".xlii").exists()                   # local removed
    assert (tmp_path / "main.py").exists()                     # source kept
    assert Registry.load().find_by_path(tmp_path) is None      # registry pruned


def test_rm_keeps_local_state_when_collection_delete_fails(tmp_path):
    proj = _make_project(tmp_path, name="rho", cid="cid-rho")
    clients = _clients(cloud=[("cid-rho", "xlii/rho")], fail_delete={"cid-rho"})
    console = FakeConsole()

    rc = _run_project_rm(clients, proj, keep_local=False, local_only=False,
                         dry_run=False, assume_yes=True, console=console)

    assert rc == 1
    assert clients.xai.collections.deleted == ["cid-rho"]
    assert (tmp_path / ".xlii").exists()
    assert (tmp_path / "main.py").read_text() == "print('hello')\n"
    assert Registry.load().find_by_path(tmp_path) is not None
    assert any("kept local .xlii" in line for line in console.lines)


def test_rm_aborts_on_no_confirmation(tmp_path, monkeypatch):
    proj = _make_project(tmp_path, name="eps", cid="cid-eps")
    clients = _clients(cloud=[("cid-eps", "xlii/eps")])
    console = FakeConsole()
    monkeypatch.setattr("xlii.cmds.project.rm.confirm", lambda *a, **k: False)

    rc = _run_project_rm(clients, proj, keep_local=False, local_only=False,
                         dry_run=False, assume_yes=False, console=console)
    assert rc == 1
    assert clients.xai.collections.deleted == []        # confirm said no
    assert (tmp_path / ".xlii").exists()
    assert any("aborted" in line for line in console.lines)


def test_rm_unbinds_persona_without_deleting_it(tmp_path):
    proj = _make_project(tmp_path, name="zeta", cid="cid-zeta")
    proj.bound_persona = "alice"
    proj.save()
    proj = ProjectConfig.load(tmp_path)
    clients = _clients(cloud=[("cid-zeta", "xlii/zeta")])

    # keep-local so the unbind is persisted to project.json (and observable).
    _run_project_rm(clients, proj, keep_local=True, local_only=False,
                    dry_run=False, assume_yes=True, console=FakeConsole())
    reloaded = ProjectConfig.load(tmp_path)
    assert reloaded.bound_persona is None
    # the persona's own files are out of scope here — rm never deletes a persona.


# --------------------------------------------------------------------------- #
#  orphan journal sweep (OQ3)
# --------------------------------------------------------------------------- #

def test_orphan_journal_unit_detects_unclaimed_only():
    cloud = {
        "cid-main": "xlii/proj",                # main collection — never an orphan
        "jid-live": "xlii-journal/proj",        # claimed (in exclude)
        "jid-dead": "xlii-journal/proj",        # unclaimed, same project → orphan
        "jid-other": "xlii-journal/other",      # different project — never swept
    }
    reg = Registry()  # empty registry → nothing claimed beyond exclude
    orphans = _orphan_journal_collections(
        cloud, reg, exclude={"jid-live"}, project_name="proj",
    )
    assert orphans == [("jid-dead", "xlii-journal/proj")]


def test_rm_sweeps_orphan_journal_collections(tmp_path):
    proj = _make_project(tmp_path, name="eta", cid="cid-eta", jid="jid-eta")
    clients = _clients(cloud=[
        ("cid-eta", "xlii/eta"),
        ("jid-eta", "xlii-journal/eta"),
        ("orphan-1", "xlii-journal/eta"),   # left behind for this project, unclaimed
        ("other-1", "xlii-journal/other"),  # another project — must not be swept
    ])
    _run_project_rm(clients, proj, keep_local=False, local_only=False,
                    dry_run=False, assume_yes=True, console=FakeConsole())
    assert set(clients.xai.collections.deleted) == {"cid-eta", "jid-eta", "orphan-1"}


# --------------------------------------------------------------------------- #
#  resolution + CLI wiring
# --------------------------------------------------------------------------- #

def test_resolve_rm_target_by_dot_path_and_name(tmp_path, monkeypatch):
    _make_project(tmp_path, name="theta", cid="cid-theta")
    # path form
    by_path = _resolve_rm_target(str(tmp_path))
    assert by_path is not None and by_path.name == "theta"
    # "." form (cwd)
    monkeypatch.chdir(tmp_path)
    by_dot = _resolve_rm_target(".")
    assert by_dot is not None and by_dot.name == "theta"
    # name form (registry lookup)
    by_name = _resolve_rm_target("theta")
    assert by_name is not None and by_name.name == "theta"
    # unknown
    assert _resolve_rm_target("nope-not-a-project-xyz") is None


def test_resolve_rm_target_prefers_registry_name_over_cwd_directory(tmp_path, monkeypatch):
    registered = tmp_path / "registered" / "backend"
    registered.mkdir(parents=True)
    _make_project(registered, name="backend", cid="cid-registered")
    colliding_cwd = tmp_path / "cwd"
    colliding_project = colliding_cwd / "backend"
    colliding_project.mkdir(parents=True)
    _make_project(colliding_project, name="backend-local", cid="cid-other")

    monkeypatch.chdir(colliding_cwd)

    resolved = _resolve_rm_target("backend")

    assert resolved is not None
    assert resolved.name == "backend"
    assert resolved.project_root == registered.resolve()


def test_cmd_project_rm_rejects_mutually_exclusive_flags(tmp_path):
    args = argparse.Namespace(name=str(tmp_path), yes=True, dry_run=False,
                              keep_local=True, local_only=True)
    assert cmd_project_rm(args) == 1   # --keep-local + --local-only is an error


# --------------------------------------------------------------------------- #
#  /project rm slash command
# --------------------------------------------------------------------------- #

def test_project_slash_is_code_only():
    from xlii.commands import find_repl_command
    from xlii.repl_cmds import register_all
    register_all()
    assert find_repl_command("/project", "code") is not None
    assert find_repl_command("/project", "chat") is None


def _slash_ctx(project, console):
    return {
        "console": console,
        "state": SimpleNamespace(project=project, quit_requested=False),
        "cfg": SimpleNamespace(),
    }


def test_project_slash_dry_run_lists_without_deleting(tmp_path):
    from xlii.repl_cmds.code import _project_handler
    proj = _make_project(tmp_path, name="iota", cid="cid-iota")
    console = FakeConsole()
    # --local-only avoids needing real clients; --dry-run takes no action.
    assert _project_handler("/project rm . --local-only --dry-run",
                            _slash_ctx(proj, console)) is True
    assert (tmp_path / ".xlii").exists()
    assert any("project rm:" in line for line in console.lines)
    assert any("dry-run" in line for line in console.lines)


def test_project_slash_usage_and_unknown_flag(tmp_path, monkeypatch):
    from xlii.repl_cmds.code import _project_handler
    proj = _make_project(tmp_path, name="kappa", cid="cid-kappa")
    monkeypatch.setattr(
        "xlii.repl_cmds.code.Registry.load",
        lambda: __import__("xlii.registry", fromlist=["Registry"]).Registry(entries=[]),
    )
    # bare /project → registry list (empty)
    c1 = FakeConsole()
    _project_handler("/project", _slash_ctx(proj, c1))
    assert any("no registered projects" in line for line in c1.lines)
    # unknown flag → warned, no action
    c2 = FakeConsole()
    _project_handler("/project rm . --bogus", _slash_ctx(proj, c2))
    assert any("unknown flag" in line for line in c2.lines)


def test_project_slash_warns_when_removing_current_project(tmp_path):
    from xlii.repl_cmds.code import _project_handler
    proj = _make_project(tmp_path, name="lam", cid="cid-lam")
    console = FakeConsole()
    _project_handler("/project rm . --local-only --dry-run", _slash_ctx(proj, console))
    assert any("CURRENT project" in line for line in console.lines)


def test_project_slash_rm_current_quits_when_local_removed(tmp_path, monkeypatch):
    """Removing the live project tears down .xlii/ — the REPL must end cleanly."""
    from xlii.repl_cmds.code import _project_handler

    proj = _make_project(tmp_path, name="mu", cid="cid-mu")
    ctx = _slash_ctx(proj, FakeConsole())
    monkeypatch.setattr(
        "xlii.cmds.project._run_project_rm",
        lambda *a, **k: 0,
    )

    _project_handler("/project rm . --local-only --yes", ctx)
    assert ctx["state"].quit_requested is True


def test_project_slash_rm_current_save_does_not_recreate_local_state(tmp_path):
    """Exit/slash save hooks must not resurrect .xlii/ after removing it."""
    from xlii.repl import REPLState
    from xlii.repl_cmds.code import _project_handler
    from tests.helpers import make_agent

    proj = _make_project(tmp_path, name="munu", cid="cid-munu")
    agent = make_agent(tmp_path)
    state = REPLState(
        console=FakeConsole(), agent=agent, project=proj,
        cfg=agent.cfg, pool=agent.pool,
    )
    ctx = {"console": FakeConsole(), "state": state, "cfg": SimpleNamespace()}

    _project_handler("/project rm . --local-only --yes", ctx)

    assert state.quit_requested is True
    assert state._project_removed_locally is True
    assert not (tmp_path / ".xlii").exists()
    state.save()
    assert not (tmp_path / ".xlii").exists()


def test_project_slash_rm_dry_run_does_not_quit(tmp_path, monkeypatch):
    from xlii.repl_cmds.code import _project_handler

    proj = _make_project(tmp_path, name="nu", cid="cid-nu")
    ctx = _slash_ctx(proj, FakeConsole())
    monkeypatch.setattr(
        "xlii.cmds.project._run_project_rm",
        lambda *a, **k: 0,
    )

    _project_handler("/project rm . --local-only --dry-run --yes", ctx)
    assert ctx["state"].quit_requested is False


def test_project_slash_rm_keep_local_does_not_quit(tmp_path, monkeypatch):
    from xlii.repl_cmds.code import _project_handler

    proj = _make_project(tmp_path, name="xi", cid="cid-xi")
    ctx = _slash_ctx(proj, FakeConsole())
    monkeypatch.setattr(
        "xlii.cmds.project._run_project_rm",
        lambda *a, **k: 0,
    )
    monkeypatch.setattr(
        "xlii.client.Clients.from_config",
        lambda cfg: _clients(cloud=[("cid-xi", "xlii/xi")]),
    )

    _project_handler("/project rm . --keep-local --yes", ctx)
    assert ctx["state"].quit_requested is False


def test_project_slash_rm_current_exits_repl_loop(tmp_path, monkeypatch):
    """quit_requested from /project rm unwinds the inline loop via _QuitSession."""
    import pytest

    from xlii.repl import REPLState, _QuitSession, run_repl_loop
    from xlii.repl_cmds.code import _project_handler
    from tests.helpers import make_agent
    from tests.test_always_quit import _ScriptedSession

    proj = _make_project(tmp_path, name="omicron", cid="cid-omicron")
    agent = make_agent(tmp_path)
    state = REPLState(
        console=FakeConsole(), agent=agent, project=proj,
        cfg=agent.cfg, pool=agent.pool,
    )
    state.command_scope = "code"
    monkeypatch.setattr(
        "xlii.cmds.project._run_project_rm",
        lambda *a, **k: 0,
    )

    def _rm_current(line, ctx):
        return _project_handler("/project rm . --local-only --yes", ctx)

    from xlii.commands import REPLCommand, register_repl_command, unregister_repl_command
    register_repl_command(REPLCommand(name="_rmquit", handler=_rm_current, repls=["code"]))
    try:
        session = _ScriptedSession(["/_rmquit"])
        with pytest.raises(_QuitSession):
            run_repl_loop(
                state, session=session,
                get_prompt_prefix=lambda: "p› ",
                run_turn=lambda *a, **k: ("", set(), None),
            )
        assert state.quit_requested is True
        assert any("bye" in line for line in state.console.lines)
    finally:
        unregister_repl_command("_rmquit")
