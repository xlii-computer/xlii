"""Tests for the /howto tarball cache — sync_corpus_cache + /howto latest wiring.

One codeload tarball fetch pre-fills every per-URL cache slot the per-topic
readers already consult. The tarball is built in memory with ``tarfile`` and the
network is fully stubbed (``hc.urlopen``) — zero real requests. Every test
points ``XDG_CACHE_HOME`` at ``tmp_path`` so the real ``~/.cache`` is never
touched (standing test-isolation rule).

The bundled and tarball manifests deliberately DIFFER (bundled names a "legacy"
topic the remote corpus dropped; the tarball adds "phantom"): slot keying and
the skip gate must follow the tarball's own manifest, not the frozen bundle.
"""

import io
import json
import tarfile
from io import BytesIO
from urllib.error import URLError

import pytest
import yaml
from rich.console import Console

import xlii.help_corpus as hc
from xlii.repl_cmds import howto

SHA = "deadbeef" * 5  # 40-hex fake commit
ROOT = f"iXaac-lab-{SHA}"  # archive root is variable — code must strip, not predict

_BASE_TOPICS = {
    "index": {"title": "Index", "path": "topics/index.md"},
    "install": {"title": "Install", "path": "topics/install.md", "tier": "core"},
    # "ghost" exists in NO bundled/docs tree, so reads must hit the cache.
    "ghost": {"title": "Ghost", "path": "topics/ghost.md", "tier": "extended"},
}


def _manifest_yaml(*, ref: str = "main", extra_topics: dict | None = None) -> str:
    topics = dict(_BASE_TOPICS)
    topics.update(extra_topics or {})
    return yaml.safe_dump(
        {
            "version": 2,
            "repo": "n3r4-life/iXaac-lab",
            "ref": ref,
            "base_path": "docs/help",
            "topics": topics,
            "commands": {"rail": {"path": "commands/rail.md"}},
        }
    )


# The frozen bundled manifest still names "legacy" — dropped upstream, so the
# tarball never supplies it. The skip gate must not require it forever.
LOCAL_MANIFEST_YAML = _manifest_yaml(
    extra_topics={"legacy": {"title": "Legacy", "path": "topics/legacy.md"}}
)
LOCAL_MANIFEST = hc._parse_manifest(yaml.safe_load(LOCAL_MANIFEST_YAML))

# The tarball's own manifest genuinely differs: extra topic "phantom", no "legacy".
TAR_MANIFEST_YAML = _manifest_yaml(
    extra_topics={"phantom": {"title": "Phantom", "path": "topics/phantom.md", "tier": "extended"}}
)

# rel path under base_path -> body, for the standard good tarball (6 slots).
CORPUS_RELS = {
    "manifest.yaml": TAR_MANIFEST_YAML,
    "topics/index.md": "# Index body",
    "topics/install.md": "# Install body",
    "topics/ghost.md": "# Ghost body",
    "topics/phantom.md": "# Phantom body",
    "commands/rail.md": "# Rail body",
}
N_SLOTS = len(CORPUS_RELS)  # 6


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Never touch ~/.cache; pin the local manifest; neutralize XLII_HELP_REF."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv("XLII_HELP_REF", raising=False)
    monkeypatch.setattr(hc, "load_manifest", lambda **kw: LOCAL_MANIFEST)


def _make_tarball(members: dict[str, bytes]) -> bytes:
    """Build a .tar.gz in memory. Keys are FULL member names (root included)."""
    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, body in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, BytesIO(body))
    return buf.getvalue()


def _good_members(
    extra: dict[str, bytes] | None = None,
    manifest_yaml: str = TAR_MANIFEST_YAML,
) -> dict[str, bytes]:
    rels = dict(CORPUS_RELS, **{"manifest.yaml": manifest_yaml})
    members = {f"{ROOT}/docs/help/{rel}": body.encode() for rel, body in rels.items()}
    members[f"{ROOT}/README.md"] = b"outside base_path - never cached"
    if extra:
        members.update(extra)
    return members


class _Resp:
    def __init__(self, data: bytes, etag: str | None = None):
        self._buf = BytesIO(data)
        self.headers = {"ETag": etag} if etag else {}

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _stub_network(monkeypatch, tar_bytes: bytes, calls: list[str] | None = None):
    """Fake hc.urlopen: commits API -> sha JSON; codeload -> the tarball;
    anything else (raw.githubusercontent) -> URLError, proving reads hit cache."""
    calls = calls if calls is not None else []

    def fake_urlopen(req, timeout=0):
        url = getattr(req, "full_url", req)
        calls.append(url)
        if "api.github.com" in url:
            return _Resp(json.dumps({"sha": SHA}).encode())
        if "codeload.github.com" in url:
            return _Resp(tar_bytes)
        raise URLError("offline (stubbed)")

    monkeypatch.setattr(hc, "urlopen", fake_urlopen)
    return calls


