"""Pointer desks: files_root remounts Files; stub stays local; no live wire."""

from __future__ import annotations

from types import SimpleNamespace

from xlii.config import ProjectConfig
from xlii.desk_files import (
    address_inside_root,
    adopt_remote,
    bind_files_root,
    decode_stub_slug,
    files_address,
    files_browse_fence,
    files_mount_address,
    files_mount_prompt_addendum,
    files_root_of,
    infer_fabric_files_address,
    is_pointer_stub_path,
    is_remote_files_address,
    is_stub_inventory_command,
    is_xlii_relpath,
    normalize_files_root,
    parent_inside_files_fence,
    pointer_stub_slug,
    reachable_files_address,
    sftp_files_address,
    stub_root_for_remote,
    writes_follow_files_mount,
)


def test_is_remote_files_address():
    assert is_remote_files_address("sftp://appbox/srv/apps/foo")
    assert is_remote_files_address("ftp://box/pub")
    assert not is_remote_files_address("/home/me/proj")
    assert not is_remote_files_address("file:///home/me/proj")
    assert not is_remote_files_address("projects://")
    assert not is_remote_files_address("")


def test_normalize_strips_slash_and_lowercases_scheme():
    assert normalize_files_root("SFTP://appbox/srv/apps/foo/") == (
        "sftp://appbox/srv/apps/foo"
    )


def test_files_address_pointer_beats_local_cwd(tmp_path):
    proj = SimpleNamespace(
        project_root=tmp_path,
        files_root="sftp://appbox/srv/apps/calc",
    )
    assert files_address(proj, shell_cwd=tmp_path) == "sftp://appbox/srv/apps/calc"
    assert files_root_of(proj) == "sftp://appbox/srv/apps/calc"


def test_files_address_local_when_no_pointer(tmp_path):
    proj = SimpleNamespace(project_root=tmp_path, files_root=None)
    assert files_address(proj) == f"file://{tmp_path}"


def test_files_browse_fence_prefers_remote_pointer(tmp_path):
    proj = SimpleNamespace(
        project_root=tmp_path,
        files_root="sftp://xliiec2//home/admin/serve-sandbox",
        node="xliiec2",
        remote_path="/home/admin/serve-sandbox",
    )
    assert files_browse_fence(proj) == "sftp://xliiec2//home/admin/serve-sandbox"


def test_files_browse_fence_local_project(tmp_path):
    proj = SimpleNamespace(project_root=tmp_path, files_root="")
    assert files_browse_fence(proj) == f"file://{tmp_path.resolve()}"


def test_address_inside_root_sftp_and_via():
    root = "sftp://xliiec2//home/admin/serve-sandbox"
    assert address_inside_root(root, root)
    assert address_inside_root(f"{root}/src", root)
    assert not address_inside_root("sftp://xliiec2//home/admin", root)
    assert address_inside_root(f"via://node1/{root}/lib", root)
    assert not address_inside_root("sftp://other//home/admin/serve-sandbox", root)


def test_parent_inside_files_fence_drops_walk_out():
    from xlii.addressing import Address

    fence = "sftp://xliiec2//home/admin/serve-sandbox"
    at = Address.parse(fence)
    up = Address.parse("sftp://xliiec2//home/admin")
    assert parent_inside_files_fence(at, up, fence=fence) is None
    child = Address.parse(f"{fence}/src")
    assert parent_inside_files_fence(child, at, fence=fence) is at
    # outside the project: no clip
    host = Address.parse("sftp://xliiec2//home/admin")
    host_up = Address.parse("sftp://xliiec2//home")
    assert parent_inside_files_fence(host, host_up, fence=fence) is host_up


def test_files_address_fabric_pointer_uses_roster_sftp(tmp_path, monkeypatch):
    from xlii import registry as R
    from xlii.desk_files import infer_fabric_files_address
    from xlii.registry import Registry, RegistryEntry

    monkeypatch.setattr(R, "REGISTRY_FILE", tmp_path / "projects.json")
    stub = tmp_path / "remote-projects" / "xliiec2" / "sandbox"
    stub.mkdir(parents=True)
    (stub / ".xlii").mkdir()
    _write_project(stub)
    reg = Registry()
    reg.upsert(RegistryEntry(
        path=str(stub.resolve()),
        collection_id="",
        name="sandbox",
        created_at="t",
        node="xliiec2",
        remote_path="/home/admin/serve-sandbox",
    ))
    reg.save()
    cfg = ProjectConfig.load(stub)
    assert infer_fabric_files_address(cfg) == "sftp://xliiec2//home/admin/serve-sandbox"
    assert files_mount_address(cfg) == "sftp://xliiec2//home/admin/serve-sandbox"
    assert files_address(cfg) == "sftp://xliiec2//home/admin/serve-sandbox"


