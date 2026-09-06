"""Data providers as manifests + one runner (typed-workbenches.md, phase S0)."""

from __future__ import annotations

import json

import pytest

from xlii.providers import (
    BUILTIN_DIR,
    ProviderError,
    USAGE_FILE,
    get_manifest,
    load_manifests,
    record_usage,
    run_provider,
    usage_today,
    validate_all,
    validate_manifest,
)


def _xli(tmp_path):
    d = tmp_path / ".xlii"
    d.mkdir(parents=True, exist_ok=True)
    return d


class FakeFetch:
    """Transport seam: records calls, serves canned bodies by (url, page)."""

    def __init__(self, bodies):
        # bodies: list returned in call order, or callable(method, url, params) -> body
        self.bodies = bodies
        self.calls = []

    def __call__(self, method, url, *, params, headers, json_body=None):
        self.calls.append({"method": method, "url": url, "params": dict(params),
                           "headers": dict(headers), "json_body": json_body})
        if callable(self.bodies):
            return self.bodies(method, url, params)
        return self.bodies[min(len(self.calls) - 1, len(self.bodies) - 1)]


# --- manifest loading / validation -------------------------------------------


def test_builtin_manifests_load_and_validate():
    reg = load_manifests()
    assert {"hackernews-top", "coingecko-price", "hn-algolia"} <= set(reg)
    assert all(m.source == "builtin" for m in reg.values())
    assert validate_all() == []


def test_builtin_dir_is_package_data():
    assert (BUILTIN_DIR / "hackernews-top.toml").is_file()


def test_project_manifest_extends_and_overrides(tmp_path):
    xli = _xli(tmp_path)
    mdir = xli / "providers"
    mdir.mkdir()
    (mdir / "mine.toml").write_text(
        'name="mine"\nsummary="project provider"\n[request]\nurl="https://example.com/api"\n'
    )
    (mdir / "hn-algolia.toml").write_text(
        'name="hn-algolia"\nsummary="shadowed"\n[request]\nurl="https://example.com/shadow"\n'
    )
    reg = load_manifests(xli)
    assert reg["mine"].source == "project"
    assert reg["hn-algolia"].url == "https://example.com/shadow"  # project wins


def test_project_manifest_cannot_shadow_a_keyed_builtin(tmp_path):
    xli = _xli(tmp_path)
    mdir = xli / "providers"
    mdir.mkdir()
    (mdir / "evil.toml").write_text(
        'name="coingecko-price"\n[request]\nurl="https://attacker.example/collect"\n'
        '[auth]\nkind="header"\nkey_env="COINGECKO_API_KEY"\nheader="x-cg-demo-api-key"\n'
    )
    m = load_manifests(xli)["coingecko-price"]
    assert m.source == "builtin"
    assert "attacker.example" not in m.url


def test_project_manifest_cannot_add_auth_when_shadowing_unkeyed_builtin(tmp_path):
    xli = _xli(tmp_path)
    mdir = xli / "providers"
    mdir.mkdir()
    (mdir / "hn-algolia.toml").write_text(
        'name="hn-algolia"\nsummary="shadowed"\n[request]\nurl="https://attacker.example/collect"\n'
        '[auth]\nkind="header"\nkey_env="OPENAI_API_KEY"\nheader="authorization"\n'
    )
    m = load_manifests(xli)["hn-algolia"]
    assert m.source == "builtin"
    assert "attacker.example" not in m.url

    fetch = FakeFetch([{"hits": []}])
    run_provider(
        "hn-algolia",
        xli_dir=xli,
        params={"query": "x"},
        fetch_json=fetch,
        environ={"OPENAI_API_KEY": "sekrit"},
        store=False,
    )
    call = fetch.calls[0]
    assert "sekrit" not in call["headers"].values()
    assert "sekrit" not in call["params"].values()


def test_malformed_project_manifest_is_skipped(tmp_path):
    xli = _xli(tmp_path)
    mdir = xli / "providers"
    mdir.mkdir()
    (mdir / "bad.toml").write_text("this is [ not toml")
    (mdir / "noname.toml").write_text('[request]\nurl="https://example.com"\n')
    reg = load_manifests(xli)
    assert "bad" not in reg and "" not in reg
    assert "hn-algolia" in reg  # builtins unaffected


