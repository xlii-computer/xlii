"""Live /code <-> /chat in-session switch (RP2).

Drives the real handlers (h_chat / h_code) against a real REPLState + a scripted
Agent (no TTY, no network). The persona's project is a real local-only project
on disk, so h_chat's lazy init never touches the network. _resolve_persona_to_load
is monkeypatched so we don't read the user's ~/.config personas.

Run with `python -m pytest` (imports tests.helpers).
"""

import json
from types import SimpleNamespace

import pytest

import xlii.cmds.sessions as sessions
from xlii.commands import find_repl_command
from xlii.profile import code_profile
from xlii.repl import REPLState
from xlii.repl_cmds import register_all
from xlii.config import ProjectConfig
from xlii.repl_cmds.switch import _pause_loop_for_switch, h_chat, h_code, switch_to_code_project
from tests.helpers import FakeConsole, make_agent

register_all()


def _write_local_project(root, name):
    (root / ".xlii").mkdir(parents=True, exist_ok=True)
    (root / ".xlii" / "project.json").write_text(json.dumps({
        "name": name, "collection_id": "", "created_at": "2026-01-01T00:00:00Z",
        "conversation_id": "testconv", "local_only": True,
        "root": str(root.resolve()),
    }))
    (root / "turns").mkdir(exist_ok=True)


def _make_persona(root, *, loadout=None):
    _write_local_project(root, "chat/bob")
    return SimpleNamespace(
        name="bob", project_root=root, turns_dir=root / "turns",
        system_prompt=lambda: "BOB SYSTEM PROMPT",
        loadout=lambda: (loadout or {}),
        touch_used=lambda: None,
        collection_id=lambda: None,
    )


def _code_state(tmp_path, *, history=None):
    """A launched-in-code REPLState: real Agent + live code Profile."""
    code_root = tmp_path / "coderepo"
    (code_root / ".xlii").mkdir(parents=True, exist_ok=True)
    agent = make_agent(code_root)              # agent.project = make_project(code_root)
    agent.history = history or [{"role": "system", "content": "CODE SYS"}]
    code_project = agent.project
    state = REPLState(console=FakeConsole(), agent=agent, project=code_project,
                      cfg=agent.cfg, pool=agent.pool)
    state.profile = code_profile(code_project, seed_limit=20)
    state.command_scope = "code"
    state.shell_cwd = code_root.resolve()
    return state, code_project


def _switch_to_bob(state, persona, monkeypatch):
    monkeypatch.setattr(sessions, "_resolve_persona_to_load", lambda req: persona)
    return h_chat("/chat --id bob", state.as_context_dict())


# --------------------------------------------------------------------------- #

def test_switch_is_in_place_not_a_restart(tmp_path, monkeypatch):
    state, _ = _code_state(tmp_path)
    agent_id = id(state.agent)
    _switch_to_bob(state, _make_persona(tmp_path / "bob"), monkeypatch)
    assert id(state.agent) == agent_id                  # same Agent, not relaunched
    assert not getattr(state, "pending_persona_switch", None)


def test_freeball_survives_live_switch(tmp_path, monkeypatch):
    """Trust is session-wide: freeball must survive `_live_switch` via the
    explicit stash/restore (load() alone would drop it). PR #188 fast-follow."""
    state, _ = _code_state(tmp_path)
    state.freeball = True
    assert state.freeball and state.yolo
    _switch_to_bob(state, _make_persona(tmp_path / "bob"), monkeypatch)
    assert state.freeball is True
    assert state.yolo is True
    h_code("/code", state.as_context_dict())
    assert state.freeball is True
    assert state.yolo is True


def test_system_prompt_swaps_and_restores(tmp_path, monkeypatch):
    state, code_project = _code_state(tmp_path)
    persona = _make_persona(tmp_path / "bob")
    _switch_to_bob(state, persona, monkeypatch)
    assert state.agent.base_system_prompt == "BOB SYSTEM PROMPT"
    h_code("/code", state.as_context_dict())
    assert state.agent.base_system_prompt == code_profile(code_project, seed_limit=20).system_prompt()


