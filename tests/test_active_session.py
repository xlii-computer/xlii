"""Tests for the ambient active-session seam (:mod:`xlii.active_session`).

The stateless doorway providers (mark://, jobs://) reach the running session through this
module-global. It duck-types the state via getattr so a fake context works and a bare read
outside a session degrades to None rather than raising.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xlii import active_session


@pytest.fixture(autouse=True)
def _clean_ambient():
    """Never leak an installed session across tests (single module-global)."""
    prev = active_session.set_active_session(None)
    yield
    active_session.set_active_session(prev)


def test_set_get_and_restore_roundtrip():
    a, b = object(), object()
    assert active_session.set_active_session(a) is None  # was cleared by the fixture
    assert active_session.active_session() is a
    prev = active_session.set_active_session(b)
    assert prev is a and active_session.active_session() is b
    active_session.set_active_session(prev)  # restore
    assert active_session.active_session() is a


def test_turns_dir_of_prefers_profile_memory(tmp_path):
    state = SimpleNamespace(profile=SimpleNamespace(memory=SimpleNamespace(turns_dir=tmp_path)))
    assert active_session.turns_dir_of(state) == tmp_path


def test_turns_dir_of_falls_back_to_persona(tmp_path):
    # no profile.memory → the legacy state.persona.turns_dir wins
    state = SimpleNamespace(profile=None, persona=SimpleNamespace(turns_dir=tmp_path))
    assert active_session.turns_dir_of(state) == tmp_path


def test_turns_dir_of_none_for_stateless():
    assert active_session.turns_dir_of(None) is None
    assert active_session.turns_dir_of(SimpleNamespace()) is None


def test_active_turns_dir_reads_the_ambient(tmp_path):
    assert active_session.active_turns_dir() is None  # cleared
    active_session.set_active_session(
        SimpleNamespace(profile=SimpleNamespace(memory=SimpleNamespace(turns_dir=tmp_path)))
    )
    assert active_session.active_turns_dir() == tmp_path


def test_active_registry_none_without_session():
    assert active_session.active_registry() is None


def test_active_registry_from_state():
    from xlii.jobs import JobRegistry

    reg = JobRegistry(max_workers=1)
    active_session.set_active_session(SimpleNamespace(job_registry=reg))
    assert active_session.active_registry() is reg


def test_active_cfg_reads_state_then_agent():
    assert active_session.active_cfg() is None
    cfg = object()
    active_session.set_active_session(SimpleNamespace(cfg=cfg))
    assert active_session.active_cfg() is cfg
    other = object()
    active_session.set_active_session(
        SimpleNamespace(cfg=None, agent=SimpleNamespace(cfg=other))
    )
    assert active_session.active_cfg() is other
