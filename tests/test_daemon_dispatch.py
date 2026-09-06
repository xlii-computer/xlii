"""Fabric F5 — the daemon's dispatch routing, unit-tested offline.

`classify_dispatch` is the pure routing brain of the XMPP command daemon: given a
decrypted message body it decides kill / verb / agent / unknown, parses the
optional `[alias]` workspace prefix, and resolves verb scripts on disk — all
without slixmpp, a subprocess, or a live server. It lives in xlii/daemon_gate.py
(no [daemon] extra needed) precisely so this routing policy can be pinned here.

The live CommandDaemon._dispatch is now a thin shell that executes whatever this
returns; the OMEMO transport around it still needs a live tailnet (guide §13).

Run directly:  ./venv/bin/python -m pytest tests/test_daemon_dispatch.py
"""

import os

import pytest

from xlii.daemon_gate import DispatchDecision, classify_dispatch, list_verbs


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #

def _make_verb(verbs_dir, name, *, executable=True):
    verbs_dir.mkdir(parents=True, exist_ok=True)
    p = verbs_dir / f"{name}.sh"
    p.write_text("#!/bin/sh\necho hi\n")
    p.chmod(0o755 if executable else 0o644)
    return p


def _classify(body, verbs_dir, *, fallback_enabled=True, fallback_workspace=""):
    return classify_dispatch(
        body,
        verbs_dir=verbs_dir,
        fallback_enabled=fallback_enabled,
        fallback_workspace=fallback_workspace,
    )


# --------------------------------------------------------------------------- #
#  kill — the built-in shutdown verb
# --------------------------------------------------------------------------- #

def test_kill_is_the_first_word(tmp_path):
    d = _classify("kill", tmp_path)
    assert d.kind == "kill"
    assert d.reply == "[daemon] shutting down."
    assert d.audit == "shutdown requested"


def test_kill_ignores_trailing_args(tmp_path):
    assert _classify("kill now please", tmp_path).kind == "kill"


def test_kill_is_case_insensitive(tmp_path):
    assert _classify("KILL", tmp_path).kind == "kill"


def test_kill_cannot_be_smuggled_behind_a_workspace_prefix(tmp_path):
    # Security property: kill is matched BEFORE the [alias] prefix is stripped,
    # so "[proj] kill" routes to the agent (prompt="kill"), never a shutdown.
    d = _classify("[proj] kill", tmp_path)
    assert d.kind == "agent"
    assert d.prompt == "kill"
    assert d.workspace == "proj"


# --------------------------------------------------------------------------- #
#  verb scripts
# --------------------------------------------------------------------------- #

def test_executable_verb_is_resolved(tmp_path):
    verbs = tmp_path / "verbs"
    path = _make_verb(verbs, "status")
    d = _classify("status", verbs)
    assert d.kind == "verb"
    assert d.verb_path == path
    assert d.verb_args == []
    assert d.audit == "verb: status"


def test_verb_args_are_split_off(tmp_path):
    verbs = tmp_path / "verbs"
    _make_verb(verbs, "deploy")
    d = _classify("deploy prod --force", verbs)
    assert d.kind == "verb"
    assert d.verb_args == ["prod", "--force"]


def test_verb_name_lookup_is_lowercased(tmp_path):
    verbs = tmp_path / "verbs"
    _make_verb(verbs, "status")
    # First word is lowercased before the <name>.sh lookup.
    assert _classify("STATUS", verbs).kind == "verb"


def test_non_executable_script_is_not_a_verb(tmp_path):
    verbs = tmp_path / "verbs"
    _make_verb(verbs, "status", executable=False)
    # Present but not +x → falls through to the agent fallback.
    assert _classify("status", verbs).kind == "agent"


def test_missing_verb_falls_through_to_agent(tmp_path):
    verbs = tmp_path / "verbs"
    _make_verb(verbs, "status")
    d = _classify("nonexistent thing", verbs)
    assert d.kind == "agent"
    assert d.prompt == "nonexistent thing"


