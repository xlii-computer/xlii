"""/btw mid-turn steering (bg-default P2): inbox, drain, command."""

from __future__ import annotations

from types import SimpleNamespace

from xlii.agent import SessionState, drain_btw_inbox
from xlii.repl_cmds import btw, register_all

register_all()


class _Console:
    def __init__(self):
        self.lines = []

    def print(self, *a, **k):
        self.lines.append(" ".join(str(x) for x in a))

    @property
    def text(self):
        return "\n".join(self.lines)


def _ctx(session=None):
    session = session or SessionState()
    agent = SimpleNamespace(session=session)
    state = SimpleNamespace(agent=agent)          # no job_registry slot — fine
    return {"console": _Console(), "state": state, "agent": agent}, session


# --- the drain (the tool-boundary injection) ---------------------------------


def test_drain_injects_one_user_message_and_clears():
    s = SessionState()
    s.btw_inbox.extend(["use pathlib", "skip the tests dir"])
    history: list = [{"role": "tool", "tool_call_id": "t1", "content": "ok"}]

    assert drain_btw_inbox(s, history) is True
    assert s.btw_inbox == []                       # cleared — never double-applied
    injected = history[-1]
    assert injected["role"] == "user"
    assert "steering" in injected["content"]
    assert "use pathlib" in injected["content"]
    assert "skip the tests dir" in injected["content"]

    # A second drain with an empty inbox is a no-op.
    assert drain_btw_inbox(s, history) is False
    assert len(history) == 2


def test_drain_noops_on_sessions_without_inbox():
    assert drain_btw_inbox(SimpleNamespace(), []) is False
    assert drain_btw_inbox(SimpleNamespace(btw_inbox=[]), []) is False


def test_drain_skips_blank_notes():
    s = SessionState()
    s.btw_inbox.extend(["   ", ""])
    history: list = []
    assert drain_btw_inbox(s, history) is False
    assert history == []


# --- the /btw command ---------------------------------------------------------


def test_btw_registered():
    from xlii.commands import find_repl_command
    for repl in ("code", "chat"):
        assert find_repl_command("/btw", repl) is not None


def test_btw_queues_note():
    ctx, session = _ctx()
    assert btw._btw_handler("/btw wait, use pathlib", ctx) is True
    assert session.btw_inbox == ["wait, use pathlib"]
    assert "queued" in ctx["console"].text


def test_btw_bare_shows_pending():
    ctx, session = _ctx()
    session.btw_inbox.append("earlier note")
    assert btw._btw_handler("/btw", ctx) is True
    out = ctx["console"].text
    assert "pending steering" in out
    assert "earlier note" in out


def test_btw_bare_empty_shows_usage():
    ctx, _ = _ctx()
    assert btw._btw_handler("/btw", ctx) is True
    assert "usage" in ctx["console"].text
