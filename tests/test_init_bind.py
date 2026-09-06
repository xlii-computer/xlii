"""RP4 — `xlii init --id` binding, `xlii chat --tui`, and the base persona.

All disk-only / monkeypatched — no network, no real ~/.config, no terminal.
Run with `python -m pytest`.
"""

import argparse
import json
from types import SimpleNamespace

import pytest

import xlii.persona


def _write_local_project(root, name="t"):
    (root / ".xlii").mkdir(parents=True, exist_ok=True)
    (root / ".xlii" / "project.json").write_text(json.dumps({
        "name": name, "collection_id": "", "created_at": "2026-01-01T00:00:00Z",
        "conversation_id": "c", "local_only": True, "root": str(root.resolve()),
    }))


# --------------------------------------------------------------------------- #
#  xlii init --id  (bind at init + rebind in place)
# --------------------------------------------------------------------------- #

def _init_args(tmp_path, **kw):
    base = dict(path=str(tmp_path), name="t", id=None, local=True, sync=False,
                force=False, collection_id=None, snapshot=False, yes=True)
    base.update(kw)
    return argparse.Namespace(**base)


def test_init_id_binds_project(tmp_path, monkeypatch):
    monkeypatch.setattr(xlii.persona, "PERSONAS_DIR", tmp_path / "personas")
    from xlii.cmds.project import cmd_init
    rc = cmd_init(_init_args(tmp_path, id="bob"))
    assert rc == 0
    data = json.loads((tmp_path / ".xlii" / "project.json").read_text())
    assert data["bound_persona"] == "bob"
    assert xlii.persona.Persona("bob").exists()        # auto-created the prompt file


def test_init_id_rebinds_existing_without_reinit(tmp_path, monkeypatch):
    monkeypatch.setattr(xlii.persona, "PERSONAS_DIR", tmp_path / "personas")
    _write_local_project(tmp_path)                      # already-initialized, unbound
    import xlii.cmds.project.init as proj_mod
    monkeypatch.setattr(proj_mod, "init_project",
                        lambda *a, **k: pytest.fail("rebind must not reinit the project"))
    rc = proj_mod.cmd_init(_init_args(tmp_path, id="bob"))
    assert rc == 0
    data = json.loads((tmp_path / ".xlii" / "project.json").read_text())
    assert data["bound_persona"] == "bob"
    assert data["collection_id"] == ""                  # non-destructive: collection untouched


def test_init_no_id_defaults_to_default_persona(tmp_path, monkeypatch):
    # RP7 / Vector C: every project records a chat-persona preference; no --id →
    # the shipped iXaac companion (so bare /chat is deterministic). It's a CHAT
    # default only — not a code bind.
    monkeypatch.setattr(xlii.persona, "PERSONAS_DIR", tmp_path / "personas")
    from xlii.cmds.project import cmd_init
    cmd_init(_init_args(tmp_path))                       # no --id
    data = json.loads((tmp_path / ".xlii" / "project.json").read_text())
    assert data["bound_persona"] == xlii.persona.DEFAULT_PERSONA_ID
    assert xlii.persona.Persona(xlii.persona.DEFAULT_PERSONA_ID).exists()  # ensured at init


def test_init_force_preserves_existing_chat_persona(tmp_path, monkeypatch):
    # RP7: `xlii init --force` with no --id must NOT silently reset a custom chat
    # persona back to `default` (it falls through past the rebind early-return).
    monkeypatch.setattr(xlii.persona, "PERSONAS_DIR", tmp_path / "personas")
    from xlii.cmds.project import cmd_init
    cmd_init(_init_args(tmp_path, id="bob"))                # bind to bob
    cmd_init(_init_args(tmp_path, force=True))             # forced reinit, no --id
    data = json.loads((tmp_path / ".xlii" / "project.json").read_text())
    assert data["bound_persona"] == "bob"                  # preserved, not clobbered


def test_init_invalid_id_refuses_to_bind(tmp_path, monkeypatch):
    monkeypatch.setattr(xlii.persona, "PERSONAS_DIR", tmp_path / "personas")
    from xlii.cmds.project import cmd_init
    rc = cmd_init(_init_args(tmp_path, id="../evil"))     # traversal-shaped name
    assert rc == 1                                        # fail fast, before any init
    assert not (tmp_path / ".xlii" / "project.json").exists()  # nothing initialized


