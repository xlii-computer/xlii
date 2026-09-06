"""dispatch_subagent advertises gaggle=, not jam=. A sneaked jam= is ignored.

G3 runs gaggles via the gaggle= param (see tests/test_gaggle.py). jam= is not
a schema field and must not fan out a crew.
"""
from __future__ import annotations
from types import SimpleNamespace
import xlii.agent as A
from tests.helpers import make_agent, make_msg


def _dispatch(agent, args):
    return A.Agent._run_worker(agent, args)


def _resp(msg):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg)],
        usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0),
    )


def _fake_clients(scripted):
    it = iter(scripted)
    return SimpleNamespace(
        chat=SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kw: next(it)))),
        label="primary",
    )


def test_dispatch_schema_has_gaggle_not_jam():
    from xlii.tools import dispatch_subagent_schema
    props = dispatch_subagent_schema()["function"]["parameters"]["properties"]
    assert "gaggle" in props
    assert "jam" not in props
    assert "gig" in props


def test_dispatch_subagent_sneaked_jam_not_orchestrated(tmp_path):
    agent = make_agent(tmp_path)
    agent.cfg.max_worker_iterations = 1
    msg = make_msg("plain worker reply", None)
    agent.pool = SimpleNamespace(
        primary=lambda: _fake_clients([_resp(msg)]),
        acquire=lambda: _fake_clients([_resp(msg)]),
        report_success=lambda c: None,
    )
    text, call = _dispatch(agent, {"task": "hello", "jam": "second-opinion"})
    assert "jam[" not in text
    assert "gaggle[" not in text
    assert "plain worker reply" in text
    assert call.iterations >= 1
