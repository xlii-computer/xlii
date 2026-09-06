"""Doctor structured findings + whitelisted apply (howto self-repair)."""

from __future__ import annotations

import os
import stat

import pytest

from xlii.cmds.diag import (
    DoctorFinding,
    DoctorReport,
    apply_doctor_fix,
    is_runnable_fix,
)


@pytest.mark.parametrize(
    "fix,ok",
    [
        ("chmod 600 /tmp/cfg", True),
        ("chmod +x /tmp/hook", True),
        ("chmod +x /tmp/hook …", True),
        ("echo '.xlii/' >> .gitignore", True),
        ("rename to .xliiignore", True),
        ("xlii keys migrate", True),
        ("xlii sync", True),
        ("xlii sync --dry-run", True),
        ("sudo ln -sf /a /b", False),
        ("remove it from the file; export XAI_MANAGEMENT_API_KEY instead", False),
        ("add keys[] to config.json or run `xlii setup`", False),
        ("delete .xlii/manifest.json and re-sync", False),
        ("", False),
    ],
)
def test_is_runnable_fix(fix, ok):
    assert is_runnable_fix(fix) is ok


def test_apply_chmod_600(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{}")
    os.chmod(p, 0o644)
    summary = apply_doctor_fix(f"chmod 600 {p}")
    assert "chmod 600" in summary
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_apply_gitignore(tmp_path):
    gi = tmp_path / ".gitignore"
    gi.write_text("*.pyc\n")
    apply_doctor_fix("echo '.xlii/' >> .gitignore", cwd=tmp_path)
    assert ".xlii/" in gi.read_text()


def test_apply_chmod_x_repairs_all_files(tmp_path):
    """The inert-hooks fix must chmod +x EVERY listed file, not just the first —
    the warning counts them all."""
    a = tmp_path / "hook_a"
    b = tmp_path / "hook_b"
    for p in (a, b):
        p.write_text("#!/bin/sh\n")
        os.chmod(p, 0o644)
    fix = f"chmod +x {a} {b}"
    assert is_runnable_fix(fix)
    summary = apply_doctor_fix(fix)
    assert os.access(a, os.X_OK)
    assert os.access(b, os.X_OK)
    assert str(a) in summary and str(b) in summary


def test_apply_rename_xliignore(tmp_path):
    (tmp_path / ".xliignore").write_text("node_modules\n")
    apply_doctor_fix("rename to .xliiignore", cwd=tmp_path)
    assert not (tmp_path / ".xliignore").exists()
    assert (tmp_path / ".xliiignore").read_text() == "node_modules\n"


def test_apply_rejects_sudo():
    with pytest.raises(ValueError, match="not auto-applicable"):
        apply_doctor_fix("sudo ln -sf /a /b")


def test_report_runnable_filters():
    report = DoctorReport(
        findings=[
            DoctorFinding("ok", "fine"),
            DoctorFinding("warn", "gitignore", "echo '.xlii/' >> .gitignore"),
            DoctorFinding("bad", "shim", "sudo ln -sf /a /b"),
            DoctorFinding("warn", "advice", "add keys[] to config.json"),
        ],
        problems=1,
        warn_count=2,
    )
    runnable = report.runnable()
    assert len(runnable) == 1
    assert runnable[0].fix == "echo '.xlii/' >> .gitignore"
