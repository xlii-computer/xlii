"""V3b — chat is the safe REPL: the mode_contract capability profile actually
GATES the chat surface (not just describes it).

Pins:
  * agent turn setup strips write tools + dispatch from the chat palette
    (project-blind default) — a hostile paste can only make the agent talk;
  * /chat --read-proj widens the session to the read-only palette (awareness
    = read), never to writers/bash;
  * non-chat surfaces are untouched (the code palette keeps everything);
  * the slash-command dispatch refuses non-conversation verbs in chat.
"""

from __future__ import annotations

from tests.helpers import FakeConsole, make_agent, make_msg


def _capture_schemas(agent):
    """Capture the tool schemas agent.run_turn advertises to the model."""
    captured = {}

    def fake(*args, **kwargs):
        captured.update(kwargs)
        return (make_msg("ok", None), None, False)

    agent._stream_orchestrator_iteration = fake
    return captured


def _names(captured) -> set[str]:
    return {s["function"]["name"] for s in captured["schemas"]}


def test_chat_palette_is_project_blind_by_default(tmp_path):
    agent = make_agent(tmp_path)
    agent.session.conversational = True
    captured = _capture_schemas(agent)
    agent.run_turn("hello")
    names = _names(captured)
    # own-memory recall + external reads stay…
    assert "search_project" in names
    assert "web_search" in names
    assert "xai_docs" in names
    # research assistant tools (network/local chromium, not repo writers)
    assert "browser" in names
    assert "plugin_call" in names
    # …but nothing that can touch the repo, run code, or delegate:
    assert "write_file" not in names
    assert "edit_file" not in names
    assert "read_file" not in names
    assert "grep" not in names
    assert "bash" not in names
    assert "code_execute" not in names
    assert "codex_run_task" not in names
    assert "dispatch_subagent" not in names
    assert "send_email" not in names
    # No inbox configured → mail tools stay off the palette (a guaranteed
    # fail used to eat the whole chat iteration cap).
    assert "read_email" not in names
    assert "search_email" not in names


def test_chat_offers_mail_read_only_when_an_account_exists(tmp_path):
    from tests.helpers import make_cfg

    cfg = make_cfg()
    cfg.email_accounts = {"personal": {"imap_host": "imap.test"}}
    agent = make_agent(tmp_path, cfg=cfg)
    agent.session.conversational = True
    captured = _capture_schemas(agent)
    agent.run_turn("hello")
    names = _names(captured)
    assert "read_email" in names
    assert "search_email" in names
    assert "send_email" not in names


def test_chat_read_proj_widens_to_read_only_palette(tmp_path):
    agent = make_agent(tmp_path)
    agent.session.conversational = True
    agent.session.chat_read_proj = True          # /chat --read-proj
    captured = _capture_schemas(agent)
    agent.run_turn("hello")
    names = _names(captured)
    # the discovery shape: read-only project awareness…
    assert "read_file" in names
    assert "grep" in names
    assert "map" in names
    assert "search_project" in names
    assert "xai_docs" in names
    # …still never writers, a shell, or an autonomous coding agent. codex_run_task
    # is a workspace-write agent whose guard keys on a read-only *controller*
    # (ctx.plan_mode), which chat never sets — so it must NOT ride --read-proj in,
    # or a hostile paste could invoke a file-writer in a mode sold as read-only.
    assert "write_file" not in names
    assert "edit_file" not in names
    assert "bash" not in names
    assert "dispatch_subagent" not in names
    assert "codex_run_task" not in names


def test_code_palette_is_untouched(tmp_path):
    # V3b constrains CHAT only — a code (non-conversational) turn keeps the
    # full default palette including writers and dispatch.
    agent = make_agent(tmp_path)
    agent.session.conversational = False
    captured = _capture_schemas(agent)
    agent.run_turn("hello")
    names = _names(captured)
    assert "write_file" in names
    assert "bash" in names
    assert "dispatch_subagent" in names


def test_chat_persona_footer_does_not_promise_write_tools():
    """Talk was claiming write_file/bash while CHAT_BLIND strips them — the
    model then 'did something' with web_search (including looking up a home
    address from the journal). Footer must match the live palette."""
    from xlii.persona import TOOL_FOOTER

    low = TOOL_FOOTER.lower()
    assert "you also have" not in low or "write_file" not in TOOL_FOOTER.split("You do")[0].lower()
    assert "do **not** have write_file" in TOOL_FOOTER
    assert "xai_docs" in TOOL_FOOTER
    assert "standard file ops" not in low
    assert "[$]" in TOOL_FOOTER
    assert "home" in low or "street" in low or "gps" in low


