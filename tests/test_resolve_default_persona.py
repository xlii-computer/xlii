"""The single source of truth for "which persona is the default here".

`resolve_default_persona` unifies `/mojo`, the daemon `/whoami`, and the
daemon fallback so talk lands on the SAME journal (mojo). Chat costumes
(`ixaac`) are a different island — not an alias. It returns an ID
(on-disk-resolvable, lowercase), never the display name.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xlii.persona import (
    CHAT_DEFAULT_PERSONA_ID,
    DEFAULT_PERSONA_DISPLAY,
    DEFAULT_PERSONA_ID,
    factory_persona_id,
    journal_knob_id,
    persona_id_from_project_name,
    resolve_default_persona,
    talk_persona_id,
)


def test_precedence_project_over_cfg_over_shipped_default():
    proj = SimpleNamespace(bound_persona="bob")
    cfg = SimpleNamespace(fallback_persona="eve")  # same field GlobalConfig now persists
    assert resolve_default_persona(project=proj, cfg=cfg) == "bob"   # binding wins
    assert resolve_default_persona(cfg=cfg) == "eve"                 # then cfg fallback
    assert resolve_default_persona() == DEFAULT_PERSONA_ID           # then shipped default


def test_blank_and_whitespace_are_treated_as_unset():
    proj = SimpleNamespace(bound_persona="   ")
    cfg = SimpleNamespace(fallback_persona="")
    assert resolve_default_persona(project=proj, cfg=cfg) == DEFAULT_PERSONA_ID


def test_leftover_ixaac_fallback_is_the_journal_not_the_costume():
    """Pre-split unnamed journal was spelled ixaac. That knob must not keep
    [M] / the daemon looking up the chat costume."""
    cfg = SimpleNamespace(fallback_persona="ixaac")
    assert journal_knob_id("ixaac") == DEFAULT_PERSONA_ID
    assert journal_knob_id("") == ""
    assert journal_knob_id("scout") == "scout"
    assert factory_persona_id(cfg) == DEFAULT_PERSONA_ID
    assert resolve_default_persona(cfg=cfg) == DEFAULT_PERSONA_ID
    # An explicit project bind to the costume still names the costume.
    proj = SimpleNamespace(bound_persona=CHAT_DEFAULT_PERSONA_ID)
    assert resolve_default_persona(project=proj, cfg=cfg) == CHAT_DEFAULT_PERSONA_ID


def test_reserved_default_and_mojo_are_factory_companion():
    from xlii.persona import canonicalize_persona_id, create_persona

    assert canonicalize_persona_id("default") == DEFAULT_PERSONA_ID
    assert canonicalize_persona_id("mojo") == DEFAULT_PERSONA_ID
    assert canonicalize_persona_id("DEFAULT") == DEFAULT_PERSONA_ID
    # iXaac is a chat costume, not an alias of the journal.
    assert canonicalize_persona_id("ixaac") == "ixaac"
    proj = SimpleNamespace(bound_persona="default")
    assert resolve_default_persona(project=proj) == DEFAULT_PERSONA_ID
    with pytest.raises(ValueError, match="reserved"):
        create_persona("default")


def test_talk_persona_id_uses_chat_folder_not_bound_default():
    """A persona island is that persona alone — bound_persona must not leak ixaac."""
    proj = SimpleNamespace(name="chat/fred", bound_persona="ixaac")
    state = SimpleNamespace(project=proj, persona=None, cfg=None)
    assert persona_id_from_project_name("chat/fred") == "fred"
    assert talk_persona_id(state=state, project=proj) == "fred"
    assert talk_persona_id(project=SimpleNamespace(name="lab", bound_persona="bob")) == "bob"


def test_returns_id_not_display_name():
    # The result must be handed straight to _lookup_persona (case-sensitive on
    # the on-disk file), so it must be the lowercase id, never the display name.
    assert resolve_default_persona() == "mojo"
    assert resolve_default_persona() != DEFAULT_PERSONA_DISPLAY


def test_whoami_reply_uses_resolver_and_shows_display_name(monkeypatch):
    # CommandDaemon lives behind the optional [daemon] extra (omemo/slixmpp).
    # CI installs .[dev,tui] only — skip cleanly when the extra is absent.
    pytest.importorskip("omemo")
    pytest.importorskip("slixmpp_omemo")

    # The daemon /whoami string: resolver picks the id; the shipped default is
    # shown by its display name (the old literal "iXaac" was a resolve hazard —
    # it never matched the lowercase on-disk id).
    from xlii.persona import DEFAULT_PERSONA_DISPLAY as DISP

    class _D:
        cfg = SimpleNamespace(node_name="throne", fallback_persona="",
                              jid="daemon@home.test")
        _whoami_reply = __import__("xlii.daemon", fromlist=["CommandDaemon"]).CommandDaemon._whoami_reply

    out = _D._whoami_reply(_D())
    assert f"persona: {DISP}" in out and "throne" in out

    # A CUSTOM fallback shows its configured id verbatim (not forced to display).
    class _D2:
        cfg = SimpleNamespace(node_name="node1", fallback_persona="scout",
                              jid="daemon@home.test")
        _whoami_reply = _D._whoami_reply

    assert "persona: scout" in _D2._whoami_reply(_D2())

    # Leftover ixaac knob (pre-split journal spelling) shows Mojo, not ixaac.
    class _D3:
        cfg = SimpleNamespace(node_name="throne", fallback_persona="ixaac",
                              jid="daemon@home.test")
        _whoami_reply = _D._whoami_reply

    assert f"persona: {DISP}" in _D3._whoami_reply(_D3())
    assert "ixaac" not in _D3._whoami_reply(_D3()).lower()
