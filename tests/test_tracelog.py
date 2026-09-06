"""Coverage for the auto-tracelog file sink.

Verifies it is opt-out-able, lands under the config dir, and actually captures
the invocation, downstream loguru diagnostics, and the exit code. The loguru
sink is global state, so the write test restores a default handler afterward."""

import sys

import xlii.tracelog as tl


def test_disabled_by_env(monkeypatch):
    monkeypatch.setenv("XLII_NO_TRACELOG", "1")
    assert tl.enabled() is False
    assert tl.setup_tracelog() is None
    monkeypatch.setenv("XLII_NO_TRACELOG", "true")
    assert tl.enabled() is False


def test_path_under_config_logs_dir():
    p = tl.tracelog_path()
    assert p.name == "xlii.log"
    assert p.parent.name == "logs"


def test_writes_invocation_errors_and_exit(tmp_path, monkeypatch):
    from loguru import logger

    monkeypatch.delenv("XLII_NO_TRACELOG", raising=False)
    logfile = tmp_path / "xlii.log"
    monkeypatch.setattr(tl, "tracelog_path", lambda: logfile)

    fid = tl.setup_tracelog()
    try:
        assert fid is not None
        tl.log_invocation(["xlii", "keys", "list"])
        logger.error("sync exploded")  # an existing-style diagnostic flows to the file
        tl.log_exit(0)
        text = logfile.read_text()
        assert "invoked: xlii keys list" in text
        assert "sync exploded" in text
        assert "exit: 0" in text
    finally:
        logger.remove()
        logger.add(sys.stderr)  # restore a sane default for the rest of the suite


def test_crash_is_captured(tmp_path, monkeypatch):
    from loguru import logger

    monkeypatch.delenv("XLII_NO_TRACELOG", raising=False)
    logfile = tmp_path / "xlii.log"
    monkeypatch.setattr(tl, "tracelog_path", lambda: logfile)

    tl.setup_tracelog()
    try:
        tl.log_crash(ValueError("boom"))
        text = logfile.read_text()
        assert "invocation crashed" in text and "boom" in text
    finally:
        logger.remove()
        logger.add(sys.stderr)