def test_conversation_detaches_on_chat(tmp_path, monkeypatch):
    # RP7: a switch must NOT carry the code conversation into chat — the persona
    # is oblivious. Chat's history is reset to the persona's own (empty) seed.
    hist = [{"role": "system", "content": "CODE SYS"},
            {"role": "user", "content": "fix the parser"},
            {"role": "assistant", "content": "done"}]
    state, _ = _code_state(tmp_path, history=list(hist))
    _switch_to_bob(state, _make_persona(tmp_path / "bob"), monkeypatch)
    assert state.agent.history == [{"role": "system", "content": "BOB SYSTEM PROMPT"}]
    assert all(m.get("content") != "fix the parser" for m in state.agent.history)


def test_switch_to_unopened_persona_does_not_leak_attachments(tmp_path, monkeypatch):
    # Persona isolation: switching into a persona with NO session.json must not
    # inherit the leaving surface's attached docs/refs (load() only resets them
    # when a session.json exists, so the switch clears first).
    state, _ = _code_state(tmp_path)
    state.attached_docs = [["leaked", "SECRET"]]            # live on the code surface
    persona = _make_persona(tmp_path / "bob")              # project.json only, no session.json
    _switch_to_bob(state, persona, monkeypatch)
    assert [n for n, _ in state.attached_docs] == []        # clean slate, no bleed


def test_persona_to_persona_detaches(tmp_path, monkeypatch):
    # A chat persona is oblivious to OTHER personas too: switching bob -> susan
    # must not carry bob's conversation into susan.
    state, _ = _code_state(tmp_path)
    _switch_to_bob(state, _make_persona(tmp_path / "bob"), monkeypatch)
    state.agent.history += [{"role": "user", "content": "bob secret"},
                            {"role": "assistant", "content": "ok"}]
    susan_root = tmp_path / "susan"
    _write_local_project(susan_root, "chat/susan")
    susan = SimpleNamespace(name="susan", project_root=susan_root,
                            turns_dir=susan_root / "turns",
                            system_prompt=lambda: "SUSAN", loadout=lambda: {},
                            touch_used=lambda: None, collection_id=lambda: None)
    monkeypatch.setattr(sessions, "_resolve_persona_to_load", lambda req: susan)
    h_chat("/chat --id susan", state.as_context_dict())
    users = [m["content"] for m in state.agent.history if m["role"] == "user"]
    assert "bob secret" not in users                        # susan can't see bob's chat


def test_code_thread_restored_on_return(tmp_path, monkeypatch):
    # RP7: leaving code parks its thread; /code restores it (not the chat thread).
    hist = [{"role": "system", "content": "CODE SYS"},
            {"role": "user", "content": "fix the parser"},
            {"role": "assistant", "content": "done"}]
    state, _ = _code_state(tmp_path, history=list(hist))
    _switch_to_bob(state, _make_persona(tmp_path / "bob"), monkeypatch)
    # talk to bob — that turn must NOT leak into the restored code thread
    state.agent.history += [{"role": "user", "content": "bob chatter"},
                            {"role": "assistant", "content": "bob reply"}]
    h_code("/code", state.as_context_dict())
    users = [m["content"] for m in state.agent.history if m["role"] == "user"]
    assert "fix the parser" in users        # code's own thread is back
    assert "bob chatter" not in users       # bob's chat did not bleed into code


def test_rail_and_plan_cleared_entering_chat(tmp_path, monkeypatch):
    from xlii.rail import RailController
    state, _ = _code_state(tmp_path)
    state.agent.rail = RailController()
    state.plan_mode = True
    _switch_to_bob(state, _make_persona(tmp_path / "bob"), monkeypatch)
    assert state.agent.rail is None
    assert state.plan_mode is False and state.agent.plan_mode is False


def test_discovery_and_ops_cleared_entering_chat(tmp_path, monkeypatch):
    persona = _make_persona(tmp_path / "bob")
    for enable in ("ops_mode", "discovery_mode"):
        state, _ = _code_state(tmp_path)
        setattr(state.agent, enable, True)
        _switch_to_bob(state, persona, monkeypatch)
        assert getattr(state.agent, enable) is False
        assert state.agent.active_mode is None


