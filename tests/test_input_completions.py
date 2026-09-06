"""Input completions (the-fold Vector F) — :shortcode: symbols + emoji v2 + shell ghost.

Two layers:
* Pure/unit tests over the in-memory tables and the compile pass — no Textual,
  no model, no I/O beyond an explicit tmp `config_dir`.
* Pilot tests (Textual `run_test`) proving the keystroke path: the capped popup,
  Tab-accept, ordinary-colon rejection, ghost text in shell context, right-arrow
  accept (insert-never-execute), and graceful nothing with no table.

THE LAW under test: no model and no I/O in the input loop.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from xlii import shell_suggest as ss
from xlii.tui import shortcodes as sc

# --------------------------------------------------------------------------- #
#  :shortcode: — pure query / match / apply
# --------------------------------------------------------------------------- #

def test_shortcode_query_triggers_on_colon_plus_two_chars():
    assert sc.shortcode_query(":box", 4) == (0, "box")
    assert sc.shortcode_query("go :box", 7) == (3, "box")  # after whitespace
    assert sc.shortcode_query(":->", 3) == (0, "->")       # symbolic arrow token


@pytest.mark.parametrize("line,col", [
    (":t", 2),          # only 1 char after ':'  -> below the 2-char floor
    ("12:30", 5),       # time — ':' preceded by a digit, not whitespace/start
    ("http://x", 5),    # URL scheme colon
    ("key: value", 4),  # dict/yaml colon followed by space (partial empty)
    (":box-tl:", 8),    # cursor after a CLOSED shortcode — no re-trigger
    ("", 0),
])
def test_shortcode_query_rejects_ordinary_colons(line, col):
    assert sc.shortcode_query(line, col) is None


def test_shortcode_matches_ranked_and_capped():
    m = sc.shortcode_matches("box")
    codes = [c for c, _g in m]
    # Prefix matches win: the four box-drawing codes rank first, ahead of any
    # subsequence match (emoji v2 adds :inbox:/:outbox:, which also contain "box").
    assert set(codes[:4]) == {"box-tl", "box-tr", "box-bl", "box-br"}
    assert {g for _c, g in m[:4]} == {"╭", "╮", "╰", "╯"}
    assert len(sc.shortcode_matches("e", limit=8)) <= 8        # cap honored
    # the spec's canonical `:th…` example resolves to a v1 SYMBOL (theta), not emoji
    assert ("theta", "θ") in sc.shortcode_matches("th")


def test_apply_shortcode_replaces_token_with_glyph():
    assert sc.apply_shortcode("go :box here", 3, 7, "╭") == "go ╭ here"


def test_served_table_is_width_safe_v2():
    """v2: the served table now carries emoji, but EVERY glyph is width-safe —
    single-width symbols OR two-cell single-scalar(+VS16) emoji, and nothing with
    the terminal-divergent sequence machinery. This is the guard that keeps a frame
    from tearing when xlii pads for a width the terminal doesn't draw."""
    from rich.cells import cell_len
    for code, g in sc.SHORTCODES.items():
        assert sc._is_width_safe(g), f"{code!r} -> {g!r} is not width-safe"
        assert cell_len(g) in (1, 2), f"{code!r} -> {g!r} has width {cell_len(g)}"
        for ch in g:                       # no served glyph carries a width-divergent codepoint
            cp = ord(ch)
            assert ch not in ("‍", "⃣"), f"{code!r} has ZWJ/keycap"
            assert not (0x1F1E6 <= cp <= 0x1F1FF), f"{code!r} has a flag pair"
            assert not (0x1F3FB <= cp <= 0x1F3FF), f"{code!r} has a skin-tone modifier"


def test_emoji_v2_present_and_exactly_two_cells():
    """The emoji half shipped (v2). Classic codes resolve, and every served emoji
    reserves exactly two cells — so what xlii pads equals what the terminal draws."""
    from rich.cells import cell_len
    for code in ("thumbsup", "smile", "rocket", "tada", "fire", "+1", "heart"):
        assert code in sc.SHORTCODES, f":{code}: should be in the v2 table"
    for code, g in sc._EMOJI.items():
        assert code in sc.SHORTCODES, f"{code!r} was dropped by the width gate"
        assert cell_len(g) == 2, f":{code}: -> {g!r} is not two cells"


