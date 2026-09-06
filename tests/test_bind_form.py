"""Bind chrome picker — closed HTML seeds /bind."""

from __future__ import annotations

from pathlib import Path

from xlii import binds as B
from xlii.bind_form import form_spec
from xlii.panes.bind_make import BindMakePane
from xlii.panes.tasks import TasksPane


def _xli(tmp_path: Path) -> Path:
    d = tmp_path / ".xlii"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_form_lists_stock_and_defaults(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setenv("XLII_CONFIG_DIR", str(cfg))
    xli = _xli(tmp_path)
    spec = form_spec(xli)
    ids = {t["id"] for t in spec["tasks"]}
    assert "new-folder" in ids
    assert "echo-hello" in ids
    keys = {f["key"] for f in spec["fkeys"]}
    assert keys == {f"f{i}" for i in range(1, 13)}
    f1 = next(f for f in spec["fkeys"] if f["key"] == "f1")
    assert f1["default"] == "help" and f1["bound"] == ""
    assert "/bind " in spec["html"]
    assert "F1" in spec["html"] or "f1" in spec["html"]


def test_form_shows_current_bind(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setenv("XLII_CONFIG_DIR", str(cfg))
    B.save_user_binds([B.Bind(task="echo-hello", menu="tools", fkey="f11")])
    spec = form_spec(_xli(tmp_path))
    assert any(b["task"] == "echo-hello" and b["fkey"] == "f11" for b in spec["binds"])
    assert "echo-hello" in spec["html"]
    f11 = next(f for f in spec["fkeys"] if f["key"] == "f11")
    assert f11["bound"] == "echo-hello"


def test_pane_form_and_tasks_action(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setenv("XLII_CONFIG_DIR", str(cfg))
    monkeypatch.setattr("xlii.active_session.active_xli_dir", lambda: _xli(tmp_path))
    pane = BindMakePane("bindmake://")
    rendered = pane.render()
    assert rendered.form and rendered.form.get("html")
    acts = TasksPane("tasks://").actions()
    assert any(a.name == "binds" and a.outcome.address == "bindmake://" for a in acts)
