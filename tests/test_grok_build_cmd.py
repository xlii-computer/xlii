"""Tests for the /grok-build slash command (ACP front door + /build alias)."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from xlii.commands import REPLCommand, find_repl_command, register_repl_command
from xlii.repl_cmds.delegate import _parse, run_delegate_command
from xlii.repl_cmds.grok_build import _grok_build_handler

FAKE = str(Path(__file__).parent / "acp_fake_server.py")


def _parse_grok(rest: str):
    from xlii.harness.detect import list_harness_names

    if not rest.strip() or rest.strip().split(None, 1)[0].lower() not in list_harness_names():
        rest = f"grok {rest}".strip()
    return _parse(rest)


class Rec:
    def __init__(self):
        self.out: list[str] = []

    def print(self, *a, **k):
        self.out.append(" ".join(str(x) for x in a))

    @property
    def text(self) -> str:
        return "\n".join(self.out)


def _ctx(tmp_path):
    project = types.SimpleNamespace(project_root=tmp_path, xli_dir=tmp_path / ".xlii")
    return {"console": Rec(), "project": project, "state": None}


@pytest.fixture(autouse=True)
def _fake_acp_argv(monkeypatch):
    monkeypatch.setattr(
        "xlii.harness.acp_session.resolve_acp_argv",
        lambda name: [sys.executable, FAKE],
    )


def test_parse_defaults():
    h, mode, model, perm, ctx, task, err = _parse_grok("do the thing")
    assert err is None
    assert (h, mode, model, perm, ctx, task) == (
        "grok",
        "agent",
        None,
        "allow",
        False,
        "do the thing",
    )


def test_parse_plan_context():
    assert _parse_grok("--plan --context wire it")[1:6] == (
        "plan",
        None,
        "allow",
        True,
        "wire it",
    )


def test_grok_build_matches_delegate_spelling(tmp_path):
    def run_line(line, handler):
        root = tmp_path / line.replace("/", "_").replace(" ", "_")[:40]
        root.mkdir()
        project = types.SimpleNamespace(project_root=root, xli_dir=root / ".xlii")
        con = Rec()
        handler(line, {"console": con, "project": project, "state": None})
        return con.text

    d = run_line("/delegate grok --plan --context wire it", run_delegate_command)
    g = run_line("/grok-build --plan --context wire it", _grok_build_handler)
    assert d == g


def test_build_alias_resolves():
    from xlii.repl_cmds import register_all

    register_all()
    cmd = find_repl_command("/build", "code")
    assert cmd is not None
    assert cmd.name == "grok-build"
    assert find_repl_command("/build", "chat") is not None


def test_duplicate_build_registration_raises():
    with pytest.raises(ValueError, match="duplicate"):
        register_repl_command(
            REPLCommand(
                name="build",
                handler=lambda line, ctx: True,
                description="collision test",
                category="general",
            )
        )


def test_command_registers_conversational():
    from xlii.repl_cmds import register_all

    register_all()
    cmd = find_repl_command("/grok-build", "code")
    assert cmd is not None
    assert cmd.name == "grok-build"
    assert cmd.conversational is True
