"""CLI-level behaviours: exit-code propagation and the `xlii ask` error path.

`python -m xlii` used to call main() without sys.exit(), silently dropping every
subcommand's exit code (the installed console-script wrapper masked it). The XMPP
daemon's agent fallback checks the return code of `xlii ask`, so this matters.
"""

import subprocess
import sys

import pytest


def _run(*args):
    return subprocess.run([sys.executable, "-m", "xlii", *args],
                          capture_output=True, text=True)


def test_module_entrypoint_propagates_failure_exit_code():
    # `ask` in a non-project fails before any network/creds work → exit 1.
    r = _run("ask", "anything", "--workspace", "/tmp")
    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    assert "not an xlii project" in r.stderr


def test_module_entrypoint_returns_zero_on_success():
    r = _run("help")
    assert r.returncode == 0


def test_ask_keeps_stdout_clean_for_capture():
    # On the error path nothing should land on stdout (the reply channel).
    r = _run("ask", "anything", "--workspace", "/tmp")
    assert r.stdout.strip() == ""


def test_daemon_subcommand_is_registered():
    # --help proves the subcommand exists and its argparse is wired.
    r = _run("daemon", "--help")
    assert r.returncode == 0
    assert "--config" in r.stdout


def test_pair_subcommand_is_registered():
    r = _run("pair", "--help")
    assert r.returncode == 0
    assert "--invite" in r.stdout
    assert "--rail" in r.stdout
    r2 = _run("daemon", "pair", "--help")
    assert r2.returncode == 0
    assert "--invite" in r2.stdout


def test_daemon_without_extra_prints_friendly_hint():
    try:
        import slixmpp  # noqa: F401
        pytest.skip("[daemon] extra installed — no-extra path can't be exercised")
    except ImportError:
        # No [daemon] extra installed -- which is exactly the no-extra path this test exercises.
        pass
    # The [daemon] extra (slixmpp/OMEMO) isn't installed in the test env, so the
    # lazy import fails → friendly install hint on stderr, exit 1 (no traceback).
    r = _run("daemon")
    assert r.returncode == 1
    assert "daemon extra" in r.stderr
    assert 'pip install "xlii[daemon]"' in r.stderr  # bracket rendered literally
    assert "Traceback" not in r.stderr


def test_unknown_command_suggests_closest():
    # A command typo gets a did-you-mean hint (not argparse's 40-name dump) and a
    # pointer to `xlii help`, exit 2, no traceback.
    r = _run("stauts")
    assert r.returncode == 2
    assert "'stauts' is not a command" in r.stderr
    assert "status" in r.stderr          # closest real command suggested first
    assert "xlii help" in r.stderr
    assert "Traceback" not in r.stderr


def test_unknown_command_without_close_match_still_points_to_help():
    # No near match → no misleading suggestion, but still the help pointer.
    r = _run("zzzzzq")
    assert r.returncode == 2
    assert "is not a command" in r.stderr
    assert "Did you mean" not in r.stderr
    assert "xlii help" in r.stderr


def test_typo_inside_valid_command_defers_to_argparse():
    # A bad flag on a REAL command must not trigger the did-you-mean path — it is
    # argparse's own error, unchanged.
    r = _run("status", "--bogus")
    assert r.returncode == 2
    assert "unrecognized arguments" in r.stderr
    assert "is not a command" not in r.stderr


def test_notify_subcommand_is_registered():
    r = _run("notify", "--help")
    assert r.returncode == 0
    assert "message" in r.stdout


def test_notify_without_extra_prints_friendly_hint():
    try:
        import slixmpp  # noqa: F401
        pytest.skip("[daemon] extra installed — no-extra path can't be exercised")
    except ImportError:
        # Same: no [daemon] extra, so the friendly-hint path for notify is reachable.
        pass
    r = _run("notify", "hello from the desktop")
    assert r.returncode == 1
    assert "daemon extra" in r.stderr
    assert 'pip install "xlii[daemon]"' in r.stderr
    assert "Traceback" not in r.stderr


_GOOD_FP = "a" * 64  # 64 hex chars = a 32-byte OMEMO identity key


def test_daemon_trust_subcommand_is_registered():
    r = _run("daemon", "trust", "--help")
    assert r.returncode == 0
    assert "jid" in r.stdout and "fingerprint" in r.stdout
    assert "--distrust" in r.stdout


def test_daemon_trust_rejects_bad_jid_before_touching_the_extra():
    # Input validation runs first, so this is deterministic with or without the
    # [daemon] extra — a typo'd JID fails fast and cleanly, no traceback.
    r = _run("daemon", "trust", "not-a-jid", _GOOD_FP)
    assert r.returncode == 1
    assert "not a bare JID" in r.stderr
    assert "Traceback" not in r.stderr


def test_daemon_trust_rejects_bad_fingerprint():
    r = _run("daemon", "trust", "me@phone.tailnet", "deadbeef")
    assert r.returncode == 1
    assert "64 hex" in r.stderr
    assert "Traceback" not in r.stderr


def test_daemon_trust_valid_input_without_extra_prints_hint():
    try:
        import slixmpp  # noqa: F401
        pytest.skip("[daemon] extra installed — no-extra path can't be exercised")
    except ImportError:
        # Same: no [daemon] extra, so the friendly-hint path for daemon trust is reachable.
        pass
    # Well-formed jid + fingerprint, but the extra is absent in the test env →
    # the lazy import fails after validation → friendly install hint, exit 1.
    r = _run("daemon", "trust", "me@phone.tailnet", _GOOD_FP)
    assert r.returncode == 1
    assert "daemon extra" in r.stderr
    assert 'pip install "xlii[daemon]"' in r.stderr
    assert "Traceback" not in r.stderr
