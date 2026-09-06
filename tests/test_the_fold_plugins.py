"""The Fold — Vector B: /plugin rename, task-able plugin rungs 1–3, panel gate.

Covers the mechanism, not stock-plugin opt-in:
- output mode parsing (raw|schema|interpret, default interpret)
- the deterministic renderers (text_template/table/message_list/paste_command)
- invoke_action mode routing + the security property (a raw/schema body never
  enters the model-facing text)
- the /plugin call CLI (rung 1) + parsers, executed with a mocked HTTP layer
- t_plugin_call honouring output mode (raw returns a receipt, not the body)
- the /get fast path (rung 3) resolving + executing via a fake completer
- the panel's subscribe/unsubscribe toggle + high-risk elevation gate
- the /lib -> /plugin rename (hidden alias) + swept error strings
"""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from rich.console import Console

from xlii import plugin as plugin_mod
from xlii import plugin_call as pc
from xlii.plugin_call import (
    invoke_action,
    parse_kv,
    render_schema_output,
    split_plugin_action,
)
from xlii.plugin_manifest import (
    OUTPUT_INTERPRET,
    OUTPUT_RAW,
    OUTPUT_SCHEMA,
    normalize_output,
    parse_manifest,
)
from xlii.repl_cmds import knowledge


# --------------------------------------------------------------------------- #
#  Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def make_plugin():
    """Write a plugin .md into the (isolated test) PLUGINS_DIR and clean it up."""
    created: list[str] = []

    def _make(pid: str, md: str) -> plugin_mod.Plugin:
        plugin_mod.PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
        (plugin_mod.PLUGINS_DIR / f"{pid}.md").write_text(md)
        created.append(pid)
        return plugin_mod.Plugin(id=pid)

    yield _make
    for pid in created:
        try:
            (plugin_mod.PLUGINS_DIR / f"{pid}.md").unlink()
        except OSError:
            # Fixture teardown: a plugin file the test already removed needs no cleanup.
            pass


@pytest.fixture
def proj(tmp_path):
    xli = tmp_path / ".xlii"
    xli.mkdir()
    return SimpleNamespace(xli_dir=xli, project_root=tmp_path, name="proj")


def _rec_console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, no_color=True, width=200)


def _text(console: Console) -> str:
    return console.file.getvalue()


_SCHEMA_MD = """---
id: demo
name: Demo
effect: read-only
trust: subscription
actions:
  - id: search
    description: search things
    method: GET
    url: https://example.com/s
    output: schema
    output_renderer: table
    output_renderer_args:
      header: Results
      list_key: hits
      columns:
        - {key: points, label: pts, max_width: 5}
        - {key: title, label: Title, max_width: 40}
    params:
      q: {required: true, description: query string}
  - id: fetch
    description: fetch raw
    method: GET
    url: https://example.com/r
    output: raw
  - id: explain
    description: needs synthesis
    method: GET
    url: https://example.com/e
---
Demo plugin.
"""

_HIGHRISK_MD = """---
id: danger
name: Danger
effect: destructive
trust: always-confirm
actions:
  - {id: nuke, description: destroy, method: POST, url: https://example.com/x}
---
"""


# --------------------------------------------------------------------------- #
#  Rung 2 core — output mode parsing
# --------------------------------------------------------------------------- #

def test_output_mode_parsed_per_action():
    m = parse_manifest(_SCHEMA_MD)
    assert m.get_action("search").output == OUTPUT_SCHEMA
    assert m.get_action("search").is_deterministic
    assert m.get_action("fetch").output == OUTPUT_RAW
    assert m.get_action("fetch").is_raw
    # No declared output → interpret (zero migration), and it is NOT deterministic.
    assert m.get_action("explain").output == OUTPUT_INTERPRET
    assert not m.get_action("explain").is_deterministic


def test_normalize_output_defaults_to_interpret():
    assert normalize_output("raw") == OUTPUT_RAW
    assert normalize_output("SCHEMA") == OUTPUT_SCHEMA
    assert normalize_output("nonsense") == OUTPUT_INTERPRET
    assert normalize_output(None) == OUTPUT_INTERPRET