def _go_offline(monkeypatch):
    def offline(req, timeout=0):
        raise URLError("offline")

    monkeypatch.setattr(hc, "urlopen", offline)


def _slot(rel: str, manifest=LOCAL_MANIFEST):
    return hc._cache_body_path(hc._remote_url(manifest, rel))


def _downloads(calls: list[str]) -> int:
    return len([u for u in calls if "codeload" in u])


# --- sync_corpus_cache --------------------------------------------------------


def test_happy_path_fills_slots_and_corpus_json(monkeypatch, tmp_path):
    calls = _stub_network(monkeypatch, _make_tarball(_good_members()))
    sync = hc.sync_corpus_cache()

    assert sync.skipped is False
    assert sync.sha == SHA
    assert sync.files == N_SLOTS
    # every corpus body landed in the exact per-URL slot readers consult (the
    # default tarball manifest shares repo/ref/base_path, so bundled keying
    # coincides here; the divergent-keying case has its own test below)
    for rel, body in CORPUS_RELS.items():
        assert _slot(rel).read_text() == body, rel
    # the returned manifest is the one parsed from the tarball
    assert sync.manifest.repo == "n3r4-life/iXaac-lab"
    assert set(sync.manifest.topics) == {"index", "install", "ghost", "phantom"}
    assert "rail" in sync.manifest.commands
    # corpus.json written last with the full record
    state = json.loads((hc.cache_dir() / "corpus.json").read_text())
    assert state == {"ref": "main", "sha": SHA, "files": N_SLOTS}
    # exactly the corpus slots + the commits-API slot http_get_cached wrote;
    # the outside-base_path README never lands anywhere
    bodies = [p.read_text() for p in hc.cache_dir().glob("*.body")]
    assert len(bodies) == N_SLOTS + 1
    assert all("outside base_path" not in b for b in bodies)
    assert _downloads(calls) == 1
    # reads resolve THROUGH the returned tarball manifest, offline: "phantom"
    # exists only in the tarball's manifest and only in the warmed cache.
    # Source is "cache", not "cache (offline)": load_topic_body now reads a
    # warmed slot BEFORE trying the network (howto-fast T3 — a mid-turn 304 is
    # still a wait), so being offline is no longer what makes the read local.
    _go_offline(monkeypatch)
    body, source = hc.load_topic_body(sync.manifest, "phantom")
    assert (body, source) == ("# Phantom body", "cache")


def test_offline_after_sync_serves_cache(monkeypatch):
    _stub_network(monkeypatch, _make_tarball(_good_members()))
    hc.sync_corpus_cache()

    _go_offline(monkeypatch)
    body, source = hc.load_topic_body(LOCAL_MANIFEST, "ghost")
    assert body == "# Ghost body"
    assert source == "cache"  # cache-first read; see the note above


def test_sha_unchanged_skips_download_despite_bundled_skew(monkeypatch):
    # The bundled manifest names "legacy" — never in the tarball. The skip
    # gate must follow the cached REMOTE manifest, or it would be permanently
    # dead and re-download on every bare /howto latest at an unchanged sha.
    calls = _stub_network(monkeypatch, _make_tarball(_good_members()))
    first = hc.sync_corpus_cache()
    assert first.skipped is False
    assert _downloads(calls) == 1

    second = hc.sync_corpus_cache()
    assert second.skipped is True
    assert second.sha == SHA
    assert second.files == N_SLOTS  # recorded count from corpus.json
    # the skipped sync's manifest is the cached REMOTE one, parsed
    assert set(second.manifest.topics) == {"index", "install", "ghost", "phantom"}
    assert _downloads(calls) == 1  # no second tarball fetch


def test_missing_slot_forces_redownload_at_same_sha(monkeypatch):
    # Mutation guard on _corpus_complete: a hole in the cache at a matching
    # sha MUST force a re-download, never skip.
    calls = _stub_network(monkeypatch, _make_tarball(_good_members()))
    sync = hc.sync_corpus_cache()
    _slot("topics/ghost.md", sync.manifest).unlink()

    again = hc.sync_corpus_cache()
    assert again.skipped is False
    assert _downloads(calls) == 2
    assert _slot("topics/ghost.md", again.manifest).is_file()  # hole re-filled