# --------------------------------------------------------------------------- #
#  agent fallback + the [alias] workspace prefix
# --------------------------------------------------------------------------- #

def test_plain_message_routes_to_agent(tmp_path):
    d = _classify("grep me the auth module", tmp_path)
    assert d.kind == "agent"
    assert d.prompt == "grep me the auth module"
    assert d.workspace == ""
    assert d.audit == "agent fallback (ws=auto)"


def test_default_workspace_is_used_when_no_prefix(tmp_path):
    d = _classify("do a thing", tmp_path, fallback_workspace="isaac")
    assert d.workspace == "isaac"
    assert d.audit == "agent fallback (ws=isaac)"


def test_workspace_prefix_overrides_default(tmp_path):
    d = _classify("[isaac2] grep the auth module", tmp_path, fallback_workspace="isaac")
    assert d.kind == "agent"
    assert d.workspace == "isaac2"          # prefix wins over the configured default
    assert d.prompt == "grep the auth module"   # prefix stripped from the prompt


def test_workspace_prefix_with_dots_and_dashes(tmp_path):
    d = _classify("[my-proj.v2] hello", tmp_path)
    assert d.workspace == "my-proj.v2"
    assert d.prompt == "hello"


def test_workspace_prefix_then_verb(tmp_path):
    # The prefix is stripped first, then the remaining first word matches a verb.
    verbs = tmp_path / "verbs"
    _make_verb(verbs, "status")
    d = _classify("[proj] status", verbs)
    assert d.kind == "verb"
    assert d.verb_path.name == "status.sh"


def test_multiline_body_after_prefix_is_kept_whole(tmp_path):
    d = _classify("[proj] line one\nline two", tmp_path)
    assert d.kind == "agent"
    assert d.prompt == "line one\nline two"


# --------------------------------------------------------------------------- #
#  fallback disabled → unknown-verb reply
# --------------------------------------------------------------------------- #

def test_fallback_disabled_yields_unknown_with_verb_list(tmp_path):
    verbs = tmp_path / "verbs"
    _make_verb(verbs, "status")
    _make_verb(verbs, "deploy")
    d = _classify("frobnicate", verbs, fallback_enabled=False)
    assert d.kind == "unknown"
    assert d.reply == "[daemon] unknown verb 'frobnicate'. Verbs: deploy, status"
    assert d.audit == "unknown verb: frobnicate"


def test_fallback_disabled_still_runs_known_verbs(tmp_path):
    verbs = tmp_path / "verbs"
    _make_verb(verbs, "status")
    assert _classify("status", verbs, fallback_enabled=False).kind == "verb"


# --------------------------------------------------------------------------- #
#  list_verbs
# --------------------------------------------------------------------------- #

def test_list_verbs_missing_dir(tmp_path):
    assert "does not exist" in list_verbs(tmp_path / "nope")


def test_list_verbs_empty_dir(tmp_path):
    (tmp_path / "verbs").mkdir()
    assert "empty" in list_verbs(tmp_path / "verbs")


def test_list_verbs_lists_only_executables_sorted(tmp_path):
    verbs = tmp_path / "verbs"
    _make_verb(verbs, "zebra")
    _make_verb(verbs, "alpha")
    _make_verb(verbs, "draft", executable=False)   # not +x → excluded
    assert list_verbs(verbs) == "alpha, zebra"


# --------------------------------------------------------------------------- #
#  edge cases
# --------------------------------------------------------------------------- #

def test_decision_is_a_plain_dataclass(tmp_path):
    # Sanity: the decision carries no behaviour, just data the daemon acts on.
    d = _classify("hello", tmp_path)
    assert isinstance(d, DispatchDecision)


