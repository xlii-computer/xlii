"""Tests for category auto-approve (terminal-native-toolkit Phase 3)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from tests.helpers import FakeConsole, make_tool_ctx
from xlii import tools
from xlii.repl_cmds.session import _parse_approve_categories, h_approve, h_safe
from xlii.shellgate import MODIFIES_SYSTEM, NETWORK, READ_ONLY
from xlii.tool_context import default_auto_approve
from xlii.tui.status import auto_approve as status_auto_approve


def test_default_auto_approve_is_empty():
    # No standing grants by default. read-only used to live here, but it never
    # gated anyway (only GATED_INTENTS consult the grant set) — the phantom
    # default was dropped with the tier consolidation.
    assert default_auto_approve() == set()


def test_gate_auto_approve_network_skips_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_confirm", lambda prompt: (_ for _ in ()).throw(AssertionError("should not prompt")))
    ctx = make_tool_ctx(tmp_path, console=FakeConsole(), auto_approve={READ_ONLY, NETWORK})
    assert tools._check_intent_and_gate(ctx, "curl example.com", "network") is None


def test_gate_modifies_system_still_prompts(tmp_path, monkeypatch):
    prompted = []

    def _confirm(prompt):
        prompted.append(prompt)
        return "n"

    monkeypatch.setattr(tools, "_confirm", _confirm)
    ctx = make_tool_ctx(tmp_path, console=FakeConsole(), auto_approve={READ_ONLY, NETWORK})
    r = tools._check_intent_and_gate(ctx, "sudo apt update", "modifies-system")
    assert r is not None and r.is_error
    assert prompted


def test_gate_strips_modifies_system_from_tool_context(tmp_path, monkeypatch):
    prompted = []

    def _confirm(prompt):
        prompted.append(prompt)
        return "n"

    monkeypatch.setattr(tools, "_confirm", _confirm)
    ctx = make_tool_ctx(tmp_path, console=FakeConsole(), auto_approve={MODIFIES_SYSTEM})
    r = tools._check_intent_and_gate(ctx, "sudo apt update", "modifies-system")
    assert r is not None and r.is_error
    assert prompted


def test_parse_approve_categories_aliases():
    cats, err = _parse_approve_categories(["ro", "net"])
    assert err is None
    assert cats == {READ_ONLY, NETWORK}


def test_parse_approve_rejects_modifies_system():
    cats, err = _parse_approve_categories(["modifies-system"])
    assert not cats
    assert err and "cannot be auto-approved" in err


def test_approve_command_sets_categories():
    agent = SimpleNamespace(auto_approve=set(), yolo=False)
    ctx = {"agent": agent, "state": None, "console": FakeConsole()}

    assert h_approve("/approve network", ctx) is True
    assert agent.auto_approve == {NETWORK}


def test_safe_clears_auto_approve():
    # /safe now also stands down freeball + its one-shot ticket (the-fold Vector C),
    # so the mock carries the session slot the real Agent always has.
    agent = SimpleNamespace(
        auto_approve={READ_ONLY, NETWORK}, yolo=True, freeball=True,
        session=SimpleNamespace(freeball_restore=None),
    )
    ctx = {"agent": agent, "state": None, "console": FakeConsole()}

    assert h_safe("/safe", ctx) is True
    assert agent.yolo is False
    assert agent.freeball is False
    assert agent.session.freeball_restore is None
    assert agent.auto_approve == set()


def test_auto_approve_not_persisted_in_session_json(tmp_path):

    from xlii.repl_state import REPLState

    (tmp_path / ".xlii").mkdir()
    agent = SimpleNamespace(
        session=SimpleNamespace(
            auto_approve={NETWORK},
            attached_refs=[],
            attached_docs=[],
            attached_files=[],
            yolo=False,
            next_turn_temp_override=None,
        )
    )
    project = SimpleNamespace(xli_dir=tmp_path / ".xlii", name="t", project_root=tmp_path)
    state = REPLState(
        console=FakeConsole(),
        agent=agent,
        project=project,
        cfg=SimpleNamespace(),
        pool=SimpleNamespace(),
    )
    state.save()
    data = json.loads((tmp_path / ".xlii" / "session.json").read_text())
    assert "auto_approve" not in data


def test_status_auto_approve_hidden_when_yolo():
    state = SimpleNamespace(yolo=True, auto_approve={READ_ONLY, NETWORK})
    assert status_auto_approve(state) == ""


def test_status_auto_approve_label():
    state = SimpleNamespace(yolo=False, auto_approve={READ_ONLY, NETWORK})
    assert status_auto_approve(state) == "approve:ro,net"
