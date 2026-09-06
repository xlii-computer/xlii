"""Vector H — Howto × Journal (`/mojo` nudge).

When `/howto` mode is on **and** the project journal is recording, the bare
`/howto` banner surfaces xlii's *second* self-model with a one-line pointer to
`/mojo` (Project Shadow's teacher), and the howto system prompt (`_FRAMING`)
routes project-history questions there too. Pure discoverability, gated on the
journal: the banner nudge must be invisible whenever the journal is off.

Like ``test_howto.py`` these use a minimal fake owner + ctx so they run without a
live session, network, or xAI account. The journal seam consumed here is
read-only: ``state.journal`` (a ``ProjectJournal``) and its ``is_recording()``.
"""

import io

from rich.console import Console

from xlii.repl_cmds import howto


class _Owner:
    """Quacks like the bits of REPLState the handler touches (see test_howto)."""

    def __init__(self, journal=None):
        self.attached_docs: list[tuple[str, str]] = []
        self.howto_mode = False
        if journal is not None:
            self.journal = journal

    def attach_doc(self, name, content):
        if not any(n == name for n, _ in self.attached_docs):
            self.attached_docs.append((name, content))

    def detach_doc(self, name):
        before = len(self.attached_docs)
        self.attached_docs = [(n, c) for n, c in self.attached_docs if n != name]
        return len(self.attached_docs) < before


class _Journal:
    """The slice of ``ProjectJournal`` the nudge reads — recorder toggle only."""

    def __init__(self, recording: bool):
        self.code_on = recording

    def is_recording(self) -> bool:
        return bool(self.code_on)


def _ctx_cap(owner):
    """ctx + the StringIO so a test can read banner output. Wide width keeps the
    one-line nudge from wrapping so token assertions are stable."""
    sio = io.StringIO()
    ctx = {
        "console": Console(file=sio, force_terminal=False, width=200),
        "state": owner,
        "project": None,
        "command_scope": "code",
    }
    return ctx, sio


# --- the gate helper, in isolation ------------------------------------------


def test_nudge_helper_silent_without_journal():
    # chat REPL / pre-journal code REPL: state.journal is None → no nudge.
    ctx, _ = _ctx_cap(_Owner())
    assert howto._journal_nudge(ctx) == ""


def test_nudge_helper_silent_when_journal_off():
    ctx, _ = _ctx_cap(_Owner(journal=_Journal(recording=False)))
    assert howto._journal_nudge(ctx) == ""


def test_nudge_helper_fires_when_recording():
    ctx, _ = _ctx_cap(_Owner(journal=_Journal(recording=True)))
    nudge = howto._journal_nudge(ctx)
    assert "/mojo" in nudge
    assert "journal on" in nudge


def test_nudge_helper_falls_back_to_code_on_if_is_recording_raises():
    class _Cranky:
        code_on = True

        def is_recording(self):
            raise RuntimeError("boom")

    ctx, _ = _ctx_cap(_Owner(journal=_Cranky()))
    assert "/mojo" in howto._journal_nudge(ctx)


def test_nudge_helper_handles_missing_state():
    assert howto._journal_nudge({"console": None}) == ""


# --- the banner: visible only when the journal is recording ------------------


def test_bare_howto_shows_nudge_when_journal_recording():
    owner = _Owner(journal=_Journal(recording=True))
    ctx, sio = _ctx_cap(owner)
    assert howto._howto_handler("/howto", ctx) is True
    out = sio.getvalue()
    assert "/mojo" in out
    assert "journal on" in out
    # still the normal howto banner, just with the extra line
    assert "howto mode" in out


def test_bare_howto_hides_nudge_when_journal_off():
    owner = _Owner(journal=_Journal(recording=False))
    ctx, sio = _ctx_cap(owner)
    assert howto._howto_handler("/howto", ctx) is True
    out = sio.getvalue()
    assert "/mojo" not in out
    assert "howto mode" in out  # banner itself unaffected


def test_bare_howto_hides_nudge_when_no_journal():
    owner = _Owner()  # no journal attribute at all (chat-like)
    ctx, sio = _ctx_cap(owner)
    assert howto._howto_handler("/howto", ctx) is True
    assert "/mojo" not in sio.getvalue()


def test_topic_banner_omits_nudge_even_when_recording():
    # The nudge rides the bare `/howto` `Topics:` banner only; a focused topic
    # load keeps its own concise success line.
    owner = _Owner(journal=_Journal(recording=True))
    ctx, sio = _ctx_cap(owner)
    assert howto._howto_handler("/howto install", ctx) is True
    assert "/mojo" not in sio.getvalue()


# --- the system prompt: framing routes project-history questions to /mojo ---


def test_framing_constant_routes_project_history_to_askjo():
    assert "/mojo" in howto._FRAMING
    assert "journal" in howto._FRAMING.lower()


def test_local_content_carries_the_askjo_routing_line():
    body = howto._local_content("code", None)
    assert "/mojo" in body
    # the existing howto guide content is still there (regression guard)
    assert "How to use xlii" in body
