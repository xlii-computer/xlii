"""Plugin maker pane + /plugin new (no $EDITOR)."""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from rich.console import Console

from xlii import plugin as plugin_mod
from xlii.panes import PREFILL, RETARGET_SLOT
from xlii.panes.plugin_make import PluginMakePane
from xlii.plugin_manifest import parse_manifest
from xlii.plugin_scaffold import render_plugin
from xlii.repl_cmds import knowledge


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, no_color=True, width=200)


def test_render_plugin_has_manifest():
    text = render_plugin("demo-api", effect="read-only", auth="none", output="interpret")
    m = parse_manifest(text)
    assert m is not None
    assert m.plugin_id == "demo-api"
    assert m.get_action("ping") is not None


def test_plugin_make_cycles_and_seeds():
    pane = PluginMakePane("pluginmake://")
    assert pane.scaffold_command().startswith("/plugin new my-api")
    leaves = [r.address for r in pane.render().rows if r.kind == "leaf"]
    pane.select_index(next(i for i, a in enumerate(leaves) if a.endswith("/effect")))
    assert pane.apply_selection() is True
    assert pane._effect == "external-write"
    assert "--effect external-write" in pane.scaffold_command()


def test_plugin_make_scaffold_prefills():
    pane = PluginMakePane("pluginmake://")
    leaves = [r for r in pane.render().rows if r.kind == "leaf"]
    i = next(n for n, r in enumerate(leaves) if r.address.endswith("/scaffold"))
    pane.select_index(i)
    assert pane.actions()[0].outcome.kind == PREFILL
    assert "--subscribe" in pane.actions()[0].outcome.text


def test_plugins_pane_action_rows_name_required_params(tmp_path, monkeypatch):
    from xlii.panes.plugins import PluginsPane

    plug_dir = tmp_path / "plugins"
    plug_dir.mkdir()
    (plug_dir / "wx.md").write_text(
        "---\n"
        "id: wx\n"
        "effect: read-only\n"
        "actions:\n"
        "  - id: geocode\n"
        "    method: GET\n"
        "    url: https://example.test/\n"
        "    params:\n"
        "      name: {required: true, description: city}\n"
        "---\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(plugin_mod, "PLUGINS_DIR", plug_dir)
    rows = PluginsPane("plugins://wx").render().rows
    texts = [r.text for r in rows]
    assert any("geocode" in t and "needs name" in t for t in texts)
    pane = PluginsPane("plugins://wx")
    run = next(a for a in pane.actions() if a.name == "run")
    assert run.outcome.kind == RETARGET_SLOT
    assert run.outcome.address == "pluginform://wx/geocode"


def test_plugins_pane_view_source_is_a_leaf(tmp_path, monkeypatch):
    from xlii.addressing import vfs_read, vfs_stat
    from xlii.addressing.builtins.register import register_builtins
    from xlii.panes import RETARGET_SLOT
    from xlii.panes.plugins import PluginsPane

    register_builtins()
    plug_dir = tmp_path / "plugins"
    plug_dir.mkdir()
    body = "---\nid: demo\nname: Demo\n---\n# hello plugin\n"
    (plug_dir / "demo.md").write_text(body, encoding="utf-8")
    monkeypatch.setattr(plugin_mod, "PLUGINS_DIR", plug_dir)

    node = vfs_stat("plugins://demo/source")
    assert node.kind == "leaf"
    assert vfs_read("plugins://demo/source").decode() == body

    pane = PluginsPane("plugins://")
    view = next(a for a in pane.actions() if a.name == "view")
    assert view.outcome.kind == RETARGET_SLOT
    assert view.outcome.address == "plugins://demo/source"
    edit = next(a for a in pane.actions() if a.name == "edit")
    assert edit.outcome.address == "pluginmake://demo"


def test_plugin_show_prints_markdown(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_mod, "PLUGINS_DIR", tmp_path / "plugins")
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "demo.md").write_text("# written\n", encoding="utf-8")
    console = _console()
    ctx = {"console": console, "project": SimpleNamespace(xli_dir=tmp_path / ".xlii")}
    assert knowledge._handle_plugin_command("/plugin show demo", ctx) is True
    assert "# written" in console.file.getvalue()


