"""RP5 — workspace ↔ loadout unification: a workspace is a saved loadout.

A workspace now carries the full loadout — attachments PLUS the pinned model and
temperature — not just refs/docs. `/loadout` is the cross-mode inspector for the
live bundle the profile bar summarizes. All disk-only / no network.

Run with `python -m pytest` (imports tests.helpers).
"""

import json
from types import SimpleNamespace

from xlii.repl import REPLState
from xlii.repl_cmds.loadout import _loadout_handler
from xlii.tui import status
from tests.helpers import FakeConsole, make_agent


def _state(tmp_path, *, sub="proj"):
    """A REPLState over a real (local) project dir with a real fake-wired Agent."""
    root = tmp_path / sub
    (root / ".xlii").mkdir(parents=True, exist_ok=True)
    agent = make_agent(root)
    return REPLState(console=FakeConsole(), agent=agent, project=agent.project,
                     cfg=agent.cfg, pool=agent.pool)


# --------------------------------------------------------------------------- #
#  a workspace is a saved loadout (model + temperature travel with it)
# --------------------------------------------------------------------------- #

def test_workspace_roundtrips_model_and_temp(tmp_path):
    state = _state(tmp_path)
    state.agent.model_override = "grok-4"
    state.agent.temperature_override = 0.3
    state.attach_doc("conventions", "BODY")
    state.save_workspace("wip")

    # Drift the live loadout, then load the saved one back.
    state.agent.model_override = None
    state.agent.temperature_override = None
    state.attached_docs = []

    assert state.load_workspace("wip") is True
    assert state.agent.model_override == "grok-4"
    assert state.agent.temperature_override == 0.3
    assert [n for n, _ in state.attached_docs] == ["conventions"]


def test_load_workspace_unknown_returns_false(tmp_path):
    state = _state(tmp_path)
    assert state.load_workspace("nope") is False


def test_session_start_does_not_auto_apply_workspace_model(tmp_path):
    # A workspace's model is NOT auto-applied at session start: the persona
    # frontmatter stays authoritative there. The pin travels only via an
    # explicit /workspace load. (Auto-restore caused two data-loss foot-guns —
    # see RP5-SUMMARY: stale model overriding an edited frontmatter, and a
    # switch erasing a saved pin.)
    s1 = _state(tmp_path)
    s1.agent.model_override = "grok-4-fast"
    s1.save_workspace("main")                       # banked into the live workspace

    s2 = _state(tmp_path)                           # fresh agent (override None), same dir
    s2.load()
    assert s2.agent.model_override is None           # NOT auto-restored at start
    assert s2.load_workspace("main") is True
    assert s2.agent.model_override == "grok-4-fast"  # explicit load applies it


def test_plain_save_does_not_capture_or_drop_model(tmp_path):
    # A plain save() must neither bank a transient model into the slot nor pop a
    # previously-saved one (the pop-on-None data-loss bug the review found).
    s = _state(tmp_path)
    s.agent.model_override = "pinned"
    s.save_workspace("wip")                          # wip now carries "pinned"
    s.agent.model_override = None                    # e.g. a switch cleared it
    s.save()                                         # plain save() must not pop wip's model
    assert s.load_workspace("wip") is True
    assert s.agent.model_override == "pinned"        # still there


def test_back_compat_old_session_without_model(tmp_path):
    state = _state(tmp_path)
    (tmp_path / "proj" / ".xlii" / "session.json").write_text(json.dumps({
        "version": 2, "current_workspace": "main",
        "workspaces": {"main": {"attached_refs": [], "attached_docs": [["d", "BODY"]]}},
        "yolo": False,
    }))
    assert state.load() is True                     # attachments restored
    assert state.agent.model_override is None        # no model key → no override
    assert [n for n, _ in state.attached_docs] == ["d"]


def test_export_import_carries_loadout(tmp_path, monkeypatch):
    gdir = tmp_path / "globals"
    gdir.mkdir()
    monkeypatch.setattr("xlii.loadout_paths.GLOBAL_LOADOUTS_DIR", gdir)

    state = _state(tmp_path)
    state.agent.model_override = "grok-4"
    state.attach_doc("d", "BODY")
    state.export_workspace("shared")
    data = json.loads((gdir / "shared.json").read_text())
    assert data["model"] == "grok-4"

    s2 = _state(tmp_path, sub="proj2")
    assert s2.import_workspace("shared", as_name="local") is True
    assert s2.load_workspace("local") is True
    assert s2.agent.model_override == "grok-4"
    assert [n for n, _ in s2.attached_docs] == ["d"]


