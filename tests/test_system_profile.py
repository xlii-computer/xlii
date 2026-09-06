"""Tests for OS / distro system profile (Phase 2)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xlii.system_profile import (
    SystemProfile,
    clear_session_profile,
    detect,
    load_system_profile,
    refresh_system_profile,
    resolve_package_name,
    summary_line,
)


@pytest.fixture(autouse=True)
def _clear_profile_cache():
    clear_session_profile()
    yield
    clear_session_profile()


def test_detect_returns_populated_profile():
    profile = detect()
    assert profile.os_name
    assert profile.kernel
    assert profile.shell


def test_debian_like_pkg_hints_in_summary():
    profile = SystemProfile(
        os_name="Parrot OS",
        os_id="parrot",
        os_like=["debian"],
        package_manager="apt",
        init_system="systemd",
        coreutils="GNU",
        shell="bash",
    )
    line = summary_line(profile)
    assert "Parrot OS" in line
    assert "pkg=apt" in line
    assert "fd→fd-find" in line
    assert "THIS platform" in line


def test_resolve_package_name_debian():
    profile = SystemProfile(os_id="ubuntu", os_like=["debian"])
    assert resolve_package_name(profile, "fd") == "fd-find"
    assert resolve_package_name(profile, "ripgrep") == "ripgrep"


def test_session_memo_returns_same_object():
    first = load_system_profile()
    second = load_system_profile()
    assert first is not None
    assert first is second


def test_refresh_forces_redetect(monkeypatch):
    load_system_profile()
    monkeypatch.setattr(
        "xlii.system_profile.detect",
        lambda: SystemProfile(os_name="Fresh", os_id="fresh", detected_at="new"),
    )
    refreshed = refresh_system_profile()
    assert refreshed is not None
    assert refreshed.os_name == "Fresh"
    assert load_system_profile() is refreshed


def test_system_prompt_includes_system_block(monkeypatch):
    from xlii.turn_prompt import build_code_system_prompt

    monkeypatch.setattr(
        "xlii.system_profile.load_system_profile",
        lambda **_: SystemProfile(
            os_name="Parrot OS",
            os_id="parrot",
            os_like=["debian"],
            package_manager="apt",
            init_system="systemd",
            coreutils="GNU",
            shell="bash",
        ),
    )

    from pathlib import Path

    project = SimpleNamespace(
        project_root=Path("/tmp"),
        xli_dir=Path("/tmp/.xlii"),
        local_only=False,
    )
    prompt = build_code_system_prompt(project)
    assert "[SYSTEM]" in prompt
    assert "pkg=apt" in prompt
    assert "fd→fd-find" in prompt


def test_probe_coreutils_busybox(monkeypatch):
    import subprocess

    class _Proc:
        stdout = "ls (BusyBox) 1.36.0"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc())
    profile = detect()
    assert profile.coreutils == "busybox"


def test_probe_coreutils_gnu(monkeypatch):
    # Real GNU coreutils prints `ls (GNU coreutils) 9.7` — uppercase "GNU".
    # Guards the case-sensitivity regression where matching against the
    # original-case line (not the lowercased copy) misdetected GNU as "unknown".
    import subprocess

    class _Proc:
        stdout = "ls (GNU coreutils) 9.7"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc())
    profile = detect()
    assert profile.coreutils == "GNU"