def test_plugins_pane_key_caption_for_subscribed(monkeypatch):
    from xlii.panes import plugins as plugins_pane
    from xlii.panes.plugins import PluginsPane

    class _P:
        id = "wx"
        def auth_env_vars(self):
            return ["WX_KEY"]
        def effect_trust(self):
            return ("read-only", "subscription")
        def description(self):
            return ""

    monkeypatch.setattr(plugins_pane, "_subscribed", lambda: {"wx"})
    monkeypatch.setattr("xlii.plugin.list_plugins", lambda: [_P()])
    monkeypatch.setattr(plugins_pane, "missing_vault_vars", lambda p: ["WX_KEY"], raising=False)
    monkeypatch.setattr("xlii.plugin_form.missing_vault_vars", lambda p: ["WX_KEY"])
    pane = PluginsPane("plugins://")
    rows = pane.render().rows
    assert rows[0].kind == "caption"
    assert "keys · 0/1 set" in rows[0].text
    assert rows[0].selected is False
    assert any(r.address == "plugins://wx" for r in rows)


def test_plugins_select_index_skips_key_caption(monkeypatch):
    """Face/TUI pass a leaf ordinal. A leading 'keys · N/M' caption used
    to make every click land one row high."""
    from xlii.panes import plugins as plugins_pane
    from xlii.panes.plugins import PluginsPane

    class _P:
        def __init__(self, pid):
            self.id = pid

        def auth_env_vars(self):
            return ["WX_KEY"] if self.id == "alpha" else []

        def effect_trust(self):
            return ("read-only", "subscription")

        def description(self):
            return ""

    monkeypatch.setattr(plugins_pane, "_subscribed", lambda: {"alpha"})
    monkeypatch.setattr("xlii.plugin.list_plugins", lambda: [_P("alpha"), _P("beta")])
    monkeypatch.setattr("xlii.plugin_form.missing_vault_vars", lambda p: ["WX_KEY"])
    pane = PluginsPane("plugins://")
    rows = pane.render().rows
    assert rows[0].kind == "caption"
    assert pane.select_index(0) is True
    assert pane.selection().node.name == "alpha"
    assert pane.select_index(1) is True
    assert pane.selection().node.name == "beta"
    painted = pane.render().rows
    assert painted[0].selected is False
    assert [r.address for r in painted if r.selected] == ["plugins://beta"]
    assert pane.select_index(99) is False


def test_plugins_pane_offers_new():
    from xlii.panes.plugins import PluginsPane

    acts = PluginsPane("plugins://").actions()
    new = next(a for a in acts if a.name == "new")
    assert new.outcome.kind == RETARGET_SLOT
    assert new.outcome.address == "pluginmake://"


def test_plugin_new_writes_without_editor(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_mod, "PLUGINS_DIR", tmp_path / "plugins")
    console = _console()
    proj = SimpleNamespace(xli_dir=tmp_path / ".xlii")
    proj.xli_dir.mkdir()
    ctx = {"console": console, "project": proj, "state": SimpleNamespace(project=proj)}
    assert knowledge._handle_plugin_command(
        "/plugin new demo-api --auth none --subscribe", ctx
    ) is True
    path = tmp_path / "plugins" / "demo-api.md"
    assert path.is_file()
    out = console.file.getvalue()
    assert "wrote" in out and "subscribed" in out
    assert "demo-api" in plugin_mod.load_subscriptions(proj.xli_dir)
    m = parse_manifest(path.read_text())
    assert m is not None and m.get_action("ping")


def test_plugin_form_loads_existing_and_keeps_extras(tmp_path, monkeypatch):
    from xlii.plugin_make_form import emit_plugin_markdown, form_spec

    monkeypatch.setattr(plugin_mod, "PLUGINS_DIR", tmp_path)
    (tmp_path / "wx.md").write_text(
        "---\n"
        "id: wx\n"
        "name: Weather\n"
        "effect: read-only\n"
        "trust: subscription\n"
        "auth_type: none\n"
        "actions:\n"
        "  - id: geocode\n"
        "    method: GET\n"
        "    url: https://example.test/geo\n"
        "    output: schema\n"
        "    output_schema: {type: object}\n"
        "    params:\n"
        "      name: {required: true, description: city}\n"
        "---\n\n# Weather\n",
        encoding="utf-8",
    )
    spec = form_spec("wx")
    assert spec["id"] == "wx"
    assert spec["name"] == "Weather"
    assert spec["actions"][0]["id"] == "geocode"
    assert spec["actions"][0]["keep"].get("output_schema") == {"type": "object"}
    text = emit_plugin_markdown(spec)
    assert "output_schema" in text
    assert "geocode" in text


