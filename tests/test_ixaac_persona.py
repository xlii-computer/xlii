"""Shipped identities: mojo (journal) + iXaac (chat costume) + the naming ritual.

All disk-only / monkeypatched: no network, no real ~/.config, no terminal. The
load-bearing guarantees:
  - a fresh install equips mojo as the journal; iXaac is the chat costume;
  - jobs are `/role`, not extra stock personas;
  - `/edit --id mojo` opens the *real* seeded file, not an auto-created blank;
  - code mode gets ZERO personality injection (byte-identical to pre-vector).
"""

from types import SimpleNamespace

import pytest

import xlii.persona
from xlii.persona import (
    CHAT_DEFAULT_PERSONA_DISPLAY,
    CHAT_DEFAULT_PERSONA_ID,
    DEFAULT_PERSONA_DISPLAY,
    DEFAULT_PERSONA_ID,
    Persona,
    ensure_default_persona,
    list_personas,
    stock_persona_prompt,
    stock_personas_dir,
)


@pytest.fixture()
def personas(tmp_path, monkeypatch):
    """Isolate the persona prompt dir (and lazy project dir) to tmp."""
    monkeypatch.setattr(xlii.persona, "PERSONAS_DIR", tmp_path / "personas")
    monkeypatch.setattr(xlii.persona, "CHAT_STATE_DIR", tmp_path / "chat")
    return tmp_path


# --------------------------------------------------------------------------- #
#  The stock asset + seeding
# --------------------------------------------------------------------------- #

def test_stock_template_ships_and_has_name_slot():
    """The bundled template exists and carries an unresolved `{name}` slot (proof
    that seeding substitutes rather than copying a hard-coded identity)."""
    raw = (stock_personas_dir() / f"{DEFAULT_PERSONA_ID}.md").read_text()
    assert "{name}" in raw
    assert "Mojo" in raw or "mojo" in raw


def test_stock_persona_prompt_substitutes_name():
    text = stock_persona_prompt("Jarvis")
    assert "{name}" not in text
    assert "The mojo is Jarvis" in text


def test_stock_personas_are_voices_not_roles():
    """Shipped chat files are costumes. Jobs live in stock_roles / `/role`."""
    names = {p.stem for p in stock_personas_dir().glob("*.md")}
    assert "mojo" in names and "ixaac" in names
    assert names.isdisjoint({"doctor", "gopher", "julie", "stubing", "capn"})


def test_ensure_default_persona_seeds_mojo(personas):
    p = ensure_default_persona()
    assert p.name == DEFAULT_PERSONA_ID
    assert p.exists()
    # Speaks as the mojo (display name), parses as a plain prompt (empty
    # loadout), and carries the three virtues as behavioral directives.
    body = p.prompt_body()
    assert body.startswith(f"The mojo is {DEFAULT_PERSONA_DISPLAY}")
    assert p.loadout() == {}
    assert not p.system_prompt().startswith("---")
    for virtue in ("Continuity", "Restraint", "Stewardship"):
        assert virtue in body
    assert "not a role" in (stock_personas_dir() / "ixaac.md").read_text().lower()
    assert "/role" in (stock_personas_dir() / "ixaac.md").read_text()


def test_legacy_unnamed_ixaac_island_is_adopted_as_mojo(personas):
    old = Persona("ixaac")
    old.write_prompt("LEGACY IXAAC")
    old.project_root.mkdir(parents=True)
    p = ensure_default_persona()
    assert p.name == "mojo"
    assert p.read_prompt() == "LEGACY IXAAC"
    assert not Persona("ixaac").exists()


