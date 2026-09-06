"""The Profile seam (RP1). These guard the load-bearing wiring decisions so the
pure refactor can't silently drift: the system-prompt source per mode, the
seed-before vs seed-after split, and — critically — the code/chat persist policy
divergence (the whitespace-truthiness footgun + the __rescan__ marker).

Run with `python -m pytest` (imports tests.helpers; bare pytest mis-collects)."""

from types import SimpleNamespace

from xlii import transcript as tx
from xlii.profile import Loadout, TurnStore, chat_profile, code_profile


def _project(root):
    return SimpleNamespace(project_root=root, xli_dir=root / ".xlii",
                           local_only=True, name="proj",
                           collection_id=None, conversation_id=None)


def _persona(root):
    return SimpleNamespace(name="bob", project_root=root, turns_dir=root / "turns",
                           system_prompt=lambda: "PERSONA PROMPT")


# --------------------------------------------------------------------------- #
#  Profile construction — the system-prompt source + seed mechanism per mode
# --------------------------------------------------------------------------- #

def test_code_profile_is_local_only_bounded(tmp_path):
    p = code_profile(_project(tmp_path), seed_limit=20)
    assert p.mode == "code"
    assert p.memory.turns_dir == tmp_path / ".xlii" / "turns"
    assert p.memory.mark_rescan is False       # local-only, no __rescan__
    assert p.memory.bounded_reply_scan is True
    assert p.loadout.persona is None           # code has no loadout
    assert "rail" in p.affordances


def test_code_profile_ignores_bound_persona(tmp_path):
    # RP7: code is isolated. `bound_persona` is a CHAT default only — code_profile
    # never consults it, so code memory stays project-local (no persona store,
    # no persona loadout). This is what keeps chat oblivious to the code.
    proj = _project(tmp_path)
    proj.bound_persona = "bob"
    p = code_profile(proj, seed_limit=20)
    assert p.mode == "code"
    assert p.identity is proj                          # bar shows the project, not bob
    assert p.memory.turns_dir == tmp_path / ".xlii" / "turns"   # project-local, NOT bob's
    assert p.loadout.persona is None                   # bob's loadout does NOT apply to code


def test_chat_profile_is_synced_unbounded(tmp_path):
    persona = _persona(tmp_path)
    p = chat_profile(persona, _project(tmp_path), seed_limit=20)
    assert p.mode == "chat"
    assert p.memory.turns_dir == tmp_path / "turns"
    assert p.memory.mark_rescan is True
    assert p.memory.bounded_reply_scan is False
    assert p.loadout.persona is persona


def test_recent_turns_loads_in_order_single_source(tmp_path):
    # One read backs both the seeded history and the banner count (parity with
    # the old single load_recent_turns call — no double-read).
    (tmp_path / "turns").mkdir()
    tx.write_turn(tmp_path / "turns", "q1", "a1")
    tx.write_turn(tmp_path / "turns", "q2", "a2")
    p = chat_profile(_persona(tmp_path), _project(tmp_path), seed_limit=20)
    recent = p.memory.recent_turns()
    assert [t.user for t in recent] == ["q1", "q2"]
    assert len(recent) == 2                     # the banner's "M loaded inline"
    assert p.memory.count() == 2


def test_code_seed_into_appends_after_system(tmp_path):
    (tmp_path / ".xlii" / "turns").mkdir(parents=True)
    tx.write_turn(tmp_path / ".xlii" / "turns", "q1", "a1")
    p = code_profile(_project(tmp_path), seed_limit=20)
    agent = SimpleNamespace(history=[{"role": "system", "content": "CODEPROMPT"}])
    n = p.memory.seed_into(agent)
    assert n == 1
    assert agent.history == [
        {"role": "system", "content": "CODEPROMPT"},
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
    ]


# --------------------------------------------------------------------------- #
#  TurnStore.persist — the per-mode policy divergence (the footguns)
# --------------------------------------------------------------------------- #

def test_chat_persist_marks_rescan_and_keeps_whitespace_truthiness(tmp_path):
    store = TurnStore(tmp_path / "turns", mark_rescan=True, bounded_reply_scan=False, seed_limit=20)
    # whitespace-only reply: chat's bare `if reply:` truthiness WRITES it (today's behavior)
    hist = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "   "}]
    dirty = store.persist(hist, "q", set())
    assert "__rescan__" in dirty
    assert store.count() == 1


def test_chat_persist_unbounded_scan_resurfaces_prior_reply(tmp_path):
    # chat's scan is intentionally UNBOUNDED (newest-first across whole history).
    store = TurnStore(tmp_path / "turns", mark_rescan=True, bounded_reply_scan=False, seed_limit=20)
    hist = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "old reply"},
        {"role": "user", "content": "new"},  # current turn produced no assistant content
    ]
    dirty = store.persist(hist, "new", set())
    assert "__rescan__" in dirty
    assert tx.get_last_turn_content(tmp_path / "turns") == {"user": "new", "assistant": "old reply"}


def test_code_persist_skips_whitespace_and_never_marks_rescan(tmp_path):
    store = TurnStore(tmp_path / ".xlii" / "turns", mark_rescan=False, bounded_reply_scan=True, seed_limit=20)
    hist = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "   "}]
    dirty = store.persist(hist, "q", set())
    assert dirty == set()                       # .strip() guard → no write, no __rescan__
    assert store.count() == 0


def test_code_persist_bounded_scan_ignores_prior_reply(tmp_path):
    # code's scan is turn-bounded — must NOT resurface a prior reply (RP0 phantom-turn).
    store = TurnStore(tmp_path / ".xlii" / "turns", mark_rescan=False, bounded_reply_scan=True, seed_limit=20)
    hist = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "old reply"},
        {"role": "user", "content": "new"},  # current turn, no reply
    ]
    dirty = store.persist(hist, "new", set())
    assert dirty == set()
    assert store.count() == 0                   # nothing written — no phantom turn


def test_code_persist_writes_real_reply_untouched_dirty(tmp_path):
    store = TurnStore(tmp_path / ".xlii" / "turns", mark_rescan=False, bounded_reply_scan=True, seed_limit=20)
    hist = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "real answer"}]
    dirty = store.persist(hist, "q", {"some/file.py"})
    assert dirty == {"some/file.py"}            # file edits preserved, no __rescan__ added
    assert tx.load_recent_turns(tmp_path / ".xlii" / "turns", 1)[0].assistant == "real answer"


def test_loadout_none_is_noop(tmp_path):
    Loadout(persona=None).apply(state=None, project=None)  # must not raise / touch anything
