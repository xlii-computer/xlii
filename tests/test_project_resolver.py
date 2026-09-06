import json

from xlii.project_resolver import project_is_alive, resolve_registered_project
from xlii.registry import Registry, RegistryEntry


def _write_project(root, name):
    (root / ".xlii").mkdir(parents=True)
    (root / ".xlii" / "project.json").write_text(json.dumps({
        "name": name,
        "root": str(root.resolve()),
        "collection_id": "",
        "created_at": "2026-01-01T00:00:00Z",
        "conversation_id": f"{name}-conv",
        "local_only": True,
    }))


def _write_legacy_project(root, name, *, collection_id="c-legacy"):
    (root / ".xli").mkdir(parents=True)
    (root / ".xli" / "project.json").write_text(json.dumps({
        "name": name,
        "root": str(root.resolve()),
        "collection_id": collection_id,
        "created_at": "2026-01-01T00:00:00Z",
        "conversation_id": f"{name}-conv",
    }))


def _entry(root, name, *, collection_id=""):
    return RegistryEntry(
        path=str(root.resolve()),
        collection_id=collection_id,
        name=name,
        created_at="2026-01-01T00:00:00Z",
    )


def test_project_is_alive_migrates_legacy_xli_only(tmp_path):
    root = tmp_path / "api"
    _write_legacy_project(root, "api")
    entry = _entry(root, "api", collection_id="c-legacy")

    assert project_is_alive(entry)
    assert (root / ".xlii" / "project.json").is_file()
    assert resolve_registered_project("api", registry=Registry(entries=[entry])).ok


def test_project_is_dead_when_incomplete_xlii_blocks_migration(tmp_path):
    root = tmp_path / "api"
    _write_legacy_project(root, "api", collection_id="c-legacy")
    (root / ".xlii").mkdir()
    entry = _entry(root, "api", collection_id="c-legacy")

    assert not project_is_alive(entry)


def test_resolves_exact_name(tmp_path):
    api = tmp_path / "api"
    _write_project(api, "api")
    registry = Registry(entries=[_entry(api, "api")])

    res = resolve_registered_project("api", registry=registry)

    assert res.ok
    assert res.exact
    assert res.project.name == "api"


def test_resolves_unique_substring_across_name_and_path(tmp_path):
    root = tmp_path / "billing-service"
    _write_project(root, "backend")
    registry = Registry(entries=[_entry(root, "backend")])

    assert resolve_registered_project("bill", registry=registry).ok


def test_reports_ambiguous_substring(tmp_path):
    api = tmp_path / "api"
    app = tmp_path / "app"
    _write_project(api, "api")
    _write_project(app, "app")
    registry = Registry(entries=[_entry(api, "api"), _entry(app, "app")])

    res = resolve_registered_project("ap", registry=registry)

    assert not res.ok
    assert res.ambiguous
    assert [e.name for e in res.matches] == ["api", "app"]


def test_dead_registry_entry_is_not_ok(tmp_path):
    entry = RegistryEntry(
        path=str(tmp_path / "missing"),
        collection_id="dead",
        name="dead",
        created_at="2026-01-01T00:00:00Z",
    )

    res = resolve_registered_project("dead", registry=Registry(entries=[entry]))

    assert not res.ok
    assert res.entry is entry
    assert not project_is_alive(entry)