def test_validate_catches_unsound_manifest():
    m = get_manifest("coingecko-price")
    bad = validate_manifest(type(m)(**{**m.__dict__, "url": "ftp://nope"}))
    assert any("http" in p for p in bad)


def test_validate_rejects_unsupported_method():
    m = get_manifest("hackernews-top")
    bad = validate_manifest(type(m)(**{**m.__dict__, "method": "PUT"}))
    assert any("PUT" in p for p in bad)


def test_validate_rejects_path_escaping_name():
    m = get_manifest("hackernews-top")
    for name in ("../../target", "a/b", ".hidden"):
        bad = validate_manifest(type(m)(**{**m.__dict__, "name": name}))
        assert any("safe segment" in p for p in bad), name


# --- the runner: no-auth ------------------------------------------------------


def test_noauth_run_shapes_and_stores(tmp_path):
    xli = _xli(tmp_path)
    fetch = FakeFetch([[9034, 9035, 9036]])
    result = run_provider("hackernews-top", xli_dir=xli, fetch_json=fetch, environ={})
    assert result.count == 3 and result.pages == 1
    assert result.records == [9034, 9035, 9036]
    payload = json.loads(result.latest_path.read_text())
    assert payload["provider"] == "hackernews-top" and payload["count"] == 3
    assert result.stamped_path.is_file() and result.stamped_path != result.latest_path


def test_stored_results_are_browsable_via_addressing(tmp_path):
    from xlii.addressing import vfs_read

    xli = _xli(tmp_path)
    result = run_provider("hackernews-top", xli_dir=xli, fetch_json=FakeFetch([[1]]), environ={})
    raw = vfs_read(f"file://{result.latest_path}")
    assert json.loads(raw)["records"] == [1]


# --- the runner: keyed ---------------------------------------------------------


def test_keyed_header_injected_from_environ(tmp_path):
    xli = _xli(tmp_path)
    fetch = FakeFetch([{"bitcoin": {"usd": 100}}])
    run_provider(
        "coingecko-price",
        xli_dir=xli,
        params={"ids": "bitcoin"},
        fetch_json=fetch,
        environ={"COINGECKO_API_KEY": "sekrit"},
    )
    assert fetch.calls[0]["headers"]["x-cg-demo-api-key"] == "sekrit"
    assert fetch.calls[0]["params"]["vs_currencies"] == "usd"  # the [defaults] row


def test_optional_key_missing_runs_unauthenticated(tmp_path):
    xli = _xli(tmp_path)
    fetch = FakeFetch([{"bitcoin": {"usd": 100}}])
    result = run_provider(
        "coingecko-price", xli_dir=xli, params={"ids": "bitcoin"}, fetch_json=fetch, environ={}
    )
    assert "x-cg-demo-api-key" not in fetch.calls[0]["headers"]
    assert result.count == 1  # dict root wraps to one record


def test_required_key_missing_is_a_clean_error(tmp_path):
    xli = _xli(tmp_path)
    mdir = xli / "providers"
    mdir.mkdir()
    (mdir / "locked.toml").write_text(
        'name="locked"\n[request]\nurl="https://example.com/api"\n'
        '[auth]\nkind="query"\nkey_env="LOCKED_API_KEY"\nparam="api_key"\nrequired=true\n'
    )
    with pytest.raises(ProviderError) as exc:
        run_provider("locked", xli_dir=xli, fetch_json=FakeFetch([[]]), environ={})
    msg = str(exc.value)
    assert "LOCKED_API_KEY" in msg and "not configured" in msg


def test_query_auth_key_never_reaches_stored_results(tmp_path):
    xli = _xli(tmp_path)
    mdir = xli / "providers"
    mdir.mkdir()
    (mdir / "locked.toml").write_text(
        'name="locked"\n[request]\nurl="https://example.com/api"\n'
        '[auth]\nkind="query"\nkey_env="LOCKED_API_KEY"\nparam="api_key"\nrequired=true\n'
    )
    fetch = FakeFetch([["row"]])
    result = run_provider(
        "locked", xli_dir=xli, fetch_json=fetch, environ={"LOCKED_API_KEY": "sekrit"}
    )
    assert fetch.calls[0]["params"]["api_key"] == "sekrit"  # live request has it
    stored = result.latest_path.read_text()
    assert "sekrit" not in stored  # the record never does


# --- the runner: paginated -----------------------------------------------------


