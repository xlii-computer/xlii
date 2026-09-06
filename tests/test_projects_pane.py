"""projects:// + ProjectsPane + /workbench new (typed-workbenches B5)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xlii.addressing import vfs_list, vfs_read
from xlii.addressing.builtins.projects import project_address
from xlii.panes.projects import ProjectsPane
from xlii.registry import Registry, RegistryEntry
from xlii.repl_cmds import workbench as WB
from xlii.workbench import load_active_type


class _Console:
    def __init__(self):
        self.lines = []

    def print(self, *a, **k):
        self.lines.append(" ".join(str(x) for x in a))

    @property
    def text(self):
        return "\n".join(self.lines)


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """A sandboxed registry with two entries (one with a workbench record)."""
    import xlii.registry as reg_mod

    reg_file = tmp_path / "registry.json"
    monkeypatch.setattr(reg_mod, "REGISTRY_FILE", reg_file)
    proj_a = tmp_path / "alpha"
    (proj_a / ".xlii").mkdir(parents=True)
    (proj_a / ".xlii" / "workbench.json").write_text('{"active": "code"}')
    (proj_a / ".xlii" / "project.json").write_text(
        '{"name":"alpha","collection_id":"","created_at":"2026-08-01"}'
    )
    proj_b = tmp_path / "beta"
    (proj_b / ".xlii").mkdir(parents=True)
    (proj_b / ".xlii" / "project.json").write_text(
        '{"name":"beta","collection_id":"c1","created_at":"2026-08-02"}'
    )
    reg = Registry(entries=[
        RegistryEntry(path=str(proj_a), collection_id="", name="alpha", created_at="2026-08-01"),
        RegistryEntry(path=str(proj_b), collection_id="c1", name="beta", created_at="2026-08-02"),
    ])
    reg.save()
    return reg


def test_provider_lists_registry(registry):
    nodes = vfs_list("projects://")
    assert [n.name for n in nodes] == ["alpha", "beta"]


def test_provider_read_includes_workbench_badge(registry):
    card = vfs_read(project_address("alpha")).decode()
    assert "workbench: code" in card and "/project switch alpha" in card


def test_provider_encodes_slash_in_project_name(registry, tmp_path):
    proj = tmp_path / "persona"
    proj.mkdir()
    reg = Registry.load()
    reg.entries.append(
        RegistryEntry(path=str(proj), collection_id="", name="chat/research", created_at="2026-08-03")
    )
    reg.save()
    nodes = vfs_list("projects://")
    slash = next(n for n in nodes if n.name == "chat/research")
    assert slash.address == project_address("chat/research")
    card = vfs_read(slash.address).decode()
    assert "name: chat/research" in card


def test_pane_groups_folders_and_chat_islands(registry, tmp_path):
    proj = tmp_path / "persona"
    proj.mkdir()
    reg = Registry.load()
    reg.entries.append(
        RegistryEntry(path=str(proj), collection_id="", name="chat/ixaac", created_at="2026-08-03")
    )
    reg.save()
    pane = ProjectsPane("projects://")
    kinds = [(r.kind, r.text) for r in pane.render().rows]
    assert kinds[0] == ("caption", "── here ──")
    assert any(k == "caption" and t == "── chats ──" for k, t in kinds)
    chat_leaves = [t for k, t in kinds if k == "leaf" and "alpha" not in t and "beta" not in t]
    assert any(t == "ixaac" or t.startswith("ixaac  ·") for t in chat_leaves)


def test_pane_rows_badge_and_actions(registry):
    pane = ProjectsPane("projects://")
    rendered = pane.render()
    texts = [r.text for r in rendered.rows]
    assert texts[0] == "── here ──"
    assert rendered.rows[1].text.startswith("alpha  · code")
    assert rendered.rows[2].text == "beta"
    acts = {a.name: a for a in pane.actions()}
    assert acts["open"].outcome.kind == "prefill"
    assert acts["open"].outcome.text.startswith("/project switch @")
    assert acts["rm"].outcome.text.startswith("/project forget ")
    assert "prune" in acts


def test_pane_hides_throne_home_and_badges_node(registry, tmp_path):
    from xlii.registry import Registry

    home = tmp_path / "scratch" / "home"
    (home / ".xlii").mkdir(parents=True)
    (home / ".xlii" / "project.json").write_text(
        '{"name":"scratch/home","collection_id":"","created_at":"t"}'
    )
    foreign = tmp_path / "remote-projects" / "node1" / "lab"
    (foreign / ".xlii").mkdir(parents=True)
    (foreign / ".xlii" / "project.json").write_text(
        '{"name":"lab","collection_id":"c9","created_at":"t"}'
    )
    reg = Registry.load()
    reg.entries.append(RegistryEntry(
        path=str(home), collection_id="", name="scratch/home", created_at="t",
    ))
    reg.entries.append(RegistryEntry(
        path=str(foreign), collection_id="c9", name="lab", created_at="t",
        node="node1",
    ))
    reg.save()
    pane = ProjectsPane("projects://")
    rows = pane.render().rows
    texts = [r.text for r in rows]
    assert not any("scratch/home" in t for t in texts)
    assert any(r.kind == "caption" and r.text == "── node1 ──" for r in rows)
    leaves = [r.text for r in rows if r.kind == "leaf"]
    assert any(t == "lab" or t.startswith("lab  ·") for t in leaves)


def test_pane_nav_and_select(registry):
    pane = ProjectsPane("projects://")
    assert pane.select_index(1)
    assert pane.selection().node.name == "beta"
    pane.handle("up")
    assert pane.selection().node.name == "alpha"


def test_pane_render_refreshes_registry_without_remount(registry):
    pane = ProjectsPane("projects://")
    reg = Registry.load()
    reg.entries.append(
        RegistryEntry(
            path=reg.entries[0].path,
            collection_id="",
            name="gamma",
            created_at="2026-08-03",
        )
    )
    reg.save()
    rendered = pane.render()
    leaves = [r.text.split("  · ")[0] for r in rendered.rows if r.kind == "leaf"]
    assert leaves == ["alpha", "beta", "gamma"]


def test_ghost_badge_and_prune(registry, tmp_path):
    from xlii.registry import Registry

    gone = tmp_path / "nope-never-existed"
    reg = Registry.load()
    reg.entries.append(
        RegistryEntry(path=str(gone), collection_id="", name="proj", created_at="2026-08-01")
    )
    reg.save()
    pane = ProjectsPane("projects://")
    texts = [r.text for r in pane.render().rows]
    assert any("proj  · ghost" in t for t in texts)
    # select the ghost (last folder after alpha, beta)
    names = [e.name for e in pane._entries]
    pane.select_index(names.index("proj"))
    acts = {a.name: a for a in pane.actions()}
    assert "open" not in acts  # can't switch into a dead /tmp
    assert acts["rm"].outcome.text.endswith(str(gone))
    r = Registry.load()
    dead = r.prune_dead()
    assert any(e.name == "proj" and e.path == str(gone) for e in dead)
    r.save()
    assert all(e.path != str(gone) for e in Registry.load().entries)


def test_dock_picks_projects_pane(registry):
    from xlii.panes.dock import Dock

    dock = Dock(slots=("A",))
    pane = dock.open_address("projects://", slot="A")
    assert isinstance(pane, ProjectsPane)


# --- /workbench new ---------------------------------------------------------


def _ctx(tmp_path):
    return {
        "console": _Console(),
        "project": SimpleNamespace(xli_dir=tmp_path / "cur" / ".xlii"),
        "state": SimpleNamespace(workbench=None),
    }


def test_new_creates_typed_project_and_teleports(tmp_path, monkeypatch):
    import xlii.repl_cmds.switch as sw

    switches = []
    monkeypatch.setattr(sw, "switch_to_code_project", lambda c, p, **k: switches.append(p.name))
    target = tmp_path / "myresearch"
    ctx = _ctx(tmp_path)
    assert WB.h_workbench(f"/workbench new chat myresearch {target}", ctx) is True
    assert load_active_type(target / ".xlii") == "chat"
    from xlii.config import ProjectConfig

    assert ProjectConfig.load(target).bound_persona is None
    assert switches == ["myresearch"]
    assert "myresearch" in ctx["console"].text


def test_new_accepts_quoted_path_with_spaces(tmp_path, monkeypatch):
    import xlii.repl_cmds.switch as sw

    monkeypatch.setattr(sw, "switch_to_code_project", lambda c, p, **k: None)
    target = tmp_path / "my projects" / "lab"
    ctx = _ctx(tmp_path)
    assert WB.h_workbench(f'/workbench new chat mylab "{target}"', ctx) is True
    assert (target / ".xlii").is_dir()


def test_new_refuses_existing_project(tmp_path, monkeypatch):
    import xlii.repl_cmds.switch as sw

    monkeypatch.setattr(sw, "switch_to_code_project", lambda c, p, **k: None)
    target = tmp_path / "dup"
    ctx = _ctx(tmp_path)
    WB.h_workbench(f"/workbench new code dup {target}", ctx)
    ctx2 = _ctx(tmp_path)
    WB.h_workbench(f"/workbench new code dup {target}", ctx2)
    assert "already an xlii project" in ctx2["console"].text


def test_new_unknown_type_does_not_create(tmp_path):
    target = tmp_path / "nope"
    ctx = _ctx(tmp_path)
    WB.h_workbench(f"/workbench new wat nope {target}", ctx)
    assert "unknown workbench type" in ctx["console"].text
    assert not target.exists()


def test_new_rejects_custom_workbench_type(tmp_path):
    target = tmp_path / "custom"
    ctx = _ctx(tmp_path)
    xli = ctx["project"].xli_dir
    xli.mkdir(parents=True, exist_ok=True)
    (xli / "workbench.toml").write_text('[[type]]\nname="custom"\n')
    WB.h_workbench(f"/workbench new custom custom {target}", ctx)
    assert "unknown workbench type" in ctx["console"].text
    assert not target.exists()


def test_new_rejects_existing_registry_name(tmp_path, monkeypatch):
    import xlii.registry as reg_mod

    reg_file = tmp_path / "registry.json"
    monkeypatch.setattr(reg_mod, "REGISTRY_FILE", reg_file)
    reg = Registry(entries=[
        RegistryEntry(path=str(tmp_path / "a"), collection_id="", name="dup", created_at="2026-08-01")
    ])
    reg.save()
    ctx = _ctx(tmp_path)
    target = tmp_path / "dup-target"
    WB.h_workbench(f"/workbench new code dup {target}", ctx)
    assert "already exists in the registry" in ctx["console"].text
    assert not target.exists()


def test_new_rejects_non_directory_path(tmp_path):
    target = tmp_path / "target-file"
    target.write_text("x")
    ctx = _ctx(tmp_path)
    WB.h_workbench(f"/workbench new code demo {target}", ctx)
    assert "is not a directory" in ctx["console"].text


def test_new_parses_quoted_path(tmp_path, monkeypatch):
    import xlii.repl_cmds.switch as sw

    switches = []
    monkeypatch.setattr(sw, "switch_to_code_project", lambda c, p, **k: switches.append(p.name))
    target = tmp_path / "my project"
    ctx = _ctx(tmp_path)
    assert WB.h_workbench(f'/workbench new code demo "{target}"', ctx) is True
    assert switches == ["demo"]
    assert target.is_dir()


def test_new_reports_bad_quoting(tmp_path):
    ctx = _ctx(tmp_path)
    assert WB.h_workbench('/workbench new code demo "/tmp/nope', ctx) is True
    assert "bad quoting" in ctx["console"].text