def test_sftp_address_keeps_absolute_path():
    assert sftp_files_address("xliiec2", "/home/admin/serve-sandbox") == (
        "sftp://xliiec2//home/admin/serve-sandbox"
    )
    assert sftp_files_address("xliiec2", "serve-sandbox") == "sftp://xliiec2/serve-sandbox"


def test_stub_slug_decodes_make_app():
    path = "/home/admin/.xlii/links/sftp-appbox-srv-apps-fuel.xlii-code.com"
    assert is_pointer_stub_path(path)
    assert pointer_stub_slug(path) == "sftp-appbox-srv-apps-fuel.xlii-code.com"
    assert decode_stub_slug(pointer_stub_slug(path)) == (
        "sftp://appbox/srv/apps/fuel.xlii-code.com"
    )


def test_infer_appbox_stub_follows_inner_files(tmp_path, monkeypatch):
    from xlii import registry as R
    from xlii.registry import Registry, RegistryEntry

    monkeypatch.setattr(R, "REGISTRY_FILE", tmp_path / "projects.json")
    stub = tmp_path / "remote-projects" / "xliiec2" / "fuel"
    stub.mkdir(parents=True)
    (stub / ".xlii").mkdir()
    _write_project(stub)
    reg = Registry()
    reg.upsert(RegistryEntry(
        path=str(stub.resolve()),
        collection_id="",
        name="fuel",
        created_at="t",
        node="xliiec2",
        remote_path="/home/admin/.xlii/links/sftp-appbox-srv-apps-fuel.xlii-code.com",
    ))
    reg.save()
    cfg = ProjectConfig.load(stub)
    assert infer_fabric_files_address(cfg) == "sftp://appbox/srv/apps/fuel.xlii-code.com"
    from xlii import address_book as AB

    monkeypatch.setattr(AB, "is_known_connection", lambda n, cfg=None: n == "xliiec2")
    assert files_mount_address(cfg) == (
        "via://xliiec2/sftp://appbox/srv/apps/fuel.xlii-code.com"
    )
    healed = ProjectConfig.load(stub)
    assert healed.files_root == "sftp://appbox/srv/apps/fuel.xlii-code.com"


def test_reachable_hops_unknown_appbox_via_node(monkeypatch):
    from xlii import address_book as AB

    monkeypatch.setattr(AB, "is_known_connection", lambda n, cfg=None: n == "xliiec2")
    proj = SimpleNamespace(node="xliiec2", remote_path="x", project_root=None)
    assert reachable_files_address(
        "sftp://appbox/srv/apps/fuel.xlii-code.com", proj,
    ) == "via://xliiec2/sftp://appbox/srv/apps/fuel.xlii-code.com"
    # Known connection is used directly — no hop.
    monkeypatch.setattr(AB, "is_known_connection", lambda n, cfg=None: True)
    assert reachable_files_address(
        "sftp://appbox/srv/apps/fuel.xlii-code.com", proj,
    ) == "sftp://appbox/srv/apps/fuel.xlii-code.com"


def test_bind_and_round_trip(tmp_path):
    _write_project(tmp_path)
    cfg = ProjectConfig.load(tmp_path)
    bind_files_root(cfg, "sftp://appbox/srv/apps/foo/")
    again = ProjectConfig.load(tmp_path)
    assert again.files_root == "sftp://appbox/srv/apps/foo"
    assert files_address(again) == "sftp://appbox/srv/apps/foo"


def test_bind_refuses_local_path(tmp_path):
    proj = SimpleNamespace(project_root=tmp_path, files_root=None)

    def _save():
        raise AssertionError("must not save")

    proj.save = _save
    try:
        bind_files_root(proj, str(tmp_path))
    except ValueError as e:
        assert "remote" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_adopt_remote_mints_stub_and_registers(tmp_path, monkeypatch):
    from xlii import desk_files as D
    from xlii import registry as R

    monkeypatch.setattr(D, "pointer_store_root", lambda: tmp_path / "links")
    monkeypatch.setattr(R, "REGISTRY_FILE", tmp_path / "projects.json")
    proj = adopt_remote("sftp://appbox/srv/apps/calc.xlii-code.com", kind="code")
    assert proj.files_root == "sftp://appbox/srv/apps/calc.xlii-code.com"
    assert proj.local_only is True
    assert proj.project_root.is_dir()
    assert proj.project_root == stub_root_for_remote(
        "sftp://appbox/srv/apps/calc.xlii-code.com"
    )
    assert (proj.project_root / ".xlii" / "project.json").is_file()
    # Re-adopt is idempotent — same stub, pointer refreshed.
    again = adopt_remote("sftp://appbox/srv/apps/calc.xlii-code.com/")
    assert again.project_root == proj.project_root
    assert R.Registry.load().find_by_path(proj.project_root) is not None


