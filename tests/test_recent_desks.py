"""Recent desks — Xlii menu MRU after Home."""

from __future__ import annotations

from types import SimpleNamespace

from xlii import recent_desks as R


def _proj(tmp_path, name, *, kind="code"):
    root = tmp_path / name.replace("/", "_")
    (root / ".xlii").mkdir(parents=True)
    (root / ".xlii" / "project.json").write_text("{}", encoding="utf-8")
    return SimpleNamespace(name=name, project_root=root, kind=kind)


def test_touch_skips_home(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "RECENT_FILE", tmp_path / "recent_desks.json")
    home = SimpleNamespace(name="scratch/home", project_root=tmp_path / "home")
    R.touch_desk(home)
    assert R.list_recent() == []


def test_touch_orders_newest_first_and_mixes_kinds(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "RECENT_FILE", tmp_path / "recent_desks.json")
    monkeypatch.setattr(R, "_alive", lambda path: True)
    lab = _proj(tmp_path, "iXaac-lab", kind="code")
    talk = _proj(tmp_path, "chat/research", kind="collection")
    R.touch_desk(lab)
    R.touch_desk(talk)
    rows = R.list_recent()
    assert [r["label"] for r in rows] == ["research", "iXaac-lab"]
    assert rows[0]["kind"] == "talk"
    assert rows[1]["kind"] == "lab"
    R.touch_desk(lab)
    assert [r["label"] for r in R.list_recent()] == ["iXaac-lab", "research"]


def test_list_recent_caps_and_drops_dead(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "RECENT_FILE", tmp_path / "recent_desks.json")
    monkeypatch.setattr(R, "SHOW", 3)
    live = []
    for i in range(6):
        p = _proj(tmp_path, f"p{i}")
        live.append(str(p.project_root.resolve()))
        R.touch_desk(p)
    rows = R.list_recent()
    assert len(rows) == 3
    assert [r["label"] for r in rows] == ["p5", "p4", "p3"]
    dead = tmp_path / "gone"
    (dead / ".xlii").mkdir(parents=True)
    (dead / ".xlii" / "project.json").write_text("{}", encoding="utf-8")
    R.touch_desk(SimpleNamespace(name="gone", project_root=dead, kind="code"))
    import shutil
    shutil.rmtree(dead)
    names = [r["label"] for r in R.list_recent(limit=10)]
    assert "gone" not in names
