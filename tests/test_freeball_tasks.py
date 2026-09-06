"""Freeball × /tasks step gate (the-fold Vector C follow-up).

Vector C shipped /yolo --freeball but flagged one friction site it didn't reach:
the /tasks per-step confirmation. Freeball's contract is *friction drops, rails
never*, so the per-step gate (friction) is now auto-yes'd under freeball — while
the untrusted-carry gate (a security rail: a shell/slash step fed by an agent
step's output) STILL fires. Keyed on freeball specifically; plain /yolo (which
sets yolo, not freeball) leaves the step gate unchanged.
"""
from __future__ import annotations

from types import SimpleNamespace

from helpers import FakeConsole, make_agent, make_cfg, script_iterations
from xlii.repl_cmds.tasks import _session_freeball, _tasks_handler
from xlii.repl_state import REPLState


def _state(tmp_path, *, agent=None, freeball=False):
    agent = agent or make_agent(tmp_path, cfg=make_cfg())
    (tmp_path / ".xlii").mkdir(parents=True, exist_ok=True)
    st = REPLState(console=FakeConsole(), agent=agent, project=agent.project,
                   cfg=agent.cfg, pool=agent.pool)
    st.command_scope = "code"
    if freeball:
        st.freeball = True   # setter delegates to agent.session.freeball
    return st


def test_session_freeball_reads_state():
    assert _session_freeball({"state": SimpleNamespace(freeball=True)}) is True
    assert _session_freeball({"state": SimpleNamespace(freeball=False)}) is False
    assert _session_freeball({}) is False   # no state → not freeball


def test_freeball_skips_step_friction_prompt(tmp_path, monkeypatch):
    prompts: list[str] = []
    monkeypatch.setattr("xlii.tools._confirm", lambda *a, **k: prompts.append("asked") or "y")
    st = _state(tmp_path, freeball=True)
    _tasks_handler("/tasks run 'printf hi'", st.as_context_dict())   # NOTE: no --yes
    assert prompts == []   # freeball auto-yes'd the ordinary per-step gate


def test_plain_session_still_prompts(tmp_path, monkeypatch):
    # Without freeball the friction gate is unchanged (also the plain-/yolo case:
    # /yolo sets yolo, not freeball, so the step gate stays put).
    prompts: list[str] = []
    monkeypatch.setattr("xlii.tools._confirm", lambda *a, **k: prompts.append("asked") or "y")
    st = _state(tmp_path, freeball=False)
    _tasks_handler("/tasks run 'printf hi'", st.as_context_dict())
    assert prompts == ["asked"]


def test_freeball_keeps_untrusted_carry_rail(tmp_path, monkeypatch):
    # The rail that must survive freeball: a shell step fed by an agent step's
    # (untrusted) output still confirms. Here we decline → it blocks.
    prompts: list[str] = []
    monkeypatch.setattr("xlii.tools._confirm",
                        lambda *a, **k: prompts.append("asked") or "n")
    agent = make_agent(tmp_path, cfg=make_cfg())
    script_iterations(agent, ("rm -rf /tmp/whatever", None))
    st = _state(tmp_path, agent=agent, freeball=True)
    _tasks_handler("/tasks run 'printf hi |> ?suggest a command |> echo {{prev}}'",
                   st.as_context_dict())
    assert prompts == ["asked"]              # the rail fired once, under freeball
    assert "untrusted" in st.console.text    # and named the untrusted reason
