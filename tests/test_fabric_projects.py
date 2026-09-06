"""Collection-first fabric projects — one menu, throne Home stays off it."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from xlii.fabric_projects import (
    apply_remote_entries,
    create_fabric_project,
    federated_visible,
    is_throne_home_entry,
    is_throne_home_name,
    parse_registry_bytes,
    pointer_dir,
    push_catalog_to_node,
    sync_fabric_projects,
    unique_registry_name,
)
from xlii.registry import Registry, RegistryEntry, _entry_from_dict


class FakeConn:
    def __init__(self, files=None):
        self.files = dict(files or {})  # path -> bytes
        self.writes = []
        self.dirs = set()

    def read(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def write(self, path, data):
        self.files[path] = data
        self.writes.append(path)

    def mkdir(self, path):
        self.dirs.add(path.rstrip("/"))

    def makedirs(self, path):
        self.dirs.add(path.rstrip("/"))


@pytest.fixture()
def regfile(tmp_path, monkeypatch):
    from xlii import registry as regmod

    f = tmp_path / "projects.json"
    f.write_text(json.dumps({"entries": []}))
    monkeypatch.setattr(regmod, "REGISTRY_FILE", f)
    return f


def _row(name, cid, path, node=""):
    return RegistryEntry(
        path=path, collection_id=cid, name=name, created_at="t", node=node,
    )


def test_throne_home_is_sacred():
    assert is_throne_home_name("scratch/home")
    assert is_throne_home_name("Scratch/Home")
    assert not is_throne_home_name("scratch/notes")
    assert is_throne_home_entry(SimpleNamespace(name="scratch/home", path="/x"))
    assert is_throne_home_entry(SimpleNamespace(
        name="desk", path="/home/n/.xlii/scratch/home",
    ))
    visible = federated_visible([
        _row("scratch/home", "", "/a/scratch/home"),
        _row("app", "c1", "/a/app"),
        _row("scratch/notes", "c2", "/a/scratch/notes"),
    ])
    assert [e.name for e in visible] == ["app", "scratch/notes"]


def test_entry_from_dict_drops_unknown_and_defaults_node():
    e = _entry_from_dict({
        "path": "/p", "collection_id": "c", "name": "n", "created_at": "t",
        "future": True,
    })
    assert e.node == "" and e.name == "n"


def test_unique_name_tags_on_collision(regfile):
    reg = Registry.load()
    reg.entries.append(_row("app", "c1", "/local/app"))
    assert unique_registry_name(reg, "app", "node1") == "node1/app"
    assert unique_registry_name(reg, "other", "node1") == "other"


def test_absorb_skips_home_keeps_node_folders_and_dups(regfile, tmp_path):
    reg = Registry.load()
    home = {"name": "scratch/home", "path": "/x/scratch/home",
            "collection_id": "c-home", "created_at": "t"}
    local = {"name": "secret", "path": "/x/secret",
             "collection_id": "", "created_at": "t"}
    live = {"name": "app", "path": "/vm/app",
            "collection_id": "c-app", "created_at": "t"}
    res = apply_remote_entries(
        reg, [home, local, live], "node1", user_root=tmp_path / "xlii",
    )
    assert res.hidden == 1
    assert res.added == 2
    assert res.skipped == 0
    reg.save()
    again = Registry.load()
    assert len(again.entries) == 2
    by_name = {e.name: e for e in again.entries}
    assert by_name["app"].collection_id == "c-app"
    assert by_name["app"].node == "node1"
    assert by_name["secret"].node == "node1"
    assert Path(by_name["app"].path).is_dir()
    assert (Path(by_name["app"].path) / ".xlii" / "project.json").is_file()
    from xlii.config import ProjectConfig

    app_cfg = ProjectConfig.load(Path(by_name["app"].path))
    assert app_cfg is not None
    assert app_cfg.files_root == "sftp://node1//vm/app"

    # same collection already here — adopt wins, no second pointer
    res2 = apply_remote_entries(
        again, [live], "node1", user_root=tmp_path / "xlii",
    )
    assert res2.added == 0 and res2.skipped == 1


def test_absorb_appbox_stub_stamps_inner_files_root(regfile, tmp_path):
    from xlii.config import PROJECT_KIND_CODE, ProjectConfig, project_kind

    reg = Registry.load()
    live = {
        "name": "fuel",
        "path": "/home/admin/.xlii/links/sftp-appbox-srv-apps-fuel.xlii-code.com",
        "collection_id": "",
        "created_at": "t",
    }
    res = apply_remote_entries(
        reg, [live], "xliiec2", user_root=tmp_path / "xlii",
    )
    assert res.added == 1
    reg.save()
    entry = Registry.load().entries[0]
    cfg = ProjectConfig.load(Path(entry.path))
    assert cfg is not None
    assert cfg.files_root == "sftp://appbox/srv/apps/fuel.xlii-code.com"
    assert project_kind(cfg) == PROJECT_KIND_CODE


def test_create_mkdirs_on_node_no_collection(regfile, tmp_path, monkeypatch):
    conn = FakeConn()
    stub = tmp_path / "links" / "lab-app"
    stub.mkdir(parents=True)
    (stub / ".xlii").mkdir()
    (stub / ".xlii" / "project.json").write_text(json.dumps({
        "name": "lab-app", "collection_id": "", "created_at": "t",
        "root": str(stub.resolve()), "local_only": True,
    }))

    def _adopt(addr, kind=None, name=""):
        return SimpleNamespace(project_root=stub, collection_id="", name=name or "lab-app")

    monkeypatch.setattr("xlii.desk_files.adopt_remote", _adopt)
    roster = {"node1": {"remote": "box"}}
    created = create_fabric_project(
        "lab-app",
        "node1",
        roster=roster,
        connect=lambda _r: conn,
        require_remote=lambda _r: None,
    )
    assert created.collection_id == ""
    assert created.node == "node1"
    assert "lab-app" in conn.dirs
    e = Registry.load().find_by_path(stub)
    assert e is not None and e.node == "node1"


def test_create_unknown_node_refuses(regfile):
    with pytest.raises(ValueError, match="no such node"):
        create_fabric_project(
            "x", "ghost", roster={"node1": {"remote": "b"}},
            connect=lambda _r: FakeConn(),
        )


def test_sync_pulls_then_pushes_hiding_throne_home(regfile, tmp_path):
    node_reg = {
        "entries": [
            {"name": "scratch/home", "path": "/vm/.xlii/scratch/home",
             "collection_id": "c-home", "created_at": "t"},
            {"name": "vm-app", "path": "/vm/app",
             "collection_id": "c-vm", "created_at": "t"},
        ]
    }
    conn = FakeConn({
        ".config/xlii/projects.json": json.dumps(node_reg).encode(),
    })
    # throne already has its own local project
    reg = Registry.load()
    throne_app = tmp_path / "throne-app"
    throne_app.mkdir()
    (throne_app / ".xlii").mkdir()
    (throne_app / ".xlii" / "project.json").write_text(json.dumps({
        "name": "throne-app", "collection_id": "c-throne",
        "created_at": "t", "root": str(throne_app.resolve()),
    }))
    reg.entries.append(_row("throne-app", "c-throne", str(throne_app)))
    reg.entries.append(_row(
        "scratch/home", "", str(tmp_path / "scratch" / "home"),
    ))
    reg.save()

    result = sync_fabric_projects(
        {"node1": {"remote": "box"}},
        connect=lambda _r: conn,
        require_remote=lambda _r: None,
        this_node="throne",
        user_root=tmp_path / "xlii",
    )
    assert result.nodes[0].added == 1
    assert result.nodes[0].hidden == 1
    names = {e.name for e in Registry.load().entries}
    assert "vm-app" in names
    assert "scratch/home" in names  # still local, never imported from node
    remote = json.loads(conn.files[".config/xlii/projects.json"])
    remote_names = {e["name"] for e in remote["entries"]}
    assert "vm-app" in remote_names
    assert "throne-app" in remote_names
    # Node may keep its own Home. Throne Home is not copied onto it.
    homes = [e for e in remote["entries"] if e["name"] == "scratch/home"]
    assert len(homes) == 1
    assert homes[0]["path"] == "/vm/.xlii/scratch/home"


def test_pointer_dir_is_under_user_root(tmp_path):
    p = pointer_dir("node1", "app", user_root=tmp_path)
    assert p == tmp_path / "remote-projects" / "node1" / "app"


def test_parse_registry_bytes_ignores_junk_rows():
    raw = json.dumps({"entries": [{"name": "a", "path": "/a",
                                   "collection_id": "c", "created_at": "t"},
                                  "nope"]}).encode()
    rows = parse_registry_bytes(raw)
    assert len(rows) == 1


def test_parse_registry_bytes_drops_address_book_and_keeps_repo():
    raw = json.dumps({
        "ftp_connections": {"box": {"host": "evil", "password": "x"}},
        "entries": [{
            "name": "app",
            "path": "/vm/app",
            "collection_id": "",
            "created_at": "t",
            "node": "node1",
            "repo": "git@github.com:acme/app.git",
            "password": "hunter2",
            "vault_ref": "box",
        }],
    }).encode()
    rows = parse_registry_bytes(raw)
    assert len(rows) == 1
    assert rows[0]["repo"] == "git@github.com:acme/app.git"
    assert "password" not in rows[0]
    assert "vault_ref" not in rows[0]
    assert "ftp_connections" not in rows[0]


def test_scan_remote_hashes_and_skips_dot_xlii():
    from xlii.config import GlobalConfig
    from xlii.sync import scan_remote

    conn = FakeConn({
        "app/readme.md": b"hi",
        "app/.xlii/project.json": b"{}",
        "app/src/a.py": b"print(1)\n",
    })
    # listdir needs directory listing, not just files dict
    conn.listdir = lambda path="": _fake_list(conn, path)
    cfg = GlobalConfig()
    cfg.max_file_bytes = 1_000_000
    out = scan_remote(conn, "app", cfg)
    assert "readme.md" in out
    assert "src/a.py" in out
    assert not any(p.startswith(".xlii") for p in out)


def _fake_list(conn, path):
    prefix = (path or "").rstrip("/")
    kids = {}
    for p in conn.files:
        rest = p
        if prefix and prefix != ".":
            if not p.startswith(prefix + "/"):
                continue
            rest = p[len(prefix) + 1:]
        first, _, more = rest.partition("/")
        if not first:
            continue
        if first not in kids:
            kids[first] = bool(more)
    return [(name, is_dir, 3) for name, is_dir in sorted(kids.items())]


def test_push_does_not_duplicate_existing_collection():
    existing = {
        "entries": [
            {"name": "app", "path": "/vm/app", "collection_id": "c1",
             "created_at": "t"},
        ]
    }
    conn = FakeConn({".config/xlii/projects.json": json.dumps(existing).encode()})
    n = push_catalog_to_node(
        conn,
        [_row("app", "c1", "/throne/app", node="throne")],
        origin_label="throne",
    )
    assert n == 0
    assert conn.writes == []
