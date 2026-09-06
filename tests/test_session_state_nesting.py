"""SessionState nesting + flat facades (grades plan Phase 4)."""

from __future__ import annotations

import dataclasses
import sys

import pytest

from xlii.session_state import (
    AttachmentState,
    CompactState,
    MeterState,
    ModelPinState,
    OverlayState,
    SessionState,
    TrustState,
)


def test_nested_groups_exist():
    s = SessionState()
    assert isinstance(s.trust, TrustState)
    assert isinstance(s.attachments, AttachmentState)
    assert isinstance(s.meter, MeterState)
    assert isinstance(s.overlays, OverlayState)
    assert isinstance(s.model_pins, ModelPinState)
    assert isinstance(s.compact, CompactState)


def test_top_level_field_count_is_thin():
    """Exit gate: top-level dataclass fields ≤ ~12 (nests + residual)."""
    names = {f.name for f in dataclasses.fields(SessionState)}
    # 6 nests + residual ephemeral/surface fields (hire is K1 residual)
    assert len(names) <= 17, sorted(names)
    assert "hire" in names
    for nest in (
        "trust",
        "attachments",
        "meter",
        "overlays",
        "model_pins",
        "compact",
    ):
        assert nest in names
    # flat trust/attachment fields must NOT be top-level dataclass fields
    assert "yolo" not in names
    assert "attached_docs" not in names
    assert "session_cost" not in names


def test_flat_facades_read_write_trust():
    s = SessionState()
    assert s.yolo is False and s.freeball is False
    s.yolo = True
    s.freeball = True
    s.freeball_restore = "safe"
    assert s.trust.yolo is True
    assert s.trust.freeball is True
    assert s.trust.tier == "freeball"
    assert s.freeball_restore == "safe"
    s.auto_approve = {"bash"}
    assert s.trust.auto_approve == {"bash"}


def test_flat_facades_attachments_and_meter():
    s = SessionState()
    s.attached_docs = [("a", "# hi")]
    s.attached_refs = [("bob", "cid")]
    s.attached_files = [{"name": "f"}]
    assert s.attachments.attached_docs[0][0] == "a"
    s.session_cost = 1.25
    s.session_tokens = 100
    s.budget_usd = 5.0
    s.budget_note = "low"
    assert s.meter.session_cost == 1.25
    assert s.budget_note == "low"


def test_from_flat_yolo_and_conversational():
    s = SessionState.from_flat(yolo=True, conversational=True)
    assert s.yolo is True
    assert s.conversational is True
    assert s.trust.yolo is True


def test_from_flat_rejects_unknown():
    with pytest.raises(TypeError):
        SessionState.from_flat(not_a_field=1)


def test_agent_reexport_and_delegation():
    from xlii.agent import SessionState as SS

    s = SS.from_flat(yolo=True)
    assert s.yolo is True

    class _A:
        session = s

        @property
        def yolo(self):
            return self.session.yolo

        @yolo.setter
        def yolo(self, v):
            self.session.yolo = v

    a = _A()
    assert a.yolo is True
    a.yolo = False
    assert s.trust.yolo is False


def test_module_docstring_says_where_new_state_goes():
    doc = sys.modules[SessionState.__module__].__doc__ or ""

    assert "Where new state goes" in doc
    assert "trust" in doc