def test_is_width_safe_admits_symbols_and_simple_or_vs16_emoji():
    assert sc._is_width_safe("→")          # symbol (the v1 shape, unchanged)
    assert sc._is_width_safe("╭")
    assert sc._is_width_safe("👍")          # simple single-scalar emoji
    assert sc._is_width_safe("🔥")
    assert sc._is_width_safe("❤️")     # base + VS16 (deliberate relaxation)
    assert sc._is_width_safe("⚠️")


@pytest.mark.parametrize("name,glyph", [
    ("zwj-family", "👨‍👩‍👧‍👦"),   # a non-ligature terminal draws 8 cells
    ("zwj-dev", "👩‍💻"),
    ("zwj-rainbow", "🏳️‍🌈"),
    ("flag-us", "🇺🇸"),                             # regional-indicator pair
    ("flag-jp", "🇯🇵"),
    ("keycap", "1️⃣"),                    # combining enclosing keycap
    ("skin-tone", "👍\U0001F3FD"),                  # base + tone modifier
    ("empty", ""),
])
def test_is_width_safe_rejects_terminal_divergent_sequences(name, glyph):
    """The heart of v2: ``cell_len`` collapses all of these to 2, but a
    non-conforming terminal renders them wider — so the structural pass must reject
    them regardless of what ``cell_len`` reports."""
    assert sc._is_width_safe(glyph) is False, name


def test_symbol_and_emoji_code_namespaces_are_disjoint():
    """A shared ``:code:`` space, but no code may mean two different glyphs — the
    merge in ``_load_table`` would silently pick one. Keep them disjoint."""
    dupes = set(sc._RAW) & set(sc._EMOJI)
    assert not dupes, f"codes defined in both tables: {sorted(dupes)}"


def test_emoji_discovery_reaches_classic_shortcodes():
    """`:thu` surfaces 👍 (the canonical exit-gate demo), and the GitHub `+1`/`-1`
    aliases resolve — `+`/`-` are already legal shortcode-token characters."""
    assert ("thumbsup", "👍") in sc.shortcode_matches("thu")
    assert ("+1", "👍") in sc.shortcode_matches("+1")
    assert ("-1", "👎") in sc.shortcode_matches("-1")
    assert ("fire", "🔥") in sc.shortcode_matches("fire")
    assert ("heart", "❤️") in sc.shortcode_matches("heart")


# --------------------------------------------------------------------------- #
#  shell ghost — redaction (the safety keystone), compile, serve
# --------------------------------------------------------------------------- #

def test_redaction_scrubs_a_bearer_token():
    # THE redaction exit gate: a curl with a token in history -> token absent.
    token = "sk-LIVE_secret_TOKEN_abc123XYZ456"
    cmd = f'curl -H "Authorization: Bearer {token}" https://api.example.com/v1/x'
    red = ss.redact(cmd)
    assert token not in red
    assert "<redacted>" in red
    assert not ss.contains_probable_secret(red)


@pytest.mark.parametrize("cmd,secret", [
    ("curl --api-key AIzaSyDeadBEEFdeadbeefdeadbeef12345678 https://x", "AIzaSyDeadBEEFdeadbeefdeadbeef12345678"),
    ("export GITHUB_TOKEN=ghp_0123456789abcdef0123456789abcdef0123", "ghp_0123456789abcdef0123456789abcdef0123"),
    ("psql postgres://admin:hunter2SecretPw@db.host/app", "hunter2SecretPw"),
    ("http --auth user:s3cr3tPassword99 example.com", "s3cr3tPassword99"),
    ("deploy --token=tok_9f8e7d6c5b4a3f2e1d0c9b8a", "tok_9f8e7d6c5b4a3f2e1d0c9b8a"),
])
def test_redaction_scrubs_many_secret_shapes(cmd, secret):
    assert secret not in ss.redact(cmd)