def test_stale_mojo_island_chat_ixaac_json_is_retargeted(personas):
    """Half-migrated journal: mojo.md exists, island json still says ixaac."""
    import json

    from xlii.persona import _adopt_legacy_unnamed_ixaac

    mojo = Persona(DEFAULT_PERSONA_ID)
    mojo.write_prompt("JOURNAL")
    xli = mojo.project_root / ".xlii"
    xli.mkdir(parents=True)
    (xli / "project.json").write_text(json.dumps({
        "name": "chat/ixaac",
        "root": str(Persona(CHAT_DEFAULT_PERSONA_ID).project_root),
        "collection_id": "col-1",
        "created_at": "t",
        "local_only": True,
    }))
    Persona(CHAT_DEFAULT_PERSONA_ID).write_prompt("COSTUME")
    _adopt_legacy_unnamed_ixaac()
    data = json.loads((xli / "project.json").read_text())
    assert data["name"] == f"chat/{DEFAULT_PERSONA_ID}"
    assert data["root"] == str(mojo.project_root.resolve())
    from xlii.config import ProjectConfig

    assert ProjectConfig.load(mojo.project_root) is not None
    # Costume file stays; we did not steal it.
    assert Persona(CHAT_DEFAULT_PERSONA_ID).read_prompt() == "COSTUME"


def test_ensure_default_persona_is_idempotent_and_non_clobbering(personas):
    p = ensure_default_persona()
    p.write_prompt("EDITED BY USER")           # simulate an edit
    again = ensure_default_persona()            # re-run setup / init
    assert again.read_prompt() == "EDITED BY USER"  # never clobbered


def test_custom_name_seeds_from_template_under_that_name(personas):
    p = ensure_default_persona("Jarvis")
    assert p.name == "Jarvis"
    assert p.prompt_body().startswith("The mojo is Jarvis")
    assert not Persona(DEFAULT_PERSONA_ID).exists()  # only the chosen name is made


# --------------------------------------------------------------------------- #
#  The install-time naming ritual (xlii setup)
# --------------------------------------------------------------------------- #

def _fake_console():
    return SimpleNamespace(print=lambda *a, **k: None)


def _install(monkeypatch, *, interactive, answer=None):
    import xlii.cmds.provision.setup as setup_mod
    monkeypatch.setattr(setup_mod, "_prompt_is_interactive", lambda: interactive)
    calls = {"input": 0}

    def fake_input(prompt=""):
        calls["input"] += 1
        return answer

    monkeypatch.setattr("builtins.input", fake_input)
    name = setup_mod._install_companion_persona(_fake_console())
    return name, calls


def test_ritual_enter_keeps_mojo(personas, monkeypatch):
    name, calls = _install(monkeypatch, interactive=True, answer="")  # bare enter
    assert name == DEFAULT_PERSONA_ID
    assert Persona(DEFAULT_PERSONA_ID).exists()
    assert Persona(CHAT_DEFAULT_PERSONA_ID).exists()  # chat costume, not a role
    assert calls["input"] == 1


def test_ritual_custom_name_is_honored(personas, monkeypatch):
    name, _ = _install(monkeypatch, interactive=True, answer="Jarvis")
    assert name == "Jarvis"
    assert Persona("Jarvis").exists()
    assert not Persona(DEFAULT_PERSONA_ID).exists()
    assert Persona(CHAT_DEFAULT_PERSONA_ID).exists()  # costume still seeded


def test_ritual_invalid_name_falls_back_to_mojo(personas, monkeypatch):
    name, _ = _install(monkeypatch, interactive=True, answer="../evil")
    assert name == DEFAULT_PERSONA_ID
    assert Persona(DEFAULT_PERSONA_ID).exists()
    assert not Persona("../evil").exists()


def test_ritual_non_tty_defaults_silently(personas, monkeypatch):
    name, calls = _install(monkeypatch, interactive=False, answer="ignored")
    assert name == DEFAULT_PERSONA_ID
    assert Persona(DEFAULT_PERSONA_ID).exists()
    assert calls["input"] == 0                 # never prompts off a TTY


def test_ritual_ixaac_costume_is_not_the_journal(personas, monkeypatch):
    from xlii.persona import CHAT_DEFAULT_PERSONA_ID, ensure_stock_persona

    ensure_stock_persona(CHAT_DEFAULT_PERSONA_ID)
    name, calls = _install(monkeypatch, interactive=False)
    assert name == DEFAULT_PERSONA_ID
    assert Persona(DEFAULT_PERSONA_ID).exists()
    assert calls["input"] == 0


