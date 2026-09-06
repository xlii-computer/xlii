"""Plugin forms: secret/form/store, closed HTML, vault write, no prefill."""

from __future__ import annotations

import json

from cryptography.fernet import Fernet

import xlii.plugin_call as pc
from xlii.plugin_form import (
    action_needs_form,
    extract_store_value,
    form_spec,
    render_form_html,
    strip_agent_secrets,
)
from xlii.plugin_manifest import parse_manifest


_LOGIN = """---
id: bluesky_login
effect: read-only
trust: subscription
actions:
  - id: login
    method: POST
    url: https://bsky.social/xrpc/com.atproto.server.createSession
    params:
      identifier: {required: true, description: handle}
      password: {required: true, secret: true, description: app password}
    store:
      plugin: bluesky_chat
      map:
        BSKY_ACCESS_JWT: accessJwt
        BSKY_DID: did
        BSKY_PDS_HOST: pds_host
    output: schema
    output_renderer: text_template
    output_renderer_args:
      template: "Logged in as {handle}."
---
"""

_SET = """---
id: demo
effect: read-only
trust: subscription
auth_env_vars:
  - DEMO_KEY
actions:
  - id: set
    params:
      DEMO_KEY: {secret: true, store: true, required: true}
    output: raw
---
"""


def test_required_params_default_to_form():
    m = parse_manifest(_LOGIN)
    login = m.get_action("login")
    ident = login.params["identifier"]
    pw = login.params["password"]
    assert ident.form is True and ident.secret is False
    assert pw.form is True and pw.secret is True
    assert action_needs_form(login, {})
    assert action_needs_form(login, {"identifier": "a.bsky.social"})
    assert not action_needs_form(login, {
        "identifier": "a.bsky.social", "password": "xxxx-xxxx",
    })


def test_agent_secrets_are_stripped():
    m = parse_manifest(_LOGIN)
    login = m.get_action("login")
    cleaned = strip_agent_secrets(login, {
        "identifier": "a.bsky.social",
        "password": "should-not-stick",
    })
    assert cleaned == {"identifier": "a.bsky.social"}
    assert action_needs_form(login, cleaned)


def test_textarea_renders_in_form():
    raw = """---
id: chat
effect: external-write
trust: subscription
actions:
  - id: compose
    method: POST
    url: https://example.test/send
    params:
      to: {required: true}
      text: {required: true, input: textarea}
---
"""
    m = parse_manifest(raw)
    spec = form_spec("chat", m.get_action("compose"))
    kinds = {f["name"]: f["kind"] for f in spec["fields"]}
    assert kinds["to"] == "text"
    assert kinds["text"] == "textarea"
    assert "<textarea" in spec["html"]


def test_form_html_is_closed_and_not_prefill():
    m = parse_manifest(_LOGIN)
    spec = form_spec("bluesky_login", m.get_action("login"),
                     seed={"identifier": "nick.bsky.social"})
    html = spec["html"]
    assert "xlii-plugin-call" in html
    assert "xlii-prefill" not in html
    assert "Sending" in html
    assert 'type="button"' in html
    assert 'type="password"' in html
    assert "nick.bsky.social" in html
    assert "should-not" not in html
    assert "allow-same-origin" not in html
    # inbound messages only from the parent Face window (cross-origin frames
    # are reachable via window.opener[0], so source must be pinned)
    assert "e.source !== window.parent" in html
    # regenerate path matches
    assert "xlii-plugin-call" in render_form_html(spec)


def test_pds_host_extracts_from_did_doc():
    body = {
        "accessJwt": "eyJ",
        "did": "did:plc:abc",
        "didDoc": {
            "service": [
                {"type": "SomethingElse", "serviceEndpoint": "https://nope.example"},
                {
                    "type": "AtprotoPersonalDataServer",
                    "serviceEndpoint": "https://stropharia.us-west.host.bsky.network",
                },
            ]
        },
    }
    assert extract_store_value(body, "accessJwt") == "eyJ"
    assert extract_store_value(body, "did") == "did:plc:abc"
    assert extract_store_value(body, "pds_host") == (
        "stropharia.us-west.host.bsky.network"
    )


def test_store_only_writes_vault(monkeypatch, tmp_path):
    import xlii.vault as vault_mod

    monkeypatch.setattr(vault_mod, "VAULT_FILE", tmp_path / "vault.enc")
    monkeypatch.setattr(vault_mod, "KEY_FILE", tmp_path / ".vault-key")
    monkeypatch.setenv(vault_mod.ENV_VAR, Fernet.generate_key().decode())

    result = pc.invoke_action("demo", _SET, "set", {"DEMO_KEY": "sk-test"})
    assert result.ok
    assert "demo.DEMO_KEY" in result.stored
    vault = vault_mod.Vault.unlock(create_if_missing=False)
    assert vault.get("demo")["DEMO_KEY"] == "sk-test"
    assert "sk-test" not in result.model_text