def test_write_plugin_spec_overwrites(tmp_path, monkeypatch):
    from xlii.plugin_make_form import write_plugin_spec

    monkeypatch.setattr(plugin_mod, "PLUGINS_DIR", tmp_path)
    path = write_plugin_spec({
        "id": "wx",
        "name": "WX",
        "effect": "read-only",
        "trust": "subscription",
        "auth": "none",
        "actions": [{
            "id": "ping", "method": "GET", "url": "https://x.test/",
            "output": "raw", "params": [],
        }],
        "body": "# WX\n",
    })
    assert path.read_text().count("ping") == 1
    write_plugin_spec({
        "id": "wx",
        "name": "WX",
        "effect": "read-only",
        "trust": "subscription",
        "auth": "none",
        "actions": [{
            "id": "lookup", "method": "GET", "url": "https://x.test/v2",
            "output": "raw", "params": [],
        }],
        "body": "# WX\n",
    })
    text = path.read_text()
    assert "lookup" in text and "ping" not in text


def test_pluginmake_stat_keeps_id():
    from xlii.addressing import vfs_stat
    from xlii.addressing.builtins.register import register_builtins

    register_builtins()
    node = vfs_stat("pluginmake://wx")
    assert "wx" in node.address
    assert node.extra.get("plugin") == "wx"


def test_write_plugin_spec_refuses_stock_id(tmp_path, monkeypatch):
    from xlii.plugin_make_form import write_plugin_spec

    monkeypatch.setattr(plugin_mod, "PLUGINS_DIR", tmp_path)
    monkeypatch.setattr(plugin_mod, "stock_markdown", lambda pid: "STOCK" if pid == "open-meteo" else None)
    try:
        write_plugin_spec({
            "id": "open-meteo",
            "actions": [{"id": "ping", "method": "GET", "url": "https://x.test/", "params": []}],
        })
    except ValueError as e:
        assert "stock" in str(e).lower()
    else:
        raise AssertionError("expected stock id to be refused")


def _subscribe_spec(pid: str, *, effect: str = "read-only",
                    trust: str = "subscription") -> dict:
    return {
        "id": pid,
        "name": pid,
        "effect": effect,
        "trust": trust,
        "auth": "none",
        "actions": [{"id": "ping", "method": "GET", "url": "https://x.test/",
                     "params": []}],
        "subscribe": True,
    }


def test_write_plugin_spec_subscribe_low_risk_ungated(tmp_path, monkeypatch):
    from xlii.plugin_make_form import write_plugin_spec

    monkeypatch.setattr(plugin_mod, "PLUGINS_DIR", tmp_path / "plugins")
    xli = tmp_path / ".xlii"
    write_plugin_spec(_subscribe_spec("wx"), xli_dir=xli)
    assert "wx" in plugin_mod.load_subscriptions(xli)


def test_write_plugin_spec_high_risk_subscribe_gated(tmp_path, monkeypatch):
    """The maker's subscribe step honours the same /admin unlock gate as
    /plugin subscribe — saving with the checkbox on must not attach a
    high-risk plugin from an unelevated session."""
    from xlii.plugin_make_form import PluginSubscribeGated, write_plugin_spec

    monkeypatch.setattr(plugin_mod, "PLUGINS_DIR", tmp_path / "plugins")
    xli = tmp_path / ".xlii"
    spec = _subscribe_spec("danger", effect="local-system", trust="always-confirm")

    # No state at all → fails closed.
    with pytest.raises(PluginSubscribeGated):
        write_plugin_spec(spec, xli_dir=xli)
    # The file was still written; the subscribe was refused.
    assert (tmp_path / "plugins" / "danger.md").is_file()
    assert "danger" not in plugin_mod.load_subscriptions(xli)

    # Unelevated session → gated, with the same refusal reason as the panel.
    with pytest.raises(PluginSubscribeGated) as exc:
        write_plugin_spec(spec, xli_dir=xli,
                          state=SimpleNamespace(elevated=False))
    assert "admin unlock" in str(exc.value)
    assert exc.value.path is not None
    assert "danger" not in plugin_mod.load_subscriptions(xli)

    # Elevated session → subscribes.
    write_plugin_spec(spec, xli_dir=xli, state=SimpleNamespace(elevated=True))
    assert "danger" in plugin_mod.load_subscriptions(xli)
