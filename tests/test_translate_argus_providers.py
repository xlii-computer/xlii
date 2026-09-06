"""The argus→xlii provider translator (S1): scripts/translate_argus_providers.py."""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import translate_argus_providers as T  # noqa: E402

from xlii.providers import _parse_manifest, validate_manifest  # noqa: E402


def _write(tmp_path, text):
    f = tmp_path / "plug.js"
    f.write_text(text)
    return f


BLOCKSTREAM_LIKE = '''
ProviderRegistry.register({
  id: "blocky",
  name: "Blocky",
  description: "Address lookup",
  categories: ["finance"],
  website: "https://blocky.example",
  authType: "none",
  configFields: [],
  async query(config, params) {
    const { query } = params;
    const resp = await fetch(`https://blocky.example/api/address/${query}`);
    const data = await resp.json();
    const results = (data.txs || []).map(tx => ({
      txid: tx.txid || "",
      fee: tx.fee || 0,
    }));
    return { success: true, results };
  },
  async testConnection() {
    const resp = await fetch(`https://blocky.example/api/tip`);
    return resp.ok ? { success: true } : { success: false };
  },
});
'''

CURRENTS_LIKE = '''
ProviderRegistry.register({
  id: "newsy",
  name: "Newsy",
  description: "News",
  categories: ["news"],
  website: "https://newsy.example",
  authType: "apiKey",
  configFields: [
    { key: "apiKey", label: "API Key", type: "password", required: true },
  ],
  endpoints: { search: "https://newsy.example/v1/search" },
  async query(config, params) {
    const key = config.apiKey;
    const resp = await fetch(`https://newsy.example/v1/search?api-key=${key}&q=${encodeURIComponent(params.query)}`);
    const json = await resp.json();
    const results = (json.news || []).map(a => ({ title: a.title || "" }));
    return { success: true, results };
  },
});
'''

MULTI_FETCH = '''
ProviderRegistry.register({
  id: "multi",
  name: "Multi",
  description: "x",
  categories: [],
  website: "",
  authType: "none",
  configFields: [],
  async query(config, params) {
    const [a, b] = await Promise.all([
      fetch(`https://a.example/${params.query}`),
      fetch(`https://b.example/${params.query}`),
    ]);
    return { success: true, results: [] };
  },
});
'''

POST_BODY = '''
ProviderRegistry.register({
  id: "posty",
  name: "Posty",
  description: "x",
  categories: [],
  website: "",
  authType: "none",
  configFields: [],
  async query(config, params) {
    const resp = await fetch("https://api.example.com/v3/search", {
      method: "POST",
      body: JSON.stringify({ query: params.query }),
    });
    const json = await resp.json();
    return { success: true, results: (json.data || []).map(d => ({ name: d.name || "" })) };
  },
});
'''

COMPUTED_QS = '''
ProviderRegistry.register({
  id: "joiny",
  name: "Joiny",
  description: "x",
  categories: [],
  website: "",
  authType: "none",
  configFields: [],
  async query(config, params) {
    const qp = [`q=${params.query}`];
    const url = `https://api.example.com/search?${qp.join("&")}`;
    const resp = await fetch(url);
    return { success: true, results: [] };
  },
});
'''

POST_SINGLE_QUOTED = POST_BODY.replace('id: "posty"', 'id: "postish"').replace(
    'method: "POST"', "method: 'post'"
)

METHOD_CALL_PROJECTION = '''
ProviderRegistry.register({
  id: "shouty",
  name: "Shouty",
  description: "x",
  categories: [],
  website: "",
  authType: "none",
  configFields: [],
  async query(config, params) {
    const resp = await fetch(`https://api.example.com/search?q=${params.query}`);
    const json = await resp.json();
    const results = (json.coins || []).map(c => ({
      symbol: c.symbol.toUpperCase(),
      name: c.name || "",
    }));
    return { success: true, results };
  },
});
'''


PARTIAL_PLUGIN = '''
ProviderRegistry.register({
  id: "party",
  name: "Party",
  description: "x",
  categories: [],
  website: "",
  authType: "none",
  configFields: [],
  async query(config, params) {
    if (m === "one") {
      const resp = await fetch(`https://api.example.com/one/${params.query}`);
      const json = await resp.json();
      return { success: true, results: (json.rows || []).map(r => ({ id: r.id || "" })) };
    }
    if (m === "two") {
      const [a, b] = await Promise.all([
        fetch(`https://api.example.com/a/${params.query}`),
        fetch(`https://api.example.com/b/${params.query}`),
      ]);
      return { success: true, results: [] };
    }
  },
});
'''


def test_clean_plugin_translates(tmp_path):
    rep = T.analyze_plugin(_write(tmp_path, BLOCKSTREAM_LIKE))
    assert rep.id == "blocky" and not rep.reasons
    assert len(rep.methods) == 1 and rep.methods[0].clean
    m = rep.methods[0]
    assert m.url == "https://blocky.example/api/address/{query}"
    assert m.records_path == "txs"
    assert m.fields == {"txid": "txid", "fee": "fee"}


def test_testconnection_fetch_does_not_leak_into_methods(tmp_path):
    rep = T.analyze_plugin(_write(tmp_path, BLOCKSTREAM_LIKE))
    assert all("api/tip" not in m.url for m in rep.methods)