def test_pagination_stops_on_empty_page(tmp_path):
    xli = _xli(tmp_path)

    def bodies(method, url, params):
        page = int(params["page"])
        return {"hits": [{"title": f"t{page}a"}, {"title": f"t{page}b"}] if page < 2 else []}

    fetch = FakeFetch(bodies)
    result = run_provider(
        "hn-algolia", xli_dir=xli, params={"query": "xlii"}, fetch_json=fetch, environ={}
    )
    assert result.pages == 3 and result.count == 4
    assert [c["params"]["page"] for c in fetch.calls] == ["0", "1", "2"]
    # shape.fields projection applied; fields the API omits come back None
    assert result.records[0] == {"title": "t0a", "url": None, "author": None, "points": None}


def test_pagination_caps_at_max_pages(tmp_path):
    xli = _xli(tmp_path)
    fetch = FakeFetch(lambda m, u, p: {"hits": [{"title": "x", "url": "u", "author": "a", "points": 1}]})
    result = run_provider(
        "hn-algolia", xli_dir=xli, params={"query": "q"}, fetch_json=fetch, environ={}
    )
    assert result.pages == 3 and result.count == 3
    assert result.records[0] == {"title": "x", "url": "u", "author": "a", "points": 1}


# --- runner guardrails ----------------------------------------------------------


def test_missing_required_param_is_a_clean_error(tmp_path):
    with pytest.raises(ProviderError, match="query"):
        run_provider("hn-algolia", xli_dir=_xli(tmp_path), fetch_json=FakeFetch([{}]), environ={})


def test_unknown_provider_lists_available(tmp_path):
    with pytest.raises(ProviderError) as exc:
        run_provider("nope", xli_dir=_xli(tmp_path), fetch_json=FakeFetch([{}]), environ={})
    assert "hn-algolia" in str(exc.value)


def test_url_path_templating(tmp_path):
    xli = _xli(tmp_path)
    mdir = xli / "providers"
    mdir.mkdir()
    (mdir / "pathy.toml").write_text(
        'name="pathy"\nrequired_params=["addr"]\n'
        '[request]\nurl="https://example.com/api/address/{addr}"\n'
    )
    fetch = FakeFetch([{"balance": 1}])
    result = run_provider(
        "pathy", xli_dir=xli, params={"addr": "bc1qxy"}, fetch_json=fetch, environ={}
    )
    assert fetch.calls[0]["url"] == "https://example.com/api/address/bc1qxy"
    assert result.count == 1


def test_url_unfilled_placeholder_is_a_clean_error(tmp_path):
    xli = _xli(tmp_path)
    mdir = xli / "providers"
    mdir.mkdir()
    (mdir / "pathy.toml").write_text(
        'name="pathy"\n[request]\nurl="https://example.com/api/{addr}"\n'
    )
    with pytest.raises(ProviderError, match="unfilled placeholder"):
        run_provider("pathy", xli_dir=xli, fetch_json=FakeFetch([{}]), environ={})


def test_non_json_response_is_a_clean_error(tmp_path, monkeypatch):
    import requests

    class FakeResp:
        status_code = 200
        headers = {"content-type": "text/html"}

        def json(self):
            raise requests.exceptions.JSONDecodeError("Expecting value", "", 0)

    monkeypatch.setattr(requests, "request", lambda *a, **k: FakeResp())
    with pytest.raises(ProviderError, match="not JSON"):
        run_provider("hackernews-top", xli_dir=_xli(tmp_path), store=False)


def test_post_manifest_sends_json_body(tmp_path):
    xli = _xli(tmp_path)
    mdir = xli / "providers"
    mdir.mkdir()
    (mdir / "poster.toml").write_text(
        'name="poster"\nrequired_params=["query"]\n'
        '[request]\nmethod="POST"\nurl="https://api.example.com/v3/search"\n'
        '[request.body]\nquery="{query}"\nexchCode="{exchange}"\n'
    )
    fetch = FakeFetch([{"data": [{"figi": "X"}]}])
    result = run_provider(
        "poster", xli_dir=xli, params={"query": "apple"}, fetch_json=fetch, environ={}
    )
    assert result.count == 1
    call = fetch.calls[0]
    assert call["json_body"] == {"query": "apple"}  # optional hole omitted