def test_bare_prefix_leaves_empty_prompt(tmp_path):
    # "[proj]" with nothing after it: prefix strips to an empty body, which
    # routes to the agent with an empty prompt (the daemon's _on_message guards
    # against wholly-empty inbound bodies upstream).
    d = _classify("[proj]", tmp_path)
    assert d.kind == "agent"
    assert d.prompt == ""
    assert d.workspace == "proj"


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses the X_OK check")
def test_non_executable_verb_check_is_meaningful(tmp_path):
    # Guards the test above: as non-root, a 0o644 script really is non-executable.
    verbs = tmp_path / "verbs"
    p = _make_verb(verbs, "status", executable=False)
    assert not os.access(p, os.X_OK)


# --------------------------------------------------------------------------- #
#  X1 — per-sender session derivation (ask --session over the fabric)
# --------------------------------------------------------------------------- #

def test_agent_decision_carries_per_sender_session(tmp_path):
    d = classify_dispatch(
        "what changed today?", verbs_dir=tmp_path,
        fallback_enabled=True, sender="me@example.org/phone",
    )
    assert d.kind == "agent"
    assert d.session == "xmpp:me@example.org"  # resource stripped, bare JID


def test_session_id_binds_the_effective_workspace(tmp_path):
    d = classify_dispatch(
        "[proj] status of the build", verbs_dir=tmp_path,
        fallback_enabled=True, sender="me@example.org",
    )
    assert d.session == "xmpp:me@example.org:proj"
    # the fallback workspace binds the same way when no prefix overrides
    d2 = classify_dispatch(
        "hello", verbs_dir=tmp_path,
        fallback_enabled=True, fallback_workspace="lab", sender="me@example.org",
    )
    assert d2.session == "xmpp:me@example.org:lab"


def test_distinct_jids_get_isolated_sessions(tmp_path):
    kw = dict(verbs_dir=tmp_path, fallback_enabled=True)
    a = classify_dispatch("hi", sender="alice@example.org", **kw)
    b = classify_dispatch("hi", sender="bob@example.org", **kw)
    assert a.session != b.session


def test_no_sender_means_one_shot(tmp_path):
    d = classify_dispatch("hi", verbs_dir=tmp_path, fallback_enabled=True)
    assert d.kind == "agent"
    assert d.session == ""


def test_verb_and_kill_decisions_carry_no_session(tmp_path):
    _make_verb(tmp_path, "status")
    v = classify_dispatch("status", verbs_dir=tmp_path,
                          fallback_enabled=True, sender="me@example.org")
    assert v.kind == "verb" and v.session == ""
    k = classify_dispatch("kill", verbs_dir=tmp_path,
                          fallback_enabled=True, sender="me@example.org")
    assert k.kind == "kill" and k.session == ""


# --------------------------------------------------------------------------- #
#  X2 — chunk_reply: chunk, don't truncate
# --------------------------------------------------------------------------- #

def test_chunk_reply_short_text_is_one_unsuffixed_message():
    from xlii.daemon_gate import chunk_reply
    assert chunk_reply("short answer", 1500) == ["short answer"]


def test_chunk_reply_splits_on_paragraphs_with_ordering_suffix():
    from xlii.daemon_gate import chunk_reply
    paras = [f"paragraph {i} " + ("x" * 80) for i in range(6)]
    text = "\n\n".join(paras)
    chunks = chunk_reply(text, 200)
    assert len(chunks) > 1
    assert all(len(c) <= 200 for c in chunks)
    n = len(chunks)
    for i, c in enumerate(chunks, 1):
        assert c.endswith(f"({i}/{n})")
    # nothing lost: every paragraph appears in exactly one chunk
    joined = "\n".join(chunks)
    for i in range(6):
        assert f"paragraph {i}" in joined


def test_chunk_reply_hard_splits_an_oversized_line():
    from xlii.daemon_gate import chunk_reply
    chunks = chunk_reply("y" * 5000, 1500)
    assert all(len(c) <= 1500 for c in chunks)
    assert sum(c.count("y") for c in chunks) == 5000


# --------------------------------------------------------------------------- #
#  Slash grammar (chat-first mouth) + the admin tier on control verbs
# --------------------------------------------------------------------------- #

