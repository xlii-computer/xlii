"""xlii/doctor.py — the kernel doctor engine (GVM B3), plus its two kernel
helpers: hooks.inert_hooks and vault.master_key_backend.

The CLI façade (cmds/diag.py) and the old import paths are covered by the
pre-existing suites (test_doctor_fix.py, test_grades_phases.py doctor test,
test_howto.py), which pass unmodified against the re-exports.
"""

from __future__ import annotations

import os
import stat
from argparse import Namespace

import pytest

from xlii.doctor import (
    DoctorFinding,
    DoctorReport,
    apply_doctor_fix,
    collect_doctor_findings,
    is_runnable_fix,
)


# --------------------------------------------------------------------------- #
#  whitelist + fix executor (kernel import paths)
# --------------------------------------------------------------------------- #

def test_whitelist_and_report_from_kernel():
    assert is_runnable_fix("chmod 600 /tmp/cfg")
    assert is_runnable_fix("xlii sync --dry-run")
    assert not is_runnable_fix("sudo ln -sf /a /b")
    report = DoctorReport(
        findings=[
            DoctorFinding("ok", "fine"),
            DoctorFinding("warn", "g", "echo '.xlii/' >> .gitignore"),
        ],
        problems=0,
        warn_count=1,
    )
    assert report.exit_code == 0
    assert [f.message for f in report.runnable()] == ["g"]


def test_apply_chmod_600_from_kernel(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{}")
    os.chmod(p, 0o644)
    assert "chmod 600" in apply_doctor_fix(f"chmod 600 {p}")
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_apply_rejects_non_whitelist():
    with pytest.raises(ValueError, match="not auto-applicable"):
        apply_doctor_fix("sudo ln -sf /a /b")


def test_apply_sync_runs_in_process_not_subprocess(tmp_path, monkeypatch):
    """The `xlii sync` fix must call xlii/sync.py directly — a body has no
    `xlii` binary on PATH. Dry-run keeps it mutation-free."""
    calls = []

    class _Stats:
        def summary(self):
            return "0 uploaded, 0 updated, 0 deleted"

    monkeypatch.setattr("xlii.sync.sync_project",
                        lambda clients, project, cfg, **kw: calls.append(kw) or _Stats())
    monkeypatch.setattr("xlii.doctor.GlobalConfig",
                        type("G", (), {"load": staticmethod(lambda: object())}))
    monkeypatch.setattr("xlii.doctor.Clients",
                        type("C", (), {"from_config": staticmethod(lambda cfg: object())}))
    monkeypatch.setattr("xlii.doctor.ProjectConfig",
                        type("P", (), {"load": staticmethod(lambda root: object())}))
    apply_doctor_fix("xlii sync --dry-run", cwd=tmp_path)
    assert calls == [{"dry_run": True}]


def test_apply_sync_times_out_after_120s(tmp_path, monkeypatch):
    import threading
    import time
    release = threading.Event()
    def _hung_sync(*_a, **_kw):
        release.wait(timeout=30)
        raise RuntimeError("should have timed out before returning")
    monkeypatch.setattr("xlii.doctor._IN_PROCESS_SYNC_TIMEOUT_S", 0.01)
    monkeypatch.setattr("xlii.sync.sync_project", _hung_sync)
    monkeypatch.setattr("xlii.doctor.GlobalConfig", type("G", (), {"load": staticmethod(lambda: object())}))
    monkeypatch.setattr("xlii.doctor.Clients", type("C", (), {"from_config": staticmethod(lambda cfg: object())}))
    monkeypatch.setattr("xlii.doctor.ProjectConfig", type("P", (), {"load": staticmethod(lambda root: object())}))
    t0 = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match="timed out after 0.01s"):
            apply_doctor_fix("xlii sync --dry-run", cwd=tmp_path)
        # the timeout must propagate without joining the hung worker
        assert time.monotonic() - t0 < 2
    finally:
        release.set()


def test_apply_sync_outside_project_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr("xlii.doctor.GlobalConfig",
                        type("G", (), {"load": staticmethod(lambda: object())}))
    monkeypatch.setattr("xlii.doctor.Clients",
                        type("C", (), {"from_config": staticmethod(lambda cfg: object())}))
    from xlii.config import ProjectConfig
    assert ProjectConfig.load(tmp_path) is None  # tmp_path is not a project
    with pytest.raises(RuntimeError, match="not an xlii project"):
        apply_doctor_fix("xlii sync --dry-run", cwd=tmp_path)