def test_ritual_non_tty_honors_throne_journal_env(personas, monkeypatch):
    monkeypatch.setenv("XLII_MOJO_NAME", "Jarvis")
    name, calls = _install(monkeypatch, interactive=False)
    assert name == "Jarvis"
    assert Persona("Jarvis").exists()
    assert not Persona(DEFAULT_PERSONA_ID).exists()
    assert calls["input"] == 0


def test_rename_factory_moves_file_and_refuses_existing(personas):
    from types import SimpleNamespace

    from xlii.persona import (
        FactoryRenameError,
        create_persona,
        factory_persona_id,
        list_bindable_personas,
        rename_factory_persona,
    )

    ensure_default_persona()
    cfg = SimpleNamespace(fallback_persona="", saves=0)
    cfg.save = lambda: setattr(cfg, "saves", cfg.saves + 1)
    assert rename_factory_persona("primebot", cfg=cfg) == "primebot"
    assert Persona("primebot").exists()
    assert not Persona(DEFAULT_PERSONA_ID).exists()
    assert cfg.fallback_persona == "primebot"
    create_persona("other")
    try:
        rename_factory_persona("other", cfg=cfg)
        raise AssertionError("should refuse existing persona")
    except FactoryRenameError as e:
        assert "already exists" in str(e)
    try:
        rename_factory_persona("default", cfg=cfg)
        raise AssertionError("should refuse reserved")
    except FactoryRenameError as e:
        assert "mobile journal" in str(e)
    assert factory_persona_id(cfg) == "primebot"
    names = [p.name for p in list_bindable_personas()]
    assert "default" not in names
    assert "primebot" in names


def test_rename_factory_target_state_conflict_does_not_move_prompt(personas):
    from types import SimpleNamespace

    from xlii.persona import FactoryRenameError, rename_factory_persona

    src = ensure_default_persona()
    src.write_prompt("USER EDITED PROMPT")
    cfg = SimpleNamespace(fallback_persona="", saves=0)
    cfg.save = lambda: setattr(cfg, "saves", cfg.saves + 1)
    Persona("orphan").project_root.mkdir(parents=True)

    with pytest.raises(FactoryRenameError, match="already exists"):
        rename_factory_persona("orphan", cfg=cfg)

    assert Persona(DEFAULT_PERSONA_ID).exists()
    assert Persona(DEFAULT_PERSONA_ID).read_prompt() == "USER EDITED PROMPT"
    assert not Persona("orphan").exists()
    assert cfg.fallback_persona == ""
    assert cfg.saves == 0


def test_list_bindable_hides_default_file(personas):
    from xlii.persona import list_bindable_personas, list_personas

    ensure_default_persona()
    Persona("default").write_prompt("leftover role-word file")
    assert any(p.name == "default" for p in list_personas())
    assert all(p.name != "default" for p in list_bindable_personas())


def test_ritual_never_clobbers_existing_companion(personas, monkeypatch):
    ensure_default_persona("Jarvis")           # user already onboarded
    name, calls = _install(monkeypatch, interactive=True, answer="Somethingelse")
    assert name == "Jarvis"                    # kept; ritual skipped
    assert calls["input"] == 0
    assert not Persona("Somethingelse").exists()


def test_ritual_adopts_leftover_ixaac_then_seeds_bartender(personas, monkeypatch):
    """Old unnamed journal was spelled ixaac. Setup moves it to mojo and
    seeds a fresh iXaac costume — not a /role."""
    old = Persona("ixaac")
    old.write_prompt("LEGACY JOURNAL")
    name, calls = _install(monkeypatch, interactive=True, answer="ignored")
    assert name == DEFAULT_PERSONA_ID
    assert calls["input"] == 0
    assert Persona(DEFAULT_PERSONA_ID).read_prompt() == "LEGACY JOURNAL"
    assert Persona(CHAT_DEFAULT_PERSONA_ID).exists()
    assert "You are iXaac" in Persona(CHAT_DEFAULT_PERSONA_ID).read_prompt()
    assert Persona(CHAT_DEFAULT_PERSONA_ID).read_prompt() != "LEGACY JOURNAL"


# --------------------------------------------------------------------------- #
#  Default resolution on the chat surface
# --------------------------------------------------------------------------- #

