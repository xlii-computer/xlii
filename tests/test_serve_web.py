"""`xlii serve` (proposals/serve-web.md W1) — offline pins, no [web] extra needed.

Same doctrine as the daemon/notify tests: registration + --help via subprocess,
extra-absent → friendly hint + exit 1, loopback default, banner text pinned
through the pure banner_lines helper.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from xlii.cmds.serve_web import _DEFAULT_PORT, _served_command, banner_lines


def _run(*args):
    return subprocess.run([sys.executable, "-m", "xlii", *args],
                          capture_output=True, text=True)


def test_serve_subcommand_is_registered():
    r = _run("serve", "--help")
    assert r.returncode == 0
    assert "--host" in r.stdout and "--port" in r.stdout and "--preview" in r.stdout
    assert "no authentication" in r.stdout.lower() or "NO AUTH" in r.stdout


def test_serve_without_extra_prints_friendly_hint():
    # Only meaningful without the [web] extra: with textual-serve importable,
    # `xlii serve` really serves (and would hang/bind here). serve-public made
    # the extra a first-class install, so this leg usually skips now.
    try:
        import textual_serve  # noqa: F401
        pytest.skip("[web] extra installed — no-extra path can't be exercised")
    except ImportError:
        # No [web] extra installed -- which is exactly the no-extra path this test exercises.
        pass
    # textual-serve isn't installed in the test env → lazy import fails →
    # friendly install hint on stderr, exit 1, no traceback.
    r = _run("serve")
    assert r.returncode == 1
    assert "web extra" in r.stderr
    assert 'pip install "xlii[web]"' in r.stderr  # bracket rendered literally
    assert "Traceback" not in r.stderr


def test_default_host_is_loopback_and_port_memorable():
    from xlii.cli import build_parser
    args = build_parser().parse_args(["serve"])
    assert args.host == "127.0.0.1"
    assert args.port == _DEFAULT_PORT
    assert args.preview is False


def test_served_command_is_the_tui_over_the_target():
    assert _served_command("/some/dir") == "xlii code /some/dir --tui"
    assert _served_command("/some/dir", preview=True) == (
        "xlii code /some/dir --preview --tui"
    )
    # Paths with spaces must survive the shell round-trip.
    assert "'/a b'" in _served_command("/a b")


def test_banner_always_says_no_auth_and_shell():
    lines = banner_lines("127.0.0.1", 8042, "xlii code /x --tui")
    joined = "\n".join(lines)
    assert "NO AUTH" in joined
    assert "SHELL ACCESS" in joined
    assert "tailnet" in joined  # the guidance, not just the warning


def test_serve_refuses_wildcard_bind_without_starting():
    from xlii.cmds.serve_web import cmd_serve

    args = type("A", (), {
        "face": False, "ws": False, "public": False,
        "host": "0.0.0.0", "port": 8042, "preview": False, "expose": False,
    })()
    assert cmd_serve(args) == 1


def test_serve_face_refuses_wildcard_bind(tmp_path):
    from xlii.serve_face import serve_face

    assert serve_face(tmp_path, host="0.0.0.0", port=0, handshake=True) == 1


def test_serve_ws_refuses_wildcard_bind(tmp_path):
    from xlii.config import ProjectConfig
    from xlii.ws_server import serve_ws

    project = ProjectConfig(
        project_root=tmp_path, name="t", collection_id="c",
        created_at="2026-01-01", local_only=True,
    )
    assert serve_ws(project, host="0.0.0.0", port=0, handshake=True) == 1


def test_bind_error_requires_expose_for_tailnet():
    from xlii.bind_posture import bind_error

    assert bind_error("0.0.0.0", expose=True, surface="serve") is not None
    assert bind_error("100.64.0.7", expose=False, surface="serve") is not None
    assert bind_error("100.64.0.7", expose=True, surface="serve") is None
    assert bind_error("127.0.0.1", expose=False, surface="serve") is None


def test_banner_escalates_for_wildcard_bind():
    joined = "\n".join(banner_lines("0.0.0.0", 8042, "cmd"))
    assert "DANGER" in joined


def test_banner_warns_on_non_localhost_bind():
    joined = "\n".join(banner_lines("100.64.0.7", 8042, "cmd"))
    assert "non-localhost" in joined