def test_active_loop_paused_when_switching_to_chat(tmp_path, monkeypatch):
    from xlii.loop import LoopController

    state, code_project = _code_state(tmp_path)
    ctrl = LoopController.start(
        xli_dir=code_project.xli_dir,
        goal="fix the code project",
        judges=["tests"],
        max_cycles=2,
        test_command="true",
    )
    state.loop = ctrl

    _switch_to_bob(state, _make_persona(tmp_path / "bob"), monkeypatch)

    assert state.loop is None
    assert ctrl.state.status == "paused"
    persisted = json.loads((code_project.xli_dir / "loop-active.json").read_text())
    assert persisted["status"] == "paused"
    assert state.project.project_root == tmp_path / "bob"
    assert "[loop] paused for surface switch" in state.console.text


def test_active_loop_not_paused_when_switching_within_same_project(tmp_path):
    # The same-project guard: a switch that lands back on the loop's own project
    # must NOT pause it. Drives _pause_loop_for_switch directly (the live call
    # graph never lands _live_switch on the same project), so a regression that
    # pauses on every switch — instead of only cross-project ones — is caught.
    from xlii.loop import LoopController

    state, code_project = _code_state(tmp_path)
    ctrl = LoopController.start(
        xli_dir=code_project.xli_dir,
        goal="fix the code project",
        judges=["tests"],
        max_cycles=2,
        test_command="true",
    )
    state.loop = ctrl

    same_project_profile = SimpleNamespace(project=code_project)
    _pause_loop_for_switch(state, same_project_profile)

    assert state.loop is ctrl                      # still attached
    assert ctrl.state.status == "active"           # not paused
    assert "[loop] paused for surface switch" not in state.console.text


def test_scope_flips_and_commands_change(tmp_path, monkeypatch):
    state, _ = _code_state(tmp_path)
    assert find_repl_command("/rail", state.command_scope) is not None   # code: rail available
    _switch_to_bob(state, _make_persona(tmp_path / "bob"), monkeypatch)
    assert state.command_scope == "chat"
    assert find_repl_command("/rail", state.command_scope) is None        # chat: rail gone
    assert find_repl_command("/marks", state.command_scope) is not None   # chat: marks present


def test_memory_repointed_to_persona(tmp_path, monkeypatch):
    state, _ = _code_state(tmp_path)
    persona = _make_persona(tmp_path / "bob")
    _switch_to_bob(state, persona, monkeypatch)
    mem = state.profile.memory
    assert mem.turns_dir == persona.turns_dir
    assert mem.mark_rescan is True and mem.bounded_reply_scan is False
    # and the project actually moved to the persona project
    assert state.project.project_root == persona.project_root
    assert state.agent.project.project_root == persona.project_root


def test_round_trip_restores_code(tmp_path, monkeypatch):
    state, code_project = _code_state(tmp_path)
    _switch_to_bob(state, _make_persona(tmp_path / "bob"), monkeypatch)
    h_code("/code", state.as_context_dict())
    assert state.profile.mode == "code"
    assert state.persona is None
    assert state.command_scope == "code"
    assert state.project.project_root == code_project.project_root
    assert state.shell_cwd == code_project.project_root.resolve()
    assert find_repl_command("/rail", state.command_scope) is not None


def test_loadout_model_override_does_not_leak_back(tmp_path, monkeypatch):
    state, _ = _code_state(tmp_path)
    persona = _make_persona(tmp_path / "bob", loadout={"model": "some-pinned-model"})
    _switch_to_bob(state, persona, monkeypatch)
    assert state.agent.model_override == "some-pinned-model"   # loadout pinned it
    h_code("/code", state.as_context_dict())
    assert state.agent.model_override is None                  # reset on switch back


def test_loadout_profile_does_not_leak_back_to_code(tmp_path, monkeypatch):
    state, _ = _code_state(tmp_path)
    state.cfg.orchestrator_model = "grok-build-0.1"
    state.cfg.worker_model = "grok-build-0.1"
    state.cfg.chat_model = "grok-build-0.1"
    persona = _make_persona(tmp_path / "bob", loadout={"profile": "vision"})

    _switch_to_bob(state, persona, monkeypatch)
    assert state.cfg.orchestrator_model == "grok-4.3"

    h_code("/code", state.as_context_dict())
    assert state.cfg.orchestrator_model == "grok-build-0.1"
    assert state.agent.session.loadout_cfg_snapshot is None


def test_cross_mode_reachability():
    assert find_repl_command("/chat", "code") is not None
    assert find_repl_command("/code", "chat") is not None