def test_adopt_remote_collection_kind(tmp_path, monkeypatch):
    from xlii import desk_files as D
    from xlii import registry as R
    from xlii.config import PROJECT_KIND_COLLECTION, project_kind

    monkeypatch.setattr(D, "pointer_store_root", lambda: tmp_path / "links")
    monkeypatch.setattr(R, "REGISTRY_FILE", tmp_path / "projects.json")
    proj = adopt_remote("sftp://box/notes", kind="collection")
    assert project_kind(proj) == PROJECT_KIND_COLLECTION


def test_writes_follow_files_mount_code_only(tmp_path):
    code = SimpleNamespace(
        project_root=tmp_path,
        files_root="sftp://appbox/srv/apps/jobsearch.xlii-code.com",
        kind="code",
        name="jobsearch",
    )
    pile = SimpleNamespace(
        project_root=tmp_path,
        files_root="sftp://appbox/srv/apps/notes",
        kind="collection",
        name="notes",
    )
    local = SimpleNamespace(project_root=tmp_path, files_root="", kind="code", name="lab")
    assert writes_follow_files_mount(code)
    assert not writes_follow_files_mount(pile)
    assert not writes_follow_files_mount(local)
    assert str(code.project_root) == str(tmp_path)


def test_is_xlii_relpath():
    assert is_xlii_relpath(".xlii/plans/current.md")
    assert is_xlii_relpath(".xlii/notes.md")
    assert not is_xlii_relpath("index.html")
    assert not is_xlii_relpath("src/.hidden")


def test_stub_inventory_command_detects_cwd_listing(tmp_path):
    root = tmp_path / "jobsearch"
    root.mkdir()
    assert is_stub_inventory_command("ls", root)
    assert is_stub_inventory_command("ls -la", root)
    assert is_stub_inventory_command("ls .", root)
    assert is_stub_inventory_command("find . -name '*.html'", root)
    assert is_stub_inventory_command("tree", root)
    assert is_stub_inventory_command("du -sh", root)
    assert is_stub_inventory_command(f"ls {root}", root)
    assert is_stub_inventory_command("sudo ls -la", root)
    assert not is_stub_inventory_command("ls .xlii", root)
    assert not is_stub_inventory_command("find .xlii -name '*.json'", root)
    assert not is_stub_inventory_command("sftp appbox ls /srv/apps/jobsearch", root)
    assert not is_stub_inventory_command("python scripts/publish.py", root)
    assert not is_stub_inventory_command("echo hello", root)


def test_files_mount_prompt_names_mount(tmp_path):
    proj = SimpleNamespace(
        project_root=tmp_path,
        files_root="sftp://appbox/srv/apps/jobsearch.xlii-code.com",
        kind="code",
        name="jobsearch",
    )
    text = files_mount_prompt_addendum(proj)
    assert text.startswith("[FILES]")
    assert "sftp://appbox/srv/apps/jobsearch.xlii-code.com" in text
    assert "NOT the codebase" in text
    assert "list_dir" in text
    local = SimpleNamespace(project_root=tmp_path, files_root="", kind="code")
    assert files_mount_prompt_addendum(local) == ""


def test_repo_round_trips(tmp_path):
    _write_project(tmp_path, repo="git@host:lab/foo.git")
    cfg = ProjectConfig.load(tmp_path)
    assert cfg.repo == "git@host:lab/foo.git"
    cfg.save()
    data = (tmp_path / ".xlii" / "project.json").read_text()
    assert "git@host:lab/foo.git" in data


def _write_project(project_dir, **overrides):
    import json

    d = project_dir / ".xlii"
    d.mkdir(parents=True, exist_ok=True)
    data = {
        "name": "t",
        "collection_id": "c1",
        "created_at": "2026-01-01",
        "conversation_id": "abc",
        "local_only": True,
        "root": str(project_dir.resolve()),
        **overrides,
    }
    (d / "project.json").write_text(json.dumps(data))