def test_force_redownloads_despite_matching_sha(monkeypatch):
    calls = _stub_network(monkeypatch, _make_tarball(_good_members()))
    hc.sync_corpus_cache()
    forced = hc.sync_corpus_cache(force=True)
    assert forced.skipped is False
    assert forced.files == N_SLOTS
    assert _downloads(calls) == 2


def test_slots_keyed_by_tarball_manifest_when_remote_repoints(monkeypatch):
    # The tarball's manifest repoints ref -> slots must be keyed by IT (the
    # manifest post-sync readers resolve through), not the bundled manifest.
    repointed = _manifest_yaml(ref="v2-docs")
    _stub_network(monkeypatch, _make_tarball(_good_members(manifest_yaml=repointed)))
    sync = hc.sync_corpus_cache()
    assert hc.help_ref(sync.manifest) == "v2-docs"

    # the manifest body sits where fetch_manifest bootstraps (bundled-keyed)…
    assert hc._read_cached(hc._remote_url(LOCAL_MANIFEST, "manifest.yaml")) == repointed
    # …and topic slots are keyed by the tarball's own manifest: offline reads
    # through sync.manifest hit; bundled-keyed URLs for the same rel do not
    _go_offline(monkeypatch)
    body, source = hc.load_topic_body(sync.manifest, "ghost")
    assert (body, source) == ("# Ghost body", "cache")
    assert hc._read_cached(hc._remote_url(LOCAL_MANIFEST, "topics/ghost.md")) is None


def test_oversize_member_skipped_sync_succeeds(monkeypatch):
    huge = b"x" * (1024 * 1024 + 1)  # > the 1 MiB per-member cap
    tar_bytes = _make_tarball(_good_members({f"{ROOT}/docs/help/topics/huge.md": huge}))
    _stub_network(monkeypatch, tar_bytes)
    sync = hc.sync_corpus_cache()
    assert sync.skipped is False
    assert sync.files == N_SLOTS  # huge.md not counted
    assert not _slot("topics/huge.md").exists()
    assert not _slot("topics/huge.md", sync.manifest).exists()
    assert _slot("topics/ghost.md", sync.manifest).is_file()


def test_hostile_members_rejected(monkeypatch):
    # traversal / absolute / wrong-extension members are skipped; sync succeeds
    evil = {
        "../evil.md": b"escape up",
        "/abs/evil.md": b"absolute",
        f"{ROOT}/docs/help/topics/../../../etc/passwd.md": b"dotdot inside",
        f"{ROOT}/docs/help/evil.py": b"print('not md or yaml')",
    }
    _stub_network(monkeypatch, _make_tarball(_good_members(evil)))
    sync = hc.sync_corpus_cache()
    assert sync.skipped is False
    assert sync.files == N_SLOTS  # only the legitimate corpus slots
    bodies = [p.read_text() for p in hc.cache_dir().glob("*.body")]
    for payload in ("escape up", "absolute", "dotdot inside", "not md or yaml"):
        assert all(payload not in b for b in bodies), payload


def test_malformed_tarball_raises_and_writes_no_corpus_json(monkeypatch):
    _stub_network(monkeypatch, b"this is not a gzip tarball")
    with pytest.raises(Exception):
        hc.sync_corpus_cache()
    assert not (hc.cache_dir() / "corpus.json").exists()