def test_chat_with_preloaded_project_never_hits_network(tmp_path, monkeypatch):
    state, _ = _code_state(tmp_path)
    persona = _make_persona(tmp_path / "bob")          # project.json already on disk
    import xlii.sync
    monkeypatch.setattr(xlii.sync, "init_project",
                        lambda *a, **k: pytest.fail("init_project must not be called"))
    _switch_to_bob(state, persona, monkeypatch)
    assert state.profile.mode == "chat"


def test_switched_prompt_takes_effect_on_next_turn(tmp_path, monkeypatch):
    # The exit-gate core: after /chat the NEXT turn must run under the persona
    # prompt. run_turn rewrites history[0] from base_system_prompt each turn, and
    # the switch set base_system_prompt — so this is deterministic (no model).
    from tests.helpers import script_iterations
    state, _ = _code_state(tmp_path)
    _switch_to_bob(state, _make_persona(tmp_path / "bob"), monkeypatch)
    script_iterations(state.agent, ("hi from bob", None))
    state.agent.run_turn("hello")
    assert state.agent.history[0] == {"role": "system", "content": "BOB SYSTEM PROMPT"}


def test_turn_persisted_after_switch_lands_in_persona_dir(tmp_path, monkeypatch):
    # The load-bearing post_turn re-route: after /chat, a turn persisted via the
    # live profile's TurnStore must land under the persona dir + mark __rescan__.
    from xlii import transcript as tx
    state, _ = _code_state(tmp_path)
    persona = _make_persona(tmp_path / "bob")
    _switch_to_bob(state, persona, monkeypatch)
    state.agent.history += [{"role": "user", "content": "hi"},
                            {"role": "assistant", "content": "a real reply"}]
    dirty = state.profile.memory.persist(state.agent.history, "hi", set())   # what _chat_post_turn does
    assert tx.count_turns(persona.turns_dir) == 1
    assert "__rescan__" in dirty                                            # chat memory is synced


def test_switch_does_not_clobber_target_session_json(tmp_path, monkeypatch):
    # A persona with its OWN saved attachments must have them LOADED on switch
    # (not overwritten by the source surface's state on the post-dispatch save).
    bob_root = tmp_path / "bob"
    persona = _make_persona(bob_root)
    (bob_root / ".xlii" / "session.json").write_text(json.dumps({
        "version": 2, "current_workspace": "main",
        "workspaces": {"main": {"attached_refs": [], "attached_docs": [["bobdoc", "BOB DOC BODY"]]}},
        "yolo": False,
    }))
    state, _ = _code_state(tmp_path)
    assert state.attached_docs == []                       # code session has no docs
    _switch_to_bob(state, persona, monkeypatch)
    names = [n for n, _ in state.attached_docs]
    assert "bobdoc" in names                               # bob's saved doc was loaded, not clobbered


def test_persona_command_in_code_hosted_chat_switches_in_place(tmp_path, monkeypatch):
    # /persona in a code-hosted chat must NOT use the legacy restart marker
    # (which would exit a code-launched process) — it switches in place.
    state, _ = _code_state(tmp_path)
    _switch_to_bob(state, _make_persona(tmp_path / "bob"), monkeypatch)
    carol = _make_persona(tmp_path / "carol")
    carol.name = "carol"
    monkeypatch.setattr(sessions, "_resolve_persona_to_load", lambda req: carol)
    ctx = state.as_context_dict()
    from xlii.repl_cmds.chat import _persona_handler
    _persona_handler("/persona carol", ctx)
    assert "_switch_persona" not in ctx                    # no restart marker → no process exit
    assert not getattr(state, "pending_persona_switch", None)
    assert state.persona.name == "carol" and state.profile.mode == "chat"


def test_prompt_prefix_follows_live_mode(tmp_path, monkeypatch):
    # The prefix closures delegate to state.profile.prompt_prefix(state); after a
    # switch the prompt reflects the live mode (chat shows the persona name).
    state, _ = _code_state(tmp_path)
    assert state.profile.prompt_prefix(state).endswith("› ")     # code, plain
    _switch_to_bob(state, _make_persona(tmp_path / "bob"), monkeypatch)
    assert state.profile.prompt_prefix(state) == "[bob] › "       # chat: persona name


