"""Unit tests for the input-hint registry (xlii.hints), seam #4.

Pure functions over REPLState — no textual, no prompt_toolkit. The registry the
Textual input's placeholder is resolved through: built-ins + plugin/third-party
registrations + an equipped role's `hint:` (seam #3), unified by resolve_hint().
"""

from __future__ import annotations

from types import SimpleNamespace

from xlii import hints
from xlii.role import ROLE_LOADOUT_KEYS, load_role


def _st(**kw):
    base = dict(
        agent=SimpleNamespace(active_mode=None, rail=None),
        project=SimpleNamespace(name="proj", project_root=None),
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _role_dir(tmp_path):
    d = tmp_path / ".xlii" / "roles"
    d.mkdir(parents=True, exist_ok=True)
    return d


# --- built-in mode hints --------------------------------------------------- #

def test_builtin_mode_hints():
    assert hints.mode_hint("code") == hints.BUILTIN_HINTS["code"]
    assert "worker" in hints.mode_hint("code")
    assert "to ask" not in hints.mode_hint("code")
    assert hints.mode_hint("chat") == hints.BUILTIN_HINTS["chat"]
    assert hints.mode_hint("plan") == hints.BUILTIN_HINTS["plan"]
    assert hints.mode_hint("howto") == hints.BUILTIN_HINTS["howto"]
    assert "/off" in hints.mode_hint("howto")
    assert "/howto off" not in hints.mode_hint("howto")
    assert hints.mode_hint("ops") == hints.BUILTIN_HINTS["ops"]
    assert "/off" in hints.mode_hint("ops")


def test_unknown_mode_falls_back_to_talk():
    assert hints.mode_hint("nonsense") == hints.TALK_HINT


def test_code_demotes_to_talk_when_not_shell_primary():
    # shell-primary off → even bare `code` input asks the AI, so it borrows the
    # talk hint (mirrors the inline REPL's bare-input rule).
    assert hints.mode_hint("code", shell_primary=False) == hints.TALK_HINT
    assert hints.mode_hint("code", shell_primary=True) == hints.BUILTIN_HINTS["code"]


# --- the public seam: register_hint ---------------------------------------- #

def test_register_hint_overrides_builtin_then_unregister_restores():
    try:
        hints.register_hint("code", "PLUGIN OVERRIDE")
        assert hints.mode_hint("code") == "PLUGIN OVERRIDE"
        assert hints.resolve_hint(_st()) == "PLUGIN OVERRIDE"
        assert hints.registered_hints()["code"] == "PLUGIN OVERRIDE"
    finally:
        hints.unregister_hint("code")
    assert hints.mode_hint("code") == hints.BUILTIN_HINTS["code"]
    assert "code" not in hints.registered_hints()


def test_register_new_mode_is_third_party_seam():
    # a third party can teach the input about a mode word the built-ins never
    # had — the same path the built-ins occupy.
    try:
        hints.register_hint("zorp", "zorp · do the thing")
        assert hints.mode_hint("zorp") == "zorp · do the thing"
    finally:
        hints.unregister_hint("zorp")


# --- mode-aware resolution over live state --------------------------------- #

def test_resolve_hint_is_mode_aware():
    assert hints.resolve_hint(_st()) == hints.BUILTIN_HINTS["code"]
    assert hints.resolve_hint(_st(howto_mode=True)) == hints.BUILTIN_HINTS["howto"]
    from xlii.mode_controller import PlanController
    st = _st(agent=SimpleNamespace(active_mode=PlanController(), rail=None))
    assert hints.resolve_hint(st) == hints.BUILTIN_HINTS["plan"]


# --- role-aware resolution (consumes seam #3's hint:) ---------------------- #

def test_resolve_hint_role_custom_hint_leads(tmp_path):
    (_role_dir(tmp_path) / "shipper.md").write_text(
        "---\nhint: shipper · cut the release · /execute\n---\nYou ship.\n",
        encoding="utf-8",
    )
    st = _st(project=SimpleNamespace(name="proj", project_root=tmp_path),
             active_role="shipper", persona=None)
    assert hints.resolve_hint(st) == "shipper · cut the release · /execute"


def test_resolve_hint_role_without_custom_hint_is_plain_mode_hint(tmp_path):
    # no `hint:` → the placeholder is just the mode hint; the frame's role tab
    # (status.frame_tabs) is what announces the role doc riding the session, so
    # the input no longer repeats role:<name> inside the box.
    (_role_dir(tmp_path) / "plain.md").write_text(
        "---\nskills: [grounded-analysis]\n---\nYou are plain.\n", encoding="utf-8"
    )
    st = _st(project=SimpleNamespace(name="proj", project_root=tmp_path),
             active_role="plain", persona=None)
    assert hints.resolve_hint(st) == hints.BUILTIN_HINTS["code"]


def test_resolve_hint_role_silent_in_chat_become(tmp_path):
    # chat-`become`: the persona IS the role (name matches), so its name already
    # leads the bar — the hint stays the plain chat hint (no role: re-skin),
    # mirroring status.role()'s chat-silence.
    (_role_dir(tmp_path) / "shipper.md").write_text(
        "---\nhint: SHOULD-NOT-SHOW\n---\nYou ship.\n", encoding="utf-8"
    )
    st = _st(project=SimpleNamespace(name="proj", project_root=tmp_path),
             active_role="shipper", persona=SimpleNamespace(name="shipper"))
    assert hints.resolve_hint(st) == hints.BUILTIN_HINTS["chat"]


def test_resolve_hint_survives_missing_role(tmp_path):
    # active_role names a descriptor that doesn't exist → fall back to the base
    # mode hint, never raising. (The frame's role tab handles announcing the role.)
    st = _st(project=SimpleNamespace(name="proj", project_root=tmp_path),
             active_role="ghost", persona=None)
    assert hints.resolve_hint(st) == hints.BUILTIN_HINTS["code"]


# --- Role.hint() accessor + ROLE_LOADOUT_KEYS (seam #3, B-side) ------------ #

def test_role_loadout_keys_carries_hint():
    assert "hint" in ROLE_LOADOUT_KEYS


def test_role_hint_accessor(tmp_path):
    (_role_dir(tmp_path) / "withhint.md").write_text(
        "---\nhint: lead the input box\n---\nbody\n", encoding="utf-8"
    )
    (_role_dir(tmp_path) / "nohint.md").write_text(
        "---\nskills: [x]\n---\nbody\n", encoding="utf-8"
    )
    assert load_role("withhint", tmp_path).hint() == "lead the input box"
    assert load_role("nohint", tmp_path).hint() == ""
