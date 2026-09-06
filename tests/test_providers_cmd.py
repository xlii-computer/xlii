"""/providers + quota ledger (typed-workbenches S2)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from xlii.commands import find_repl_command
from xlii.providers import (
    ProviderError,
    get_manifest,
    quota_bucket,
    record_usage,
    run_provider,
    usage_today,
)
from xlii.repl_cmds import providers as P
from xlii.repl_cmds import register_all

register_all()


class _Console:
    def __init__(self):
        self.lines = []

    def print(self, *a, **k):
        self.lines.append(" ".join(str(x) for x in a))

    @property
    def text(self):
        return "\n".join(self.lines)


def _xli(tmp_path):
    d = tmp_path / ".xlii"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ctx(xli):
    return {"console": _Console(), "project": SimpleNamespace(xli_dir=xli)}


def _fake_fetch(bodies):
    calls = []

    def fetch(method, url, *, params, headers, json_body=None):
        calls.append(params)
        return bodies[min(len(calls) - 1, len(bodies) - 1)]

    fetch.calls = calls
    return fetch


# --- the quota ledger -----------------------------------------------------------


def test_usage_ledger_counts_and_prunes(tmp_path):
    xli = _xli(tmp_path)
    record_usage(xli_dir=xli, name="p", n=2, today="2026-08-01")
    record_usage(xli_dir=xli, name="p", n=1, today="2026-08-01")
    record_usage(xli_dir=xli, name="p", n=5, today="2026-08-02")
    assert usage_today(xli, "p", today="2026-08-01") == 3
    assert usage_today(xli, "p", today="2026-08-02") == 5
    assert usage_today(xli, "p", today="2026-08-03") == 0
    for i in range(20):  # prune keeps the last 14 days per provider
        record_usage(xli_dir=xli, name="p", today=f"2026-09-{i + 1:02d}")
    days = json.loads((xli / "provider-usage.json").read_text())["p"]
    assert len(days) == 14


@pytest.mark.parametrize("junk", ["[]", "null", '"nope"', '{"p": 3}', '{"p": {"d": "x"}}'])
def test_ledger_survives_a_structurally_corrupt_file(tmp_path, junk):
    """Best-effort means shape too: a valid-JSON ledger of the wrong shape must
    never turn a successful run into a crash."""
    xli = _xli(tmp_path)
    (xli / "provider-usage.json").write_text(junk)
    assert usage_today(xli, "p", today="d") == 0
    record_usage(xli_dir=xli, name="p", n=2, today="d")  # must not raise
    assert usage_today(xli, "p", today="d") == 2


def test_quota_is_counted_per_account_not_per_manifest(tmp_path):
    """Sibling manifests on one key share the account's daily budget — two
    manifests × one call is 2/3, not 1/3 twice."""
    xli = _xli(tmp_path)
    mdir = xli / "providers"
    mdir.mkdir()
    for n in ("sib-a", "sib-b"):
        (mdir / f"{n}.toml").write_text(
            f'name="{n}"\n[request]\nurl="https://example.com/{n}"\n'
            '[auth]\nkind="query"\nkey_env="SHARED_KEY"\nparam="k"\nrequired=false\n'
            "[quota]\ndaily = 3\n"
        )
    assert quota_bucket(get_manifest("sib-a", xli)) == "SHARED_KEY"
    run_provider("sib-a", xli_dir=xli, fetch_json=_fake_fetch([["r"]]), environ={})
    result = run_provider("sib-b", xli_dir=xli, fetch_json=_fake_fetch([["r"]]), environ={})
    assert result.quota_note == "2/3 today"


def test_a_failed_page_still_spends_quota(tmp_path):
    """The ledger must not under-report exactly when a provider starts refusing:
    a run that dies on page 2 counts both attempts."""
    xli = _xli(tmp_path)
    mdir = xli / "providers"
    mdir.mkdir()
    (mdir / "paged.toml").write_text(
        'name="paged"\n[request]\nurl="https://example.com/api"\n'
        '[pagination]\nkind="page"\nmax_pages=3\n'
        "[quota]\ndaily = 10\n"
    )
    calls = []

    def flaky(method, url, *, params, headers, json_body=None):
        calls.append(params)
        if len(calls) == 2:
            raise ProviderError("provider timed out after 20s")
        return ["r"]

    with pytest.raises(ProviderError):
        run_provider("paged", xli_dir=xli, fetch_json=flaky, environ={})
    assert usage_today(xli, "paged") == 2


def test_run_records_usage_and_notes_quota(tmp_path, monkeypatch):
    xli = _xli(tmp_path)
    mdir = xli / "providers"
    mdir.mkdir()
    (mdir / "budgeted.toml").write_text(
        'name="budgeted"\n[request]\nurl="https://example.com/api"\n'
        "[quota]\ndaily = 3\n"
    )
    for _ in range(2):
        result = run_provider(
            "budgeted", xli_dir=xli, fetch_json=_fake_fetch([["r"]]), environ={}
        )
    assert result.quota_note == "2/3 today"
    result = run_provider(
        "budgeted", xli_dir=xli, fetch_json=_fake_fetch([["r"]]), environ={}
    )
    assert result.quota_note == "3/3 today"
    result = run_provider(
        "budgeted", xli_dir=xli, fetch_json=_fake_fetch([["r"]]), environ={}
    )
    assert "over daily limit" in result.quota_note  # advisory — the run still ran


# --- the /providers command -------------------------------------------------------


def test_command_is_code_repl_only():
    """/providers reaches the network and writes into the project — both outside
    the chat surface's project-blind contract."""
    assert find_repl_command("/providers", "code") is not None
    assert find_repl_command("/providers", "chat") is None