def test_naked_chat_bootstraps_ixaac_costume_when_no_personas(personas):
    from xlii.cmds.sessions.resolve import _resolve_persona_to_load
    p = _resolve_persona_to_load(None)
    assert p is not None and p.name == CHAT_DEFAULT_PERSONA_ID
    assert p.prompt_body().startswith(f"You are {CHAT_DEFAULT_PERSONA_DISPLAY}")
    assert Persona(DEFAULT_PERSONA_ID).exists()  # journal seeded too


def test_naked_chat_skips_journal_last_used(personas):
    """Bare chat sits with a costume, not the journal — even if last-used is mojo."""
    mojo = ensure_default_persona()
    mojo.touch_used()
    from xlii.cmds.sessions.resolve import _resolve_persona_to_load
    p = _resolve_persona_to_load(None)
    assert p.name == CHAT_DEFAULT_PERSONA_ID


def test_naked_chat_keeps_last_used_costume(personas):
    from xlii.persona import create_persona
    from xlii.cmds.sessions.resolve import _resolve_persona_to_load

    ensure_default_persona()
    other = create_persona("ada")
    other.touch_used()
    p = _resolve_persona_to_load(None)
    assert p.name == "ada"


def test_explicit_mojo_before_setup_seeds_from_stock(personas):
    from xlii.cmds.sessions.resolve import _resolve_persona_to_load
    p = _resolve_persona_to_load(DEFAULT_PERSONA_ID)   # missing → seed real template
    assert p.name == DEFAULT_PERSONA_ID
    assert p.prompt_body().startswith(f"The mojo is {DEFAULT_PERSONA_DISPLAY}")
    assert list_personas()[0].name == DEFAULT_PERSONA_ID


def test_explicit_ixaac_seeds_bartender_not_journal(personas):
    from xlii.cmds.sessions.resolve import _resolve_persona_to_load
    p = _resolve_persona_to_load(CHAT_DEFAULT_PERSONA_ID)
    assert p.name == CHAT_DEFAULT_PERSONA_ID
    body = p.prompt_body()
    assert body.startswith(f"You are {CHAT_DEFAULT_PERSONA_DISPLAY}")
    assert "not a role" in body.lower()
    assert "/role" in body
    assert not Persona(DEFAULT_PERSONA_ID).exists()


# --------------------------------------------------------------------------- #
#  /edit --id mojo opens the real file (never a blank auto-create)
# --------------------------------------------------------------------------- #

def test_edit_id_mojo_opens_the_real_seeded_file(personas, monkeypatch):
    ensure_default_persona()                   # the shipped journal is on disk
    import xlii.repl_cmds.chat as chat_mod
    opened = {}
    monkeypatch.setattr(chat_mod, "open_in_editor", lambda path: opened.setdefault("path", path))
    ctx = {"console": _fake_console(), "state": None, "persona": None}
    chat_mod._edit_handler(f"/edit --id {DEFAULT_PERSONA_ID}", ctx)
    assert opened["path"] == Persona(DEFAULT_PERSONA_ID).prompt_path
    # opened the REAL seeded content, not a blank generic prompt
    assert f"The mojo is {DEFAULT_PERSONA_DISPLAY}" in Persona(DEFAULT_PERSONA_ID).read_prompt()


# --------------------------------------------------------------------------- #
#  Code mode: ZERO personality injection (the most important guarantee)
# --------------------------------------------------------------------------- #

def test_code_mode_prompt_is_invariant_to_persona(personas, monkeypatch):
    """Creating the iXaac persona must not change the code-mode system prompt by a
    single byte — code stays deterministic (Vector C exit gate)."""
    from xlii.turn_prompt import build_code_system_prompt

    xli_dir = personas / ".xlii"
    xli_dir.mkdir(parents=True, exist_ok=True)
    project = SimpleNamespace(xli_dir=xli_dir, project_root=personas, local_only=True)

    before = build_code_system_prompt(project)
    ensure_default_persona()                   # equip the companion
    after = build_code_system_prompt(project)

    assert before == after                     # byte-identical
    for marker in ("iXaac", "Joshua", "WarGames", "summoned deliberately"):
        assert marker not in after             # no persona text leaked in