def _slash(body, verbs_dir, **kw):
    kw.setdefault("fallback_enabled", True)
    return classify_dispatch(body, verbs_dir=verbs_dir, grammar="slash", **kw)


def test_slash_commands_route_and_bare_words_are_chat(tmp_path):
    _make_verb(tmp_path, "status")
    assert _slash("/kill", tmp_path).kind == "kill"
    assert _slash("/webcode", tmp_path).kind == "webcode"
    d = _slash("/status now", tmp_path)
    assert d.kind == "verb" and d.verb_args == ["now"]
    # The collision the grammar exists to fix: bare control words are prompts.
    for body in ("kill the lights idea we discussed", "kill", "webcode", "status now"):
        d = _slash(body, tmp_path)
        assert d.kind == "agent" and d.prompt == body


def test_bare_remote_control_opens(tmp_path):
    d = classify_dispatch(
        "remote-control", verbs_dir=tmp_path, fallback_enabled=True,
    )
    assert d.kind == "remote_lab" and d.remote_lab_action == "open"
    d = classify_dispatch(
        "remote-control lock", verbs_dir=tmp_path, fallback_enabled=True,
    )
    assert d.remote_lab_action == "lock"


def test_slash_remote_control_is_a_control_verb(tmp_path):
    d = _slash("/remote-control", tmp_path)
    assert d.kind == "remote_lab" and d.remote_lab_action == "open"
    assert _slash("/remote-control drop", tmp_path).remote_lab_action == "drop"
    assert _slash("/remote-lab status", tmp_path).remote_lab_action == "status"
    denied = _slash("/remote-control open", tmp_path, is_admin=False)
    assert denied.kind == "unknown" and denied.reply == "[daemon] not permitted."
    # Bare word is chat (slash grammar).
    assert _slash("remote-control open", tmp_path).kind == "agent"


def test_slash_is_recognized_on_the_raw_body_only(tmp_path):
    # The bare grammar pins "[alias] kill" as a prompt; slash keeps the same
    # invariant — a command can't be smuggled behind a workspace prefix.
    d = _slash("[proj] /kill", tmp_path)
    assert d.kind == "agent" and d.workspace == "proj" and d.prompt == "/kill"


def test_slash_miss_is_unknown_never_agent(tmp_path):
    d = _slash("/frobnicate", tmp_path)
    assert d.kind == "unknown"
    assert "/frobnicate" in d.reply and "/webcode" in d.reply


def test_slash_whoami_and_node_route_to_node_identity(tmp_path):
    # "which body am I texting?" — routing recognizes the verb; the daemon fills
    # the live node_name in _dispatch (classify is pure, name-free).
    assert _slash("/whoami", tmp_path).kind == "node"
    assert _slash("/node", tmp_path).kind == "node"
    # Not a destructive control verb → no is_admin gate needed to identify.
    assert _slash("/whoami", tmp_path, is_admin=False).kind == "node"


def test_slash_bare_chat_with_fallback_disabled_hints_commands(tmp_path):
    d = _slash("hello there", tmp_path, fallback_enabled=False)
    assert d.kind == "unknown" and "/webcode" in d.reply


def test_slash_bare_keeps_workspace_prefix_and_session(tmp_path):
    d = _slash("[proj] whats the plan", tmp_path, sender="me@phone.tailnet/dev")
    assert d.kind == "agent" and d.workspace == "proj"
    assert d.session == "xmpp:me@phone.tailnet:proj"


def test_admin_gate_denies_control_verbs_in_both_grammars(tmp_path):
    for body, verb in (("kill", "kill"), ("webcode", "webcode"),
                       ("remote-control", "remote-control")):
        d = classify_dispatch(body, verbs_dir=tmp_path,
                              fallback_enabled=True, is_admin=False)
        assert d.kind == "unknown"
        assert d.reply == "[daemon] not permitted."
        assert "denied" in d.audit and verb in d.audit
    for body in ("/kill", "/webcode preview", "/remote-control"):
        d = _slash(body, tmp_path, is_admin=False)
        assert d.kind == "unknown" and d.reply == "[daemon] not permitted."


