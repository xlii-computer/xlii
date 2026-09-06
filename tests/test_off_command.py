"""/off — leave every overlay/mode at once, back to the base surface."""

from __future__ import annotations

from types import SimpleNamespace


from xlii.agent import SessionState
from xlii.repl_cmds import off, register_all

register_all()


class _Console:
    def __init__(self):
        self.lines = []

    def print(self, *a, **k):
        self.lines.append(" ".join(str(x) for x in a))

    @property
    def text(self):
        return "\n".join(self.lines)


def _agent(tmp_path):
    return SimpleNamespace(
        active_mode=None,
        howto_mode=False,
        image_mode=False,
        set_mode=None,
        history=[],
        session=SessionState(),
    )


def _mode_slot(agent):
    """A tiny active_mode slot with a working set_mode, mirroring Agent."""
    def set_mode(m):
        agent.active_mode = m
    agent.set_mode = set_mode


def _ctx(agent, state=None):
    st = state if state is not None else SimpleNamespace(
        agent=agent, cursor_sessions=None,
        howto_mode=False, image_mode=False,
        attached_docs=[], attach_doc=lambda *a, **k: None,
        detach_doc=lambda *a, **k: False,
    )
    st.agent = agent
    return {"console": _Console(), "state": st, "agent": agent,
            "command_scope": "code"}


# --- registered + nothing-to-leave --------------------------------------------


def test_off_registered_code_and_chat():
    from xlii.commands import find_repl_command
    for repl in ("code", "chat"):
        assert find_repl_command("/off", repl) is not None


def test_off_when_nothing_active_says_so(tmp_path):
    agent = _agent(tmp_path)
    _mode_slot(agent)
    ctx = _ctx(agent)
    assert off._off_handler("/off", ctx) is True
    assert "nothing to leave" in ctx["console"].text


# --- clears the mode-controller slot (plan/rail/debug/discovery/ops) -----------


def test_off_clears_active_mode_controller(tmp_path):
    from xlii.rail import RailController

    agent = _agent(tmp_path)
    _mode_slot(agent)
    agent.active_mode = RailController()
    ctx = _ctx(agent)
    assert off._off_handler("/off", ctx) is True
    assert agent.active_mode is None
    assert "left" in ctx["console"].text
    assert "rail" in ctx["console"].text.lower()


def test_off_clears_plan_mode(tmp_path):
    from xlii.mode_controller import PlanController

    agent = _agent(tmp_path)
    _mode_slot(agent)
    agent.active_mode = PlanController()
    ctx = _ctx(agent)
    off._off_handler("/off", ctx)
    assert agent.active_mode is None


# --- clears the howto overlay -------------------------------------------------


def test_off_clears_howto(tmp_path):
    agent = _agent(tmp_path)
    _mode_slot(agent)
    st = SimpleNamespace(
        agent=agent, cursor_sessions=None, howto_mode=True, image_mode=False,
        attached_docs=[("howto", "guide body")],
        detach_doc=lambda name: True, attach_doc=lambda *a, **k: None,
    )
    ctx = _ctx(agent, st)
    off._off_handler("/off", ctx)
    assert st.howto_mode is False
    assert "howto" in ctx["console"].text


# --- clears a foreground harness ----------------------------------------------


def test_off_clears_foreground_harness(tmp_path):
    left = []

    class _Reg:
        foreground = "cursor"

        def leave_mode(self):
            left.append(True)
            self.foreground = None
            return "cursor"

    agent = _agent(tmp_path)
    _mode_slot(agent)
    st = SimpleNamespace(
        agent=agent, cursor_sessions=_Reg(), howto_mode=False, image_mode=False,
        attached_docs=[], detach_doc=lambda *a: False, attach_doc=lambda *a, **k: None,
    )
    ctx = _ctx(agent, st)
    off._off_handler("/off", ctx)
    assert left == [True]
    assert st.cursor_sessions.foreground is None


# --- clears MULTIPLE layers in one shot ---------------------------------------


def test_off_clears_mode_and_howto_together(tmp_path):
    from xlii.rail import RailController

    agent = _agent(tmp_path)
    _mode_slot(agent)
    agent.active_mode = RailController()
    st = SimpleNamespace(
        agent=agent, cursor_sessions=None, howto_mode=True, image_mode=False,
        attached_docs=[("howto", "g")], detach_doc=lambda name: True,
        attach_doc=lambda *a, **k: None,
    )
    ctx = _ctx(agent, st)
    off._off_handler("/off", ctx)
    assert agent.active_mode is None
    assert st.howto_mode is False
    out = ctx["console"].text
    assert "rail" in out.lower() and "howto" in out


# --- leaves the base surface + trust alone ------------------------------------


def test_off_does_not_touch_base_surface_or_trust(tmp_path):
    # yolo on + scratch surface + a live mode — /off clears only the mode,
    # leaving trust (yolo) and the base surface (scratch) untouched.
    from xlii.rail import RailController

    agent = _agent(tmp_path)
    _mode_slot(agent)
    agent.active_mode = RailController()
    agent.session.trust.yolo = True
    st = SimpleNamespace(
        agent=agent, cursor_sessions=None, howto_mode=False, image_mode=False,
        scratch=True, attached_docs=[], detach_doc=lambda *a: False,
        attach_doc=lambda *a, **k: None,
    )
    ctx = _ctx(agent, st)
    off._off_handler("/off", ctx)
    assert agent.active_mode is None           # mode cleared
    assert agent.session.yolo is True          # trust untouched
    assert st.scratch is True                  # base surface untouched