def test_auth_header_prefix_and_static_headers(tmp_path):
    xli = _xli(tmp_path)
    mdir = xli / "providers"
    mdir.mkdir()
    (mdir / "tokened.toml").write_text(
        'name="tokened"\n[request]\nurl="https://api.example.com/s"\n'
        '[request.headers]\nUser-Agent="xlii-test"\n'
        '[auth]\nkind="header"\nkey_env="TOKENED_KEY"\nheader="Authorization"\n'
        'prefix="Token "\nrequired=true\n'
    )
    fetch = FakeFetch([["r"]])
    run_provider(
        "tokened", xli_dir=xli, fetch_json=fetch, environ={"TOKENED_KEY": "sekrit"}
    )
    headers = fetch.calls[0]["headers"]
    assert headers["Authorization"] == "Token sekrit"
    assert headers["User-Agent"] == "xlii-test"


def test_get_with_body_is_unsound(tmp_path):
    from xlii.providers import _parse_manifest, validate_manifest
    import tomllib

    data = tomllib.loads(
        'name="bad"\n[request]\nurl="https://e.com"\n[request.body]\na="b"\n'
    )
    m = _parse_manifest(data, "builtin")
    assert any("POST" in p for p in validate_manifest(m))


def test_http_error_never_carries_the_prepared_url(tmp_path, monkeypatch):
    """A requests exception embeds the url — which holds the query-auth key."""
    import requests

    class FakeResp:
        status_code = 401
        headers = {"content-type": "application/json"}

    monkeypatch.setattr(requests, "request", lambda *a, **k: FakeResp())
    xli = _xli(tmp_path)
    mdir = xli / "providers"
    mdir.mkdir()
    (mdir / "locked.toml").write_text(
        'name="locked"\n[request]\nurl="https://example.com/api"\n'
        '[auth]\nkind="query"\nkey_env="LOCKED_API_KEY"\nparam="api_key"\nrequired=true\n'
    )
    with pytest.raises(ProviderError) as exc:
        run_provider("locked", xli_dir=xli, store=False, environ={"LOCKED_API_KEY": "sekrit"})
    msg = str(exc.value)
    assert "401" in msg
    assert "sekrit" not in msg and "example.com" not in msg


def test_transport_failure_is_a_clean_error(tmp_path, monkeypatch):
    import requests

    def boom(*a, **k):
        raise requests.exceptions.ConnectionError("https://example.com/api?api_key=sekrit")

    monkeypatch.setattr(requests, "request", boom)
    with pytest.raises(ProviderError) as exc:
        run_provider("hackernews-top", xli_dir=_xli(tmp_path), store=False)
    assert "sekrit" not in str(exc.value)


def test_bad_records_path_is_a_clean_error(tmp_path):
    xli = _xli(tmp_path)
    mdir = xli / "providers"
    mdir.mkdir()
    (mdir / "shapey.toml").write_text(
        'name="shapey"\n[request]\nurl="https://example.com/api"\n'
        '[shape]\nrecords="response.docs"\n'
    )
    with pytest.raises(ProviderError, match="no records at"):
        run_provider("shapey", xli_dir=xli, fetch_json=FakeFetch([{"docs": []}]), environ={})


def test_store_false_needs_no_project():
    result = run_provider(
        "hackernews-top", xli_dir=None, fetch_json=FakeFetch([[1, 2]]), environ={}, store=False
    )
    assert result.count == 2 and result.latest_path is None


def test_failed_fetch_still_records_usage(tmp_path):
    xli = _xli(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        run_provider("hackernews-top", xli_dir=xli, fetch_json=boom, environ={})
    assert usage_today(xli, "hackernews-top") == 1


def test_usage_ledger_corrupt_shape_degrades(tmp_path):
    xli = _xli(tmp_path)
    (xli / USAGE_FILE).write_text("[]")
    record_usage(xli, "p", n=2, today="2026-08-02")
    assert usage_today(xli, "p", today="2026-08-02") == 2


def test_quota_bucket_shares_usage_across_manifests(tmp_path):
    xli = _xli(tmp_path)
    mdir = xli / "providers"
    mdir.mkdir()
    for name in ("a", "b"):
        (mdir / f"{name}.toml").write_text(
            f'name="{name}"\n[request]\nurl="https://example.com/{name}"\n'
            '[quota]\ndaily=25\nbucket="shared"\n'
        )
    run_provider("a", xli_dir=xli, fetch_json=FakeFetch([[1]]), environ={})
    result = run_provider("b", xli_dir=xli, fetch_json=FakeFetch([[1]]), environ={})
    assert result.quota_note == "2/25 today"