def test_download_cap_enforced_during_read(monkeypatch):
    # Stream far past 10 MiB — must abort during the chunked read.
    _stub_network(monkeypatch, b"\0" * (10 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="cap"):
        hc.sync_corpus_cache()
    assert not (hc.cache_dir() / "corpus.json").exists()


def test_aggregate_decompressed_cap_stops_bomb(monkeypatch):
    # The 10 MiB download cap is on COMPRESSED bytes (gzip on zeros ~1000:1),
    # and 1 MiB is per member — many small members must trip the AGGREGATE
    # decompressed cap. Cap lowered so the fixture stays tiny: bomb shape,
    # not bomb size.
    monkeypatch.setattr(hc, "_TARBALL_TOTAL_MEMBER_BYTES", 4096)
    bombs = {f"{ROOT}/docs/help/topics/bomb{i}.md": b"z" * 1024 for i in range(8)}
    _stub_network(monkeypatch, _make_tarball(_good_members(bombs)))
    with pytest.raises(ValueError, match="aggregate cap"):
        hc.sync_corpus_cache()
    assert not (hc.cache_dir() / "corpus.json").exists()


def test_member_count_cap_stops_bomb(monkeypatch):
    monkeypatch.setattr(hc, "_TARBALL_MAX_MEMBERS", 5)
    many = {f"{ROOT}/docs/help/topics/m{i}.md": b"x" for i in range(10)}
    _stub_network(monkeypatch, _make_tarball(_good_members(many)))
    with pytest.raises(ValueError, match="members"):
        hc.sync_corpus_cache()
    assert not (hc.cache_dir() / "corpus.json").exists()


# --- /howto latest wiring -------------------------------------------------------


class _Owner:
    def __init__(self):
        self.attached_docs: list[tuple[str, str]] = []
        self.howto_mode = False

    def attach_doc(self, name, content):
        self.attached_docs.append((name, content))

    def detach_doc(self, name):
        before = len(self.attached_docs)
        self.attached_docs = [(n, c) for n, c in self.attached_docs if n != name]
        return len(self.attached_docs) < before


def _ctx(owner):
    sio = io.StringIO()
    ctx = {
        "console": Console(file=sio, force_terminal=False, width=200),
        "state": owner,
        "project": None,
        "command_scope": "code",
    }
    return ctx, sio


def test_howto_latest_syncs_then_attaches_index_from_cache(monkeypatch):
    # raw.githubusercontent is stubbed to URLError, so the index attach below
    # can ONLY succeed off the slots the tarball sync just warmed.
    _stub_network(monkeypatch, _make_tarball(_good_members()))
    monkeypatch.setattr(howto, "load_manifest", lambda **kw: LOCAL_MANIFEST)
    owner = _Owner()
    ctx, sio = _ctx(owner)

    assert howto._howto_handler("/howto latest", ctx) is True
    out = sio.getvalue()
    assert f"help corpus synced — {N_SLOTS} files" in out
    assert f"n3r4-life/iXaac-lab@{SHA[:7]}" in out
    assert "whole corpus now offline-available" in out
    assert "howto mode" in out
    docs = dict(owner.attached_docs)
    assert "# Index body" in docs["howto"]
    assert owner.howto_mode is True


def test_howto_latest_skip_prints_already_current(monkeypatch):
    _stub_network(monkeypatch, _make_tarball(_good_members()))
    monkeypatch.setattr(howto, "load_manifest", lambda **kw: LOCAL_MANIFEST)
    hc.sync_corpus_cache()  # warm once so the handler's sync skips

    owner = _Owner()
    ctx, sio = _ctx(owner)
    assert howto._howto_handler("/howto latest", ctx) is True
    out = sio.getvalue()
    assert f"help corpus already current (@{SHA[:7]})" in out
    assert "# Index body" in dict(owner.attached_docs)["howto"]


def test_howto_latest_falls_back_when_sync_raises(monkeypatch):
    # Malformed tarball -> sync raises -> handler must run today's per-topic
    # path unchanged (fetch_manifest + fetch_topic_body).
    _stub_network(monkeypatch, b"corrupt bytes")
    monkeypatch.setattr(howto, "load_manifest", lambda **kw: LOCAL_MANIFEST)
    monkeypatch.setattr(howto, "fetch_manifest", lambda: LOCAL_MANIFEST)
    monkeypatch.setattr(howto, "fetch_topic_body", lambda m, tid: f"# LEGACY {tid}")

    owner = _Owner()
    ctx, sio = _ctx(owner)
    assert howto._howto_handler("/howto latest", ctx) is True
    out = sio.getvalue()
    assert "corpus synced" not in out
    assert "already current" not in out
    assert "fetching help from GitHub…" in out  # the legacy path's banner
    assert "# LEGACY index" in dict(owner.attached_docs)["howto"]
    assert owner.howto_mode is True


def test_howto_latest_topic_does_not_sync(monkeypatch):
    # `/howto latest <topic>` is untouched: no tarball fetch, per-topic only.
    calls = _stub_network(monkeypatch, _make_tarball(_good_members()))
    monkeypatch.setattr(howto, "load_manifest", lambda **kw: LOCAL_MANIFEST)
    monkeypatch.setattr(howto, "fetch_manifest", lambda: LOCAL_MANIFEST)
    monkeypatch.setattr(howto, "fetch_topic_body", lambda m, tid: f"# REMOTE {tid}")

    owner = _Owner()
    ctx, sio = _ctx(owner)
    assert howto._howto_handler("/howto latest install", ctx) is True
    assert _downloads(calls) == 0
    assert "# REMOTE install" in dict(owner.attached_docs)["howto"]
    assert "corpus synced" not in sio.getvalue()
