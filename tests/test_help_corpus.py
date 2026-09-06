"""Tests for the help corpus manifest, topic loading, and etag cache."""

from urllib.error import HTTPError, URLError

import xlii.help_corpus as hc
from xlii.help_corpus import (
    load_manifest,
    load_topic_body,
    render_topic_index,
    see_also_for,
)


def test_manifest_loads_core_topics():
    manifest = load_manifest()
    assert "install" in manifest.topics
    assert "troubleshoot" in manifest.topics
    assert manifest.resolve("fix") == "troubleshoot"
    assert manifest.resolve("setup") == "install"
    assert manifest.resolve("nope") is None


def test_manifest_v2_tiers_commands_and_graph():
    m = load_manifest()
    assert m.version == 2
    assert m.topics["install"].tier == "core"
    assert m.topics["modes"].tier == "extended"
    assert "rail" in m.commands
    assert m.commands["rail"].topic == "rail"
    assert "execute" in see_also_for(m, "plan")  # command graph edge
    assert "rail" in see_also_for(m, "modes")    # topic graph edge


def test_load_topic_install():
    manifest = load_manifest()
    body, source = load_topic_body(manifest, "install")
    assert "xlii setup" in body
    assert "xlii doctor" in body
    assert source in ("bundled", "docs")


def test_project_topic_override(tmp_path):
    manifest = load_manifest()
    topics_dir = tmp_path / "help" / "topics"
    topics_dir.mkdir(parents=True)
    (topics_dir / "install.md").write_text("PROJECT INSTALL GUIDE")
    body, source = load_topic_body(manifest, "install", xli_dir=tmp_path)
    assert body == "PROJECT INSTALL GUIDE"
    assert source == "project"


def test_render_topic_index_lists_attach_commands():
    manifest = load_manifest()
    index = render_topic_index(manifest)
    assert "/howto install" in index
    assert "/describe" in index  # index now steers users to /describe
    assert "index" not in index.split("|")[0]  # index topic omitted from table


# --- etag cache ---------------------------------------------------------------


class _Resp:
    def __init__(self, body, etag):
        self._b = body.encode()
        self.headers = {"ETag": etag}

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_http_get_cached_stores_then_304_reuses(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    state = {"n": 0}

    def fake_urlopen(req, timeout=0):
        state["n"] += 1
        if state["n"] == 1:
            return _Resp("HELLO", '"etag-1"')
        raise HTTPError(req.full_url, 304, "Not Modified", {}, None)

    monkeypatch.setattr(hc, "urlopen", fake_urlopen)
    body, source = hc.http_get_cached("https://example.test/x.md")
    assert (body, source) == ("HELLO", "github")
    body2, source2 = hc.http_get_cached("https://example.test/x.md")
    assert (body2, source2) == ("HELLO", "cache")  # 304 → served from cache


def test_http_get_cached_offline_falls_back_to_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(hc, "urlopen", lambda req, timeout=0: _Resp("CACHED", '"e"'))
    hc.http_get_cached("https://example.test/y.md")  # prime

    def boom(req, timeout=0):
        raise URLError("offline")

    monkeypatch.setattr(hc, "urlopen", boom)
    body, source = hc.http_get_cached("https://example.test/y.md")
    assert body == "CACHED"
    assert "offline" in source


def test_load_command_doc_none_when_unconfigured():
    m = load_manifest()
    assert hc.load_command_doc(m, "definitely-not-a-command", allow_remote=False) is None
