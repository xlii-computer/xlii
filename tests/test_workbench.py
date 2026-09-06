"""Typed workbenches, phase B0 — registry-as-data + /workbench (typed-workbenches.md).

Product law: three packs only — home | chat | code. Research doors live on chat;
switch (not join) is the home desk door into a registered project.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from xlii.commands import find_repl_command
from xlii.repl_cmds import register_all
from xlii.repl_cmds import workbench as WB
from xlii.workbench import (
    BUILTIN_WORKBENCHES,
    DEFAULT_TYPE,
    LEGACY_TYPE_ALIASES,
    WorkbenchType,
    get_workbench,
    list_workbenches,
    load_active_type,
    load_registry,
    quick_launch_buttons,
    resolve_active,
    save_active_type,
    validate_registry,
)

register_all()


class _Console:
    def __init__(self):
        self.lines = []

    def print(self, *a, **k):
        self.lines.append(" ".join(str(x) for x in a))

    @property
    def text(self):
        return "\n".join(self.lines)


def _xli(tmp_path):
    d = tmp_path / ".xlii"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ctx(xli):
    return {
        "console": _Console(),
        "project": SimpleNamespace(xli_dir=xli),
        "state": SimpleNamespace(workbench=None),
    }


# --- registry integrity -----------------------------------------------------


def test_builtin_catalog_is_sound():
    assert validate_registry() == []


def test_builtin_catalog_has_the_v1_types():
    """Three product packs only — research/general retired as peer packs."""
    assert set(BUILTIN_WORKBENCHES) == {"chat", "code", "home"}
    for t in BUILTIN_WORKBENCHES.values():
        assert t.default_posture in ("chat", "code")
        assert isinstance(t.panes, tuple)
        assert isinstance(t.quick_launch, tuple)
        assert t.quick_launch, f"{t.name} should declare a quick-launch pack"


def test_pack_pane_ids_unions_panes_and_ql_doors():
    """Face slot/Panels catalog = declared panes + QL pane: doors, not attach."""
    from xlii.workbench import pack_pane_ids

    chat = pack_pane_ids(BUILTIN_WORKBENCHES["chat"])
    assert "plugins" in chat  # QL door, not in chat.panes
    assert "wiki" in chat
    assert "explorer" not in chat
    code = pack_pane_ids(BUILTIN_WORKBENCHES["code"])
    assert "explorer" in code and "git" in code
    assert "plugins" not in code
    home = pack_pane_ids(BUILTIN_WORKBENCHES["home"])
    assert "projects" in home and "plugins" in home
    assert pack_pane_ids(None) == ()


def test_quick_launch_packs_differ_by_type():
    """three-faces: code vs chat are different button packs, not modes."""
    code = {b["id"] for b in quick_launch_buttons(BUILTIN_WORKBENCHES["code"])}
    chat = {b["id"] for b in quick_launch_buttons(BUILTIN_WORKBENCHES["chat"])}
    assert "plan" in code and "git" in code
    assert "plugins" in chat and "results" in chat
    assert "plan" not in chat  # lab door not on chat pack


def test_home_pack_leads_with_switch():
    """Home desk pack opens projects/switch first (product verb, not join)."""
    home = BUILTIN_WORKBENCHES["home"]
    assert home.quick_launch[0] == "switch"
    assert "projects" in home.panes
    buttons = quick_launch_buttons(home)
    assert buttons[0]["id"] == "switch"
    assert buttons[0]["label"] == "switch"
    assert buttons[0]["action"] == "pane:projects"


def test_legacy_join_id_labels_as_switch():
    """Old packs that still name 'join' resolve to the switch product label."""
    from xlii.workbench import QUICK_LAUNCH_META

    assert QUICK_LAUNCH_META["join"][0] == "switch"
    assert QUICK_LAUNCH_META["switch"][0] == "switch"


def test_chat_pack_absorbs_research_doors():
    """Research is chat power tools — fetch · keep · show live on the chat pack."""
    chat = BUILTIN_WORKBENCHES["chat"]
    ids = set(chat.quick_launch)
    panes = set(chat.panes)
    for need in (
        "plugins", "kg", "canvas", "browser", "locker", "bookmarks", "ref",
        "sources", "results", "artifacts",
    ):
        assert need in ids, f"chat quick_launch missing {need}"
    for need in ("locker", "bookmarks", "sources", "results", "artifacts", "canvas", "wiki"):
        assert need in panes, f"chat panes missing {need}"
    buttons = {b["id"]: b for b in quick_launch_buttons(chat)}
    assert buttons["locker"]["action"] == "pane:locker"
    assert buttons["ref"]["action"].startswith("seed:/ref")
    assert buttons["plugins"]["action"] == "pane:plugins"
    assert buttons["browser"]["action"] == "browser:open"
    assert buttons["kg"]["action"] == "research:kg"
    assert buttons["canvas"]["action"] == "research:canvas"


def test_fkey_pack_rows_share_doors_with_quick_launch():
    """Pack-door helper still lists workbench places (not the F-key bar)."""
    from xlii.workbench import fkey_pack_rows

    rows = fkey_pack_rows(BUILTIN_WORKBENCHES["code"])
    assert rows[0] == ("F1", "help", "cmd:help")
    labels = {lab for _k, lab, _a in rows[1:]}
    assert "plan" in labels and "git" in labels
    c_labels = {lab for _k, lab, _a in fkey_pack_rows(BUILTIN_WORKBENCHES["chat"])[1:]}
    assert "plugins" in c_labels
    assert "plan" not in c_labels


def test_default_type_is_chat():
    assert DEFAULT_TYPE == "chat"
    assert "menu" in BUILTIN_WORKBENCHES["chat"].panes
    assert "locker" in BUILTIN_WORKBENCHES["chat"].panes


def test_validate_flags_unknown_pane():
    bad = {"x": WorkbenchType("x", panes=("nosuchpane",))}
    problems = validate_registry(bad)
    assert len(problems) == 1 and "nosuchpane" in problems[0]


def test_validate_flags_bad_posture():
    bad = {"x": WorkbenchType("x", default_posture="sideways")}
    assert validate_registry(bad)


# --- override file ----------------------------------------------------------


def test_override_merges_over_builtin(tmp_path):
    xli = _xli(tmp_path)
    (xli / "workbench.toml").write_text(
        '[[type]]\nname="chat"\nambient="custom tip"\n'
    )
    wb = get_workbench("chat", xli)
    assert wb.ambient == "custom tip"
    # untouched columns survive the merge
    assert wb.panes == BUILTIN_WORKBENCHES["chat"].panes


def test_override_adds_new_type(tmp_path):
    xli = _xli(tmp_path)
    (xli / "workbench.toml").write_text(
        '[[type]]\nname="notes"\npanes=["wiki","tasks"]\ndefault_posture="chat"\n'
    )
    names = [t.name for t in list_workbenches(xli)]
    assert names[:3] == ["chat", "code", "home"]  # builtin order first
    assert "notes" in names


def test_override_malformed_rows_degrade_never_brick(tmp_path):
    xli = _xli(tmp_path)
    (xli / "workbench.toml").write_text(
        '[[type]]\nname=""\n'  # no name — skipped
        '[[type]]\nname="bad"\ndefault_posture="sideways"\n'  # bad posture — skipped
        '[[type]]\nname="ok"\npanes=["wiki"]\n'
    )
    reg = load_registry(xli)
    assert "bad" not in reg and "ok" in reg
    assert set(BUILTIN_WORKBENCHES) <= set(reg)


def test_override_invalid_toml_falls_back_to_builtin(tmp_path):
    xli = _xli(tmp_path)
    (xli / "workbench.toml").write_text("this is [ not toml")
    assert load_registry(xli) == BUILTIN_WORKBENCHES


# --- active type persistence -------------------------------------------------


def test_active_defaults_to_chat(tmp_path):
    assert load_active_type(_xli(tmp_path)) == "chat"


def test_save_then_load_roundtrip(tmp_path):
    xli = _xli(tmp_path)
    save_active_type(xli, "code")
    assert load_active_type(xli) == "code"
    assert json.loads((xli / "workbench.json").read_text()) == {"active": "code"}


def test_legacy_active_aliases_map_to_product_packs(tmp_path):
    """research → chat, general → home (old workbench.json still boots)."""
    xli = _xli(tmp_path)
    assert LEGACY_TYPE_ALIASES["research"] == "chat"
    assert LEGACY_TYPE_ALIASES["general"] == "home"
    save_active_type(xli, "research")
    assert load_active_type(xli) == "chat"
    save_active_type(xli, "general")
    assert load_active_type(xli) == "home"


def test_stale_active_record_degrades_to_default(tmp_path):
    xli = _xli(tmp_path)
    save_active_type(xli, "ghost-type")
    assert load_active_type(xli) == "chat"


def test_resolve_active_returns_the_row(tmp_path):
    xli = _xli(tmp_path)
    save_active_type(xli, "code")
    assert resolve_active(xli).name == "code"


# --- the /workbench command ---------------------------------------------------


def test_command_registered_both_repls():
    assert find_repl_command("/workbench", "code") is not None
    assert find_repl_command("/workbench", "chat") is not None


def test_bare_lists_catalog_with_active_marked(tmp_path):
    xli = _xli(tmp_path)
    save_active_type(xli, "code")
    ctx = _ctx(xli)
    assert WB.h_workbench("/workbench", ctx) is True
    out = ctx["console"].text
    assert "active workbench" in out and "code" in out
    for name in ("chat", "home", "code"):
        assert name in out
    # No peer pack named research/general (word may still appear in ambient tips).
    assert "▸ [cyan]research" not in out and "  [cyan]research" not in out
    assert "▸ [cyan]general" not in out and "  [cyan]general" not in out


def test_switch_persists_and_syncs_session_state(tmp_path):
    xli = _xli(tmp_path)
    ctx = _ctx(xli)
    assert WB.h_workbench("/workbench chat", ctx) is True
    assert load_active_type(xli) == "chat"
    assert ctx["state"].workbench is not None
    assert ctx["state"].workbench.name == "chat"
    assert "chat" in ctx["console"].text


def test_switch_unknown_type_is_a_helpful_error(tmp_path):
    xli = _xli(tmp_path)
    ctx = _ctx(xli)
    assert WB.h_workbench("/workbench wat", ctx) is True
    out = ctx["console"].text
    assert "unknown workbench type 'wat'" in out
    assert "chat" in out  # the valid-types list
    assert load_active_type(xli) == "chat"  # unchanged


def test_switch_without_project_context_does_not_persist():
    ctx = {"console": _Console(), "project": None, "state": None}
    assert WB.h_workbench("/workbench code", ctx) is True
    assert "not persisted" in ctx["console"].text


# --- B2: application ------------------------------------------------------------


def _ctx_with_project(xli):
    project = SimpleNamespace(xli_dir=xli, bound_persona=None, saved=0)
    project.save = lambda: setattr(project, "saved", project.saved + 1)
    return {
        "console": _Console(),
        "project": project,
        "state": SimpleNamespace(workbench=None),
    }


def test_switch_does_not_bind_persona_by_default(tmp_path):
    """three-faces Q2: pack ≠ identity — no auto-bind on pack switch."""
    xli = _xli(tmp_path)
    # Override so chat carries a suggested persona without auto-binding.
    (xli / "workbench.toml").write_text(
        '[[type]]\nname="chat"\npersona="research"\n'
    )
    ctx = _ctx_with_project(xli)
    assert WB.h_workbench("/workbench chat", ctx) is True
    assert ctx["project"].bound_persona is None
    assert ctx["project"].saved == 0
    assert "not a mode" in ctx["console"].text or "quick-launch" in ctx["console"].text


def test_switch_bind_persona_opt_in(tmp_path):
    xli = _xli(tmp_path)
    (xli / "workbench.toml").write_text(
        '[[type]]\nname="chat"\npersona="research"\n'
    )
    ctx = _ctx_with_project(xli)
    assert WB.h_workbench("/workbench chat --bind-persona", ctx) is True
    assert ctx["project"].bound_persona == "research"
    assert ctx["project"].saved == 1
    from xlii.persona import Persona

    assert Persona("research").exists()


def test_stock_persona_seed_is_non_clobbering():
    from xlii.persona import Persona, ensure_stock_persona

    p = ensure_stock_persona("research")
    p.write_prompt("customized")
    ensure_stock_persona("research")
    assert Persona("research").prompt_path.read_text() == "customized"


def test_switch_to_plain_type_leaves_existing_binding_alone(tmp_path):
    xli = _xli(tmp_path)
    ctx = _ctx_with_project(xli)
    ctx["project"].bound_persona = "my-persona"
    assert WB.h_workbench("/workbench code", ctx) is True
    assert ctx["project"].bound_persona == "my-persona"  # never strips
    assert ctx["project"].saved == 0


def test_ambient_note_names_the_workbench(tmp_path):
    from xlii.repl_cmds.mojo import build_mojo_ambient
    from xlii.workbench import BUILTIN_WORKBENCHES

    state = SimpleNamespace(
        project=None, journal=None, workbench=None, cfg=None, agent=None,
        scratch=False, no_sync=False, hire=None,
    )
    # bearings always prepend; no desk banner without a project
    empty = build_mojo_ambient(state, "hello")
    assert "[bearings]" in empty
    assert "current desk" not in empty
    desk = SimpleNamespace(name="iXaac-lab", project_root=str(tmp_path / "iXaac-lab"),
                           xli_dir=None)
    state.project = desk
    named = build_mojo_ambient(state, "hello")
    assert "current desk: iXaac-lab" in named
    assert str(tmp_path / "iXaac-lab") in named
    state.workbench = BUILTIN_WORKBENCHES["chat"]
    build_mojo_ambient(state, "hello")
    # chat is the default type — ambient note only for non-default packs
    # (or always when ambient is set — check actual behavior)
    state.workbench = BUILTIN_WORKBENCHES["code"]
    out = build_mojo_ambient(state, "hello")
    assert "active workbench: code" in out
    state.workbench = BUILTIN_WORKBENCHES["home"]
    out = build_mojo_ambient(state, "hello")
    assert "active workbench: home" in out and "switch" in out


def test_face_workbench_refreshes_chrome_not_posture(tmp_path):
    """three-faces: workbench switch must not flip posture; chrome carries quick_launch."""
    from types import SimpleNamespace as NS
    from xlii.serve_face import FaceServer
    from xlii.workbench import BUILTIN_WORKBENCHES

    sent = []
    state = NS(workbench=BUILTIN_WORKBENCHES["code"], project=None, agent=None, cfg=None)
    server = FaceServer(boot=NS(state=state))
    server.send = lambda obj: sent.append(obj)
    assert server.posture == "chat"
    server._apply_workbench_chrome()
    assert server.posture == "chat"  # pack only — no auto flip to code
    chromes = [o for o in sent if o.get("type") == "chrome_state"]
    assert chromes and any(
        b.get("id") == "plan" for b in (chromes[-1].get("quick_launch") or [])
    )
    server.posture = "code"
    server._sync_command_scope()
    state.workbench = BUILTIN_WORKBENCHES["chat"]
    server._wb_posture_applied = "code"
    server._apply_workbench_chrome()
    assert server.posture == "code"  # user flip preserved across chat pack


def test_bind_that_fails_to_persist_leaves_the_binding_untouched(tmp_path):
    xli = _xli(tmp_path)
    (xli / "workbench.toml").write_text(
        '[[type]]\nname="chat"\npersona="research"\n'
    )
    ctx = _ctx_with_project(xli)

    def _boom():
        raise OSError("disk full")

    ctx["project"].save = _boom
    assert WB.h_workbench("/workbench chat --bind-persona", ctx) is True
    assert ctx["project"].bound_persona is None  # never bind what didn't persist
    assert "binding unchanged" in ctx["console"].text
    assert load_active_type(xli) == "chat"  # the switch itself still stands


def test_workbench_chrome_applies_even_when_hard_busy(tmp_path):
    """Quick-strip refresh is not gated on hard busy (posture no longer waits)."""
    from types import SimpleNamespace as NS
    from xlii.serve_face import FaceServer
    from xlii.workbench import BUILTIN_WORKBENCHES

    sent = []
    state = NS(workbench=BUILTIN_WORKBENCHES["code"], project=None, agent=None, cfg=None)
    server = FaceServer(boot=NS(state=state))
    server.send = lambda obj: sent.append(obj)
    server._busy.set()
    server._apply_workbench_chrome()
    assert server.posture == "chat"
    assert any(o.get("type") == "chrome_state" for o in sent)
