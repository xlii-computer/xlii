"""Coverage for bootstrap.list_api_keys parsing + pagination.

The management API returns keys under the camelCase `apiKeys` field with a
`paginationToken` cursor. An earlier version read only `api_keys`/`keys` and
made a single request, so it parsed zero keys and `xlii keys list` reported
every key as "not found on xAI". These lock the parsing against that regression.
No network — `_request` is monkeypatched."""

import xlii.bootstrap as boot


def test_parses_camelcase_and_follows_pagination(monkeypatch):
    pages = [
        {"apiKeys": [{"apiKeyId": "a"}, {"apiKeyId": "b"}], "paginationToken": "tok1"},
        {"apiKeys": [{"apiKeyId": "c"}], "paginationToken": "tok2"},
        {"apiKeys": [], "paginationToken": None},
    ]
    calls = []

    def fake_request(method, url, mgmt, **kw):
        calls.append(url)
        return pages[len(calls) - 1]

    monkeypatch.setattr(boot, "_request", fake_request)
    out = boot.list_api_keys("mgmt", "team")
    assert [k["apiKeyId"] for k in out] == ["a", "b", "c"]
    # later requests carried the pagination cursor
    assert "paginationToken=tok1" in calls[1]
    assert "paginationToken=tok2" in calls[2]
    assert len(calls) == 3


def test_single_page_without_token(monkeypatch):
    monkeypatch.setattr(boot, "_request", lambda *a, **k: {"apiKeys": [{"apiKeyId": "x"}]})
    assert [k["apiKeyId"] for k in boot.list_api_keys("m", "t")] == ["x"]


def test_legacy_snake_case_and_bare_list(monkeypatch):
    monkeypatch.setattr(boot, "_request", lambda *a, **k: {"api_keys": [{"apiKeyId": "y"}]})
    assert boot.list_api_keys("m", "t")[0]["apiKeyId"] == "y"
    monkeypatch.setattr(boot, "_request", lambda *a, **k: [{"apiKeyId": "z"}])
    assert boot.list_api_keys("m", "t")[0]["apiKeyId"] == "z"


def test_empty_response_is_empty_list(monkeypatch):
    monkeypatch.setattr(boot, "_request", lambda *a, **k: None)
    assert boot.list_api_keys("m", "t") == []


def test_request_wraps_transport_error_as_bootstrap_error(monkeypatch):
    """Unreachable management API must not traceback — BootstrapError, one line."""
    import httpx
    import pytest

    def boom(*a, **k):
        raise httpx.ConnectError("connection refused", request=None)

    monkeypatch.setattr(boot.httpx, "request", boom)
    with pytest.raises(boot.BootstrapError, match="management API unreachable"):
        boot._request("GET", "https://example.test/auth/teams", "mgmt")


def test_keys_list_prints_one_line_on_transport_error(tmp_path, monkeypatch):
    """`xlii keys list` exits 1 with a one-line message when the API is down."""
    import httpx
    from types import SimpleNamespace

    from xlii.cmds.provision import keys as keys_mod
    from xlii.tui import console as tui_console

    lines: list[str] = []
    monkeypatch.setattr(tui_console, "print", lambda *a, **k: lines.append(" ".join(str(x) for x in a)))

    cfg = SimpleNamespace(
        management_api_key="mgmt",
        team_id="team",
        keys=[{"label": "a", "api_key_id": "kid"}],
    )
    monkeypatch.setattr(keys_mod, "GlobalConfig", SimpleNamespace(load=lambda: cfg))
    monkeypatch.setattr(keys_mod, "discover_team_id", lambda c: "team")

    def boom(*a, **k):
        raise httpx.ConnectError("connection refused", request=None)

    monkeypatch.setattr(boot.httpx, "request", boom)
    # list_api_keys goes through real _request
    rc = keys_mod.cmd_keys(SimpleNamespace(action="list"))
    assert rc == 1
    assert any("unreachable" in ln.lower() or "BootstrapError" in ln or "connection" in ln.lower()
               for ln in lines)
    assert not any("Traceback" in ln for ln in lines)
