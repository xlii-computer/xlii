"""Role-scoped slash commands — proposals/roles.md R4.

Role scope is a *peer of `repls`* inside `find_repl_command`: a role command is
addressed `/<role>:<name>` and resolves only while that role is active. The whole
mechanism lives in the resolver/registry (Layer 2) — never the routing overlay,
so an inactive role degrades to an unknown command, not a dispatch change.
"""

from __future__ import annotations

import pytest

from xlii.commands import (
    _INDEX,
    REPLCommand,
    _split_ns,
    dispatch_repl_command,
    find_repl_command,
    register_repl_command,
    unregister_repl_command,
)
from tests.helpers import FakeConsole


def _h(line, ctx):
    return True


@pytest.fixture
def role_cmds():
    """A role-owned command and a GLOBAL one of the same bare name — they must
    coexist (disjoint role scope), like code:/status and chat:/status do."""
    register_repl_command(REPLCommand(name="review-design", handler=_h,
                                      role="architect", repls=["code"]))
    register_repl_command(REPLCommand(name="review-design", handler=_h, repls=["code"]))
    yield
    unregister_repl_command("review-design")  # removes both claimants


def test_split_ns():
    assert _split_ns("architect:review-design") == ("architect", "review-design")
    assert _split_ns("plan") == (None, "plan")
    assert _split_ns("a:b:c") == ("a", "b:c")  # only the first colon splits


def test_resolves_only_when_role_active(role_cmds):
    line = "/architect:review-design"
    assert find_repl_command(line, repl="code", active_role="architect") is not None
    assert find_repl_command(line, repl="code", active_role=None) is None       # inactive
    assert find_repl_command(line, repl="code", active_role="tester") is None   # other role


def test_bare_form_hits_global_not_role(role_cmds):
    cmd = find_repl_command("/review-design", repl="code", active_role="architect")
    assert cmd is not None and cmd.role is None   # bare addresses the global


def test_global_and_role_coexist_in_index(role_cmds):
    assert {c.role for c in _INDEX["review-design"]} == {None, "architect"}


def test_collision_on_same_token_surface_role():
    register_repl_command(REPLCommand(name="zz-role-cmd", handler=_h,
                                      role="architect", repls=["code"]))
    try:
        with pytest.raises(ValueError):
            register_repl_command(REPLCommand(name="zz-role-cmd", handler=_h,
                                              role="architect", repls=["code"]))
    finally:
        unregister_repl_command("zz-role-cmd")


def test_dispatch_parked_role_nudges(role_cmds):
    console = FakeConsole()
    handled = dispatch_repl_command(
        "/architect:review-design",
        {"console": console, "command_scope": "code", "active_role": None},
    )
    assert handled is True
    assert any("belongs to role" in ln and "architect" in ln for ln in console.lines)


def test_dispatch_handles_when_active():
    calls: list = []
    register_repl_command(REPLCommand(
        name="zz-active", handler=lambda line, ctx: calls.append(line) or True,
        role="architect", repls=["code"]))
    try:
        handled = dispatch_repl_command(
            "/architect:zz-active",
            {"console": FakeConsole(), "command_scope": "code", "active_role": "architect"},
        )
        assert handled is True and calls == ["/architect:zz-active"]
    finally:
        unregister_repl_command("zz-active")


def test_global_command_unaffected_by_active_role():
    from xlii.repl_cmds import register_all
    register_all()
    assert find_repl_command("/plan", repl="code", active_role="architect") is not None
    assert find_repl_command("/plan", repl="code", active_role=None) is not None