def test_persona_command_native_chat_switches_in_place(tmp_path, monkeypatch):
    # RP4 fix: /persona must switch IN PLACE even in a native chat session (no
    # code stash) — the legacy restart marker silently no-op'd in the chat-TUI.
    from xlii.profile import chat_profile
    state, _ = _code_state(tmp_path)
    bob = _make_persona(tmp_path / "bob")
    state.profile = chat_profile(bob, state.project, seed_limit=20)
    state.persona = bob
    state.command_scope = "chat"
    state._profile_stash = None                       # native chat: no code stash
    carol = _make_persona(tmp_path / "carol")
    carol.name = "carol"
    monkeypatch.setattr(sessions, "_resolve_persona_to_load", lambda req: carol)
    ctx = state.as_context_dict()
    from xlii.repl_cmds.chat import _persona_handler
    _persona_handler("/persona carol", ctx)
    assert "_switch_persona" not in ctx               # NOT the legacy restart marker
    assert not getattr(state, "pending_persona_switch", None)
    assert state.persona.name == "carol" and state.profile.mode == "chat"


def test_one_shot_temp_override_reset_on_switch(tmp_path, monkeypatch):
    state, _ = _code_state(tmp_path)
    state.next_turn_temp_override = 0.9
    _switch_to_bob(state, _make_persona(tmp_path / "bob"), monkeypatch)
    assert state.agent.next_turn_temp_override is None


def test_bare_chat_sits_with_ixaac_not_bound_persona(tmp_path, monkeypatch):
    # Bare /chat sits with the shipped chat costume (iXaac) — a voice, not a
    # /role and not the journal. Project bound_persona is leftover glue.
    import xlii.persona
    state, _ = _code_state(tmp_path)
    state.project.bound_persona = "bob"
    captured = {}

    def fake_resolve(req):
        captured["req"] = req
        return None  # bail early after recording the resolved name

    monkeypatch.setattr(sessions, "_resolve_persona_to_load", fake_resolve)
    h_chat("/chat", state.as_context_dict())
    assert captured["req"] == xlii.persona.CHAT_DEFAULT_PERSONA_ID


def test_bare_chat_unset_falls_back_to_chat_costume(tmp_path, monkeypatch):
    import xlii.persona
    state, _ = _code_state(tmp_path)  # no bound_persona
    captured = {}

    def fake_resolve(req):
        captured["req"] = req
        return None

    monkeypatch.setattr(sessions, "_resolve_persona_to_load", fake_resolve)
    h_chat("/chat", state.as_context_dict())
    assert captured["req"] == xlii.persona.CHAT_DEFAULT_PERSONA_ID


def _chat_surface_state(tmp_path, *, scratch=False):
    """A session with no code surface stashed (chat-launched): chat profile,
    persona set, no stash — the shape V3a's shared gate must serve."""
    state, _ = _code_state(tmp_path)
    state.profile = None
    state._profile_stash = None
    state.command_scope = "chat"
    state.persona = SimpleNamespace(name="bob")
    state.scratch = scratch
    return state


def test_scratch_code_with_no_stash_runs_the_gate(tmp_path, monkeypatch):
    # V3a: scratch → /code no longer refuses with "no code project…" — it runs
    # the SAME detect → init → resolve gate as `xlii code`. A real project at
    # the live cwd satisfies the EntryGate (requires_project) and the door
    # switches to it.
    proj_root = tmp_path / "realproj"
    _write_local_project(proj_root, "realproj")
    state = _chat_surface_state(tmp_path, scratch=True)
    state.shell_cwd = proj_root.resolve()
    from xlii.repl_cmds import switch as switch_mod
    switched = []
    monkeypatch.setattr(
        switch_mod, "switch_to_code_project",
        lambda ctx, project: switched.append(project) or True,
    )
    out = h_code("/code", state.as_context_dict())
    assert out is True
    assert switched and switched[0].name == "realproj"      # gate resolved → switched
    assert not any("no code project" in ln for ln in state.console.lines)


def test_code_with_no_stash_gate_init_creates_and_switches(tmp_path, monkeypatch):
    # The remediation half: no project at cwd → the gate's `init` choice
    # initializes a local project (the detect → init → resolve flow) and the
    # door switches to it.
    bare = tmp_path / "bare"
    bare.mkdir()
    state = _chat_surface_state(tmp_path)
    state.shell_cwd = bare.resolve()
    from xlii.cmds.sessions import code as code_mod
    from xlii.repl_cmds import switch as switch_mod
    monkeypatch.setattr(code_mod, "_prompt_launch_gate", lambda project, root: "init")
    switched = []
    monkeypatch.setattr(
        switch_mod, "switch_to_code_project",
        lambda ctx, project: switched.append(project) or True,
    )
    out = h_code("/code", state.as_context_dict())
    assert out is True
    assert switched and switched[0].local_only               # initialized local-only
    assert switched[0].project_root == bare.resolve()
    assert (bare / ".xlii" / "project.json").exists()        # detect → init happened


