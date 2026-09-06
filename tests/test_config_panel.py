"""`/config` — the session-knobs panel (per-role models · budget · no-sync · theme).

Command routing rides the published panel-host seam (fake host); the panel's
row/pick/budget/toggle logic is pure against `state` so it tests headlessly;
pilot tests dock the real widget and drive the v2 picker + budget modals.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from xlii import daemon_toml
from xlii.repl_cmds import config_panel
from xlii.tui import panels


class _Console:
    def __init__(self):
        self.lines = []

    def print(self, *a, **k):
        self.lines.append(" ".join(str(x) for x in a))

    @property
    def text(self):
        return "\n".join(self.lines)


class _FakeHost(panels.PanelHost):
    def __init__(self, *, side="right"):
        self.calls = []
        self._side = side

    def show_panel(self, side, view, *, state):
        self.calls.append(("show", side, view))
        return True

    def hide_panel(self):
        return True

    def is_open(self):
        return False

    def current_side(self):
        return self._side


def _ctx(state="s"):
    return {"console": _Console(), "state": state}


# --- command routing ---------------------------------------------------------


def test_config_registered_and_view_known():
    from xlii.commands import find_repl_command
    from xlii.repl_cmds import register_all

    register_all()
    for repl in ("code", "chat"):
        assert find_repl_command("/config", repl) is not None
    assert "config" in panels.panel_views()


def test_config_routes_to_panel_host():
    host = _FakeHost(side="left")
    prev = panels.set_panel_host(host)
    try:
        ctx = _ctx()
        assert config_panel._cmd_config("/config", ctx) is True
        assert ("show", "left", "config") in host.calls
        assert "docked" in ctx["console"].text
    finally:
        panels.set_panel_host(prev)


def test_config_without_host_nudges_tui():
    prev = panels.set_panel_host(None)
    try:
        ctx = _ctx()
        assert config_panel._cmd_config("/config", ctx) is True
        out = ctx["console"].text
        assert "--tui" in out
        assert "xlii models set" in out      # the headless alternative is named
    finally:
        panels.set_panel_host(prev)


# --- panel row/cycle logic (headless — pure against state.cfg) ---------------


def _fake_cfg():
    cfg = SimpleNamespace(
        orchestrator_model="m-build",
        worker_model="m-build",
        chat_model="m-chat",
        help_model="m-build",
        orchestrator_temperature=0.7,
        worker_temperature=0.3,
        chat_temperature=0.7,
        max_tool_iterations=20,
        max_worker_iterations=10,
        max_parallel_workers=8,
        claim_gates="warn",
        import_foreign_skills=True,
        editor="",
        tui_hotkey_modifier="alt",
        tui_panel_side="right",
        pricing={"m-build": {"input": 1, "output": 2},
                 "m-chat": {"input": 3, "output": 4}},
        saves=0,
    )
    cfg.get_model_for_role = lambda role: {
        "orchestrator": cfg.orchestrator_model,
        "worker": cfg.worker_model,
        "chat": cfg.chat_model,
        "help": cfg.help_model,
    }[role]

    def _save():
        cfg.saves += 1

    cfg.save = _save
    return cfg


def _panel_for(cfg, **state_kw):
    pytest.importorskip("textual")
    session = state_kw.pop(
        "session",
        SimpleNamespace(budget_usd=None, session_cost=0.0, budget_env_cleared=False),
    )
    state = SimpleNamespace(
        cfg=cfg,
        agent=SimpleNamespace(session=session),
        no_sync=False,
        scratch=False,
        **state_kw,
    )
    view = panels.get_panel_view("config")
    return view(state, actions=panels.PanelActions(state))


def test_rows_show_resolved_models_and_price_hints():
    p = _panel_for(_fake_cfg())
    rows = dict(p._rows())
    assert "m-build" in rows["role:orchestrator"]
    assert "$1/2 per M" in rows["role:orchestrator"]
    assert "m-chat" in rows["role:chat"]
    assert "no cap" in rows["budget"]
    assert "off" in rows["nosync"]
    assert "theme" in rows["theme"]
    assert "canvas" in rows["canvas"]
    assert "dark" in rows["canvas"]


def test_rows_have_sections_and_v21_knobs():
    p = _panel_for(_fake_cfg())
    rows = dict(p._rows())
    # section headers (disabled in the widget) frame the four groups
    for hdr in ("hdr:models", "hdr:session", "hdr:app", "hdr:identity"):
        assert hdr in rows
    assert "profile" in rows
    assert "0.70" in rows["temp:orchestrator"]
    assert "0.30" in rows["temp:worker"]
    assert "tool iter" in rows["iterations"]
    assert "20" in rows["iterations"]
    assert " · saved" in rows["iterations"]
    assert "worker iter" in rows["workeriter"]
    assert "10" in rows["workeriter"]
    assert "keep-session" in rows["keepsession"]
    assert "claim gates" in rows["claimgates"]
    assert "warn" in rows["claimgates"]
    assert "retrieval" in rows["retrieval"]
    assert "hybrid" in rows["retrieval"]
    assert "swarm" in rows["swarm"]
    assert " · session" in rows["budget"]
    assert " · session" in rows["nosync"]
    assert "alt" in rows["hotkey"]
    assert "right" in rows["panelside"]
    assert "panewidth" in rows
    assert "on" in rows["skills"]
    assert "image ed" in rows["imageed"]
    assert "browser" in rows["browser"]
    assert "terminal" in rows["terminal"]
    assert "this project" in rows["termcwd"]
    assert "chat iter" in rows["chatiter"]
    assert "persona" in rows["persona"]
    assert "xmpp" in rows["xmpp"]


def test_swarm_badge_saved_when_value_matches_disk(monkeypatch):
    # Round-2 sweep: the swarm row hardcoded " · session" even after persisting to
    # config.json — a lie about scope. It now reflects the real scope.
    from xlii.config import GlobalConfig

    cfg = _fake_cfg()  # live max_parallel_workers = 8
    monkeypatch.setattr(
        GlobalConfig, "load", staticmethod(lambda: SimpleNamespace(max_parallel_workers=8))
    )
    row = dict(_panel_for(cfg)._rows())["swarm"]
    assert " · saved" in row
    assert " · session" not in row


def test_swarm_badge_session_when_unsaved_override(monkeypatch):
    from xlii.config import GlobalConfig

    cfg = _fake_cfg()  # live = 8
    monkeypatch.setattr(
        GlobalConfig, "load", staticmethod(lambda: SimpleNamespace(max_parallel_workers=4))
    )  # disk = 4 → live diverges → session-scoped override
    row = dict(_panel_for(cfg)._rows())["swarm"]
    assert " · session" in row
    assert " · saved" not in row


def test_budget_row_shows_cap_and_spend():
    session = SimpleNamespace(budget_usd=5.0, session_cost=1.25, budget_env_cleared=False)
    p = _panel_for(_fake_cfg(), session=session)
    rows = dict(p._rows())
    assert "$5.00 cap" in rows["budget"]
    assert "$1.25 spent" in rows["budget"]


def test_cycle_role_advances_and_persists():
    cfg = _fake_cfg()
    p = _panel_for(cfg)
    ring = p._candidates()
    assert ring == ["m-build", "m-chat"]
    p._cycle_role("help")                     # m-build → m-chat
    assert cfg.help_model == "m-chat"
    assert cfg.saves == 1
    p._cycle_role("help")                     # wraps back
    assert cfg.help_model == "m-build"
    assert cfg.saves == 2
    # orchestrator untouched by cycling help
    assert cfg.orchestrator_model == "m-build"


def test_set_role_persists_named_model():
    cfg = _fake_cfg()
    p = _panel_for(cfg)
    p._set_role("chat", "m-build")            # what the picker calls on choose
    assert cfg.chat_model == "m-build"
    assert cfg.saves == 1
    p._set_role("chat", "")                   # empty pick is a no-op
    assert cfg.chat_model == "m-build"
    assert cfg.saves == 1


# --- budget editing (v2 — pure parse/apply against the session) ---------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("5", (True, 5.0)),
        ("$2.50", (True, 2.5)),
        ("", (True, None)),
        ("clear", (True, None)),
        ("  OFF ", (True, None)),
        ("0", (False, None)),
        ("-3", (False, None)),
        ("cheap", (False, None)),
    ],
)
def test_parse_budget(raw, expected):
    pytest.importorskip("textual")
    assert panels.ConfigPanel._parse_budget(raw) == expected


def test_apply_budget_sets_and_clears_session_fields():
    session = SimpleNamespace(budget_usd=None, session_cost=0.0, budget_env_cleared=False)
    p = _panel_for(_fake_cfg(), session=session)
    assert p._apply_budget("5") is True       # same fields /budget writes
    assert session.budget_usd == 5.0
    assert session.budget_env_cleared is False
    assert p._apply_budget("") is True        # empty clears (env won't reseed)
    assert session.budget_usd is None
    assert session.budget_env_cleared is True
    assert p._apply_budget("nope") is False   # invalid leaves state alone
    assert session.budget_usd is None


# --- no-sync toggle (v2 — session-only, scratch locks it on) ------------------


def test_toggle_no_sync_flips_session_flag():
    p = _panel_for(_fake_cfg())
    state = p._state
    assert p._toggle_no_sync() is True
    assert state.no_sync is True
    assert "ON" in dict(p._rows())["nosync"]
    assert p._toggle_no_sync() is True
    assert state.no_sync is False


def test_toggle_no_sync_refused_in_scratch():
    p = _panel_for(_fake_cfg())
    p._state.scratch = True
    p._state.no_sync = True                   # scratch forces it on
    assert p._toggle_no_sync() is False
    assert p._state.no_sync is True
    assert "scratch-locked" in dict(p._rows())["nosync"]


# --- v2.1 knobs (pure apply/toggle logic — persisted via cfg.save) ------------


def test_apply_iterations_persists_within_bounds():
    cfg = _fake_cfg()
    p = _panel_for(cfg)
    assert p._apply_iterations("50") is True
    assert cfg.max_tool_iterations == 50
    assert cfg.saves == 1
    for bad in ("0", "101", "many", ""):
        assert p._apply_iterations(bad) is False
    assert cfg.max_tool_iterations == 50      # invalid input never lands
    assert cfg.saves == 1


def test_apply_worker_iterations_persists_within_bounds():
    cfg = _fake_cfg()
    p = _panel_for(cfg)
    assert p._apply_worker_iterations("25") is True
    assert cfg.max_worker_iterations == 25
    assert cfg.saves == 1
    for bad in ("0", "101", "many", ""):
        assert p._apply_worker_iterations(bad) is False
    assert cfg.max_worker_iterations == 25
    assert cfg.saves == 1


def test_cycle_claim_gates_warn_strict_off():
    cfg = _fake_cfg()
    cfg.claim_gates = "warn"
    p = _panel_for(cfg)
    assert p._cycle_claim_gates() is True
    assert cfg.claim_gates == "strict"
    assert cfg.saves == 1
    assert p._cycle_claim_gates() is True
    assert cfg.claim_gates == "off"
    assert p._cycle_claim_gates() is True
    assert cfg.claim_gates == "warn"


def test_cycle_retrieval_mode_hybrid_semantic_keyword():
    cfg = _fake_cfg()
    cfg.retrieval_mode = "hybrid"
    p = _panel_for(cfg)
    assert p._cycle_retrieval_mode() is True
    assert cfg.retrieval_mode == "semantic"
    assert cfg.saves == 1
    assert p._cycle_retrieval_mode() is True
    assert cfg.retrieval_mode == "keyword"
    assert p._cycle_retrieval_mode() is True
    assert cfg.retrieval_mode == "hybrid"


def test_toggle_keep_session_writes_episode_flag(tmp_path):
    import xlii.episode as ep

    cfg = _fake_cfg()
    xli = tmp_path / ".xlii"
    xli.mkdir()
    project = SimpleNamespace(xli_dir=xli, saves=0)
    p = _panel_for(cfg, project=project)
    assert ep.read_keep_session(xli) is False
    assert p._toggle_keep_session() is True
    assert ep.read_keep_session(xli) is True
    assert p._toggle_keep_session() is True
    assert ep.read_keep_session(xli) is False


def test_apply_swarm_session_only_without_save_keyword():
    cfg = _fake_cfg()
    p = _panel_for(cfg)
    assert p._apply_swarm("4") is True
    assert cfg.max_parallel_workers == 4
    assert cfg.saves == 0          # session-only until user appends ' save'
    assert p._apply_swarm("6 save") is True
    assert cfg.max_parallel_workers == 6
    assert cfg.saves == 1


def test_apply_temp_persists_within_bounds():
    cfg = _fake_cfg()
    p = _panel_for(cfg)
    assert p._apply_temp("orchestrator", "0.2") is True
    assert cfg.orchestrator_temperature == 0.2
    assert cfg.saves == 1
    assert p._apply_temp("orchestrator", "0") is True      # deliberate 0.0 allowed
    assert cfg.orchestrator_temperature == 0.0
    for bad in ("2.5", "-1", "warm"):
        assert p._apply_temp("chat", bad) is False
    assert cfg.chat_temperature == 0.7
    assert p._apply_temp("help", "0.5") is False           # help has no temp knob


def test_cycle_panel_side_flips_and_persists():
    cfg = _fake_cfg()
    cfg.tui_panel_side = "right"
    p = _panel_for(cfg)
    app = SimpleNamespace(_panel_side="right", saves=0)

    def _set(side, *, persist=True):
        app._panel_side = side
        if persist:
            cfg.tui_panel_side = side
            cfg.save()
        return side

    app.set_panel_side = _set
    p._actions = SimpleNamespace(notify=lambda *a, **k: None, app=app)

    assert p._cycle_panel_side() is True
    assert cfg.tui_panel_side == "left"
    assert app._panel_side == "left"
    assert cfg.saves == 1
    assert "left" in dict(p._rows())["panelside"]


def test_toggle_skills_import_flips_and_persists():
    cfg = _fake_cfg()
    p = _panel_for(cfg)
    assert p._toggle_skills_import() is True
    assert cfg.import_foreign_skills is False
    assert cfg.saves == 1
    assert "OFF" in dict(p._rows())["skills"]
    assert p._toggle_skills_import() is True
    assert cfg.import_foreign_skills is True


# --- identity rows (display-only — no seam to edit yet) -----------------------


def test_persona_row_default_and_project_bound():
    p = _panel_for(_fake_cfg())
    assert "(mojo)" in p._persona_label()
    p._state.project = SimpleNamespace(bound_persona="karl")
    assert p._persona_label() == "karl (project-bound)"


def test_xmpp_row_reads_daemon_toml(tmp_path, monkeypatch):
    import xlii.daemon_gate as daemon_gate

    p = _panel_for(_fake_cfg())
    monkeypatch.setattr(daemon_gate, "DEFAULT_CONFIG_PATH", tmp_path / "daemon.toml")
    assert p._xmpp_label() == "not configured"

    (tmp_path / "daemon.toml").write_text(
        '[daemon]\njid = "bot@example.org"\n'
        '[whitelist]\nallowed_jids = ["me@example.org", "alt@example.org"]\n'
    )
    assert p._xmpp_label() == "bot@example.org · 2 allowed"


# --- v3: persona binding (picker → ProjectConfig.save) ------------------------


class _FakeProject:
    def __init__(self, bound=None):
        self.bound_persona = bound
        self.saves = 0

    def save(self):
        self.saves += 1


def test_bind_persona_writes_project_config():
    p = _panel_for(_fake_cfg())
    project = _FakeProject()
    p._state.project = project
    assert p._bind_persona("karl") is True
    assert project.bound_persona == "karl"
    assert project.saves == 1
    assert "karl (project-bound)" in dict(p._rows())["persona"]
    assert p._bind_persona("(unbind)") is True     # the picker's unbind option
    assert project.bound_persona is None
    assert project.saves == 2


def test_bind_persona_refused_without_project():
    p = _panel_for(_fake_cfg())
    p._state.project = None
    assert p._bind_persona("karl") is False


# --- v3: daemon JID editing (targeted rewrite, validated, atomic) -------------


def test_rewrite_daemon_jid_preserves_rest_of_file(tmp_path):
    pytest.importorskip("textual")
    toml = tmp_path / "daemon.toml"
    toml.write_text(
        "# my daemon\n[daemon]\njid = \"old@example.org\"\npassword_env = \"SECRET_ENV\"\n"
        "[whitelist]\nallowed_jids = [\"me@example.org\"]\n"
    )
    ok, msg = daemon_toml.rewrite_daemon_jid(toml, "new@example.org")
    assert ok and "new@example.org" in msg
    text = toml.read_text()
    assert 'jid = "new@example.org"' in text
    assert "# my daemon" in text                   # comments survive
    assert 'password_env = "SECRET_ENV"' in text   # untouched
    assert 'allowed_jids = ["me@example.org"]' in text


def test_rewrite_daemon_jid_inserts_when_missing(tmp_path):
    pytest.importorskip("textual")
    toml = tmp_path / "daemon.toml"
    toml.write_text("[daemon]\npassword_env = \"SECRET_ENV\"\n")
    ok, _msg = daemon_toml.rewrite_daemon_jid(toml, "bot@example.org")
    assert ok
    assert 'jid = "bot@example.org"' in toml.read_text()


def test_rewrite_daemon_jid_missing_file(tmp_path):
    pytest.importorskip("textual")
    ok, msg = daemon_toml.rewrite_daemon_jid(tmp_path / "nope.toml", "a@b.c")
    assert ok is False
    assert "daemon.toml.example" in msg


# --- v4: whitelist add/remove (targeted rewrite, never password) -------------


def test_rewrite_allowed_jids_preserves_rest_of_file(tmp_path):
    pytest.importorskip("textual")
    toml = tmp_path / "daemon.toml"
    toml.write_text(
        "# my daemon\n[daemon]\njid = \"bot@example.org\"\npassword_env = \"SECRET_ENV\"\n"
        "[whitelist]\nallowed_jids = [\"me@example.org\"]\n"
    )
    ok, msg = daemon_toml.rewrite_allowed_jids(
        toml, ["me@example.org", "you@example.org"]
    )
    assert ok and "2 JID" in msg
    text = toml.read_text()
    assert 'jid = "bot@example.org"' in text
    assert 'password_env = "SECRET_ENV"' in text
    assert "# my daemon" in text
    assert 'allowed_jids = ["me@example.org", "you@example.org"]' in text


# The exact daemon.toml shape documented in docs/HOWTO.md §14: the
# ``allowed_jids`` line carries a trailing comment and a ``[rate_limit]``
# section follows. A DOTALL over-match would delete both.
_HOWTO_DAEMON_TOML = (
    "[daemon]\n"
    'jid = "daemon@desktop.tailnet"        # the daemon\'s own account\n'
    'password_env = "XMPP_DAEMON_PASSWORD" # env var holding its password\n'
    "progress_after_s = 0                  # >0: ONE \"still working…\" message\n"
    "\n"
    "[whitelist]\n"
    'allowed_jids = ["you@desktop.tailnet"]  # only these JIDs are answered; all else\n'
    "                                        # is silently dropped (empty ⇒ refuse to start)\n"
    "\n"
    "[rate_limit]                  # defaults shown; per-JID sliding window + lockout\n"
    "max_per_minute = 10\n"
    "lockout_threshold = 5\n"
    "lockout_duration_s = 300\n"
    "\n"
    "[agent_fallback]\n"
    "enabled = true\n"
    "\n"
    "[trust]\n"
    "blind_trust = false\n"
)


def test_rewrite_allowed_jids_byte_identical_with_trailing_comment(tmp_path):
    """The docs/HOWTO.md shape has a trailing comment on the allowed_jids line
    AND a following [rate_limit] section. Adding a JID must rewrite ONLY the
    array on that one line — every other byte identical, [rate_limit] intact.

    (Regression for the DOTALL non-greedy `[.*?]` over-match that expanded to
    the next `]` at end-of-line — deleting the comment and whole sections.)"""
    pytest.importorskip("textual")
    toml = tmp_path / "daemon.toml"
    toml.write_text(_HOWTO_DAEMON_TOML)

    ok, msg = daemon_toml.whitelist_add_jid(toml, "friend@desktop.tailnet")
    assert ok and "2 JID" in msg

    before = _HOWTO_DAEMON_TOML.splitlines()
    after = toml.read_text().splitlines()
    assert len(before) == len(after)          # no line added or deleted
    for b, a in zip(before, after):
        if b.lstrip().startswith("allowed_jids"):
            # only the array changed; the `allowed_jids = ` prefix and the
            # trailing comment are preserved verbatim
            assert a == (
                'allowed_jids = ["you@desktop.tailnet", "friend@desktop.tailnet"]'
                "  # only these JIDs are answered; all else"
            )
        else:
            assert a == b                     # byte-identical elsewhere

    text = toml.read_text()
    assert "[rate_limit]" in text             # the swallowed section survives
    assert "max_per_minute = 10" in text
    assert "lockout_threshold = 5" in text
    assert "[agent_fallback]" in text
    assert "# is silently dropped (empty ⇒ refuse to start)" in text


def test_parse_allowed_jids_ignores_trailing_comment_quotes():
    """A quoted token in the line's trailing comment (or a `]` further down the
    file) must not leak into the parsed whitelist — the array is single-line."""
    pytest.importorskip("textual")
    text = (
        "[whitelist]\n"
        'allowed_jids = ["a@x"]  # only ever trust a real "friend"\n'
        "[rate_limit]\nmax_per_minute = 10\n"
    )
    assert daemon_toml.parse_allowed_jids_from_text(text) == ["a@x"]


def test_whitelist_add_and_remove(tmp_path):
    pytest.importorskip("textual")
    toml = tmp_path / "daemon.toml"
    toml.write_text(
        "[daemon]\njid = \"bot@example.org\"\npassword_env = \"SECRET_ENV\"\n"
        "[whitelist]\nallowed_jids = [\"me@example.org\"]\n"
    )
    ok, _ = daemon_toml.whitelist_add_jid(toml, "you@example.org")
    assert ok
    text = toml.read_text()
    assert "you@example.org" in text and 'password_env = "SECRET_ENV"' in text
    ok, msg = daemon_toml.whitelist_add_jid(toml, "you@example.org")
    assert not ok and "already" in msg
    ok, _ = daemon_toml.whitelist_remove_jid(toml, "me@example.org")
    assert ok
    assert "me@example.org" not in toml.read_text()
    assert "you@example.org" in toml.read_text()


def test_whitelist_add_rejects_invalid_jid(tmp_path):
    pytest.importorskip("textual")
    toml = tmp_path / "daemon.toml"
    toml.write_text("[daemon]\njid = \"bot@example.org\"\n")
    ok, msg = daemon_toml.whitelist_add_jid(toml, "not-a-jid")
    assert not ok and "invalid" in msg.lower()


def test_whitelist_inserts_section_when_missing(tmp_path):
    pytest.importorskip("textual")
    toml = tmp_path / "daemon.toml"
    toml.write_text("[daemon]\njid = \"bot@example.org\"\npassword_env = \"SECRET_ENV\"\n")
    ok, _ = daemon_toml.whitelist_add_jid(toml, "me@example.org")
    assert ok
    text = toml.read_text()
    assert "[whitelist]" in text
    assert 'allowed_jids = ["me@example.org"]' in text
    assert 'password_env = "SECRET_ENV"' in text


def test_create_persona_from_picker_binds(tmp_path, monkeypatch):
    """Drive the REAL ``_create_persona_from_picker`` apply closure (the path
    the picker's ``(create…)`` runs), not ``create_persona`` directly: an
    invalid name is rejected with a notify, a valid name creates + binds, and a
    duplicate is rejected — nothing binds on either rejection."""
    pytest.importorskip("textual")
    import xlii.persona as persona_mod

    monkeypatch.setattr(persona_mod, "PERSONAS_DIR", tmp_path / "personas")
    (tmp_path / "personas").mkdir()
    p = _panel_for(_fake_cfg())
    project = SimpleNamespace(bound_persona=None, saves=0)
    project.save = lambda: setattr(project, "saves", project.saves + 1)
    p._state.project = project
    notes = []
    p._actions = SimpleNamespace(
        notify=lambda m, severity="information": notes.append((severity, m)),
        app=None,
    )

    # Capture the apply closure _create_persona_from_picker hands to _prompt,
    # so we exercise the real create+bind logic without a live modal host.
    captured = {}
    monkeypatch.setattr(
        type(p), "_prompt",
        lambda self, prompt, apply: captured.__setitem__("apply", apply),
    )
    p._create_persona_from_picker()
    apply = captured["apply"]

    # is_valid_name rejection — no file, no bind, a warning is surfaced.
    assert apply("bad name!") is False
    assert list((tmp_path / "personas").glob("*.md")) == []
    assert project.bound_persona is None
    assert project.saves == 0
    assert any("invalid persona name" in m and s == "warning" for s, m in notes)

    # Valid name — creates the file through the create_persona seam and binds.
    notes.clear()
    assert apply("nova") is True
    assert (tmp_path / "personas" / "nova.md").exists()
    assert project.bound_persona == "nova"
    assert project.saves == 1
    assert any("created persona nova" in m for _s, m in notes)

    # Duplicate rejection — Persona(name).exists() is true, so no re-create and
    # the existing binding is left as-is.
    project.bound_persona = None
    notes.clear()
    assert apply("nova") is False
    assert project.bound_persona is None
    assert any("already exists" in m and s == "warning" for s, m in notes)


# --- v3: privacy row (on-demand account check — report, never a toggle) -------


def test_privacy_label_without_key():
    p = _panel_for(_fake_cfg())               # fake cfg has no management_api_key
    assert "XAI_MANAGEMENT_API_KEY" in p._fetch_privacy_label()


def test_privacy_label_reports_team_facts(monkeypatch):
    import xlii.xai_mgmt as mgmt

    cfg = _fake_cfg()
    cfg.management_api_key = "k"
    cfg.team_id = "t1"
    p = _panel_for(cfg)
    monkeypatch.setattr(mgmt, "resolve_active_team", lambda key, pref=None: "t1")
    monkeypatch.setattr(
        mgmt, "team_status",
        lambda key, team_id: {"tierId": "tier-2", "isSelfServeZdrEligible": True},
    )
    label = p._fetch_privacy_label()
    assert "tier-2" in label
    assert "ZDR-eligible" in label
    assert "console-managed" in label          # no sharing flag on the team object

    monkeypatch.setattr(
        mgmt, "team_status",
        lambda key, team_id: {"tierId": "tier-2", "dataSharingEnabled": True},
    )
    assert "data-sharing ON" in p._fetch_privacy_label()


def test_check_privacy_headless_fills_row(monkeypatch):
    p = _panel_for(_fake_cfg())
    monkeypatch.setattr(p, "_fetch_privacy_label", lambda: "tier-2 · no ZDR · data-sharing: console-managed")
    p._check_privacy()                         # no app → runs inline
    assert p._privacy == "tier-2 · no ZDR · data-sharing: console-managed"
    assert "tier-2" in dict(p._rows())["privacy"]


# --- the real widget in the real app -----------------------------------------


def _fake_agent():
    from xlii.agent import SessionState
    return SimpleNamespace(
        console=None, rail=None, debug=None, plan_mode=False,
        active_mode=None, howto_mode=False, history=[],
        model_override=None, session=SessionState(),
    )


def _row_index(ol, rid):
    return next(
        i for i in range(ol.option_count) if ol.get_option_at_index(i).id == rid
    )


def _app_with_config_panel(tmp_path, cfg):
    from xlii.tui_textual import XliiApp

    st = SimpleNamespace(
        shell_cwd=tmp_path,
        project=SimpleNamespace(project_root=tmp_path, name="proj"),
        agent=_fake_agent(),
        cfg=cfg,
        no_sync=False,
        scratch=False,
    )
    app = XliiApp(project_name="proj", agent=st.agent,
                  run_turn=lambda q: ("", set(), None), state=st)
    return app, st


def test_config_panel_docks_and_picker_sets_role(tmp_path):
    pytest.importorskip("textual")

    async def body():
        from textual.widgets import OptionList

        cfg = _fake_cfg()
        app, _st = _app_with_config_panel(tmp_path, cfg)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            assert app._show_panel_view("right", "config") is True
            await pilot.pause()

            panel = app.query_one(panels.ConfigPanel)
            assert panel is not None
            ol = app.query_one("#panel-config-list", OptionList)
            assert ol.option_count == len(panel._rows())

            # Select the help row — opens the picker on the current model.
            ol.highlighted = _row_index(ol, "role:help")
            await pilot.pause()
            ol.focus()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, panels.ModelPickerModal)
            picker = app.screen.query_one("#picker-list", OptionList)
            assert picker.highlighted == 0        # ● m-build (current)

            # Choose m-chat — persists through cfg.save, modal closes.
            await pilot.press("down", "enter")
            await pilot.pause()
            assert cfg.help_model == "m-chat"
            assert cfg.saves == 1
            assert not isinstance(app.screen, panels.ModelPickerModal)
    asyncio.run(body())


def test_config_panel_budget_row_edits_in_place(tmp_path):
    pytest.importorskip("textual")

    async def body():
        from textual.widgets import OptionList

        cfg = _fake_cfg()
        app, st = _app_with_config_panel(tmp_path, cfg)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            assert app._show_panel_view("right", "config") is True
            await pilot.pause()

            ol = app.query_one("#panel-config-list", OptionList)
            ol.highlighted = _row_index(ol, "budget")
            await pilot.pause()
            ol.focus()
            await pilot.press("enter")
            await pilot.pause()

            from xlii.tui.app import PromptModal
            assert isinstance(app.screen, PromptModal)
            # The PromptModal's input has focus — type a cap and submit.
            await pilot.press("7", "enter")
            await pilot.pause()
            assert st.agent.session.budget_usd == 7.0
            row = dict(app.query_one(panels.ConfigPanel)._rows())["budget"]
            assert "$7.00 cap" in row
    asyncio.run(body())


def test_config_panel_nosync_row_toggles(tmp_path):
    pytest.importorskip("textual")

    async def body():
        from textual.widgets import OptionList

        cfg = _fake_cfg()
        app, st = _app_with_config_panel(tmp_path, cfg)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            assert app._show_panel_view("right", "config") is True
            await pilot.pause()

            ol = app.query_one("#panel-config-list", OptionList)
            ol.highlighted = _row_index(ol, "nosync")
            await pilot.pause()
            ol.focus()
            await pilot.press("enter")
            await pilot.pause()
            assert st.no_sync is True
            await pilot.press("enter")
            await pilot.pause()
            assert st.no_sync is False
    asyncio.run(body())


def test_config_panel_persona_row_opens_picker_and_binds(tmp_path, monkeypatch):
    pytest.importorskip("textual")
    import xlii.persona as persona_mod

    async def body():
        from textual.widgets import OptionList

        cfg = _fake_cfg()
        app, st = _app_with_config_panel(tmp_path, cfg)
        project = _FakeProject()
        st.project.bound_persona = None            # make the fake project bindable
        st.project.save = project.save
        monkeypatch.setattr(
            persona_mod, "list_personas",
            lambda: [SimpleNamespace(name="ixaac"), SimpleNamespace(name="karl")],
        )
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            assert app._show_panel_view("right", "config") is True
            await pilot.pause()

            ol = app.query_one("#panel-config-list", OptionList)
            ol.highlighted = _row_index(ol, "persona")
            await pilot.pause()
            ol.focus()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, panels.ModelPickerModal)
            picker = app.screen.query_one("#picker-list", OptionList)
            assert picker.option_count == 4        # ixaac · karl · (create…) · (unbind)

            # current is (unbind) → highlighted starts there; pick "karl".
            picker.highlighted = 1
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert st.project.bound_persona == "karl"
            assert project.saves == 1
    asyncio.run(body())


def test_config_panel_hotkey_row_rebinds_live(tmp_path):
    pytest.importorskip("textual")

    async def body():
        from textual.widgets import OptionList

        cfg = _fake_cfg()
        app, _st = _app_with_config_panel(tmp_path, cfg)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            assert app._show_panel_view("right", "config") is True
            await pilot.pause()

            ol = app.query_one("#panel-config-list", OptionList)
            ol.highlighted = _row_index(ol, "hotkey")
            await pilot.pause()
            ol.focus()
            await pilot.press("enter")
            await pilot.pause()

            # PromptModal — type a new modifier; App.set_hotkey_modifier
            # rebinds the doorway keys live AND persists to cfg.
            for ch in "ctrl+alt":
                await pilot.press(ch if ch != "+" else "plus")
            await pilot.press("enter")
            await pilot.pause()
            assert cfg.tui_hotkey_modifier == "ctrl+alt"
            assert cfg.saves == 1
            assert any(k.startswith(("ctrl+alt+", "alt+ctrl+")) for k in app._doorway_keys)
    asyncio.run(body())


# --- api region row (regional xAI edges) -------------------------------------


def test_region_row_shows_global_edge_by_default(monkeypatch):
    monkeypatch.delenv("XAI_REGION", raising=False)
    p = _panel_for(_fake_cfg())
    rows = dict(p._rows())
    assert "api.x.ai" in rows["region"]
    assert "saved" in rows["region"]


def test_region_row_shows_env_override(monkeypatch):
    monkeypatch.setenv("XAI_REGION", "us-west-2")
    cfg = _fake_cfg()
    cfg.region = "eu-west-1"  # env must beat the saved field, and say so
    p = _panel_for(cfg)
    rows = dict(p._rows())
    assert "us-west-2.api.x.ai" in rows["region"]
    assert "env XAI_REGION" in rows["region"]


def test_apply_region_persists_and_rebuilds_pool_live(monkeypatch):
    monkeypatch.delenv("XAI_REGION", raising=False)
    cfg = _fake_cfg()
    cfg.region = None
    rebuilt = []
    pool = SimpleNamespace(rebuild_from_config=lambda c: rebuilt.append(c))
    p = _panel_for(cfg, pool=pool)
    notes = []
    p._actions.notify = lambda msg, **k: notes.append(msg)

    assert p._apply_region("us-west-2") is True
    assert cfg.region == "us-west-2"
    assert cfg.saves == 1              # persisted through the same save seam
    assert rebuilt == [cfg]            # pool hot-swapped, not next-session
    assert "us-west-2.api.x.ai" in notes[-1] and "(live + persisted)" in notes[-1]

    # 'global' clears back to the global edge
    assert p._apply_region("global") is True
    assert cfg.region is None
    assert "api.x.ai (live + persisted)" in notes[-1]


def test_apply_region_rejects_junk_and_degrades_without_pool(monkeypatch):
    monkeypatch.delenv("XAI_REGION", raising=False)
    cfg = _fake_cfg()
    p = _panel_for(cfg)   # no pool on state → honest next-session note
    notes = []
    p._actions.notify = lambda msg, **k: notes.append(msg)

    assert p._apply_region("bad region!") is False
    assert getattr(cfg, "region", None) is None

    assert p._apply_region("us-east-1") is True
    assert "lands next session" in notes[-1]


def test_apply_region_rejects_unicode_before_persisting(monkeypatch):
    # str.isalnum() alone admits '²²', which builds an IDNA-invalid hostname
    # that would crash ClientPool.from_config at the NEXT launch. The gate is
    # ASCII-only and fires BEFORE cfg.region/save — junk must never persist.
    monkeypatch.delenv("XAI_REGION", raising=False)
    cfg = _fake_cfg()
    p = _panel_for(cfg)
    notes = []
    p._actions.notify = lambda msg, **k: notes.append(msg)

    assert p._apply_region("²²") is False
    assert getattr(cfg, "region", None) is None
    assert cfg.saves == 0
    assert "invalid region:" in notes[-1]


def test_apply_region_env_override_is_surfaced(monkeypatch):
    monkeypatch.setenv("XAI_REGION", "us-west-2")
    cfg = _fake_cfg()
    p = _panel_for(cfg, pool=SimpleNamespace(rebuild_from_config=lambda c: None))
    notes = []
    p._actions.notify = lambda msg, **k: notes.append(msg)

    assert p._apply_region("eu-west-1") is True
    assert cfg.region == "eu-west-1"           # the field still saves…
    assert "XAI_REGION=us-west-2 overrides" in notes[-1]  # …but the note is honest
    assert "us-west-2.api.x.ai" in notes[-1]


def test_apply_region_pool_failure_degrades_to_next_session(monkeypatch):
    monkeypatch.delenv("XAI_REGION", raising=False)
    cfg = _fake_cfg()

    def _boom(c):
        raise RuntimeError("no keys")

    p = _panel_for(cfg, pool=SimpleNamespace(rebuild_from_config=_boom))
    notes = []
    p._actions.notify = lambda msg, **k: notes.append(msg)

    assert p._apply_region("us-west-2") is True   # the save still succeeded
    assert cfg.region == "us-west-2"
    assert any("pool rebuild failed" in n for n in notes)
    assert "lands next session" in notes[-1]


def test_region_row_dispatches_to_picker(monkeypatch):
    p = _panel_for(_fake_cfg())
    called = []
    p._pick_region = lambda: called.append(True)
    event = SimpleNamespace(stop=lambda: None, option=SimpleNamespace(id="region"))
    p.on_option_list_option_selected(event)
    assert called == [True]


def test_pick_region_offers_edges_and_custom(monkeypatch):
    monkeypatch.delenv("XAI_REGION", raising=False)
    cfg = _fake_cfg()
    cfg.region = "us-east-1"
    p = _panel_for(cfg)
    pushed = []
    p._modal_host = lambda: SimpleNamespace(
        push_screen=lambda modal, cb: pushed.append((modal, cb)))

    p._pick_region()
    modal, cb = pushed[0]
    ids = [o[0] for o in modal._options]
    assert ids[0] == "global"
    assert "us-west-2" in ids and "us-east-1" in ids and "eu-west-1" in ids
    assert ids[-1] == "custom…"
    assert modal._current == "us-east-1"

    # a saved custom region isn't in the known ring — no current marker
    cfg.region = "ap-south-1"
    pushed.clear()
    p._pick_region()
    assert pushed[0][0]._current is None

    # 'custom…' routes to the free-text prompt, not directly to apply
    prompts = []
    p._prompt = lambda text, apply: prompts.append(text)
    pushed[0][1]("custom…")
    assert prompts and "region" in prompts[0]