def test_bound_code_does_not_apply_persona_loadout(tmp_path, monkeypatch):
    # RP7: code is isolated from chat. `xlii code` on a project whose chat-persona
    # is bob must NOT inherit bob's loadout (model/docs/refs) — code keeps its own.
    # (bound_persona is a chat default only; code never reads it.)
    from xlii.cmds import sessions as S
    from xlii.cmds.sessions import code as sessions_code
    import argparse

    monkeypatch.setattr(xlii.persona, "PERSONAS_DIR", tmp_path / "personas")
    (tmp_path / "personas").mkdir(parents=True, exist_ok=True)
    # A persona that pins a model the configured cfg knows (no warning path).
    model = __import__("xlii.config", fromlist=["GlobalConfig"]).GlobalConfig.load().orchestrator()
    (tmp_path / "personas" / "bob.md").write_text(f"---\nmodel: {model}\n---\nYou are bob.")

    code_root = tmp_path / "repo"
    _write_local_project(code_root, name="repo")
    data = json.loads((code_root / ".xlii" / "project.json").read_text())
    data["bound_persona"] = "bob"
    (code_root / ".xlii" / "project.json").write_text(json.dumps(data))

    for _k in ("XLII_SESSION", "XLII_SESSION_PROJECT", "XLII_SHELL_STYLE"):
        monkeypatch.setenv(_k, "")

    class _FakePool:
        @classmethod
        def from_config(cls, cfg, *, require_management=True):
            return _FakePool()

        def primary(self):
            return None

    import xlii.session_boot as session_boot
    monkeypatch.setattr(session_boot, "ClientPool", _FakePool)
    captured = {}
    monkeypatch.setattr(sessions_code, "run_repl_loop", lambda state, **k: captured.update(state=state))

    args = argparse.Namespace(target=str(code_root), yolo=False, rail=False,
                              no_sync=True, tui=False, force=False)
    S.cmd_code(args)
    assert captured["state"].agent.model_override is None    # bob's loadout NOT applied to code


# --------------------------------------------------------------------------- #
#  base persona at install
# --------------------------------------------------------------------------- #

def test_ensure_default_persona_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(xlii.persona, "PERSONAS_DIR", tmp_path / "personas")
    # Isolate the persona PROJECT base too, not just the prompt dir — otherwise
    # `project_root` resolves to the real ~/.xlii/chat/default and the lazy-project
    # assertion below fails on any machine where that dir exists (e.g. left behind
    # by other tests that exercise the default persona's turn store).
    monkeypatch.setattr(xlii.persona, "CHAT_STATE_DIR", tmp_path / "chat")
    p = xlii.persona.ensure_default_persona()
    assert xlii.persona.Persona(xlii.persona.DEFAULT_PERSONA_ID).exists()
    xlii.persona.ensure_default_persona()               # idempotent — must not raise
    assert not p.project_root.exists()                  # prompt-file only; project stays lazy


# --------------------------------------------------------------------------- #
#  xlii chat --tui  (the TUI hosts the chat profile)
# --------------------------------------------------------------------------- #

def test_chat_tui_launches_with_chat_profile(tmp_path, monkeypatch):
    from xlii.cmds.sessions import chat as sessions_chat
    import xlii.tui_textual

    persona_root = tmp_path / "bob"
    _write_local_project(persona_root, name="chat/bob")
    (persona_root / "turns").mkdir(parents=True, exist_ok=True)
    persona = SimpleNamespace(
        name="bob", project_root=persona_root, turns_dir=persona_root / "turns",
        system_prompt=lambda: "BOB", loadout=lambda: {},
        touch_used=lambda: None, collection_id=lambda: None,
    )

    class _FakePool:
        @classmethod
        def from_config(cls, cfg, *, require_management=True):
            return _FakePool()

        def primary(self):
            return None

    # The --tui branch + _mark_session_active write these env vars directly.
    # Use setenv (NOT delenv — delenv on an absent key registers no teardown),
    # so monkeypatch reliably reverts whatever the code sets (no cross-test leak).
    for _k in ("XLII_SESSION", "XLII_SESSION_PROJECT", "XLII_SHELL_STYLE"):
        monkeypatch.setenv(_k, "")
    monkeypatch.setattr(sessions_chat, "_resolve_persona_to_load", lambda req: persona)
    import xlii.session_boot as session_boot
    monkeypatch.setattr(session_boot, "ClientPool", _FakePool)
    monkeypatch.setattr(session_boot, "startup_sync", lambda *a, **k: SimpleNamespace(status="ok", stats=SimpleNamespace(summary=lambda: "")))
    monkeypatch.setattr(session_boot, "init_project", lambda *a, **k: pytest.fail("persona project pre-exists"))

    captured = {}

    def fake_launch(*, project_name, agent, run_turn, state):
        captured["project_name"] = project_name
        captured["mode"] = state.profile.mode
        return 0

    monkeypatch.setattr(xlii.tui_textual, "launch", fake_launch)

    rc = sessions_chat._chat_run_session("bob", yolo=False, tui=True)
    assert rc == 0
    assert captured["mode"] == "chat"          # the TUI hosts the chat profile
    assert captured["project_name"] == "bob"   # clean persona name, not "chat/bob"