def test_admin_gate_leaves_verbs_and_agent_open(tmp_path):
    _make_verb(tmp_path, "status")
    assert _slash("/status", tmp_path, is_admin=False).kind == "verb"
    assert _slash("hello", tmp_path, is_admin=False).kind == "agent"
    d = classify_dispatch("status", verbs_dir=tmp_path,
                          fallback_enabled=True, is_admin=False)
    assert d.kind == "verb"


# --------------------------------------------------------------------------- #
#  ask_command_line — the mojo read: persona wins over workspace/session (F2)
# --------------------------------------------------------------------------- #

def test_ask_command_line_persona_wins_and_drops_session():
    from xlii.daemon_gate import ask_command_line

    # Persona set → run AS the persona; --workspace/--session dropped (the
    # persona is the continuity — one entity, many surfaces). --yolo is not
    # appended: a remote chat turn is not a gates-off grant.
    # persona branch: the chat surface has no bash/write tools (dispatch
    # refuses unadvertised names), so its only reachable effect is skipping
    # paid-action confirms that would EOF-deny headless (generate_image).
    cmd = ask_command_line("hi", persona="ixaac", workspace="proj", session="xmpp:me")
    assert cmd == ["xlii", "ask", "--persona", "ixaac", "hi"]
    assert "--workspace" not in cmd and "--session" not in cmd


def test_ask_command_line_yolo_stays_off_the_project_path():
    from xlii.daemon_gate import ask_command_line

    # The legacy project-scoped turn has the FULL palette (bash included) —
    # yolo there would auto-approve shell, so it must never ride that branch.
    assert "--yolo" not in ask_command_line("hi", workspace="proj", session="s")
    assert "--yolo" not in ask_command_line("hi")


def test_ask_command_line_legacy_workspace_session():
    from xlii.daemon_gate import ask_command_line

    # No persona → the legacy project-scoped turn.
    assert ask_command_line("hi", workspace="proj", session="xmpp:me") == [
        "xlii", "ask", "--workspace", "proj", "--session", "xmpp:me", "hi"]
    # Blank persona is treated as unset.
    assert ask_command_line("hi", persona="  ", workspace="proj") == [
        "xlii", "ask", "--workspace", "proj", "hi"]
    # Bare turn (nothing set).
    assert ask_command_line("hi") == ["xlii", "ask", "hi"]


def test_ask_command_line_threads_attachments_before_the_prompt(tmp_path):
    from xlii.daemon_gate import ask_command_line

    # Media-in (#3): each fetched file becomes --attach <path>, before the prompt
    # so argparse binds them and the prompt stays last.
    cmd = ask_command_line("what is this?", persona="ixaac",
                           attachments=["/tmp/a.png", "/tmp/b.pdf"])
    assert cmd == ["xlii", "ask", "--persona", "ixaac",
                   "--attach", "/tmp/a.png", "--attach", "/tmp/b.pdf", "what is this?"]
    # None/empty → unchanged (byte-identical to the text path).
    assert ask_command_line("hi", attachments=None) == ["xlii", "ask", "hi"]


def test_ask_command_line_outbox_grants_the_delivery_channel():
    from xlii.daemon_gate import ask_command_line

    # Media-out: the outbox dir becomes --outbox <dir> (before the prompt), on
    # BOTH branches — a project-scoped agent can hand you files too.
    cmd = ask_command_line("send it", persona="ixaac", outbox="/tmp/ob")
    assert cmd == ["xlii", "ask", "--persona", "ixaac",
                   "--outbox", "/tmp/ob", "send it"]
    cmd = ask_command_line("send it", workspace="proj", outbox="/tmp/ob")
    assert cmd == ["xlii", "ask", "--workspace", "proj", "--outbox", "/tmp/ob", "send it"]
    # Unset → absent.
    assert "--outbox" not in ask_command_line("hi", persona="ixaac")
