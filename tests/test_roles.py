"""Role descriptor catalog — proposals/roles.md R1.

Loader (global + project override), descriptor parsing, malformed detection,
and the `/role` + `xlii role` surfaces. Global roles are isolated under a temp
XLII_CONFIG_DIR so tests never read the real ~/.config/xlii.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xlii.role import format_role_summary, load_role, load_roles
from tests.helpers import FakeConsole

VALID = (
    "---\n"
    "skills: [system-design, adr]\n"
    "docs: [arch]\n"
    "model: grok-4\n"
    "---\n"
    "You are a staff architect.\n"
)


@pytest.fixture
def cfg_dir(tmp_path, monkeypatch):
    d = tmp_path / "config"
    (d / "roles").mkdir(parents=True)
    monkeypatch.setenv("XLII_CONFIG_DIR", str(d))
    return d


def _grole(cfg_dir, name, text):
    (cfg_dir / "roles" / f"{name}.md").write_text(text)


def _prole(project_root, name, text):
    d = project_root / ".xlii" / "roles"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(text)


# --------------------------------------------------------------------------- #
#  Loader
# --------------------------------------------------------------------------- #

def test_loader_lists_global_and_project_with_override(cfg_dir, tmp_path):
    _grole(cfg_dir, "architect", VALID)
    _grole(cfg_dir, "tester", "---\nskills: [pytest]\n---\nYou run tests.\n")
    proj = tmp_path / "proj"
    _prole(proj, "architect", "---\nskills: [adr]\n---\nProject architect.\n")
    roles = load_roles(proj)
    assert {"architect", "tester"} <= set(roles)         # plus the bundled stock roles
    assert roles["architect"].scope == "project"         # project overrides global
    assert roles["architect"].loadout()["skills"] == ["adr"]
    assert roles["tester"].scope == "global"


def test_only_stock_when_no_global_or_project(cfg_dir, tmp_path):
    roles = load_roles(tmp_path / "proj")   # empty global + no project roles
    assert roles and all(r.scope == "stock" for r in roles.values())


# --------------------------------------------------------------------------- #
#  Descriptor parsing + validation
# --------------------------------------------------------------------------- #

def test_descriptor_parsing(cfg_dir):
    _grole(cfg_dir, "architect", VALID)
    r = load_role("architect")
    assert r.loadout() == {
        "skills": ["system-design", "adr"], "docs": ["arch"], "model": "grok-4",
    }
    assert r.identity() == "You are a staff architect."
    assert r.description() == "You are a staff architect."


def test_description_prefers_frontmatter_key(cfg_dir):
    _grole(cfg_dir, "r", "---\ndescription: Designs systems\nskills: [x]\n---\n# H\nbody\n")
    assert load_role("r").description() == "Designs systems"


def test_malformed_unterminated_frontmatter(cfg_dir):
    _grole(cfg_dir, "broken", "---\nskills: [x]\nno closing fence\nbody")
    err = load_role("broken").validate()
    assert err and "malformed" in err


def test_empty_descriptor_flagged(cfg_dir):
    _grole(cfg_dir, "empty", "   \n")
    err = load_role("empty").validate()
    assert err and "empty descriptor" in err


def test_valid_role_validates_clean(cfg_dir):
    _grole(cfg_dir, "architect", VALID)
    assert load_role("architect").validate() is None


def test_summary_lists_loadout_and_identity(cfg_dir):
    _grole(cfg_dir, "architect", VALID)
    out = "\n".join(format_role_summary(load_role("architect")))
    assert "skills: system-design, adr" in out and "model: grok-4" in out
    assert "identity: You are a staff architect." in out


# --------------------------------------------------------------------------- #
#  /role slash command
# --------------------------------------------------------------------------- #

def _ctx(console, project_root):
    return {"console": console, "project": SimpleNamespace(project_root=project_root)}


def test_slash_role_lists(cfg_dir, tmp_path):
    from xlii.repl_cmds.role import h_role
    _grole(cfg_dir, "architect", VALID)
    console = FakeConsole()
    h_role("/role", _ctx(console, tmp_path / "proj"))
    assert any("architect" in ln for ln in console.lines)


def test_slash_role_show_and_unknown(cfg_dir, tmp_path):
    from xlii.repl_cmds.role import h_role
    _grole(cfg_dir, "architect", VALID)
    c1 = FakeConsole()
    h_role("/role architect", _ctx(c1, tmp_path / "p"))
    assert any("model: grok-4" in ln for ln in c1.lines)
    c2 = FakeConsole()
    h_role("/role nope", _ctx(c2, tmp_path / "p"))
    assert any("no such role" in ln for ln in c2.lines)


def test_slash_role_lists_stock_out_of_box(cfg_dir, tmp_path):
    # with bundled stock roles, bare /role always lists at least those
    from xlii.repl_cmds.role import h_role
    console = FakeConsole()
    h_role("/role", _ctx(console, tmp_path / "p"))
    assert any("code-architect" in ln for ln in console.lines)


# --------------------------------------------------------------------------- #
#  xlii role CLI
# --------------------------------------------------------------------------- #

def test_cli_list_and_show(cfg_dir, tmp_path, monkeypatch, capsys):
    from xlii.cmds import roles as rcli
    _grole(cfg_dir, "architect", VALID)
    monkeypatch.setattr(rcli, "_resolve_root", lambda: tmp_path / "proj")
    assert rcli.cmd_role_list(SimpleNamespace()) == 0
    assert "architect" in capsys.readouterr().out
    assert rcli.cmd_role_show(SimpleNamespace(name="architect")) == 0
    assert "skills: system-design, adr" in capsys.readouterr().out
    assert rcli.cmd_role_show(SimpleNamespace(name="ghost")) == 1


# --------------------------------------------------------------------------- #
#  R2 — activation (equip in code, become in chat)
# --------------------------------------------------------------------------- #

def _repl_state(project_root):
    from xlii.repl import REPLState
    from tests.test_rail import _bare_agent

    project_root.mkdir(parents=True, exist_ok=True)
    agent = _bare_agent()
    agent.history = [{"role": "system", "content": "s"}]
    project = SimpleNamespace(
        project_root=project_root, xli_dir=project_root / ".xlii",
        local_only=True, name="p",
    )
    return REPLState(console=FakeConsole(), agent=agent, project=project,
                     cfg=SimpleNamespace(), pool=[])


def _code_ctx(state):
    return {"console": state.console, "state": state, "project": state.project,
            "agent": state.agent, "cfg": state.cfg, "persona": None}


def test_code_equip_applies_loadout_and_off_reverses(cfg_dir, tmp_path):
    from xlii.repl_cmds.role import h_role
    from xlii.skills import SKILL_ATTACH_PREFIX

    proj = tmp_path / "proj"
    sk = proj / ".xlii" / "skills" / "adr"
    sk.mkdir(parents=True)
    (sk / "SKILL.md").write_text("---\nname: adr\ndescription: write ADRs\n---\nSteps.\n")
    _prole(proj, "architect", "---\nskills: [adr]\n---\nYou are an architect.\n")

    state = _repl_state(proj)
    h_role("/role architect", _code_ctx(state))
    assert state.active_role == "architect"
    assert any(n == SKILL_ATTACH_PREFIX + "adr" for n, _ in state.attached_docs)

    h_role("/role off", _code_ctx(state))
    assert state.active_role is None
    assert not any(n == SKILL_ATTACH_PREFIX + "adr" for n, _ in state.attached_docs)


def test_code_equip_attaches_identity_doc_and_off_detaches(cfg_dir, tmp_path):
    """Equipping a role in code adopts its STANCE, not just its loadout: the
    identity body rides attached_docs under role:<name>, detached on /role off."""
    from xlii.repl_cmds.role import h_role
    from xlii.role import ROLE_ATTACH_PREFIX

    proj = tmp_path / "proj"
    _prole(proj, "operator", "---\ndescription: d\n---\nYou operate the farm.\n")

    state = _repl_state(proj)
    h_role("/role operator", _code_ctx(state))
    assert state.active_role == "operator"
    doc = [(n, c) for n, c in state.attached_docs if n == ROLE_ATTACH_PREFIX + "operator"]
    assert doc and "You operate the farm." in doc[0][1]

    h_role("/role off", _code_ctx(state))
    assert state.active_role is None
    assert not any(n == ROLE_ATTACH_PREFIX + "operator" for n, _ in state.attached_docs)


def test_role_default_persists_and_clears(cfg_dir, tmp_path):
    from xlii.config import ProjectConfig
    from xlii.repl_cmds.role import h_role

    proj = tmp_path / "proj"
    (proj / ".xlii").mkdir(parents=True)
    project = ProjectConfig(project_root=proj, name="p", collection_id="c",
                            created_at="2026-01-01", local_only=True)
    project.save()
    _grole(cfg_dir, "operator", "---\ndescription: d\n---\nYou operate.\n")
    ctx = {"console": FakeConsole(), "state": None, "project": project, "persona": None}

    h_role("/role default operator", ctx)
    assert ProjectConfig.load(proj).default_role == "operator"   # persisted to disk

    h_role("/role default nope", ctx)                            # unknown → refused
    assert ProjectConfig.load(proj).default_role == "operator"   # unchanged

    h_role("/role default off", ctx)
    assert ProjectConfig.load(proj).default_role is None


def test_apply_default_role_equips_at_boot(cfg_dir, tmp_path):
    from xlii.session_boot import _apply_default_role
    from xlii.role import ROLE_ATTACH_PREFIX
    from tests.helpers import FakeConsole

    proj = tmp_path / "proj"
    _grole(cfg_dir, "operator", "---\ndescription: d\n---\nYou run the platform.\n")
    state = _repl_state(proj)

    # None → no-op; unknown → quiet skip; set → equipped with its stance doc.
    state.project.default_role = None
    _apply_default_role(state, state.project, FakeConsole())
    assert state.active_role is None

    state.project.default_role = "ghost-role"
    _apply_default_role(state, state.project, FakeConsole())
    assert state.active_role is None

    state.project.default_role = "operator"
    _apply_default_role(state, state.project, FakeConsole())
    assert state.active_role == "operator"
    assert any(n == ROLE_ATTACH_PREFIX + "operator" for n, _ in state.attached_docs)


def test_role_profile_restores_on_off(cfg_dir, tmp_path):
    from xlii.config import GlobalConfig
    from xlii.repl_cmds.role import h_role

    proj = tmp_path / "proj"
    _prole(proj, "visioner", "---\nprofile: vision\n---\nYou inspect images.\n")
    state = _repl_state(proj)
    state.cfg = GlobalConfig()
    state.cfg.orchestrator_model = "grok-build-0.1"
    state.cfg.worker_model = "grok-build-0.1"
    state.cfg.chat_model = "grok-build-0.1"
    state.agent.cfg = state.cfg

    h_role("/role visioner", _code_ctx(state))
    assert state.cfg.orchestrator_model == "grok-4.3"

    h_role("/role off", _code_ctx(state))
    assert state.cfg.orchestrator_model == "grok-build-0.1"
    assert state.agent.session.loadout_cfg_snapshot is None


def test_off_when_nothing_equipped(cfg_dir, tmp_path):
    from xlii.repl_cmds.role import h_role
    state = _repl_state(tmp_path / "proj")
    h_role("/role off", _code_ctx(state))
    assert any("no role equipped" in ln for ln in state.console.lines)


def test_list_marks_active(cfg_dir, tmp_path):
    from xlii.repl_cmds.role import h_role
    _grole(cfg_dir, "architect", VALID)
    state = _repl_state(tmp_path / "proj")
    state.active_role = "architect"
    h_role("/role", _code_ctx(state))
    assert any("architect" in ln and "active" in ln for ln in state.console.lines)


def test_chat_become_routes_through_switch(cfg_dir, tmp_path, monkeypatch):
    from xlii.repl_cmds.role import RoleAsPersona, h_role
    _grole(cfg_dir, "architect", VALID)
    captured = {}
    monkeypatch.setattr("xlii.repl_cmds.switch.switch_to_persona",
                        lambda ctx, persona: captured.setdefault("p", persona) or True)
    state = _repl_state(tmp_path / "proj")
    ctx = {"console": state.console, "state": state, "project": state.project,
           "persona": object()}  # truthy persona → chat surface
    h_role("/role architect", ctx)
    assert isinstance(captured.get("p"), RoleAsPersona)
    assert captured["p"].name == "architect"
    assert state.active_role == "architect"  # set after the switch cleared it


def test_activate_malformed_refuses(cfg_dir, tmp_path):
    from xlii.repl_cmds.role import h_role
    _grole(cfg_dir, "broken", "---\nskills: [x]\nno closing fence\nbody")
    state = _repl_state(tmp_path / "proj")
    h_role("/role broken", _code_ctx(state))
    assert state.active_role is None
    assert any("malformed" in ln for ln in state.console.lines)


# --------------------------------------------------------------------------- #
#  R3 — bundled stock roles + skill
# --------------------------------------------------------------------------- #

def test_stock_roles_ship_and_list_out_of_box(cfg_dir, tmp_path):
    # empty global (cfg_dir) + no project → only the bundled stock roles
    roles = load_roles(tmp_path / "noproj")
    assert {
        "code-architect", "test-engineer", "debugger", "app-operator",
        "fleet-conductor",
    } <= set(roles)
    arch = roles["code-architect"]
    assert arch.scope == "stock" and arch.validate() is None
    assert arch.loadout()["skills"] == ["grounded-analysis"]
    fleet = roles["fleet-conductor"]
    assert fleet.scope == "stock" and fleet.validate() is None
    assert fleet.loadout()["skills"] == ["vectoring", "grounded-analysis"]
    # The app-serving operator role ships and validates clean (no loadout — its
    # knowledge is inline system-stance, not attachments).
    op = roles["app-operator"]
    assert op.scope == "stock" and op.validate() is None


def test_global_role_overrides_stock(cfg_dir, tmp_path):
    _grole(cfg_dir, "code-architect", "---\nskills: [x]\n---\nMy architect.\n")
    roles = load_roles(tmp_path / "p")
    assert roles["code-architect"].scope == "global"      # global wins over stock
    assert roles["code-architect"].loadout()["skills"] == ["x"]


def test_equip_fleet_conductor_attaches_vectoring(cfg_dir, tmp_path):
    """Exit gate: /role fleet-conductor attaches the vectoring skill out of the box."""
    from xlii.repl_cmds.role import h_role
    from xlii.skills import SKILL_ATTACH_PREFIX

    state = _repl_state(tmp_path / "proj")
    h_role("/role fleet-conductor", _code_ctx(state))
    assert state.active_role == "fleet-conductor"
    names = [n for n, _ in state.attached_docs]
    assert SKILL_ATTACH_PREFIX + "vectoring" in names
    assert SKILL_ATTACH_PREFIX + "grounded-analysis" in names
    from xlii.role import ROLE_ATTACH_PREFIX
    assert ROLE_ATTACH_PREFIX + "fleet-conductor" in names


def test_equip_stock_role_attaches_stock_skill(cfg_dir, tmp_path):
    """Exit gate: a shipped starter activates and produces its expected attachment
    (the bundled grounded-analysis skill) with nothing installed."""
    from xlii.repl_cmds.role import h_role
    from xlii.skills import SKILL_ATTACH_PREFIX

    state = _repl_state(tmp_path / "proj")
    h_role("/role code-architect", _code_ctx(state))
    assert state.active_role == "code-architect"
    assert any(n == SKILL_ATTACH_PREFIX + "grounded-analysis" for n, _ in state.attached_docs)


def test_live_switch_clears_active_role(cfg_dir, tmp_path):
    """A /code<->/chat switch drops the equip marker (parity with rail/debug)."""
    from xlii.profile import code_profile
    from xlii.repl_cmds.switch import _live_switch

    proj_root = tmp_path / "proj"
    state = _repl_state(proj_root)
    state.active_role = "architect"
    # minimal code profile target; _live_switch repoints onto it
    from xlii.config import ProjectConfig
    import json
    (proj_root / ".xlii").mkdir(parents=True, exist_ok=True)
    (proj_root / ".xlii" / "project.json").write_text(json.dumps(
        {"root": str(proj_root), "name": "p", "collection_id": "", "created_at": "x"}))
    project = ProjectConfig.load(proj_root)
    state.agent.project = project
    state.profile = None
    prof = code_profile(project, seed_limit=0)
    _live_switch({"state": state, "console": state.console}, prof, persona=None)
    assert state.active_role is None