# --------------------------------------------------------------------------- #
#  The slash surface: dispatch refuses non-conversation verbs in chat
# --------------------------------------------------------------------------- #

def _chat_ctx(tmp_path):
    agent = make_agent(tmp_path)
    agent.session.conversational = True
    state = agent.session
    return {
        "state": None,
        "agent": agent,
        "session": state,
        "persona": object(),          # persona-present ⇒ chat scope
        "command_scope": "chat",
        "console": FakeConsole(),
    }


def test_slash_gate_refuses_sync_in_chat(tmp_path):
    from xlii.commands import dispatch_repl_command
    from xlii.repl_cmds import register_all

    register_all()
    ctx = _chat_ctx(tmp_path)
    assert dispatch_repl_command("/sync", ctx) is True
    assert any("not available in the chat REPL" in ln for ln in ctx["console"].lines)


def test_slash_gate_refuses_yolo_and_config_in_chat(tmp_path):
    from xlii.commands import dispatch_repl_command
    from xlii.repl_cmds import register_all

    register_all()
    ctx = _chat_ctx(tmp_path)
    assert dispatch_repl_command("/yolo", ctx) is True
    assert dispatch_repl_command("/config", ctx) is True
    denied = [ln for ln in ctx["console"].lines if "not available in the chat REPL" in ln]
    assert len(denied) == 2          # both refused, neither handler ran


def test_slash_gate_allows_plugin_and_get_in_chat(tmp_path):
    from xlii.commands import dispatch_repl_command
    from xlii.repl_cmds import register_all

    register_all()
    ctx = _chat_ctx(tmp_path)
    assert dispatch_repl_command("/plugin", ctx) is True
    assert dispatch_repl_command("/get", ctx) is True
    assert not any("not available in the chat REPL" in ln for ln in ctx["console"].lines)


def test_slash_gate_plan_is_gateway_not_self_start(tmp_path):
    from xlii.commands import dispatch_repl_command
    from xlii.repl_cmds import register_all

    register_all()
    ctx = _chat_ctx(tmp_path)
    agent = ctx["agent"]
    assert dispatch_repl_command("/plan", ctx) is True
    blob = "\n".join(ctx["console"].lines)
    assert "lab mode" in blob
    assert "plan mode ON" not in blob
    assert not bool(getattr(agent, "plan_mode", False))


def test_slash_gate_allows_clear_in_chat(tmp_path):
    from xlii.commands import dispatch_repl_command
    from xlii.repl_cmds import register_all

    register_all()
    ctx = _chat_ctx(tmp_path)
    assert dispatch_repl_command("/clear", ctx) is True
    assert dispatch_repl_command("/cls", ctx) is True
    assert dispatch_repl_command("/clear-screen", ctx) is True
    assert not any("not available in the chat REPL" in ln for ln in ctx["console"].lines)


def test_slash_gate_allows_tasks_in_chat(tmp_path):
    from xlii.commands import dispatch_repl_command
    from xlii.repl_cmds import register_all

    register_all()
    ctx = _chat_ctx(tmp_path)
    assert dispatch_repl_command("/tasks list", ctx) is True
    assert not any("not available in the chat REPL" in ln for ln in ctx["console"].lines)


def test_slash_gate_allows_conversation_verbs(tmp_path, monkeypatch):
    # /chat itself is a conversation verb — the gate must let it through to the
    # handler (here: a stub persona resolution + switch).
    import xlii.cmds.sessions as sessions
    from types import SimpleNamespace

    from xlii.commands import dispatch_repl_command
    from xlii.repl_cmds import register_all

    register_all()
    ctx = _chat_ctx(tmp_path)
    persona = SimpleNamespace(
        name="bob",
        project_root=tmp_path / "bobproj",
        system_prompt=lambda: "BOB",
        loadout=lambda: {},
        touch_used=lambda: None,
        collection_id=lambda: None,
    )
    monkeypatch.setattr(sessions, "_resolve_persona_to_load", lambda req: persona)
    monkeypatch.setattr(
        "xlii.repl_cmds.switch.switch_to_persona", lambda c, p: True
    )
    assert dispatch_repl_command("/chat --id bob", ctx) is True
    assert not any("not available in the chat REPL" in ln for ln in ctx["console"].lines)


