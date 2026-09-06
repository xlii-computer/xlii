"""Tests for plugin_call HTTP execution."""

import json
import urllib.request

from xlii.plugin_call import (
    _SafeRedirectHandler,
    call_plugin_action,
    execute_http_action,
    invoke_action,
    missing_required,
    parse_call_line,
    seed_call_line,
)
from xlii.plugin_manifest import ActionSpec, ParamSpec


class _FakeResponse:
    status = 200

    def __init__(self, body: str = "ok") -> None:
        self._body = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self) -> bytes:
        return self._body


def test_resolve_params_required():
    action = ActionSpec(
        id="geocode",
        description="",
        method="GET",
        url="https://example.com/",
        params={"name": ParamSpec(name="name", required=True)},
    )
    merged, errors = action.resolve_params({})
    assert "missing required param" in errors[0]
    assert not merged


def test_call_plugin_action_validates_unknown_action():
    raw = """---
id: demo
actions:
  - id: ping
    method: GET
    url: https://example.com/
---
body
"""
    try:
        call_plugin_action("demo", raw, "missing", {})
        assert False, "expected ValueError"
    except ValueError as e:
        assert "unknown action" in str(e)


def test_cross_origin_redirect_drops_plugin_headers():
    req = urllib.request.Request(
        "http://archivebox.local/api",
        headers={
            "Authorization": "Bearer secret-token",
            "X-ArchiveBox-API-Key": "secret-token",
        },
    )

    redirected = _SafeRedirectHandler().redirect_request(
        req,
        None,
        302,
        "Found",
        {},
        "https://attacker.example/collect",
    )

    assert redirected is not None
    assert redirected.header_items() == []


def test_same_origin_redirect_preserves_plugin_headers():
    req = urllib.request.Request(
        "http://archivebox.local/api",
        headers={"Authorization": "Bearer secret-token"},
    )

    redirected = _SafeRedirectHandler().redirect_request(
        req,
        None,
        302,
        "Found",
        {},
        "http://archivebox.local/login",
    )

    assert redirected is not None
    assert ("Authorization", "Bearer secret-token") in redirected.header_items()


def test_execute_http_action_blocks_user_supplied_file_url(monkeypatch):
    action = ActionSpec(
        id="fetch",
        description="",
        method="GET",
        url="{feed_url}",
        params={"feed_url": ParamSpec(name="feed_url", required=True)},
    )
    monkeypatch.setattr(
        "xlii.plugin_call._open_plugin_url",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("urlopen called")),
    )

    code, body, err = execute_http_action(action, {"feed_url": "file:///etc/passwd"})

    assert code == 0
    assert body == ""
    assert err == "unsupported url scheme: file"


def test_execute_http_action_blocks_static_manifest_private_host(monkeypatch):
    action = ActionSpec(
        id="meta",
        description="",
        method="GET",
        url="http://169.254.169.254/latest/meta-data/",
    )
    monkeypatch.setattr(
        "xlii.plugin_call._open_plugin_url",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("urlopen called")),
    )
    code, body, err = execute_http_action(action, {})
    assert code == 0 and body == ""
    assert "blocked host" in err


def test_execute_http_action_blocks_private_host_for_whole_url_param():
    action = ActionSpec(
        id="fetch",
        description="",
        method="GET",
        url="{feed_url}",
        params={"feed_url": ParamSpec(name="feed_url", required=True)},
    )

    code, body, err = execute_http_action(action, {"feed_url": "http://127.0.0.1:8000/feed"})

    assert code == 0
    assert body == ""
    assert err == "blocked host: 127.0.0.1"


_COMPOSE_MD = """---
id: chat
effect: external-write
trust: subscription
actions:
  - id: compose
    method: POST
    url: https://pds.example/xrpc/chat.bsky.convo.sendMessage
    headers:
      Content-Type: application/json
    params:
      to: {required: true}
      text: {required: true, input: textarea}
    compose:
      steps:
        - id: resolve
          skip_if: did
          url: https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle
          query: {handle: $to}
          take: {did: did}
        - id: convo
          url: https://pds.example/xrpc/chat.bsky.convo.getConvoForMembers
          query: {members: $did}
          take: {convoId: convo.id}
      body:
        convoId: $convoId
        message:
          text: $text
---
"""


def test_compose_resolves_handle_then_sends(monkeypatch):
    seen = []

    def fake(action, params, **kw):
        seen.append((action.url, dict(params)))
        if "resolveHandle" in action.url:
            assert params["handle"] == "ixaac.bsky.social"
            return 200, json.dumps({"did": "did:plc:abc"}), ""
        if "getConvoForMembers" in action.url:
            assert params["members"] == "did:plc:abc"
            return 200, json.dumps({"convo": {"id": "convo-1"}}), ""
        if "sendMessage" in action.url:
            assert params["convoId"] == "convo-1"
            assert params["message"] == {"text": "buenos"}
            return 200, json.dumps({"id": "m1", "text": "buenos"}), ""
        return 0, "", f"unexpected {action.url}"

    monkeypatch.setattr("xlii.plugin_call.execute_http_action", fake)
    result = invoke_action(
        "chat", _COMPOSE_MD, "compose",
        {"to": "ixaac.bsky.social", "text": "buenos"},
    )
    assert result.ok
    assert result.user_text == "Sent to ixaac.bsky.social."
    assert [u.split("/")[-1] for u, _ in seen] == [
        "com.atproto.identity.resolveHandle",
        "chat.bsky.convo.getConvoForMembers",
        "chat.bsky.convo.sendMessage",
    ]


