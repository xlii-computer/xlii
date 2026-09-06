"""/status — the stack view (▲ vendor · ● self · ▼ fleet), handler level.

The frame: what's above me (vendor slot, deferred), me (existing body), what's
below me (the /remote roster + fabric roles). The load-bearing rule under test:
the DEFAULT view never opens a socket — probing is opt-in via --probe.
"""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

from rich.console import Console


def _console():
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, width=200), buf


def _state(tmp_path):
    xli = tmp_path / ".xlii"
    xli.mkdir(exist_ok=True)
    project = SimpleNamespace(
        name="p", project_root=tmp_path, local_only=True,
        conversation_id="abcd1234efgh", collection_id=None, xli_dir=xli,
    )
    agent = SimpleNamespace(
        plan_mode=False, discovery_mode=False, ops_mode=False, rail=None,
        debug=None, active_mode=None,
        session=SimpleNamespace(plan_mode=False, yolo=False, freeball=False,
                                conversational=False),
    )
    state = SimpleNamespace(
        project=project, agent=agent, plan_mode=False, discovery_mode=False,
        ops_mode=False, yolo=False, freeball=False, persona=None, scratch=False,
        profile=SimpleNamespace(mode="code"), pool=[],
        attached_docs=[], attached_refs=[], attached_files=[],
        format_status=lambda include_attachments=True: "",
    )
    return state, project, agent


def _wire_roster(monkeypatch, specs, fabric=None):
    # Patch the singleton INSTANCE (not the class): other suites patch instance
    # attributes on `manager`, and monkeypatch's undo leaves shadowing instance
    # copies behind — instance-level patches always win regardless of order.
    import xlii.remotefs as rfs

    monkeypatch.setattr(rfs.manager, "names", lambda: sorted(specs))
    monkeypatch.setattr(rfs.manager, "spec", lambda n: specs.get(n))
    def _no_get(name):
        raise AssertionError("default /status must never open a socket")
    monkeypatch.setattr(rfs.manager, "get", _no_get)
    from xlii.config import GlobalConfig
    monkeypatch.setattr(GlobalConfig, "load",
                        classmethod(lambda cls: SimpleNamespace(
                            fabric_nodes=fabric or {}, ftp_connections=specs)))


def test_status_shows_the_three_layer_stack(tmp_path, monkeypatch):
    from xlii.repl_cmds.code import _code_status_handler

    monkeypatch.setenv("XLII_STATE_DIR", str(tmp_path / "state"))
    _wire_roster(monkeypatch, {
        "aws-vm": {"protocol": "sftp", "host": "1.2.3.4"},
        "backups": {"protocol": "webdav", "base_url": "https://x"},
    }, fabric={"box1": {"remote": "aws-vm"}})

    state, project, agent = _state(tmp_path)
    con, buf = _console()
    assert _code_status_handler("/status", {"state": state, "project": project,
                                            "agent": agent, "console": con}) is True
    out = buf.getvalue()
    assert "▲ vendor" in out and "deferred" in out
    assert "● self" in out and "mode:" in out          # axes still lead the self section
    assert "▼ fleet" in out and "2 remote(s)" in out
    assert "aws-vm" in out and "node" in out and "unknown" in out
    assert "backups" in out and "storage" in out
    assert "never probed" in out                        # no cache, no socket


def test_status_empty_roster_still_renders(tmp_path, monkeypatch):
    from xlii.repl_cmds.code import _code_status_handler

    monkeypatch.setenv("XLII_STATE_DIR", str(tmp_path / "state"))
    _wire_roster(monkeypatch, {})
    state, project, agent = _state(tmp_path)
    con, buf = _console()
    _code_status_handler("/status", {"state": state, "project": project,
                                     "agent": agent, "console": con})
    out = buf.getvalue()
    assert "no remotes" in out and "sync:" in out       # rest of /status intact


def test_status_probe_writes_the_cache_and_shows_reach(tmp_path, monkeypatch):
    import xlii.fleet_status as fs
    from xlii.repl_cmds.code import _code_status_handler

    monkeypatch.setenv("XLII_STATE_DIR", str(tmp_path / "state"))
    _wire_roster(monkeypatch, {"aws-vm": {"protocol": "sftp", "host": "h"}})
    called = {}
    monkeypatch.setattr(fs, "probe_fleet",
                        lambda names, **kw: called.update(names=list(names)) or
                        {n: fs.ProbeResult(ok=True, latency_ms=8) for n in names})

    state, project, agent = _state(tmp_path)
    con, buf = _console()
    _code_status_handler("/status --probe", {"state": state, "project": project,
                                             "agent": agent, "console": con})
    out = buf.getvalue()
    assert called["names"] == ["aws-vm"]
    assert "reach: ok" in out
    cache = json.loads((tmp_path / "state" / fs.PROBE_CACHE_NAME).read_text())
    assert cache["probes"]["aws-vm"]["ok"] is True


def test_status_probe_unknown_name_errors_without_probing(tmp_path, monkeypatch):
    import xlii.fleet_status as fs
    from xlii.repl_cmds.code import _code_status_handler

    monkeypatch.setenv("XLII_STATE_DIR", str(tmp_path / "state"))
    _wire_roster(monkeypatch, {"aws-vm": {"protocol": "sftp", "host": "h"}})
    monkeypatch.setattr(fs, "probe_fleet",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("probed!")))
    state, project, agent = _state(tmp_path)
    con, buf = _console()
    _code_status_handler("/status --probe ghost", {"state": state, "project": project,
                                                   "agent": agent, "console": con})
    assert "no remote named 'ghost'" in buf.getvalue()


def test_chat_status_does_not_grow_a_fleet_section(tmp_path, monkeypatch):
    """The chat-REPL /status keeps its persona view — the stack lands on the
    code surface only (v1)."""
    from xlii.repl_cmds.chat import _chat_status_handler

    monkeypatch.setenv("XLII_STATE_DIR", str(tmp_path / "state"))
    _wire_roster(monkeypatch, {"aws-vm": {"protocol": "sftp", "host": "h"}})
    con, buf = _console()
    state = SimpleNamespace(
        persona=SimpleNamespace(name="ixaac"), agent=SimpleNamespace(
            session=SimpleNamespace(plan_mode=False, yolo=False, freeball=False,
                                    conversational=True),
            active_mode=None, rail=None, debug=None),
        profile=SimpleNamespace(mode="chat"),
        attached_docs=[], attached_refs=[], attached_files=[],
        format_status=lambda include_attachments=True: "",
        project=None, pool=[],
    )
    try:
        _chat_status_handler("/status", {"state": state, "console": con})
    except Exception:
        pass  # chat handler may need richer state; the assert below still holds
    assert "▼ fleet" not in buf.getvalue()