def test_login_stores_into_other_plugin(monkeypatch, tmp_path):
    import xlii.vault as vault_mod

    monkeypatch.setattr(vault_mod, "VAULT_FILE", tmp_path / "vault.enc")
    monkeypatch.setattr(vault_mod, "KEY_FILE", tmp_path / ".vault-key")
    monkeypatch.setenv(vault_mod.ENV_VAR, Fernet.generate_key().decode())

    body = json.dumps({
        "accessJwt": "eyJhbGc.token",
        "did": "did:plc:xyz",
        "handle": "nick.bsky.social",
        "didDoc": {
            "service": [{
                "type": "AtprotoPersonalDataServer",
                "serviceEndpoint": "https://enoki.us-east.host.bsky.network",
            }]
        },
    })

    def fake_http(action, params, **kw):
        assert params["identifier"] == "nick.bsky.social"
        assert params["password"] == "app-pass"
        return 200, body, ""

    monkeypatch.setattr(pc, "execute_http_action", fake_http)
    result = pc.invoke_action(
        "bluesky_login", _LOGIN, "login",
        {"identifier": "nick.bsky.social", "password": "app-pass"},
    )
    assert result.ok
    assert "Logged in as nick.bsky.social" in result.user_text
    assert "eyJhbGc.token" not in result.model_text
    vault = vault_mod.Vault.unlock(create_if_missing=False)
    slot = vault.get("bluesky_chat")
    assert slot["BSKY_ACCESS_JWT"] == "eyJhbGc.token"
    assert slot["BSKY_DID"] == "did:plc:xyz"
    assert slot["BSKY_PDS_HOST"] == "enoki.us-east.host.bsky.network"


def test_redact_strips_jwts():
    from xlii.plugin_form import looks_like_secret_payload, redact_plugin_text

    raw = json.dumps({
        "handle": "nick.bsky.social",
        "accessJwt": "eyJhbGc." + "x" * 80,
        "did": "did:plc:abc",
    })
    out = redact_plugin_text(raw)
    assert "eyJ" not in out
    assert "nick.bsky.social" in out
    assert looks_like_secret_payload(raw) is True


def test_withhold_stored_secret_body_non_jwt_echo():
    from xlii.plugin_form import (
        stored_secret_wire_receipt,
        withhold_stored_secret_body,
    )

    stored = ["plugin.API_KEY"]
    echo = "<html>invalid key sk-live-not-a-jwt</html>\n\nstored plugin.API_KEY"
    assert withhold_stored_secret_body(echo, stored=stored)
    assert stored_secret_wire_receipt(stored) == "stored plugin.API_KEY"


def test_withhold_stored_secret_body_allows_redacted_json():
    from xlii.plugin_form import withhold_stored_secret_body

    jwt = "eyJhbGc." + "x" * 80
    body = json.dumps({"accessJwt": jwt, "ok": True}) + "\n\nstored plugin.TOKEN"
    assert not withhold_stored_secret_body(body, stored=["plugin.TOKEN"])


def test_runtime_uses_stock_not_stale_disk(tmp_path, monkeypatch):
    import xlii.plugin as plugin_mod

    plug = tmp_path / "plugins"
    plug.mkdir()
    (plug / "bluesky_login.md").write_text(
        "---\nid: bluesky_login\neffect: read-only\ntrust: subscription\n"
        "actions:\n  - id: login\n    method: POST\n"
        "    url: https://example.test/\n    params: {}\n---\n"
    )
    monkeypatch.setattr(plugin_mod, "PLUGINS_DIR", plug)
    plugin_mod._STOCK_TEXT = None
    p = plugin_mod.Plugin(id="bluesky_login")
    m = p.manifest()
    assert m is not None
    login = m.get_action("login")
    assert login.params["password"].secret is True
    assert login.store_map.get("BSKY_ACCESS_JWT") == "accessJwt"


def test_stock_bluesky_login_is_form_store():
    from pathlib import Path

    import xlii

    raw = (Path(xlii.__file__).parent / "stock_plugins" / "bluesky_login.md").read_text()
    m = parse_manifest(raw)
    login = m.get_action("login")
    assert login is not None
    assert login.params["password"].secret is True
    assert login.store_plugin == "bluesky_chat"
    assert login.store_map["BSKY_PDS_HOST"] == "pds_host"
    spec = form_spec("bluesky_login", login)
    assert "xlii-plugin-call" in spec["html"]
    assert "xlii auth set" not in raw
    assert "xli auth" not in raw


def test_agent_tool_opens_form_not_asking(monkeypatch, tmp_path):
    from types import SimpleNamespace

    import xlii.plugin as plugin_mod
    from xlii.tool_context import ToolContext
    from xlii.tool_handlers import t_plugin_call

    plug = tmp_path / "plugins"
    plug.mkdir()
    (plug / "demo.md").write_text(_SET)
    monkeypatch.setattr(plugin_mod, "PLUGINS_DIR", plug)

    opened: list[dict] = []
    ctx = ToolContext(
        project=SimpleNamespace(),
        clients=None,
        cfg=SimpleNamespace(),
        subscribed_plugins=["demo"],
        open_plugin_form=opened.append,
    )
    out = t_plugin_call(ctx, {
        "plugin": "demo", "action": "set",
        "params": {"DEMO_KEY": "agent-must-not-win"},
    })
    assert not out.is_error
    assert "form opened" in out.content.lower()
    assert opened and opened[0]["action"] == "set"
    # secret the agent tried to pass is not seeded into the form
    fields = {f["name"]: f for f in opened[0]["fields"]}
    assert fields["DEMO_KEY"]["kind"] == "secret"
    assert fields["DEMO_KEY"]["value"] == ""
