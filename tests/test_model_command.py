"""/model — live orchestrator/worker model switch (persist-by-default).

The two knobs the handler drives are both resolved fresh at dispatch (agent.py
reads the orchestrator model each turn, worker_agent.py the worker model each
dispatch), so mutating the in-memory cfg lands next turn; cfg.save() persists.
"""

from __future__ import annotations

import json

from xlii.config import GlobalConfig
from xlii.repl_cmds.code import _model_handler
from tests.helpers import FakeConsole, make_agent


def _ctx(tmp_path, monkeypatch, *, model_override=None):
    """A real GlobalConfig (defaults) + agent, with save() pointed at tmp."""
    cfg = GlobalConfig()
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr("xlii.config.GLOBAL_CONFIG_FILE", cfg_file)
    con = FakeConsole()
    agent = make_agent(tmp_path, cfg=cfg, console=con, model_override=model_override)
    return cfg, cfg_file, con, {"agent": agent, "console": con}, agent


def test_model_switch_is_live_and_persisted_by_default(tmp_path, monkeypatch):
    cfg, cfg_file, con, ctx, agent = _ctx(tmp_path, monkeypatch)

    assert _model_handler("/model grok-4", ctx) is True
    # live this session…
    assert cfg.orchestrator_model == "grok-4"
    assert cfg.orchestrator() == "grok-4"
    # …and written to disk (survives restart)
    assert json.loads(cfg_file.read_text())["orchestrator_model"] == "grok-4"
    # no persona pin existed, so none was invented (keeps status honest)
    assert agent.model_override is None


def test_session_flag_does_not_persist(tmp_path, monkeypatch):
    cfg, cfg_file, con, ctx, agent = _ctx(tmp_path, monkeypatch)

    _model_handler("/model grok-4 --session", ctx)
    assert cfg.orchestrator_model == "grok-4"      # live this session
    assert not cfg_file.exists()                   # but never saved


def test_worker_flag_targets_worker_model(tmp_path, monkeypatch):
    cfg, cfg_file, con, ctx, agent = _ctx(tmp_path, monkeypatch)

    _model_handler("/model grok-build-0.1 --worker", ctx)
    assert cfg.worker_model == "grok-build-0.1"
    assert cfg.orchestrator_model is None          # orchestrator untouched
    assert json.loads(cfg_file.read_text())["worker_model"] == "grok-build-0.1"


def test_active_persona_pin_is_moved_so_switch_is_visible(tmp_path, monkeypatch):
    # A loadout pinned model_override, which shadows cfg at agent.py:344. The
    # switch must move the pin or it would be silently ignored this session.
    cfg, cfg_file, con, ctx, agent = _ctx(tmp_path, monkeypatch, model_override="grok-3")

    _model_handler("/model grok-4", ctx)
    assert agent.model_override == "grok-4"
    assert cfg.orchestrator_model == "grok-4"


def test_bare_model_reports_current_without_persisting(tmp_path, monkeypatch):
    cfg, cfg_file, con, ctx, agent = _ctx(tmp_path, monkeypatch)

    _model_handler("/model", ctx)
    assert "orchestrator:" in con.text
    assert cfg.orchestrator() in con.text
    assert "chat:" in con.text
    assert not cfg_file.exists()


def test_chat_flag_targets_chat_model(tmp_path, monkeypatch):
    cfg, cfg_file, con, ctx, agent = _ctx(tmp_path, monkeypatch)

    _model_handler("/model grok-4.20-reasoning --chat", ctx)
    assert cfg.chat_model == "grok-4.20-reasoning"
    assert cfg.orchestrator_model is None
    assert json.loads(cfg_file.read_text())["chat_model"] == "grok-4.20-reasoning"


def test_profile_flag_applies_triple_session_only(tmp_path, monkeypatch):
    cfg, cfg_file, con, ctx, agent = _ctx(tmp_path, monkeypatch)

    _model_handler("/model --profile vision --session", ctx)
    assert cfg.orchestrator_model == "grok-4.3"
    assert cfg.worker_model == "grok-build-0.1"
    assert cfg.chat_model == "grok-4.3"
    assert cfg.help_model == "grok-build-0.1"
    assert agent.model_override is None
    assert not cfg_file.exists()


def test_profile_flag_persists_by_default(tmp_path, monkeypatch):
    cfg, cfg_file, con, ctx, agent = _ctx(tmp_path, monkeypatch)

    _model_handler("/model --profile economy", ctx)
    assert cfg.orchestrator_model == "grok-build-0.1"
    assert cfg.chat_model == "grok-build-0.1"
    assert json.loads(cfg_file.read_text())["chat_model"] == "grok-build-0.1"