def _chat_tui_env(tmp_path, monkeypatch):
    """Disk-only chat-persona scaffold for the --tui drop-back tests: a local
    project with network/sync/pool stubbed out. Returns the chat submodule."""
    from xlii.cmds.sessions import chat as sessions_chat

    persona_root = tmp_path / "bob"
    _write_local_project(persona_root, name="chat/bob")
    (persona_root / "turns").mkdir(parents=True, exist_ok=True)
    persona = SimpleNamespace(
        name="bob", project_root=persona_root, turns_dir=persona_root / "turns",
        system_prompt=lambda: "BOB", loadout=lambda: {},
        touch_used=lambda: None, collection_id=lambda: None,
    )

    class _FakePool:
        @classmethod
        def from_config(cls, cfg, *, require_management=True):
            return _FakePool()

        def primary(self):
            return None

    for _k in ("XLII_SESSION", "XLII_SESSION_PROJECT", "XLII_SHELL_STYLE"):
        monkeypatch.setenv(_k, "")
    monkeypatch.setattr(sessions_chat, "_resolve_persona_to_load", lambda req: persona)
    import xlii.session_boot as session_boot
    monkeypatch.setattr(session_boot, "ClientPool", _FakePool)
    monkeypatch.setattr(session_boot, "startup_sync", lambda *a, **k: SimpleNamespace(status="ok", stats=SimpleNamespace(summary=lambda: "")))
    monkeypatch.setattr(session_boot, "init_project", lambda *a, **k: pytest.fail("persona project pre-exists"))
    return sessions_chat


def test_chat_tui_terminal_dropback_enters_inline_loop(tmp_path, monkeypatch):
    # Bug fix: launching straight into --tui and typing /terminal must drop to the
    # inline REPL, not end the process. The TUI returns WITHOUT quit_requested, so
    # the session falls through to the inline run_repl_loop.
    import xlii.tui_textual
    sessions_chat = _chat_tui_env(tmp_path, monkeypatch)
    monkeypatch.setattr(xlii.tui_textual, "run_tui_over_session",
                        lambda state, agent, *, project_name: None)  # /terminal: no quit
    entered = {}
    monkeypatch.setattr(sessions_chat, "run_repl_loop", lambda state, **k: entered.update(yes=True))
    rc = sessions_chat._chat_run_session("bob", yolo=False, tui=True)
    assert entered.get("yes") is True
    assert rc == 0


def test_chat_tui_quit_does_not_enter_inline_loop(tmp_path, monkeypatch):
    # Counterpart: /exit·/quit in the TUI set quit_requested, so the process ends
    # right after the TUI — the inline loop is NOT entered.
    import xlii.tui_textual
    sessions_chat = _chat_tui_env(tmp_path, monkeypatch)

    def _quit(state, agent, *, project_name):
        state.quit_requested = True
    monkeypatch.setattr(xlii.tui_textual, "run_tui_over_session", _quit)
    entered = {}
    monkeypatch.setattr(sessions_chat, "run_repl_loop", lambda state, **k: entered.update(yes=True))
    rc = sessions_chat._chat_run_session("bob", yolo=False, tui=True)
    assert entered.get("yes") is None
    assert rc == 0