def test_list_shows_key_and_quota_state(tmp_path, monkeypatch):
    xli = _xli(tmp_path)
    monkeypatch.setenv("COINGECKO_API_KEY", "x")
    ctx = _ctx(xli)
    assert P.h_providers("/providers", ctx) is True
    out = ctx["console"].text
    assert "coingecko-price" in out and "hn-algolia" in out
    assert "✓" in out  # the keyed provider shows configured


def test_run_via_command_stores_and_reports(tmp_path, monkeypatch):
    xli = _xli(tmp_path)
    import xlii.repl_cmds.providers as prov_mod

    calls = []

    def fake_run(name, xli_dir, params=None, **kw):
        calls.append((name, params))
        return SimpleNamespace(provider=name, count=2, pages=1, quota_note="",
                               records=[{"a": 1}, {"b": 2}], latest_path=None)

    monkeypatch.setattr(prov_mod, "run_provider", fake_run)
    ctx = _ctx(xli)
    assert P.h_providers("/providers run hn-algolia query=xlii", ctx) is True
    assert calls == [("hn-algolia", {"query": "xlii"})]
    assert "2 records" in ctx["console"].text


def test_run_quoted_param_with_spaces(tmp_path, monkeypatch):
    xli = _xli(tmp_path)
    import xlii.repl_cmds.providers as prov_mod

    calls = []

    def fake_run(name, xli_dir, params=None, **kw):
        calls.append((name, params))
        return SimpleNamespace(provider=name, count=1, pages=1, quota_note="",
                               records=[{}], latest_path=None)

    monkeypatch.setattr(prov_mod, "run_provider", fake_run)
    ctx = _ctx(xli)
    assert P.h_providers('/providers run gdelt-doc query="climate change"', ctx) is True
    assert calls == [("gdelt-doc", {"query": "climate change"})]


def test_bare_name_is_sugar_for_run(tmp_path, monkeypatch):
    import xlii.repl_cmds.providers as prov_mod

    calls = []
    monkeypatch.setattr(
        prov_mod, "run_provider",
        lambda name, xli_dir, params=None, **kw: calls.append(name) or SimpleNamespace(
            provider=name, count=0, pages=1, quota_note="", records=[], latest_path=None),
    )
    ctx = _ctx(_xli(tmp_path))
    assert P.h_providers("/providers hn-algolia query=x", ctx) is True
    assert calls == ["hn-algolia"]


def test_quoted_params_round_trip(tmp_path, monkeypatch):
    """shlex parsing, so a multi-word value survives: query="climate change"."""
    import xlii.repl_cmds.providers as prov_mod

    calls = []
    monkeypatch.setattr(
        prov_mod, "run_provider",
        lambda name, xli_dir, params=None, **kw: calls.append(params) or SimpleNamespace(
            provider=name, count=0, pages=1, quota_note="", records=[], latest_path=None),
    )
    ctx = _ctx(_xli(tmp_path))
    assert P.h_providers('/providers run hn-algolia query="climate change"', ctx) is True
    assert calls == [{"query": "climate change"}]
    ctx2 = _ctx(_xli(tmp_path))
    assert P.h_providers('/providers run hn-algolia query="oops', ctx2) is True
    assert "unbalanced quotes" in ctx2["console"].text


def test_new_scaffolds_a_valid_manifest(tmp_path):
    xli = _xli(tmp_path)
    ctx = _ctx(xli)
    assert P.h_providers("/providers new myapi https://api.example.com/v1", ctx) is True
    dest = xli / "providers" / "myapi.toml"
    assert dest.is_file()
    m = get_manifest("myapi", xli)
    assert m is not None and m.source == "project"
    assert "scaffolded" in ctx["console"].text
    # refuse to clobber
    ctx2 = _ctx(xli)
    P.h_providers("/providers new myapi https://api.example.com/v1", ctx2)
    assert "already exists" in ctx2["console"].text


def test_run_unknown_provider_is_a_clean_error(tmp_path):
    ctx = _ctx(_xli(tmp_path))
    assert P.h_providers("/providers run nope", ctx) is True
    assert "unknown provider" in ctx["console"].text


# --- translator quota capture (S1 pipeline gains the quota row) --------------------


def test_translator_captures_ratelimit():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import translate_argus_providers as T

    text = '''
ProviderRegistry.register({
  id: "rated", name: "Rated", description: "x", categories: ["news"],
  website: "", authType: "none", configFields: [],
  rateLimit: { requestsPerMinute: 10, requestsPerDay: 500 },
  async query(config, params) {
    const resp = await fetch(`https://api.example.com/q?term=${params.query}`);
    const json = await resp.json();
    return { success: true, results: (json.rows || []).map(r => ({ t: r.t || "" })) };
  },
});
'''
    import tempfile

    f = Path(tempfile.mkdtemp()) / "rated.js"
    f.write_text(text)
    rep = T.analyze_plugin(f)
    assert rep.rate_daily == 500
    manifest = T.draft_manifest(rep, rep.methods[0])
    assert "[quota]" in manifest and "daily = 500" in manifest