def test_compile_table_redacts_ranks_and_dedups(tmp_path):
    token = "ghp_ThisIsAFakeButRealShapedToken0123456789"
    entries = {
        "git status": {"count": 5, "seq": 10},
        "git commit -m wip": {"count": 1, "seq": 9},
        f"curl -H 'Authorization: token {token}' https://api": {"count": 2, "seq": 8},
        "ls -al": {"count": 3, "seq": 7},
    }
    table = ss.compile_table(entries)
    joined = "\n".join(table)
    assert token not in joined                       # redacted at compile
    assert table[0] == "git status"                  # frecency: most-used first
    assert len(table) == len(set(table))             # deduped


def test_compile_drops_unbounded_secret(tmp_path):
    # A high-entropy blob we can't confidently bound -> the whole command is dropped.
    entries = {"weird AbCd1234EfGh5678IjKl9012MnOp3456 arg": {"count": 9, "seq": 9}}
    assert ss.compile_table(entries) == []


def test_ghost_suggestion_prefix_match_and_graceful_empty():
    table = ["git status", "git commit -m wip", "ls -al"]
    assert ss.ghost_suggestion("git s", table) == "tatus"
    assert ss.ghost_suggestion("git status", table) is None   # equal -> nothing
    assert ss.ghost_suggestion("", table) is None
    assert ss.ghost_suggestion("git s", []) is None           # NO TABLE -> graceful nothing