# --------------------------------------------------------------------------- #
#  Rung 2 render — the deterministic renderers
# --------------------------------------------------------------------------- #

def test_render_table():
    a = parse_manifest(_SCHEMA_MD).get_action("search")
    body = '{"hits": [{"points": 42, "title": "Hello"}, {"points": 7, "title": "World"}]}'
    out = render_schema_output(a, body)
    assert "Results" in out and "pts" in out and "42" in out and "Hello" in out


def test_render_message_list_and_template():
    md = """---
id: r
actions:
  - id: a
    method: GET
    url: http://x
    output: schema
    output_renderer: message_list
    output_renderer_args: {header: News, list_key: items, item_template: "- {title}", empty_message: none}
  - id: b
    method: GET
    url: http://x
    output: schema
    output_renderer: text_template
    output_renderer_args: {template: "{name} @ {loc}"}
---
"""
    m = parse_manifest(md)
    assert render_schema_output(m.get_action("a"), '{"items":[{"title":"X"},{"title":"Y"}]}') == "News\n- X\n- Y"
    assert render_schema_output(m.get_action("a"), '{"items":[]}') == "News\nnone"
    assert render_schema_output(m.get_action("b"), '{"name":"Bob","loc":"NYC"}') == "Bob @ NYC"
    # missing placeholder degrades to "" rather than raising
    assert render_schema_output(m.get_action("b"), '{"name":"Bob"}') == "Bob @ "


def test_render_degrades_to_body_on_non_json_or_unknown_renderer():
    a = parse_manifest(_SCHEMA_MD).get_action("search")
    assert render_schema_output(a, "not json at all") == "not json at all"


# --------------------------------------------------------------------------- #
#  Rung 2 — invoke_action mode routing + the security property
# --------------------------------------------------------------------------- #

def test_invoke_action_modes(monkeypatch):
    body = '{"hits":[{"points":9,"title":"T"}]}'
    monkeypatch.setattr(pc, "execute_http_action", lambda a, p, **k: (200, body, ""))

    schema = invoke_action("demo", _SCHEMA_MD, "search", {"q": "x"})
    assert schema.mode == OUTPUT_SCHEMA
    assert "T" in schema.user_text and "Results" in schema.user_text
    # SECURITY: the body must NOT be in the model-facing text for schema/raw.
    assert body not in schema.model_text
    assert schema.model_text == schema.receipt

    raw = invoke_action("demo", _SCHEMA_MD, "fetch", {})
    assert raw.mode == OUTPUT_RAW
    assert raw.user_text == body
    assert body not in raw.model_text

    interp = invoke_action("demo", _SCHEMA_MD, "explain", {})
    assert interp.mode == OUTPUT_INTERPRET
    assert body in interp.model_text  # interpret returns the body to the model


def test_invoke_action_unknown_action_raises():
    with pytest.raises(ValueError, match="unknown action"):
        invoke_action("demo", _SCHEMA_MD, "nope", {})


# --------------------------------------------------------------------------- #
#  Rung 1 — CLI parsers
# --------------------------------------------------------------------------- #

def test_split_plugin_action_last_dot():
    assert split_plugin_action("open-meteo.geocode") == ("open-meteo", "geocode")
    assert split_plugin_action("alpha-vantage.time_series") == ("alpha-vantage", "time_series")
    with pytest.raises(ValueError):
        split_plugin_action("noaction")


def test_parse_kv_strings_and_json_objects():
    got = parse_kv(["name=London", "count=3", "flag=true", 'msg={"text":"hi"}', "arr=[1,2]"])
    assert got["name"] == "London"
    assert got["count"] == "3"          # scalar stays a literal string
    assert got["flag"] == "true"        # NOT coerced to python bool
    assert got["msg"] == {"text": "hi"}  # object parses as JSON (nested params)
    assert got["arr"] == [1, 2]
    with pytest.raises(ValueError):
        parse_kv(["noequals"])


# --------------------------------------------------------------------------- #
#  Rung 1 — /plugin call REPL path (zero model, honours mode)
# --------------------------------------------------------------------------- #

