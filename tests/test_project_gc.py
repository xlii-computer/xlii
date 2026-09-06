from __future__ import annotations

from types import SimpleNamespace

from xlii.cmds.project import gc as gc_mod
from xlii.registry import Registry, RegistryEntry
from tests.helpers import FakeConsole


class _FakeCollections:
    def __init__(self, cloud=None):
        self._cloud = list(cloud or [])
        self.deleted: list[str] = []

    def list(self, limit=500, pagination_token=None):
        cols = [
            SimpleNamespace(collection_id=cid, collection_name=name)
            for cid, name in self._cloud
        ]
        return SimpleNamespace(collections=cols, pagination_token=None)

    def delete(self, collection_id):
        self.deleted.append(collection_id)


def test_gc_preserves_local_only_registry_entries(tmp_path, monkeypatch):
    monkeypatch.setattr("xlii.registry.REGISTRY_FILE", tmp_path / "projects.json")
    monkeypatch.setattr(gc_mod, "console", FakeConsole())
    collections = _FakeCollections(cloud=[])
    clients = SimpleNamespace(xai=SimpleNamespace(collections=collections))
    monkeypatch.setattr(gc_mod.Clients, "from_config", lambda cfg: clients)

    Registry(entries=[
        RegistryEntry(path=str(tmp_path / "one"), collection_id="", name="one", created_at="now"),
        RegistryEntry(path=str(tmp_path / "two"), collection_id="", name="two", created_at="now"),
    ]).save()

    rc = gc_mod.cmd_gc(SimpleNamespace(dry_run=False, yes=True))

    assert rc == 0
    assert collections.deleted == []
    assert [e.name for e in Registry.load().entries] == ["one", "two"]