def test_record_shell_command_fires_compile_on_window(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_SHELL_SUGGEST_WINDOW", "5")
    fired = [ss.record_shell_command("ls -al", config_dir=tmp_path) for _ in range(5)]
    assert fired == [False, False, False, False, True]
    assert "ls -al" in ss.load_table(config_dir=tmp_path)


def test_record_redacts_before_buffering(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_SHELL_SUGGEST_WINDOW", "1")
    token = "xoxb-secret-slack-000111222333"
    ss.record_shell_command(f"curl -H 'Authorization: Bearer {token}' https://slack", config_dir=tmp_path)
    # both the served table AND the raw history buffer are token-free
    assert token not in "\n".join(ss.load_table(config_dir=tmp_path))
    assert token not in (tmp_path / "shell_suggest" / "history.json").read_text()


def test_session_close_compiles_pending(tmp_path):
    ss.record_shell_command("make test", config_dir=tmp_path)   # below window
    assert ss.load_table(config_dir=tmp_path) == []             # not compiled yet
    assert ss.session_close_compile(config_dir=tmp_path) is True
    assert "make test" in ss.load_table(config_dir=tmp_path)


def test_model_distiller_output_is_rescrubbed(tmp_path):
    # Even if a distiller tried to re-introduce a secret, compile re-scrubs it.
    token = "ghp_evilLeak0123456789abcdef0123456789abcd"

    def leaky_distiller(cands):
        return [f"curl -H 'Authorization: Bearer {token}'", "git status"]

    table = ss.compile_table({"git status": {"count": 1, "seq": 1}}, distiller=leaky_distiller)
    assert token not in "\n".join(table)


def test_failing_distiller_falls_back_to_deterministic(tmp_path):
    def boom(cands):
        raise RuntimeError("model down")

    table = ss.compile_table({"git status": {"count": 2, "seq": 2}}, distiller=boom)
    assert table == ["git status"]     # graceful: deterministic table stands


# --------------------------------------------------------------------------- #
#  distiller enablement (opt-in) — XLII_SHELL_SUGGEST_AI + build_shell_distiller
# --------------------------------------------------------------------------- #

def test_shell_suggest_ai_enabled_is_opt_in(monkeypatch):
    """OFF unless XLII_SHELL_SUGGEST_AI is set truthy — the deterministic-by-default,
    no-surprise-spend contract the whole feature rests on."""
    monkeypatch.delenv("XLII_SHELL_SUGGEST_AI", raising=False)
    assert ss.shell_suggest_ai_enabled() is False
    for on in ("1", "true", "YES", "On"):
        monkeypatch.setenv("XLII_SHELL_SUGGEST_AI", on)
        assert ss.shell_suggest_ai_enabled() is True
    for off in ("0", "", "false", "no", "off"):
        monkeypatch.setenv("XLII_SHELL_SUGGEST_AI", off)
        assert ss.shell_suggest_ai_enabled() is False


def test_build_shell_distiller_off_by_default_short_circuits(monkeypatch):
    """Disabled → None WITHOUT touching the pool (a pool that would explode proves
    the toggle short-circuits before any client/key work)."""
    monkeypatch.delenv("XLII_SHELL_SUGGEST_AI", raising=False)
    class Boom:
        def primary(self):
            raise AssertionError("pool touched while the distiller is disabled")
    state = SimpleNamespace(pool=Boom(), cfg=SimpleNamespace(worker=lambda: "m"))
    assert ss.build_shell_distiller(state) is None


def test_build_shell_distiller_builds_when_enabled(monkeypatch):
    """Enabled + a primary key + a worker model → a distiller closure (the model is
    NOT called here; make_model_distiller only wires the seam)."""
    monkeypatch.setenv("XLII_SHELL_SUGGEST_AI", "1")
    state = SimpleNamespace(
        pool=SimpleNamespace(primary=lambda: SimpleNamespace(chat=None)),
        cfg=SimpleNamespace(worker=lambda: "grok-worker-cheap"),
    )
    assert callable(ss.build_shell_distiller(state))


def test_build_shell_distiller_graceful_no_pool_or_cfg(monkeypatch):
    monkeypatch.setenv("XLII_SHELL_SUGGEST_AI", "1")
    assert ss.build_shell_distiller(SimpleNamespace()) is None            # no .pool/.cfg at all


def test_build_shell_distiller_graceful_when_primary_raises(monkeypatch):
    """No chat key provisioned (primary() raises) → None, deterministic stands."""
    monkeypatch.setenv("XLII_SHELL_SUGGEST_AI", "1")
    class Pool:
        def primary(self):
            raise RuntimeError("only the dedicated journal key is configured")
    state = SimpleNamespace(pool=Pool(), cfg=SimpleNamespace(worker=lambda: "m"))
    assert ss.build_shell_distiller(state) is None


def test_build_shell_distiller_graceful_empty_model(monkeypatch):
    monkeypatch.setenv("XLII_SHELL_SUGGEST_AI", "1")
    state = SimpleNamespace(pool=SimpleNamespace(primary=lambda: object()),
                            cfg=SimpleNamespace(worker=lambda: ""))
    assert ss.build_shell_distiller(state) is None


def test_keystroke_serve_path_does_no_io(monkeypatch):
    """By construction: the serve functions take their table as an argument and
    only scan in memory. Sabotage file + socket I/O and prove they still work."""
    import builtins
    import socket

    # warm the lazy fuzzy_score import so the sabotage can't hit an uncached import
    sc.shortcode_matches("box")

    def _no_open(*a, **k):
        raise AssertionError("keystroke path opened a file")

    def _no_socket(*a, **k):
        raise AssertionError("keystroke path opened a socket")

    monkeypatch.setattr(builtins, "open", _no_open)
    monkeypatch.setattr(socket, "socket", _no_socket)
    monkeypatch.setattr(socket, "getaddrinfo", _no_socket)

    assert sc.shortcode_query(":box", 4) == (0, "box")
    assert sc.shortcode_matches("box")
    assert ss.ghost_suggestion("git s", ["git status"]) == "tatus"


# --------------------------------------------------------------------------- #
#  Pilot tests — the real keystroke path in a Textual app
# --------------------------------------------------------------------------- #

pytest.importorskip("textual")

from xlii.tui.app import XliiApp  # noqa: E402
from xlii.tui.input_surface import _PromptInput  # noqa: E402


def _fake_agent():
    from xlii.agent import SessionState
    return SimpleNamespace(
        console=None, rail=None, debug=None, plan_mode=False, active_mode=None,
        howto_mode=False, history=[], model_override=None, session=SessionState(),
    )


def _state(tmp_path, **extra):
    st = SimpleNamespace(
        shell_cwd=tmp_path,
        project=SimpleNamespace(project_root=tmp_path, name="proj"),
        agent=_fake_agent(),
    )
    for k, v in extra.items():
        setattr(st, k, v)
    return st


def _app(tmp_path, **extra):
    return XliiApp(project_name="proj", agent=None,
                   run_turn=lambda q: ("", set(), None), state=_state(tmp_path, **extra))


def _run(coro_fn):
    asyncio.run(coro_fn())


def test_pilot_shortcode_popup_tab_inserts_glyph(tmp_path):
    async def body():
        app = _app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            inp = app.query_one("#input", _PromptInput)
            inp.focus()
            await pilot.press(":", "b", "o", "x")
            await pilot.pause()
            assert inp._sc_open is True
            assert app.query_one("#shortcode-popup").display is True
            await pilot.press("tab")
            await pilot.pause()
            assert inp.text in ("╭", "╮", "╰", "╯")   # a box glyph, inserted
            assert inp._sc_open is False              # popup dismissed
    _run(body)


def test_pilot_emoji_shortcode_tab_inserts_emoji(tmp_path):
    """v2: the same keystroke path inserts an emoji. `:fire` -> 🔥 (a two-cell
    single-scalar glyph), proving emoji flow through the popup like any symbol."""
    async def body():
        app = _app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            inp = app.query_one("#input", _PromptInput)
            inp.focus()
            await pilot.press(":", "f", "i", "r", "e")
            await pilot.pause()
            assert inp._sc_open is True
            await pilot.press("tab")
            await pilot.pause()
            assert inp.text == "🔥"                    # the emoji, replacing :fire
            assert inp._sc_open is False
    _run(body)


def test_pilot_ordinary_colon_never_triggers_popup(tmp_path):
    async def body():
        app = _app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            inp = app.query_one("#input", _PromptInput)
            inp.focus()
            await pilot.press("1", "2", ":", "3", "0")
            await pilot.pause()
            assert inp.text == "12:30"
            assert inp._sc_open is False
    _run(body)


def test_pilot_ghost_text_shell_context_right_arrow_accepts(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_CONFIG_DIR", str(tmp_path / "cfg"))
    ss.store_table(["git status", "git commit -m wip"])   # -> cfg/shell_suggest/table.json

    async def body():
        app = _app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            inp = app.query_one("#input", _PromptInput)
            inp.focus()
            await pilot.press("g", "i", "t", " ", "s")
            await pilot.pause()
            assert inp._ghost_suffix == "tatus"                  # ghost computed
            assert "git status" in inp.render_line(0).text       # rendered inline
            await pilot.press("right")
            await pilot.pause()
            assert inp.text == "git status"                      # right-arrow inserted
            assert inp._ghost_suffix == ""
            # insert-never-execute: nothing ran (Enter is still required)
            from xlii.tui.transcript import TranscriptLog
            assert "$ git status" not in app.query_one("#log", TranscriptLog).plain_text()
    _run(body)


def test_pilot_no_table_means_no_ghost(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_CONFIG_DIR", str(tmp_path / "empty-cfg"))

    async def body():
        app = _app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            inp = app.query_one("#input", _PromptInput)
            inp.focus()
            assert inp._shell_table == []
            await pilot.press("g", "i", "t", " ", "s")
            await pilot.pause()
            assert inp._ghost_suffix == ""      # gracefully nothing
    _run(body)


def test_pilot_ghost_only_in_shell_context(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_CONFIG_DIR", str(tmp_path / "cfg"))
    ss.store_table(["git status"])

    async def body():
        # a persona makes bare input talk-primary (not shell) -> no ghost text
        app = _app(tmp_path, persona=SimpleNamespace(name="ada"))
        async with app.run_test() as pilot:
            await pilot.pause()
            inp = app.query_one("#input", _PromptInput)
            inp.focus()
            assert inp._app_is_shell() is False
            await pilot.press("g", "i", "t", " ", "s")
            await pilot.pause()
            assert inp._ghost_suffix == ""
    _run(body)