def test_plugin_call_repl_executes_deterministically(make_plugin, proj, monkeypatch):
    make_plugin("demo", _SCHEMA_MD)
    plugin_mod.add_subscription(proj.xli_dir, "demo")
    monkeypatch.setattr(pc, "execute_http_action",
                        lambda a, p, **k: (200, '{"hits":[{"points":9,"title":"Zed"}]}', ""))
    console = _rec_console()
    ctx = {"console": console, "project": proj, "state": SimpleNamespace(project=proj)}

    handled = knowledge._handle_plugin_command("/plugin call demo.search q=hello", ctx)
    assert handled is True
    out = _text(console)
    assert "demo.search" in out and "Zed" in out  # rendered output shown to the user


def test_plugin_call_repl_rejects_unsubscribed(proj):
    console = _rec_console()
    ctx = {"console": console, "project": proj, "state": SimpleNamespace(project=proj)}
    assert knowledge._handle_plugin_command("/plugin call demo.search q=x", ctx) is True
    assert "not subscribed" in _text(console)


def test_plugin_call_repl_bad_spec(proj):
    console = _rec_console()
    ctx = {"console": console, "project": proj, "state": SimpleNamespace(project=proj)}
    knowledge._handle_plugin_command("/plugin call noaction", ctx)
    assert "plugin" in _text(console).lower()


# --------------------------------------------------------------------------- #
#  Rename — /lib is a hidden alias of /plugin; subscription still works
# --------------------------------------------------------------------------- #

def test_lib_is_hidden_alias_of_plugin():
    # register once in a subprocess-free way: knowledge.register() is idempotent
    # only across a fresh registry, so guard against double-register here.
    from xlii.commands import _INDEX, find_repl_command
    if "plugin" not in _INDEX:
        knowledge.register()
    c_plugin = find_repl_command("/plugin all", repl="code")
    c_lib = find_repl_command("/lib all", repl="code")
    assert c_plugin is not None and c_lib is not None
    assert c_plugin is c_lib               # same command
    assert c_plugin.name == "plugin"
    assert "lib" in c_plugin.aliases       # alias => hidden in /help


def test_plugin_subscribe_via_command_writes_plugins_txt(make_plugin, proj):
    make_plugin("demo", _SCHEMA_MD)
    console = _rec_console()
    ctx = {"console": console, "project": proj, "state": SimpleNamespace(project=proj)}
    # subscribe through the canonical verb
    knowledge._handle_plugin_command("/plugin subscribe demo", ctx)
    assert "demo" in plugin_mod.load_subscriptions(proj.xli_dir)
    # ...and unsubscribe through the hidden-alias code path (same handler)
    knowledge._handle_plugin_command("/lib unsubscribe demo", ctx)
    assert "demo" not in plugin_mod.load_subscriptions(proj.xli_dir)


def test_plugin_all_shows_effect_trust_badges(make_plugin, proj):
    make_plugin("demo", _SCHEMA_MD)
    console = _rec_console()
    ctx = {"console": console, "project": proj, "state": SimpleNamespace(project=proj)}
    knowledge._handle_plugin_command("/plugin all", ctx)
    out = _text(console)
    assert "demo" in out and "read-only" in out and "subscription" in out


# --------------------------------------------------------------------------- #
#  Rung 2 — t_plugin_call tool path: raw returns a receipt, not the body
# --------------------------------------------------------------------------- #

def test_t_plugin_call_raw_returns_receipt_not_body(make_plugin, monkeypatch):
    from xlii.tool_handlers import t_plugin_call

    make_plugin("demo", _SCHEMA_MD)
    body = '{"hits":[{"points":9,"title":"SECRET-INJECT"}]}'
    monkeypatch.setattr(pc, "execute_http_action", lambda a, p, **k: (200, body, ""))
    console = _rec_console()
    ctx = SimpleNamespace(subscribed_plugins=["demo"], console=console)

    res = t_plugin_call(ctx, {"plugin": "demo", "action": "fetch", "params": {}})
    # the model-facing result is the receipt; the untrusted body is NOT in it
    assert "SECRET-INJECT" not in res.content
    assert "raw mode" in res.content and "demo.fetch" in res.content
    # ...but the body WAS shown to the user
    assert "SECRET-INJECT" in _text(console)