def test_auth_param_name_captured_from_query_string(tmp_path):
    rep = T.analyze_plugin(_write(tmp_path, CURRENTS_LIKE))
    m = rep.methods[0]
    assert m.clean
    assert m.auth_param_seen == "api-key"  # the real param, not configFields' "apiKey"
    assert "__AUTH__" not in m.url and "api-key" not in m.url


def test_multi_fetch_flagged(tmp_path):
    rep = T.analyze_plugin(_write(tmp_path, MULTI_FETCH))
    assert not rep.methods[0].clean
    assert any("Promise.all" in r for r in rep.methods[0].reasons)


def test_post_body_translates(tmp_path):
    """Cohort 2: a POST with a simple JSON.stringify body is clean."""
    rep = T.analyze_plugin(_write(tmp_path, POST_BODY))
    m = rep.methods[0]
    assert m.clean
    assert m.http_method == "POST"
    assert m.body == {"query": "{query}"}


def test_computed_body_still_flagged(tmp_path):
    text = POST_BODY.replace(
        "JSON.stringify({ query: params.query })",
        "JSON.stringify(payload)",
    )
    rep = T.analyze_plugin(_write(tmp_path, text))
    assert not rep.methods[0].clean
    assert any("JSON.stringify" in r for r in rep.methods[0].reasons)


def test_single_quoted_lowercase_post_translates(tmp_path):
    """method: 'post' is as common in argus as method: \"POST\"."""
    rep = T.analyze_plugin(_write(tmp_path, POST_SINGLE_QUOTED))
    m = rep.methods[0]
    assert m.clean
    assert m.http_method == "POST"
    assert m.body == {"query": "{query}"}


def test_method_call_projection_flagged(tmp_path):
    """row.symbol.toUpperCase() is a JS transformation, not a key path."""
    rep = T.analyze_plugin(_write(tmp_path, METHOD_CALL_PROJECTION))
    m = rep.methods[0]
    assert not m.clean
    assert any("method call in projection" in r for r in m.reasons)
    assert "symbol" not in m.fields  # never emitted as a dotted lookup
    assert m.fields["name"] == "name"


def test_computed_query_string_flagged(tmp_path):
    rep = T.analyze_plugin(_write(tmp_path, COMPUTED_QS))
    assert not rep.methods[0].clean
    assert any("computed URL hole" in r or "built in variable" in r for r in rep.methods[0].reasons)


def test_draft_manifest_is_valid_toml_and_sound(tmp_path):
    rep = T.analyze_plugin(_write(tmp_path, CURRENTS_LIKE))
    text = T.draft_manifest(rep, rep.methods[0])
    data = tomllib.loads(text)
    m = _parse_manifest(data, "builtin")
    assert m is not None
    assert validate_manifest(m) == []
    assert m.auth.param == "api-key"
    assert m.auth.key_env == "NEWSY_API_KEY"


def test_draft_manifest_quotes_special_param_keys(tmp_path):
    rep = T.analyze_plugin(_write(tmp_path, BLOCKSTREAM_LIKE))
    m = rep.methods[0]
    m.fields["__urlparams__"] = {"conditions[term]": "{query}"}
    text = T.draft_manifest(rep, m)
    data = tomllib.loads(text)
    assert data["request"]["params"]["conditions[term]"] == "{query}"


def test_draft_manifest_escapes_defaults_and_body_strings(tmp_path):
    rep = T.analyze_plugin(_write(tmp_path, POST_BODY))
    m = rep.methods[0]
    m.defaults = {"query": 'x\\y"z'}
    m.body["query"] = 'x\\y"z'
    text = T.draft_manifest(rep, m)
    data = tomllib.loads(text)
    assert data["defaults"]["query"] == 'x\\y"z'
    assert data["request"]["body"]["query"] == 'x\\y"z'


def test_post_body_numeric_and_bool_literals_preserved(tmp_path):
    text = POST_BODY.replace(
        "JSON.stringify({ query: params.query })",
        "JSON.stringify({ query: params.query, limit: 10, enabled: true })",
    )
    rep = T.analyze_plugin(_write(tmp_path, text))
    manifest = T.draft_manifest(rep, rep.methods[0])
    data = tomllib.loads(manifest)
    assert data["request"]["body"]["limit"] == 10
    assert data["request"]["body"]["enabled"] is True


def test_draft_manifest_is_repeatable(tmp_path):
    """Drafting must not consume the analysis — the report reads it afterwards."""
    rep = T.analyze_plugin(_write(tmp_path, CURRENTS_LIKE))
    m = rep.methods[0]
    m.fields["__urlparams__"] = {"q": "{query}"}
    first = T.draft_manifest(rep, m)
    assert T.draft_manifest(rep, m) == first
    assert m.fields["__urlparams__"] == {"q": "{query}"}
    assert tomllib.loads(first)["request"]["params"]["q"] == "{query}"


def test_report_status_is_clean_only_when_every_method_is(tmp_path, capsys):
    (tmp_path / "a.js").write_text(BLOCKSTREAM_LIKE)
    (tmp_path / "b.js").write_text(MULTI_FETCH)
    (tmp_path / "c.js").write_text(PARTIAL_PLUGIN)
    T.main(["--plugins", str(tmp_path)])
    summary = json.loads(capsys.readouterr().out.splitlines()[0])
    assert summary == {"plugins": 3, "clean": 1, "partial": 1, "flagged": 1, "drafts_written": 0}
