"""Phase 3 harness tests — ACP session, local adapters, codex tool."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from xlii.harness.acp_session import AcpHarnessSession, acp_harness_names, resolve_acp_argv
from xlii.harness.detect import HarnessSpec, HARNESS_SPECS, load_local_harnesses, register_harness
from xlii.tool_handlers import t_codex_run_task
from xlii.tool_context import ToolContext

FAKE = str(Path(__file__).parent / "acp_fake_server.py")


def test_acp_harness_names_includes_builtin():
    names = acp_harness_names()
    assert "cursor" in names
    assert "grok" in names
    assert "claude" in names


def test_resolve_acp_argv_cursor(monkeypatch):
    monkeypatch.setattr(
        "xlii.harness.acp_session.resolve_binary",
        lambda spec: "/fake/cursor-agent" if spec.name == "cursor" else None,
    )
    assert resolve_acp_argv("cursor") == ["/fake/cursor-agent", "acp"]


def test_resolve_acp_argv_grok(monkeypatch):
    monkeypatch.setattr(
        "xlii.harness.acp_session.resolve_binary",
        lambda spec: "/fake/grok" if spec.name == "grok" else None,
    )
    assert resolve_acp_argv("grok") == ["/fake/grok", "agent", "stdio"]


def test_acp_session_run(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "xlii.harness.acp_session.resolve_acp_argv",
        lambda name: [sys.executable, FAKE],
    )
    session = AcpHarnessSession("cursor", project_root=tmp_path, mode="ask")
    result = session.run("hello")
    assert result.error is None
    assert "q=opt-a" in result.text
    assert "plan=False" in result.text


def test_load_local_harness(tmp_path):
    xli_dir = tmp_path / ".xlii"
    xli_dir.mkdir()
    (xli_dir / "harness.local.py").write_text(
        "from xlii.harness.detect import HarnessSpec\n"
        "def register(reg):\n"
        "    reg(HarnessSpec(name='opencode', binaries=('opencode',), "
        "binary_env='XLII_OPENCODE_BIN', tier='cross_agent', "
        "auth_hint='opencode login', default_model='auto'), acp=True)\n"
    )
    load_local_harnesses(xli_dir)
    assert "opencode" in HARNESS_SPECS
    from xlii.harness import acp_session

    assert "opencode" in acp_session._EXTRA_ACP_SUFFIX
    HARNESS_SPECS.pop("opencode", None)
    acp_session._EXTRA_ACP_SUFFIX.pop("opencode", None)


def test_load_local_harness_unloads_on_project_switch(tmp_path):
    project_a = tmp_path / "a"
    project_b = tmp_path / "b"
    project_a.mkdir()
    project_b.mkdir()
    xli_a = project_a / ".xlii"
    xli_a.mkdir()
    (xli_a / "harness.local.py").write_text(
        "from xlii.harness.detect import HarnessSpec\n"
        "def register(reg):\n"
        "    reg(HarnessSpec(name='opencode', binaries=('opencode',), "
        "binary_env='XLII_OPENCODE_BIN', tier='cross_agent', "
        "auth_hint='opencode login', default_model='auto'), acp=True)\n"
    )
    load_local_harnesses(xli_a)
    assert "opencode" in HARNESS_SPECS

    load_local_harnesses(project_b / ".xlii")
    assert "opencode" not in HARNESS_SPECS


def test_load_local_harness_unloads_when_file_removed(tmp_path):
    xli_dir = tmp_path / ".xlii"
    xli_dir.mkdir()
    local = xli_dir / "harness.local.py"
    local.write_text(
        "from xlii.harness.detect import HarnessSpec\n"
        "def register(reg):\n"
        "    reg(HarnessSpec(name='opencode', binaries=('opencode',), "
        "binary_env='XLII_OPENCODE_BIN', tier='cross_agent', "
        "auth_hint='opencode login', default_model='auto'), acp=True)\n"
    )
    load_local_harnesses(xli_dir)
    assert "opencode" in HARNESS_SPECS

    # Remove the file while staying in the same project: the harness must drop.
    local.unlink()
    load_local_harnesses(xli_dir)
    assert "opencode" not in HARNESS_SPECS
    from xlii.harness import acp_session

    assert "opencode" not in acp_session._EXTRA_ACP_SUFFIX


def test_register_harness_acp():
    register_harness(
        HarnessSpec(
            name="_test_harness",
            binaries=("_test",),
            binary_env="XLII_TEST_BIN",
            tier="cross_agent",
            auth_hint="test",
            default_model="test",
        ),
        acp=True,
        acp_argv_suffix=("agent", "stdio"),
    )
    from xlii.harness import acp_session

    assert acp_session._EXTRA_ACP_SUFFIX["_test_harness"] == ("agent", "stdio")
    HARNESS_SPECS.pop("_test_harness", None)
    acp_session._EXTRA_ACP_SUFFIX.pop("_test_harness", None)


def test_codex_run_task_tool(tmp_path, monkeypatch):
    import xlii.harness.codex as codex_mod

    monkeypatch.setattr(codex_mod, "resolve_codex_cli", lambda: "/fake/codex")
    monkeypatch.setattr(
        codex_mod.subprocess,
        "run",
        lambda cmd, **kw: SimpleNamespace(
            stdout=json.dumps({"result": "migration done"}),
            stderr="",
            returncode=0,
        ),
    )
    ctx = ToolContext(
        project=SimpleNamespace(project_root=str(tmp_path), collection_id="", xli_dir=tmp_path / ".xlii"),
        clients=SimpleNamespace(),
        cfg=SimpleNamespace(retrieval_mode="hybrid", pricing={}),
    )
    result = t_codex_run_task(ctx, {"task": "migrate config loader", "mode": "ask"})
    assert not result.is_error
    assert "migration done" in result.content


def test_codex_run_task_rejects_bad_timeout(tmp_path):
    ctx = ToolContext(
        project=SimpleNamespace(project_root=str(tmp_path), collection_id="", xli_dir=tmp_path / ".xlii"),
        clients=SimpleNamespace(),
        cfg=SimpleNamespace(retrieval_mode="hybrid", pricing={}),
    )
    result = t_codex_run_task(ctx, {"task": "x", "timeout_s": "not-an-int"})
    assert result.is_error
    assert "timeout_s" in result.content


def test_acp_cursor_extensions_reject(tmp_path):
    from xlii.acp_client import AcpClient

    c = AcpClient(cwd=tmp_path, argv=[sys.executable, FAKE], permission="reject")
    with c:
        c.new_session()
        res = c.prompt("x")
    assert "plan=False" in res.text


def test_acp_update_todos_notification(tmp_path):
    from xlii.acp_client import AcpClient

    events = []
    c = AcpClient(
        cwd=tmp_path,
        argv=[sys.executable, FAKE],
        on_event=lambda k, u: events.append(k),
    )
    with c:
        c.new_session()
        c.prompt("x")
    assert "cursor/update_todos" in events