# --------------------------------------------------------------------------- #
#  /edit is narrowed in chat — persona/doc facets stay, repo-write facets go
# --------------------------------------------------------------------------- #

def test_edit_repo_write_facets_denied_in_chat(tmp_path, monkeypatch):
    import xlii.repl_cmds.chat as chatmod
    from xlii.commands import dispatch_repl_command
    from xlii.repl_cmds import register_all

    register_all()
    called: list[str] = []
    monkeypatch.setattr(chatmod, "_edit_file", lambda *a, **k: called.append("file") or True)
    monkeypatch.setattr(chatmod, "_edit_plugin", lambda *a, **k: called.append("plugin") or True)
    monkeypatch.setattr(chatmod, "_edit_doc", lambda *a, **k: called.append("doc") or True)

    # --file / --plugin (repo/filesystem writes) are denied at the facet gate,
    # never reaching the sub-handler.
    ctx = _chat_ctx(tmp_path)
    assert dispatch_repl_command("/edit --file /etc/passwd", ctx) is True
    assert dispatch_repl_command("/edit --plugin some-id", ctx) is True
    assert called == []
    denied = [ln for ln in ctx["console"].lines if "not available in the chat REPL" in ln]
    assert len(denied) == 2

    # --doc (your own store) passes the facet gate through to the handler.
    ctx2 = _chat_ctx(tmp_path)
    assert dispatch_repl_command("/edit --doc notes", ctx2) is True
    assert called == ["doc"]
    assert not any("not available in the chat REPL" in ln for ln in ctx2["console"].lines)


def test_edit_repo_write_facet_untouched_outside_chat(tmp_path, monkeypatch):
    # The facet gate is scope-bound: in the full (code) surface /edit --file works.
    import xlii.repl_cmds.chat as chatmod

    called: list[str] = []
    monkeypatch.setattr(chatmod, "_edit_file", lambda *a, **k: called.append("file") or True)
    ctx = {"console": FakeConsole(), "command_scope": "code", "persona": None, "state": None}
    assert chatmod._edit_handler("/edit --file foo.py", ctx) is True
    assert called == ["file"]
    assert not any("not available in the chat REPL" in ln for ln in ctx["console"].lines)


# --------------------------------------------------------------------------- #
#  /chat --read-proj — the awareness flag
# --------------------------------------------------------------------------- #

def test_parse_chat_args():
    from xlii.repl_cmds.switch import _parse_chat_args

    assert _parse_chat_args("/chat") == (None, False)
    assert _parse_chat_args("/chat bob") == ("bob", False)
    assert _parse_chat_args("/chat --id bob") == ("bob", False)
    assert _parse_chat_args("/chat --read-proj") == (None, True)
    assert _parse_chat_args("/chat bob --read-proj") == ("bob", True)
    assert _parse_chat_args("/chat --read-proj --id bob") == ("bob", True)


def test_h_chat_arms_and_rearms_awareness(tmp_path, monkeypatch):
    import xlii.cmds.sessions as sessions
    from types import SimpleNamespace

    from xlii.repl_cmds.switch import h_chat

    agent = make_agent(tmp_path)
    state = SimpleNamespace(
        agent=agent, profile=None, persona=None, project=agent.project,
    )
    ctx = {
        "state": state,
        "agent": agent,
        "persona": None,
        "project": agent.project,
        "console": FakeConsole(),
        "command_scope": "chat",
    }
    persona = type("P", (), {
        "name": "bob",
        "project_root": tmp_path / "bobproj",
        "system_prompt": lambda self: "BOB",
        "loadout": lambda self: {},
        "touch_used": lambda self: None,
        "collection_id": lambda self: None,
    })()
    monkeypatch.setattr(sessions, "_resolve_persona_to_load", lambda req: persona)
    monkeypatch.setattr("xlii.repl_cmds.switch.switch_to_persona", lambda c, p: True)

    h_chat("/chat --id bob --read-proj", ctx)
    assert agent.session.chat_read_proj is True
    # a plain /chat re-arms the project-blind default
    h_chat("/chat --id bob", ctx)
    assert agent.session.chat_read_proj is False
