"""Model profiles — R1 presets (build, reason, economy, vision)."""

from __future__ import annotations

import json

from xlii.config import GlobalConfig
from xlii.model_profiles import (
    apply_model_profile,
    effective_model_profiles,
    get_model_profile,
    profile_matches_cfg,
)


def test_builtin_profiles_include_vision():
    cfg = GlobalConfig()
    profiles = effective_model_profiles(cfg)
    assert "vision" in profiles
    assert profiles["vision"]["orchestrator"] == "grok-4.3"
    assert profiles["vision"]["chat"] == "grok-4.3"
    assert profiles["vision"]["help"] == "grok-build-0.1"


def test_apply_profile_updates_all_roles(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text("{}")
    monkeypatch.setattr("xlii.config.GLOBAL_CONFIG_FILE", cfg_file)
    cfg = GlobalConfig.load()

    apply_model_profile(cfg, "vision", persist=True)
    assert cfg.orchestrator_model == "grok-4.3"
    assert cfg.chat_model == "grok-4.3"
    assert cfg.help_model == "grok-build-0.1"
    assert profile_matches_cfg(cfg, "vision")
    data = json.loads(cfg_file.read_text())
    assert data["orchestrator_model"] == "grok-4.3"


def test_user_profile_overrides_builtin():
    cfg = GlobalConfig()
    cfg.model_profiles = {
        "vision": {"orchestrator": "grok-4.20-reasoning", "chat": "grok-4.20-reasoning"},
    }
    prof = get_model_profile(cfg, "vision")
    assert prof["orchestrator"] == "grok-4.20-reasoning"


def test_unknown_profile_raises():
    try:
        get_model_profile(GlobalConfig(), "nope")
        assert False, "expected KeyError"
    except KeyError:
        # KeyError is the expected outcome; the assert above fires if it never arrives.
        pass


def test_snapshot_restore_cfg_models():
    from xlii.model_profiles import restore_cfg_models, snapshot_cfg_models

    cfg = GlobalConfig()
    cfg.orchestrator_model = "custom-orch"
    cfg.worker_model = "custom-worker"
    cfg.chat_model = "custom-chat"
    cfg.help_model = "custom-help"
    snap = snapshot_cfg_models(cfg)
    apply_model_profile(cfg, "vision", persist=False)
    assert cfg.orchestrator_model == "grok-4.3"
    restore_cfg_models(cfg, snap)
    assert cfg.orchestrator_model == "custom-orch"
    assert cfg.worker_model == "custom-worker"
    assert cfg.chat_model == "custom-chat"
    assert cfg.help_model == "custom-help"