def test_legacy_global_loadouts_are_drained_into_canonical(tmp_path, monkeypatch):
    # No permanent fallback: a legacy ~/.xli/workspaces loadout is MOVED into the
    # canonical dir on the first read, so it stays reachable (import/load) and a
    # subsequent delete sticks — the legacy file is gone, not merely shadowed.
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    canon = tmp_path / "canon"
    canon.mkdir()
    monkeypatch.setattr("xlii.loadout_paths.LEGACY_GLOBAL_LOADOUTS_DIR", legacy)
    monkeypatch.setattr("xlii.loadout_paths.GLOBAL_LOADOUTS_DIR", canon)

    (legacy / "old.json").write_text(json.dumps({
        "version": 1,
        "attached_refs": [],
        "attached_docs": [["legacy-doc", "BODY"]],
        "attached_files": [],
    }))
    (canon / "new.json").write_text(json.dumps({
        "version": 1,
        "attached_refs": [],
        "attached_docs": [["canon-doc", "BODY"]],
        "attached_files": [],
    }))

    assert REPLState.list_global_workspaces() == ["new", "old"]

    state = _state(tmp_path)
    assert state.import_workspace("old", as_name="restored") is True
    assert state.load_workspace("restored") is True
    assert [n for n, _ in state.attached_docs] == ["legacy-doc"]

    assert REPLState.delete_global_workspace("old") is True
    assert not (legacy / "old.json").exists()
    assert REPLState.list_global_workspaces() == ["new"]


def test_delete_global_loadout_after_legacy_shadow_drained(tmp_path, monkeypatch):
    # A name present in BOTH roots: on read the legacy shadow is drained (canonical
    # wins, the shadow is dropped), so a single delete clears the only remaining
    # copy and the name can't resurface from a lingering legacy file.
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    canon = tmp_path / "canon"
    canon.mkdir()
    monkeypatch.setattr("xlii.loadout_paths.LEGACY_GLOBAL_LOADOUTS_DIR", legacy)
    monkeypatch.setattr("xlii.loadout_paths.GLOBAL_LOADOUTS_DIR", canon)

    payload = json.dumps({
        "version": 1,
        "attached_refs": [],
        "attached_docs": [["dup-doc", "BODY"]],
        "attached_files": [],
    })
    (legacy / "dup.json").write_text(payload)
    (canon / "dup.json").write_text(payload)

    assert REPLState.list_global_workspaces() == ["dup"]

    # One delete fully removes the name from both roots...
    assert REPLState.delete_global_workspace("dup") is True
    assert not (canon / "dup.json").exists()
    assert not (legacy / "dup.json").exists()
    assert REPLState.list_global_workspaces() == []

    # ...and a delete that matches nothing reports False.
    assert REPLState.delete_global_workspace("dup") is False


# --------------------------------------------------------------------------- #
#  the profile bar's loadout segment shows the active workspace name (RP5)
# --------------------------------------------------------------------------- #

def test_bar_loadout_main_workspace_stays_silent(tmp_path):
    state = _state(tmp_path)
    assert status.loadout(state) == ""              # main + no attachments → empty


def test_bar_loadout_shows_workspace_name(tmp_path):
    state = _state(tmp_path)
    state.current_workspace = "wip"
    assert status.loadout(state) == "wip"           # named workspace, no attachments

    state.attach_doc("d", "B")
    seg = status.loadout(state)
    assert seg.startswith("wip · ")                 # name leads, then the counts
    assert "1 doc" in seg


# --------------------------------------------------------------------------- #
#  /loadout — the cross-mode inspector (RP5: was chat-only)
# --------------------------------------------------------------------------- #

def test_loadout_command_works_in_code_mode(tmp_path):
    state = _state(tmp_path)                         # no persona, no profile
    state.agent.model_override = "grok-4"
    assert _loadout_handler("/loadout", state.as_context_dict()) is True
    text = state.console.text
    assert "loadout" in text
    assert "grok-4" in text                          # active model surfaced


def test_loadout_command_shows_declared_persona(tmp_path):
    state = _state(tmp_path)
    persona = SimpleNamespace(name="bob", loadout=lambda: {"model": "grok-4", "docs": ["conv"]})
    state.profile = SimpleNamespace(loadout=SimpleNamespace(persona=persona))
    _loadout_handler("/loadout", state.as_context_dict())
    text = state.console.text
    assert "bob" in text                             # persona name in the header
    assert "declared model" in text                  # frontmatter surfaced
