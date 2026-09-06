"""Canary: the suite must not see the operator's real home / ~/.xlii."""

from __future__ import annotations

from pathlib import Path

import tests.conftest as iso


def test_conftest_home_seam_is_not_the_real_home():
    """Pin the conftest-import HOME isolation dir (Wave 1.4)."""
    assert iso._TEST_HOME != iso._REAL_HOME
    assert Path(iso._TEST_HOME).name.startswith("xlii-test-home-")


def test_persona_chat_state_dir_is_under_isolated_home():
    """CHAT_STATE_DIR is baked at import from the isolated HOME — not the real one."""
    from xlii.persona import CHAT_STATE_DIR

    assert CHAT_STATE_DIR == Path(iso._TEST_HOME) / ".xlii" / "chat"
    real = Path(iso._REAL_HOME).resolve()
    assert real not in CHAT_STATE_DIR.resolve().parents
    assert CHAT_STATE_DIR.resolve() != real / ".xlii" / "chat"


def test_config_dir_seam_still_active():
    from xlii.config import GLOBAL_CONFIG_DIR

    assert str(GLOBAL_CONFIG_DIR) == iso._TEST_CONFIG_DIR
    assert GLOBAL_CONFIG_DIR.resolve() != (Path(iso._REAL_HOME) / ".config" / "xlii").resolve()
