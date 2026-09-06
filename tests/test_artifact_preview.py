"""External artifact preview (media M3) — GUI launch vs headless, mocked."""

from __future__ import annotations

from types import SimpleNamespace

from xlii import artifact_preview as ap


def _img(tmp_path):
    p = tmp_path / "img-test.png"
    p.write_bytes(b"\x89PNG-fake")
    return p


def test_configured_image_editor_beats_xdg(tmp_path, monkeypatch):
    launched = []
    monkeypatch.setattr("xlii.desk.resolve_image_editor", lambda cfg=None: "gimp")
    monkeypatch.setattr(ap, "_has_display", lambda: True)
    monkeypatch.setattr(ap, "_opener_command", lambda: ["xdg-open"])
    monkeypatch.setattr(ap.subprocess, "Popen",
                        lambda argv, **kw: launched.append(argv) or SimpleNamespace())
    p = _img(tmp_path)
    assert ap.open_artifact(p) is True
    assert launched == [["gimp", str(p)]]


def test_gui_session_launches_the_os_opener(tmp_path, monkeypatch):
    launched = []
    monkeypatch.setattr(ap, "_opener_command", lambda: ["xdg-open"])
    monkeypatch.setattr(ap, "_has_display", lambda: True)
    monkeypatch.setattr(ap.subprocess, "Popen",
                        lambda argv, **kw: launched.append(argv) or SimpleNamespace())
    p = _img(tmp_path)
    assert ap.open_artifact(p) is True
    assert launched == [["xdg-open", str(p)]]


def test_headless_prints_the_path_and_noops(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ap, "_opener_command", lambda: ["xdg-open"])
    monkeypatch.setattr(ap, "_has_display", lambda: False)
    monkeypatch.setattr(ap.subprocess, "Popen",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not launch")))
    p = _img(tmp_path)
    assert ap.open_artifact(p) is False       # no window…
    assert str(p) in capsys.readouterr().out  # …but the path IS the preview


def test_no_opener_available_degrades_to_path(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ap, "_opener_command", lambda: None)
    p = _img(tmp_path)
    assert ap.open_artifact(p) is False
    assert str(p) in capsys.readouterr().out


def test_missing_artifact_reports(tmp_path, capsys):
    assert ap.open_artifact(tmp_path / "nope.png") is False
    assert "not found" in capsys.readouterr().out


def test_launch_failure_degrades_to_path(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ap, "_opener_command", lambda: ["xdg-open"])
    monkeypatch.setattr(ap, "_has_display", lambda: True)

    def _boom(*a, **k):
        raise OSError("no opener")

    monkeypatch.setattr(ap.subprocess, "Popen", _boom)
    p = _img(tmp_path)
    assert ap.open_artifact(p) is False
    assert str(p) in capsys.readouterr().out


def test_main_headless_exits_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ap, "_has_display", lambda: False)
    p = _img(tmp_path)
    assert ap.main([str(p)]) == 0             # headless is SUCCESS, not failure
    assert str(p) in capsys.readouterr().out


def test_main_missing_path_exits_nonzero(tmp_path):
    assert ap.main([str(tmp_path / "nope.png")]) == 1


def test_main_help():
    assert ap.main([]) == 0