def test_t_plugin_call_interpret_returns_body(make_plugin, monkeypatch):
    from xlii.tool_handlers import t_plugin_call

    make_plugin("demo", _SCHEMA_MD)
    body = '{"answer": 42}'
    monkeypatch.setattr(pc, "execute_http_action", lambda a, p, **k: (200, body, ""))
    ctx = SimpleNamespace(subscribed_plugins=["demo"], console=_rec_console())
    res = t_plugin_call(ctx, {"plugin": "demo", "action": "explain", "params": {}})
    assert body in res.content  # interpret puts the body in front of the model


# --------------------------------------------------------------------------- #
#  Rung 3 — /get fast path
# --------------------------------------------------------------------------- #

def _fake_completer_returning(action_id, params):
    import json
    payload = json.dumps({"action": action_id, "params": params})
    return lambda messages: payload


def test_get_fast_path_fires_for_schema_action(make_plugin, proj, monkeypatch):
    make_plugin("demo", _SCHEMA_MD)
    plugin_mod.add_subscription(proj.xli_dir, "demo")
    monkeypatch.setattr(pc, "execute_http_action",
                        lambda a, p, **k: (200, '{"hits":[{"points":1,"title":"FAST"}]}', ""))
    # the "one structured call" is faked to resolve to the schema action
    monkeypatch.setattr("xlii.wiki_author.session_completer",
                        lambda state, **k: _fake_completer_returning("search", {"q": "x"}))
    console = _rec_console()
    ctx = {"console": console, "project": proj, "state": SimpleNamespace(project=proj)}

    assert knowledge._get_fast_path("find FAST things", ctx) is True
    out = _text(console)
    assert "deterministic" in out and "FAST" in out and "demo.search" in out


def test_get_fast_path_declines_without_deterministic_action(make_plugin, proj, monkeypatch):
    # a plugin whose only action is interpret → the fast path does not apply
    md = """---
id: only_interp
effect: read-only
trust: subscription
actions:
  - {id: a, description: synth, method: GET, url: http://x}
---
"""
    make_plugin("only_interp", md)
    plugin_mod.add_subscription(proj.xli_dir, "only_interp")
    called = {"completer": False}

    def _boom(state, **k):
        called["completer"] = True
        return lambda m: "{}"
    monkeypatch.setattr("xlii.wiki_author.session_completer", _boom)
    ctx = {"console": _rec_console(), "project": proj, "state": SimpleNamespace(project=proj)}

    assert knowledge._get_fast_path("do a thing", ctx) is False
    # it bailed BEFORE spending the structured model call (no deterministic action)
    assert called["completer"] is False


def test_get_handler_falls_through_to_orchestration(make_plugin, proj, monkeypatch):
    # no subscribed plugins → fast path declines → /get rewrites for the model
    monkeypatch.setattr("xlii.wiki_author.session_completer", lambda state, **k: None)
    ctx = {"console": _rec_console(), "project": proj, "state": SimpleNamespace(project=proj)}
    handled = knowledge._get_handler("/get the weather", ctx)
    assert handled is False               # falls through to the model
    assert "_get_rewritten" in ctx and "plugin_search" in ctx["_get_rewritten"]


# --------------------------------------------------------------------------- #
#  Panel — subscribe/unsubscribe toggle + high-risk elevation gate
# --------------------------------------------------------------------------- #

def test_panel_toggle_subscribes_low_risk(make_plugin, proj):
    from xlii.tui.plugins_panel import toggle_subscription

    make_plugin("demo", _SCHEMA_MD)
    state = SimpleNamespace(project=proj, elevated=False)
    outcome, _ = toggle_subscription(state, "demo")
    assert outcome == "subscribed"
    assert "demo" in plugin_mod.load_subscriptions(proj.xli_dir)
    outcome, _ = toggle_subscription(state, "demo")
    assert outcome == "unsubscribed"
    assert "demo" not in plugin_mod.load_subscriptions(proj.xli_dir)


