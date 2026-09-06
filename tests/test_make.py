"""`xlii make` (S4 instant-apps) — the create→build→publish orchestration.

Pins the flow: validate the name, scaffold, build into the app dir, then publish
to <name>.<domain> and print ONLY the URL to stdout. The agent build turn and
the sftp publish are the shipped seams (reused, tested elsewhere); here we lock
the orchestration and its guards against fakes.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import xlii.cmds.make as make_mod


def _args(name="calc", description="a calculator", *, apps_dir, **kw):
    d = dict(name=name, description=description, domain="xlii-code.com",
             conn="appbox", root="srv/apps", apps_dir=apps_dir,
             no_publish=False, yolo=True)
    d.update(kw)
    return argparse.Namespace(**d)


def _fake_build_writing_index(app_dir, goal, *, yolo, ui):
    Path(app_dir).mkdir(parents=True, exist_ok=True)
    (Path(app_dir) / "index.html").write_text("<html>ok</html>")
    return 0


def test_invalid_name_is_refused_before_any_work(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(make_mod, "_scaffold", lambda *a, **k: calls.append("scaffold") or True)
    assert make_mod.cmd_make(_args(name="Bad Name!", apps_dir=str(tmp_path))) == 1
    assert calls == []                                    # refused before scaffolding
    assert "invalid app name" in capsys.readouterr().err


def test_happy_path_builds_publishes_and_prints_only_the_url(tmp_path, monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(make_mod, "_scaffold", lambda app_dir, name, ui: True)

    def fake_build(app_dir, goal, *, yolo, ui):
        seen["goal"] = goal
        return _fake_build_writing_index(app_dir, goal, yolo=yolo, ui=ui)
    monkeypatch.setattr(make_mod, "_build", fake_build)
    monkeypatch.setattr(make_mod, "_publish",
                        lambda app_dir, conn, root, host, ui: seen.setdefault("pub", (conn, root, host)) and 0 or 0)

    assert make_mod.cmd_make(_args(apps_dir=str(tmp_path))) == 0
    assert capsys.readouterr().out.strip() == "https://calc.xlii-code.com"   # URL only
    assert seen["pub"] == ("appbox", "srv/apps", "calc.xlii-code.com")
    # The build goal carries the description + the app-operator house rules.
    assert "a calculator" in seen["goal"] and "self-contained" in seen["goal"]


def test_name_is_lowercased_into_the_host(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(make_mod, "_scaffold", lambda *a, **k: True)
    monkeypatch.setattr(make_mod, "_build", _fake_build_writing_index)
    monkeypatch.setattr(make_mod, "_publish", lambda app_dir, conn, root, host, ui: 0)
    assert make_mod.cmd_make(_args(name="Calc", apps_dir=str(tmp_path))) == 0
    assert capsys.readouterr().out.strip() == "https://calc.xlii-code.com"


def test_no_publish_prints_local_path_and_skips_publish(tmp_path, monkeypatch, capsys):
    published = []
    monkeypatch.setattr(make_mod, "_scaffold", lambda *a, **k: True)
    monkeypatch.setattr(make_mod, "_build", _fake_build_writing_index)
    monkeypatch.setattr(make_mod, "_publish", lambda *a, **k: published.append(1) or 0)
    assert make_mod.cmd_make(_args(apps_dir=str(tmp_path), no_publish=True)) == 0
    assert published == []                                # nothing published
    assert str(tmp_path / "calc") in capsys.readouterr().out


def test_build_that_makes_no_index_is_an_error_and_does_not_publish(tmp_path, monkeypatch):
    published = []
    monkeypatch.setattr(make_mod, "_scaffold", lambda *a, **k: True)
    monkeypatch.setattr(make_mod, "_build", lambda *a, **k: 0)     # returns ok but writes nothing
    monkeypatch.setattr(make_mod, "_publish", lambda *a, **k: published.append(1) or 0)
    assert make_mod.cmd_make(_args(apps_dir=str(tmp_path))) == 1
    assert published == []


def test_build_failure_short_circuits_before_publish(tmp_path, monkeypatch):
    published = []
    monkeypatch.setattr(make_mod, "_scaffold", lambda *a, **k: True)
    monkeypatch.setattr(make_mod, "_build", lambda *a, **k: 1)
    monkeypatch.setattr(make_mod, "_publish", lambda *a, **k: published.append(1) or 0)
    assert make_mod.cmd_make(_args(apps_dir=str(tmp_path))) == 1
    assert published == []


def test_scaffold_creates_a_local_only_project(tmp_path):
    from rich.console import Console

    from xlii.config import ProjectConfig

    app_dir = tmp_path / "calc"
    assert make_mod._scaffold(app_dir, "calc", Console(stderr=True)) is True
    proj = ProjectConfig.load(app_dir)
    assert proj is not None and proj.local_only is True   # no Collection, no keys


def test_publish_writes_files_root_pointer(tmp_path, monkeypatch):
    from rich.console import Console

    from xlii.config import ProjectConfig

    app_dir = tmp_path / "calc"
    assert make_mod._scaffold(app_dir, "calc", Console(stderr=True)) is True

    class _Conn:
        pass

    monkeypatch.setattr("xlii.remotefs.manager.get", lambda name: _Conn())
    monkeypatch.setattr("xlii.remotefs.publish", lambda *a, **k: (1, 0, 0))
    assert make_mod._publish(
        app_dir, "appbox", "srv/apps", "calc.xlii-code.com", Console(stderr=True),
    ) == 0
    proj = ProjectConfig.load(app_dir)
    assert proj.files_root == "sftp://appbox/srv/apps/calc.xlii-code.com"
