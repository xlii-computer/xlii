"""/mojo — one-shot ask to iXaac, grounded in this project's record (fabric F2).

The verb resolves the DEFAULT persona (never a name — the whole line is the
question), fuses the project journal + wiki into the turn, answers in one shot,
and leaves the live surface untouched (no mode switch). Offline: the persona
engine and resolver are stubbed, so no Collection / pool is touched.
"""

from __future__ import annotations

from types import SimpleNamespace

from tests.helpers import FakeConsole


def _state(project=None, journal=None):
    return SimpleNamespace(project=project, profile=None, persona=None,
                           journal=journal, pool=SimpleNamespace(), cfg=SimpleNamespace(),
                           yolo=False)


def _ctx(project=None, journal=None):
    return {"console": FakeConsole(), "state": _state(project, journal), "project": project}


def _wire(monkeypatch, captured):
    """Stub the persona engine + resolver so h_mojo runs fully offline."""
    monkeypatch.setattr("xlii.cmds.sessions.resolve._lookup_persona",
                        lambda pid: SimpleNamespace(name=pid))
    monkeypatch.setattr(
        "xlii.cmds.sessions.ask.run_persona_oneshot",
        lambda persona, prompt, **kw: (
            captured.update(persona=persona, prompt=prompt, kw=kw) or "ANSWER"))
    # If any test path reaches the mode switch, it's a hard failure — /mojo must
    # NEVER switch modes anymore.
    monkeypatch.setattr("xlii.repl_cmds.switch.switch_to_persona",
                        lambda ctx, persona: captured.setdefault("SWITCHED", True) or True)


def test_mojo_registered_in_code_and_chat():
    from xlii.commands import find_repl_command
    from xlii.repl_cmds import register_all

    register_all()
    assert find_repl_command("/mojo", repl="code") is not None
    assert find_repl_command("/mojo", repl="chat") is not None


def test_askjo_resolves_as_a_hidden_alias_of_mojo():
    from xlii.commands import find_repl_command
    from xlii.repl_cmds import register_all

    register_all()
    ak = find_repl_command("/askjo", repl="code")
    assert ak is not None and ak.name == "mojo"     # alias → the mojo verb


def test_mojo_is_one_shot_and_never_switches_mode(monkeypatch):
    from xlii.repl_cmds import mojo

    captured = {}
    _wire(monkeypatch, captured)
    ctx = _ctx(project=SimpleNamespace(bound_persona=None, xli_dir=None))
    assert mojo.h_mojo("/mojo what did I work on recently?", ctx) is True
    # It answered via the one-shot engine, with the DEFAULT persona + raw question.
    assert captured["persona"].name == "mojo"
    assert captured["prompt"] == "what did I work on recently?"
    assert "SWITCHED" not in captured                 # NO mode switch, ever
    # drain is off in the REPL (snappy); it persists locally.
    assert captured["kw"]["drain"] is False and captured["kw"]["persist"] is True


def test_mojo_defaults_to_bound_persona_when_set(monkeypatch):
    from xlii.repl_cmds import mojo

    captured = {}
    _wire(monkeypatch, captured)
    ctx = _ctx(project=SimpleNamespace(bound_persona="bob", xli_dir=None))
    mojo.h_mojo("/mojo hey", ctx)
    assert captured["persona"].name == "bob"          # project binding wins


def test_mojo_arg_is_a_question_not_a_persona_name(monkeypatch):
    """The footgun is dead: `/mojo bob` asks iXaac the question 'bob', it does
    NOT switch to (or create) a persona named 'bob'."""
    from xlii.repl_cmds import mojo

    captured = {}
    _wire(monkeypatch, captured)
    ctx = _ctx(project=SimpleNamespace(bound_persona=None, xli_dir=None))
    mojo.h_mojo("/mojo bob", ctx)
    assert captured["persona"].name == "mojo"        # default persona, not 'bob'
    assert captured["prompt"] == "bob"                # 'bob' was the QUESTION


def test_mojo_fuses_the_project_journal_and_wiki(monkeypatch):
    from xlii.repl_cmds import mojo

    captured = {}
    _wire(monkeypatch, captured)
    journal = SimpleNamespace(
        recall_context=lambda q, wiki_context="": f"JOURNAL[{q}]+{wiki_context}")
    ctx = _ctx(project=SimpleNamespace(bound_persona=None, xli_dir=None), journal=journal)
    mojo.h_mojo("/mojo status?", ctx)
    # The journal's recall_context feeds ambient_context (the fusion seam).
    # Desk banner prepends, so JOURNAL is no longer at offset 0.
    ambient = captured["kw"]["ambient_context"]
    assert "JOURNAL[status?]" in ambient
    head = ambient.lstrip()
    assert (
        head.startswith("[bearings]")
        or head.startswith("[current desk:")
        or head.startswith("JOURNAL[")
    )


def test_mojo_bare_prints_usage_and_asks_nothing(monkeypatch):
    from xlii.repl_cmds import mojo

    captured = {}
    _wire(monkeypatch, captured)
    ctx = _ctx(project=SimpleNamespace(bound_persona=None, xli_dir=None))
    assert mojo.h_mojo("/mojo", ctx) is True
    assert "persona" not in captured                  # no turn run
    assert any("usage" in ln for ln in ctx["console"].lines)


def test_mojo_needs_a_session():
    from xlii.repl_cmds import mojo

    ctx = {"console": FakeConsole(), "state": None}
    assert mojo.h_mojo("/mojo hi", ctx) is True
    assert any("interactive" in ln for ln in ctx["console"].lines)