def test_compose_skips_resolve_when_to_is_did(monkeypatch):
    seen = []

    def fake(action, params, **kw):
        seen.append(action.url)
        if "getConvoForMembers" in action.url:
            return 200, json.dumps({"convo": {"id": "c2"}}), ""
        if "sendMessage" in action.url:
            return 200, json.dumps({"id": "m2"}), ""
        return 0, "", "should-skip-resolve"

    monkeypatch.setattr("xlii.plugin_call.execute_http_action", fake)
    result = invoke_action(
        "chat", _COMPOSE_MD, "compose",
        {"to": "did:plc:xyz", "text": "hi"},
    )
    assert result.ok
    assert not any("resolveHandle" in u for u in seen)


def test_json_content_type_encodes_string_params(monkeypatch):
    """Manifest JSON content-type must not urlencode identifier=… (Bluesky 400)."""
    action = ActionSpec(
        id="login",
        description="",
        method="POST",
        url="https://bsky.social/xrpc/com.atproto.server.createSession",
        headers={"Content-Type": "application/json"},
        params={
            "identifier": ParamSpec(name="identifier", required=True),
            "password": ParamSpec(name="password", required=True, secret=True),
        },
    )
    seen = {}

    def fake_urlopen(req, timeout, *, check_host=True):
        seen["data"] = req.data
        seen["ctype"] = req.get_header("Content-type")
        return _FakeResponse("{}")

    monkeypatch.setattr("xlii.plugin_call._open_plugin_url", fake_urlopen)
    code, _body, err = execute_http_action(
        action, {"identifier": "nick.bsky.social", "password": "app-pass"},
    )
    assert code == 200 and err == ""
    assert seen["ctype"] and "json" in seen["ctype"].lower()
    assert json.loads(seen["data"].decode()) == {
        "identifier": "nick.bsky.social",
        "password": "app-pass",
    }


def test_execute_http_action_blocks_static_manifest_localhost():
    action = ActionSpec(
        id="list",
        description="",
        method="GET",
        url="http://127.0.0.1:8000/api/v1/core/snapshots",
    )
    code, body, err = execute_http_action(action, {}, timeout=7)
    assert code == 0 and body == ""
    assert "blocked host" in err


def test_execute_http_action_blocks_dummy_env_suffix_on_private_host(monkeypatch):
    """A static private host must not bypass SSRF checks via ``/${VAR}``."""
    action = ActionSpec(
        id="meta",
        description="",
        method="GET",
        url="http://169.254.169.254/${UNUSED}",
    )
    monkeypatch.setattr(
        "xlii.plugin_call._open_plugin_url",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("urlopen called")),
    )
    code, body, err = execute_http_action(action, {})
    assert code == 0 and body == ""
    assert "blocked host" in err


def test_execute_http_action_allows_vault_env_localhost(monkeypatch):
    """`${ARCHIVEBOX_BASE}` is the user's pick — host check is skipped."""
    action = ActionSpec(
        id="list",
        description="",
        method="GET",
        url="${ARCHIVEBOX_BASE}/api/v1/core/snapshots",
    )
    seen = {}

    def fake_urlopen(req, timeout, *, check_host=True):
        seen["url"] = req.full_url
        seen["check_host"] = check_host
        return _FakeResponse("local ok")

    monkeypatch.setattr("xlii.plugin_call._open_plugin_url", fake_urlopen)
    code, body, err = execute_http_action(
        action, {}, env={"ARCHIVEBOX_BASE": "http://127.0.0.1:8000"}, timeout=7,
    )
    assert code == 200 and body == "local ok" and err == ""
    assert seen["url"] == "http://127.0.0.1:8000/api/v1/core/snapshots"
    assert seen["check_host"] is False


def test_seed_and_parse_call_line():
    action = ActionSpec(
        id="geocode", description="", method="GET", url="https://example.com/",
        params={
            "name": ParamSpec(name="name", required=True, description="city"),
            "count": ParamSpec(name="count", default="1"),
        },
    )
    assert missing_required(action, {}) == ["name"]
    assert missing_required(action, {"name": ""}) == ["name"]
    assert missing_required(action, {"name": "London"}) == []
    line = seed_call_line("open-meteo", "geocode", action)
    assert line == "/plugin call open-meteo.geocode name="
    pid, aid, params = parse_call_line("/plugin call open-meteo.geocode name=London")
    assert (pid, aid, params) == ("open-meteo", "geocode", {"name": "London"})
    pid, aid, params = parse_call_line(
        '/plugin call wikipedia.page_summary title="ben franklin"'
    )
    assert (pid, aid) == ("wikipedia", "page_summary")
    assert params["title"] == "ben franklin"
    pid, aid, params = parse_call_line(
        "/plugin call wikipedia.page_summary title=ben franklin"
    )
    assert params["title"] == "ben franklin"
    pid, aid, params = parse_call_line("__plugin_call__:open-meteo:geocode")
    assert (pid, aid, params) == ("open-meteo", "geocode", {})
    assert parse_call_line("/panel home") is None