def test_code_with_no_stash_gate_cancel_stays_put(tmp_path, monkeypatch):
    # Cancel at the gate → no switch, no init, session intact.
    bare = tmp_path / "bare"
    bare.mkdir()
    state = _chat_surface_state(tmp_path)
    state.shell_cwd = bare.resolve()
    from xlii.cmds.sessions import code as code_mod
    from xlii.repl_cmds import switch as switch_mod
    monkeypatch.setattr(code_mod, "_prompt_launch_gate", lambda project, root: "cancel")
    called = []
    monkeypatch.setattr(
        switch_mod, "switch_to_code_project",
        lambda ctx, project: called.append(project) or True,
    )
    out = h_code("/code", state.as_context_dict())
    assert out is True
    assert called == []                                      # never switched
    assert not (bare / ".xlii").exists()                     # nothing initialized
    assert any("cancelled" in ln for ln in state.console.lines)


def test_switch_to_code_project_teleports_scope(tmp_path, monkeypatch):
    state, old_project = _code_state(tmp_path, history=[
        {"role": "system", "content": "OLD"},
        {"role": "user", "content": "old task"},
    ])
    target_root = tmp_path / "target"
    _write_local_project(target_root, "target")
    (target_root / ".xlii" / "workbench.json").write_text('{"active":"code"}')
    target = ProjectConfig.load(target_root)
    assert target is not None

    reloaded = {}
    monkeypatch.setattr("xlii.commands.reload_project_commands", lambda xli_dir: reloaded.setdefault("commands", xli_dir) or 0)
    monkeypatch.setattr("xlii.tools.load_project_tools", lambda xli_dir: reloaded.setdefault("tools", xli_dir))

    switch_to_code_project(state.as_context_dict(), target, reset=True)

    assert state.project.project_root == target_root
    assert state.agent.project.project_root == target_root
    assert state.shell_cwd == target_root.resolve()
    assert state.command_scope == "code"
    assert state.persona is None
    assert getattr(state.workbench, "name", None) == "code"
    assert state.agent.history == [state.agent.history[0]]
    assert reloaded["commands"] == target.xli_dir
    assert reloaded["tools"] == target.xli_dir
    assert state._history_stash[f"code:{old_project.name}"][0]["content"] == "old task"


def test_switch_from_scratch_rebuilds_journal_code_auto(tmp_path, monkeypatch):
    """Scratch → real project must honor the target's journal code_auto.

    Regression: _live_switch used to keep the scratch session's ProjectJournal
    (code_on=False, wrong root), so /project find --go never started journaling.
    """
    from xlii.journal import ProjectJournal, build_project_journal, read_journal_auto

    state, _old = _code_state(tmp_path)
    state.scratch = True
    state.no_sync = True
    # Scratch-shaped journal: off, bound to the old project
    state.journal = ProjectJournal(
        project=state.project, pool=None, cfg=state.cfg, code_on=False,
    )
    assert state.journal.is_recording() is False

    target_root = tmp_path / "autojournal"
    _write_local_project(target_root, "autojournal")
    target = ProjectConfig.load(target_root)
    assert target is not None
    jdir = target.xli_dir / "journal"
    jdir.mkdir(parents=True, exist_ok=True)
    (jdir / "config.json").write_text(json.dumps({"code_auto": True}))
    assert read_journal_auto(target) is True

    monkeypatch.setattr("xlii.commands.reload_project_commands", lambda xli_dir: 0)
    monkeypatch.setattr("xlii.tools.load_project_tools", lambda xli_dir: None)

    switch_to_code_project(state.as_context_dict(), target)

    assert state.scratch is False
    assert state.no_sync is False
    assert state.journal is not None
    assert state.journal.project.project_root == target_root
    assert state.journal.is_recording() is True
    # build path matches cold cmd_code launch
    assert build_project_journal(state).is_recording() is True
