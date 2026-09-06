"""systemd-honest /kill — mocked systemctl, no live units."""

from types import SimpleNamespace

from xlii.daemon_kill import UNIT_CANDIDATES, disable_systemd_restart


def test_skip_under_pytest_home(monkeypatch):
    monkeypatch.setenv("XLII_TEST_HOME", "/tmp/xlii-test-home")
    assert "test" in disable_systemd_restart()


def test_no_systemd(monkeypatch):
    monkeypatch.delenv("XLII_TEST_HOME", raising=False)
    monkeypatch.setattr("xlii.daemon_kill._systemd_available", lambda: False)
    assert disable_systemd_restart() == "not systemd — process will exit"


def test_masks_user_unit(monkeypatch):
    monkeypatch.delenv("XLII_TEST_HOME", raising=False)
    monkeypatch.setattr("xlii.daemon_kill._systemd_available", lambda: True)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        unit = cmd[-1] if cmd else ""
        if "status" in cmd and unit == UNIT_CANDIDATES[0]:
            return SimpleNamespace(returncode=0, stdout="Loaded: loaded")
        return SimpleNamespace(returncode=0, stdout="")

    msg = disable_systemd_restart(run=fake_run)
    assert "masked" in msg
    assert any("mask" in c for c in calls)
    assert any("--now" in c for c in calls)