# --------------------------------------------------------------------------- #
#  collect_doctor_findings — pure collector + emit stream
# --------------------------------------------------------------------------- #

def _run_collect(tmp_path, monkeypatch, config_name="nope.json", **kw):
    monkeypatch.setattr("xlii.doctor.ProjectConfig",
                        type("P", (), {"load": staticmethod(lambda root: None)}))
    return collect_doctor_findings(
        tmp_path, config_file=tmp_path / config_name, **kw
    )


def test_collector_is_pure_without_emit(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("xlii.vault._resolve_key", lambda: (None, None))
    report = _run_collect(tmp_path, monkeypatch)
    assert capsys.readouterr().out == ""  # no printing
    kinds = {f.severity for f in report.findings}
    assert "bad" in kinds  # missing config.json is a problem
    assert report.exit_code == 1
    # the vault/legacy-aliases line still lands as an ok finding
    assert any("legacy aliases OK" in f.message for f in report.findings)


def test_collector_emit_streams_in_order(tmp_path, monkeypatch):
    monkeypatch.setattr("xlii.vault._resolve_key", lambda: (None, None))
    events: list[tuple[str, object]] = []
    report = _run_collect(tmp_path, monkeypatch, emit=lambda k, p: events.append((k, p)))
    assert events[0] == ("header", "install")
    assert events[-1] == ("note", "[dim](not inside an xlii project — project checks skipped)[/dim]")
    streamed = [p for k, p in events if k == "finding"]
    assert streamed == report.findings  # every finding streamed, in order


def test_collector_threads_config_file(tmp_path, monkeypatch):
    """The config-file path is a parameter, not a baked-in global."""
    cfg = tmp_path / "config.json"
    cfg.write_text("{}")
    os.chmod(cfg, 0o600)
    monkeypatch.setattr("xlii.vault._resolve_key", lambda: (None, None))
    monkeypatch.setattr("xlii.doctor.GlobalConfig", type("G", (), {
        "load": staticmethod(lambda: type("C", (), {
            "key_pairs": staticmethod(lambda: [("k", "v")]),
            "plaintext_key_count": staticmethod(lambda: 0),
            "effective_judges": staticmethod(lambda: {}),
        })()),
        "mgmt_key_in_file": staticmethod(lambda: False),
    }))
    report = _run_collect(tmp_path, monkeypatch, config_name="config.json")
    assert any(f.message == "config.json present, perms 0600" for f in report.findings)


def test_facade_run_doctor_matches_collector(tmp_path, monkeypatch):
    """cmds/diag.run_doctor(args) keeps its old signature and exit contract."""
    from xlii.cmds import diag

    monkeypatch.setattr(diag, "GLOBAL_CONFIG_FILE", tmp_path / "nope.json")
    monkeypatch.setattr(diag.ProjectConfig, "load", classmethod(lambda cls, *a, **k: None))
    monkeypatch.setattr("xlii.vault._resolve_key", lambda: (None, None))
    report = diag.run_doctor(Namespace(online=False, migrate_legacy=False, dry_run=False))
    assert isinstance(report, DoctorReport)
    assert report.exit_code == 1  # missing config.json


# --------------------------------------------------------------------------- #
#  hooks.inert_hooks + vault.master_key_backend
# --------------------------------------------------------------------------- #

def test_inert_hooks_finds_only_non_executable(tmp_path):
    from xlii.hooks import inert_hooks

    hooks = tmp_path / "hooks" / "pre-turn"
    hooks.mkdir(parents=True)
    live = hooks / "live.sh"
    dead = hooks / "dead.sh"
    for p in (live, dead):
        p.write_text("#!/bin/sh\n")
    os.chmod(live, 0o755)
    os.chmod(dead, 0o644)
    assert inert_hooks(tmp_path) == [dead]
    assert inert_hooks(tmp_path / "empty") == []  # no hooks dir → no inert hooks


def test_master_key_backend_delegates_the_chain(monkeypatch):
    import xlii.vault as vault

    monkeypatch.setattr(vault, "_resolve_key", lambda: (b"k", vault.BACKEND_ENV))
    assert vault.master_key_backend() == (b"k", vault.BACKEND_ENV)
