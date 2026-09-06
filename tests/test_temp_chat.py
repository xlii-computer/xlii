"""One-shot /temp --chat routing (model-routing A1)."""

from __future__ import annotations

from types import SimpleNamespace

from xlii.agent import Agent
from xlii.multimodal import PreparedTurn
from xlii.repl_cmds.code import _temp_handler
from tests.helpers import FakeConsole, make_agent, make_cfg


def test_temp_chat_only_applies_on_conversational_turn(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    agent.session.conversational = True
    agent.cfg = make_cfg(chat="chat-model", orchestrator="orch-model", temp=0.5)
    agent.cfg.chat_temp = lambda: 0.7

    temps: list[float] = []

    def fake_stream(self, *, model, schemas, temperature, cache_hdrs, backend=None):
        temps.append(temperature)
        return SimpleNamespace(content="ok", tool_calls=None), None, False

    monkeypatch.setattr(Agent, "_stream_orchestrator_iteration", fake_stream)
    monkeypatch.setattr(
        "xlii.agent.prepare_user_turn",
        lambda msg, *a, **kw: PreparedTurn(outgoing=msg, compact=msg, has_images=False),
    )

    agent.next_turn_temp_override = 1.1
    agent.session.next_turn_temp_chat_only = True
    agent.run_turn("hi")
    assert temps == [1.1]

    agent.next_turn_temp_override = 1.2
    agent.session.next_turn_temp_chat_only = True
    agent.session.conversational = False
    agent.run_turn("code turn")
    assert len(temps) == 2
    assert temps[1] == 0.5


def test_temp_handler_sets_chat_only_flag(tmp_path):
    agent = make_agent(tmp_path)
    agent.cfg.chat_temp = lambda: 0.7
    ctx = {"agent": agent, "cfg": agent.cfg, "console": FakeConsole()}
    assert _temp_handler("/temp 0.9 --chat", ctx) is True
    assert agent.next_turn_temp_override == 0.9
    assert agent.session.next_turn_temp_chat_only is True