def test_panel_high_risk_subscribe_gated_until_elevated(make_plugin, proj):
    from xlii.tui.plugins_panel import can_subscribe, toggle_subscription

    make_plugin("danger", _HIGHRISK_MD)
    p = plugin_mod.Plugin(id="danger")
    assert p.is_high_risk()

    unelevated = SimpleNamespace(project=proj, elevated=False)
    allowed, reason = can_subscribe(unelevated, p)
    assert allowed is False and "admin unlock" in reason
    outcome, _ = toggle_subscription(unelevated, "danger")
    assert outcome == "gated"
    assert "danger" not in plugin_mod.load_subscriptions(proj.xli_dir)  # NOT subscribed

    elevated = SimpleNamespace(project=proj, elevated=True)
    allowed, _ = can_subscribe(elevated, p)
    assert allowed is True
    outcome, _ = toggle_subscription(elevated, "danger")
    assert outcome == "subscribed"
    assert "danger" in plugin_mod.load_subscriptions(proj.xli_dir)


def test_repl_subscribe_high_risk_gated_until_elevated(make_plugin, proj):
    make_plugin("danger", _HIGHRISK_MD)
    console = _rec_console()
    ctx = {
        "console": console,
        "project": proj,
        "state": SimpleNamespace(project=proj, elevated=False),
    }
    knowledge._handle_plugin_command("/plugin subscribe danger", ctx)
    assert "danger" not in plugin_mod.load_subscriptions(proj.xli_dir)
    assert "admin unlock" in _text(console)

    ctx["state"] = SimpleNamespace(project=proj, elevated=True)
    knowledge._handle_plugin_command("/plugin subscribe danger", ctx)
    assert "danger" in plugin_mod.load_subscriptions(proj.xli_dir)


def test_repl_new_subscribe_high_risk_gated(proj, monkeypatch):
    console = _rec_console()
    ctx = {
        "console": console,
        "project": proj,
        "state": SimpleNamespace(project=proj, elevated=False),
    }
    high_risk_body = _HIGHRISK_MD.replace("id: danger", "id: fresh-danger")
    monkeypatch.setattr(
        "xlii.plugin_scaffold.render_plugin",
        lambda *a, **k: high_risk_body,
    )
    knowledge._handle_plugin_command(
        "/plugin new fresh-danger --effect destructive --trust always-confirm --subscribe",
        ctx,
    )
    assert "fresh-danger" not in plugin_mod.load_subscriptions(proj.xli_dir)
    assert "admin unlock" in _text(console)


def test_panel_unsubscribe_high_risk_never_gated(make_plugin, proj):
    # removing access needs no privilege even for a high-risk plugin
    from xlii.tui.plugins_panel import toggle_subscription

    make_plugin("danger", _HIGHRISK_MD)
    plugin_mod.add_subscription(proj.xli_dir, "danger")
    outcome, _ = toggle_subscription(SimpleNamespace(project=proj, elevated=False), "danger")
    assert outcome == "unsubscribed"


def test_live_subscriptions_unions_desk_with_persona(tmp_path):
    """Talk runs on the persona project; the pane writes the desk folder."""
    persona = tmp_path / "ixaac" / ".xlii"
    desk = tmp_path / "folder" / ".xlii"
    persona.mkdir(parents=True)
    desk.mkdir(parents=True)
    plugin_mod.save_subscriptions(persona, ["coingecko"])
    plugin_mod.save_subscriptions(desk, ["arxiv", "wikipedia"])
    only_persona = plugin_mod.live_subscriptions(SimpleNamespace(xli_dir=persona))
    assert only_persona == ["coingecko"]
    both = plugin_mod.live_subscriptions(
        SimpleNamespace(xli_dir=persona),
        SimpleNamespace(plugin_xli_dirs=(desk,)),
    )
    assert both == ["coingecko", "arxiv", "wikipedia"]
    # desk-only (persona has no plugins.txt) — Home ● must still reach talk
    empty_persona = tmp_path / "empty" / ".xlii"
    empty_persona.mkdir(parents=True)
    desk_only = plugin_mod.live_subscriptions(
        SimpleNamespace(xli_dir=empty_persona),
        SimpleNamespace(plugin_xli_dirs=(desk,)),
    )
    assert desk_only == ["arxiv", "wikipedia"]
