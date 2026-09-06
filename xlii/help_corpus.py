"""Help corpus for /howto and /describe — manifest, bundled topics, GitHub fetch.

Source of truth lives in ``docs/help/`` (manifest + topic shards + per-command
expansive docs). The **core tier** is copied into ``xlii/help/`` by
``scripts/bundle_help.py`` so offline ``/howto`` works without network. The
**extended tier** (most of the corpus) is GitHub-sourced so it is always current,
with an etag cache at ``~/.cache/xlii/help/`` that makes it fast and keeps it
working offline after the first fetch.

Three always-current surfaces compose here:
  - ``/describe <cmd>`` — live registry facts (from code) + expansive prose
    (``commands/<cmd>.md``, GitHub/cached) + see-also graph edges.
  - ``/howto <topic>`` — bundled (core) or GitHub/cached (extended) shards.
  - ``/howto latest`` — force a fresh GitHub pull.

Override the git ref with ``XLII_HELP_REF`` (default: manifest's ``ref``).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tarfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BUNDLED_DIR = Path(__file__).resolve().parent / "help"
_DOCS_HELP_DIR = _REPO_ROOT / "docs" / "help"
_FETCH_TIMEOUT = 10  # seconds
_USER_AGENT = "xlii-howto"


# --------------------------------------------------------------------------- #
# Manifest model
# --------------------------------------------------------------------------- #


# Help-bar contract. New topics must declare both or check_docs fails —
# long titles (em-dashes, "covers X") blew the TUI/face dropdown off-screen.
HELP_MENU_GROUPS = ("start", "work", "more")
HELP_MENU_MAX = 22


@dataclass(frozen=True)
class HelpTopic:
    id: str
    title: str
    path: str
    tier: str = "extended"  # "core" (bundled offline) | "extended" (GitHub/cache)
    aliases: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    see_also: tuple[str, ...] = ()  # other topic ids / command names
    menu: str = ""   # short Help-bar label (≤ HELP_MENU_MAX)
    group: str = ""  # start | work | more


@dataclass(frozen=True)
class CommandDoc:
    """An expansive, GitHub-sourced description for a single command (for
    /describe fusion). The live registry stays the source of truth for
    signature/flags; this adds prose + graph edges."""

    name: str
    path: str
    topic: str = ""  # the /howto topic that covers this command in depth
    see_also: tuple[str, ...] = ()


@dataclass(frozen=True)
class HelpManifest:
    version: int
    repo: str
    ref: str
    base_path: str
    topics: dict[str, HelpTopic]
    commands: dict[str, CommandDoc] = field(default_factory=dict)

    def resolve(self, name: str) -> Optional[str]:
        """Map a user token (topic id or alias) to a canonical topic id."""
        key = name.strip().lower().replace("_", "-")
        if not key:
            return None
        if key in self.topics:
            return key
        for tid, topic in self.topics.items():
            if key in topic.aliases:
                return tid
        return None

    def topic_ids(self) -> list[str]:
        return sorted(self.topics)


def _parse_manifest(data: dict[str, Any]) -> HelpManifest:
    raw_topics = data.get("topics") or {}
    topics: dict[str, HelpTopic] = {}
    for tid, spec in raw_topics.items():
        if not isinstance(spec, dict):
            continue
        topics[tid] = HelpTopic(
            id=tid,
            title=str(spec.get("title") or tid),
            path=str(spec.get("path") or f"topics/{tid}.md"),
            tier=str(spec.get("tier") or "extended"),
            aliases=tuple(spec.get("aliases") or ()),
            tags=tuple(spec.get("tags") or ()),
            see_also=tuple(spec.get("see_also") or ()),
            menu=str(spec.get("menu") or "").strip(),
            group=str(spec.get("group") or "").strip(),
        )
    raw_commands = data.get("commands") or {}
    commands: dict[str, CommandDoc] = {}
    for cname, spec in raw_commands.items():
        if not isinstance(spec, dict):
            continue
        commands[cname] = CommandDoc(
            name=cname,
            path=str(spec.get("path") or f"commands/{cname}.md"),
            topic=str(spec.get("topic") or ""),
            see_also=tuple(spec.get("see_also") or ()),
        )
    return HelpManifest(
        version=int(data.get("version") or 1),
        repo=str(data.get("repo") or "xlii-computer/xlii"),
        ref=str(data.get("ref") or "main"),
        base_path=str(data.get("base_path") or "docs/help"),
        topics=topics,
        commands=commands,
    )


# Parsed-manifest cache keyed on (path, mtime_ns, size). The face re-reads the
# manifest on every chrome_state (Help menu rows) — a 60 ms YAML parse per
# posture flip / HUD refresh was most of the "sluggish flip". A changed file
# on disk (dev tree edit, re-bundle) misses the key and re-parses.
_MANIFEST_CACHE: dict[tuple[str, int, int], HelpManifest] = {}


def load_manifest(*, bundled: bool = True) -> HelpManifest:
    """Load manifest from the packaged bundle (default) or docs/help in a dev tree."""
    path = _BUNDLED_DIR / "manifest.yaml"
    if not (bundled and path.is_file()):
        path = _DOCS_HELP_DIR / "manifest.yaml"
        if not path.is_file():
            raise FileNotFoundError("help manifest not found (bundle docs/help first)")
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    hit = _MANIFEST_CACHE.get(key)
    if hit is not None:
        return hit
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("help manifest is not a mapping")
    manifest = _parse_manifest(data)
    _MANIFEST_CACHE.clear()
    _MANIFEST_CACHE[key] = manifest
    return manifest


def help_ref(manifest: HelpManifest) -> str:
    return os.environ.get("XLII_HELP_REF", manifest.ref).strip() or manifest.ref


# --------------------------------------------------------------------------- #
# etag cache  (~/.cache/xlii/help)
# --------------------------------------------------------------------------- #


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "xlii" / "help"


def _etags_path() -> Path:
    return cache_dir() / "etags.json"


def _cache_body_path(url: str) -> Path:
    digest = hashlib.sha256(url.encode()).hexdigest()[:24]
    return cache_dir() / f"{digest}.body"


def _load_etags() -> dict[str, str]:
    try:
        return json.loads(_etags_path().read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_etags(etags: dict[str, str]) -> None:
    try:
        cache_dir().mkdir(parents=True, exist_ok=True)
        _etags_path().write_text(json.dumps(etags, indent=0))
    except OSError:
        # An unwritable cache just means the next fetch revalidates instead of using an etag.
        pass


def _read_cached(url: str) -> Optional[str]:
    try:
        return _cache_body_path(url).read_text()
    except OSError:
        return None


def _write_cached(url: str, body: str, etag: Optional[str]) -> None:
    try:
        cache_dir().mkdir(parents=True, exist_ok=True)
        _cache_body_path(url).write_text(body)
        if etag:
            etags = _load_etags()
            etags[url] = etag
            _save_etags(etags)
    except OSError:
        # An unwritable cache costs a re-fetch next time, nothing more.
        pass


def http_get_cached(url: str) -> tuple[str, str]:
    """GET a URL with an etag-conditional request. Returns (body, source).

    source ∈ {"github", "cache", "cache (offline)"}. Raises only when the fetch
    fails AND nothing is cached.
    """
    etags = _load_etags()
    headers = {"User-Agent": _USER_AGENT}
    if url in etags:
        headers["If-None-Match"] = etags[url]
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=_FETCH_TIMEOUT) as resp:  # noqa: S310
            body = resp.read().decode("utf-8", errors="replace")
            etag = resp.headers.get("ETag")
            _write_cached(url, body, etag)
            return body, "github"
    except HTTPError as e:
        if e.code == 304:  # Not Modified — our cache is current
            cached = _read_cached(url)
            if cached is not None:
                return cached, "cache"
        raise
    except URLError:
        cached = _read_cached(url)
        if cached is not None:
            return cached, "cache (offline)"
        raise


# --------------------------------------------------------------------------- #
# Topic loading: project override → bundle → docs/help (dev) → cache/fetch
# --------------------------------------------------------------------------- #


def _read_file(root: Path, rel_path: str) -> Optional[str]:
    path = root / rel_path
    if path.is_file():
        return path.read_text().strip()
    return None


def _remote_url(manifest: HelpManifest, rel_path: str) -> str:
    ref = help_ref(manifest)
    return (
        f"https://raw.githubusercontent.com/{manifest.repo}/{ref}/"
        f"{manifest.base_path}/{rel_path}"
    )


def load_topic_body(
    manifest: HelpManifest,
    topic_id: str,
    *,
    xli_dir: Optional[Path] = None,
    allow_remote: bool = True,
) -> tuple[str, str]:
    """Resolve a topic body. Returns (body, source).

    Order: project override → bundled (core) → docs/help (dev tree) → etag cache
    → GitHub (extended). ``source`` is one of: "project", "bundled", "docs",
    "cache", "github". Every step before the last is local, so a warmed cache
    (see :func:`sync_corpus_cache`) means this never blocks a turn on the
    network. Raises FileNotFoundError when nothing resolves.
    """
    topic = manifest.topics.get(topic_id)
    if topic is None:
        raise KeyError(topic_id)

    if xli_dir is not None:
        override = _read_file(xli_dir / "help", f"topics/{topic_id}.md")
        if override is not None:
            return override, "project"

    bundled = _read_file(_BUNDLED_DIR, topic.path)
    if bundled is not None:
        return bundled, "bundled"

    docs = _read_file(_DOCS_HELP_DIR, topic.path)
    if docs is not None:
        return docs, "docs"

    url = _remote_url(manifest, topic.path)
    # Cache BEFORE network, not network-with-etag-then-cache: this read happens
    # mid-turn (attaching an extended-tier shard), and http_get_cached always
    # pays a round-trip — a 304 is still a network wait the operator feels. A
    # warmed slot is therefore authoritative here; `/howto latest <topic>`
    # (fetch_topic_body) is the explicit door for "get me the newest copy", and
    # sync_corpus_cache refreshes the whole set off the turn path.
    cached = _read_cached(url)
    if cached is not None:
        return cached.strip(), "cache"

    if allow_remote:
        try:
            body, source = http_get_cached(url)
            return body.strip(), source
        except (HTTPError, URLError, OSError):
            pass
    raise FileNotFoundError(
        f"help topic {topic_id!r} not available offline "
        f"(extended tier — try `/howto latest {topic_id}` with a connection)"
    )


def fetch_topic_body(manifest: HelpManifest, topic_id: str) -> str:
    """Force a fresh GitHub pull of one topic (etag-cached). Raises on failure."""
    topic = manifest.topics[topic_id]
    body, _ = http_get_cached(_remote_url(manifest, topic.path))
    return body.strip()


def fetch_manifest() -> HelpManifest:
    """Fetch manifest.yaml from GitHub (etag-cached). Raises on hard failure."""
    bundled = load_manifest()
    body, _ = http_get_cached(_remote_url(bundled, "manifest.yaml"))
    data = yaml.safe_load(body)
    if not isinstance(data, dict):
        raise ValueError("remote help manifest is not a mapping")
    return _parse_manifest(data)


# --------------------------------------------------------------------------- #
# Whole-corpus tarball sync — one fetch pre-fills every per-URL cache slot
# --------------------------------------------------------------------------- #


_TARBALL_MAX_BYTES = 10 * 1024 * 1024  # compressed download cap (docs/help ~320 KB)
_TARBALL_MEMBER_MAX_BYTES = 1024 * 1024  # per accepted member, decompressed
_TARBALL_TOTAL_MEMBER_BYTES = 32 * 1024 * 1024  # aggregate accepted bytes, decompressed
_TARBALL_MAX_MEMBERS = 2000  # tar entries walked before we call it a bomb


class CorpusSync(NamedTuple):
    """Result of :func:`sync_corpus_cache`."""

    manifest: HelpManifest  # parsed from the tarball's manifest.yaml
    sha: str                # resolved commit
    files: int              # cache slots written (previously recorded count on skip)
    skipped: bool           # True = sha unchanged + cache complete, nothing downloaded


def _corpus_state_path() -> Path:
    return cache_dir() / "corpus.json"


def _load_corpus_state() -> dict[str, Any]:
    try:
        data = json.loads(_corpus_state_path().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _resolve_ref_sha(manifest: HelpManifest) -> str:
    """Resolve the help ref to a commit sha via the GitHub API.

    Rides :func:`http_get_cached` (JSON is text; etag 304s are free against the
    API rate limit). Raises when the sha can't be resolved."""
    ref = help_ref(manifest)
    body, _ = http_get_cached(f"https://api.github.com/repos/{manifest.repo}/commits/{ref}")
    data = json.loads(body)
    sha = data.get("sha") if isinstance(data, dict) else None
    if not isinstance(sha, str) or not sha:
        raise ValueError(f"could not resolve help ref {ref!r} to a commit sha")
    return sha


def _corpus_complete(local: HelpManifest, manifest: HelpManifest) -> bool:
    """True when the manifest itself and every topic/command path of
    ``manifest`` already have a body in the cache.

    ``manifest`` should be the cached REMOTE manifest when one exists — the
    tarball only ever supplies the files that manifest names, so gating on the
    frozen bundled manifest would leave the skip check permanently false the
    moment upstream renames or drops a path. The manifest slot itself is keyed
    by ``local`` (mirroring how :func:`fetch_manifest` bootstraps)."""
    if not _cache_body_path(_remote_url(local, "manifest.yaml")).is_file():
        return False
    rels = [t.path for t in manifest.topics.values()]
    rels += [c.path for c in manifest.commands.values()]
    return all(_cache_body_path(_remote_url(manifest, rel)).is_file() for rel in rels)


def _download_tarball(repo: str, sha: str) -> bytes:
    """Fetch the sha-pinned codeload tarball (immutable URL, not API-rate-limited).

    Binary — deliberately NOT :func:`http_get_cached` (which is text-only). The
    10 MiB hard cap is enforced *during* the chunked read; Content-Length is not
    trusted."""
    url = f"https://codeload.github.com/{repo}/tar.gz/{sha}"
    req = Request(url, headers={"User-Agent": _USER_AGENT})
    chunks: list[bytes] = []
    total = 0
    with urlopen(req, timeout=_FETCH_TIMEOUT) as resp:  # noqa: S310
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > _TARBALL_MAX_BYTES:
                raise ValueError(f"help tarball exceeds the {_TARBALL_MAX_BYTES:,}-byte cap")
            chunks.append(chunk)
    return b"".join(chunks)


def _tarball_bodies(data: bytes, base_path: str) -> dict[str, str]:
    """``{rel_path: body}`` for every safe corpus member of the tarball.

    The archive's variable root directory (``{name}-{sha}`` vs ``{name}-{ref}``)
    is handled by stripping the FIRST path component, never by predicting it.
    Only ``.md``/``.yaml`` members under ``base_path`` are accepted; members
    over 1 MiB are skipped. Bodies are never written to member-derived
    filesystem paths — traversal is impossible by construction — but
    absolute/``..`` members are rejected anyway.

    Decompression-bomb guards (the 10 MiB download cap is on COMPRESSED bytes;
    gzip on zeros expands ~1000:1): the walk raises past ``_TARBALL_MAX_MEMBERS``
    tar entries, and past ``_TARBALL_TOTAL_MEMBER_BYTES`` aggregate accepted
    decompressed bytes — callers degrade to the per-topic path."""
    prefix = base_path.strip("/") + "/"
    bodies: dict[str, str] = {}
    members_seen = 0
    total_bytes = 0
    with tarfile.open(fileobj=BytesIO(data), mode="r:gz") as tar:
        for member in tar:
            members_seen += 1
            if members_seen > _TARBALL_MAX_MEMBERS:
                raise ValueError(
                    f"help tarball has more than {_TARBALL_MAX_MEMBERS:,} members — refusing"
                )
            if not member.isfile():
                continue
            parts = PurePosixPath(member.name).parts
            if member.name.startswith("/") or ".." in parts or len(parts) < 2:
                continue  # defensive: absolute / traversal / rootless members
            rest = "/".join(parts[1:])  # strip the variable archive root
            if not rest.startswith(prefix):
                continue
            rel = rest[len(prefix):]
            if not rel.endswith((".md", ".yaml")):
                continue
            if member.size > _TARBALL_MEMBER_MAX_BYTES:
                continue
            fobj = tar.extractfile(member)
            if fobj is None:
                continue
            raw = fobj.read(_TARBALL_MEMBER_MAX_BYTES + 1)
            if len(raw) > _TARBALL_MEMBER_MAX_BYTES:
                continue  # header understated the size — enforce during read too
            total_bytes += len(raw)
            if total_bytes > _TARBALL_TOTAL_MEMBER_BYTES:
                raise ValueError(
                    f"help tarball decompresses past the "
                    f"{_TARBALL_TOTAL_MEMBER_BYTES:,}-byte aggregate cap — refusing"
                )
            bodies[rel] = raw.decode("utf-8", errors="replace")
    return bodies


def _cached_remote_manifest(local: HelpManifest) -> Optional[HelpManifest]:
    """Parse the manifest from its own cache slot (how a *skipped* sync still
    returns a corpus-accurate manifest). None when absent or corrupt."""
    cached = _read_cached(_remote_url(local, "manifest.yaml"))
    if cached is None:
        return None
    try:
        data = yaml.safe_load(cached)
    except yaml.YAMLError:
        return None
    return _parse_manifest(data) if isinstance(data, dict) else None


def sync_corpus_cache(*, force: bool = False) -> CorpusSync:
    """One conditional fetch that warms the WHOLE help-corpus cache.

    Purely a cache-warming transport: every body lands in the exact per-URL
    slot (``_cache_body_path(_remote_url(...))``) the per-topic readers already
    consult on 304s and network failure — no read path changes. Slot keys are
    manifest-following, mirroring today's per-topic flow: the manifest body is
    keyed by the bundled manifest's URL (exactly where :func:`fetch_manifest`
    reads it) and every other body by the tarball's OWN parsed manifest — the
    one this function returns and post-sync readers resolve through.
    ``corpus.json`` (``{"ref", "sha", "files"}``) is written LAST, only after
    every slot landed, so a corrupt tarball never leaves a "current" marker
    behind. Raises on any failure — callers degrade to the per-topic path."""
    local = load_manifest()
    ref = help_ref(local)
    sha = _resolve_ref_sha(local)

    if not force:
        state = _load_corpus_state()
        if state.get("sha") == sha:
            # Completeness must gate on the cached REMOTE manifest (what the
            # tarball actually supplied), not the frozen bundled one — any
            # upstream rename/drop would otherwise make the skip permanently
            # dead and re-download on every bare `/howto latest`.
            cached = _cached_remote_manifest(local)
            if _corpus_complete(local, cached or local):
                return CorpusSync(
                    manifest=cached or local,
                    sha=sha,
                    files=int(state.get("files") or 0),
                    skipped=True,
                )

    data = _download_tarball(local.repo, sha)
    bodies = _tarball_bodies(data, local.base_path)

    raw_manifest = bodies.get("manifest.yaml")
    if raw_manifest is None:
        raise ValueError("help tarball has no manifest.yaml under base_path")
    parsed = yaml.safe_load(raw_manifest)
    if not isinstance(parsed, dict):
        raise ValueError("help tarball manifest.yaml is not a mapping")
    manifest = _parse_manifest(parsed)

    cache_dir().mkdir(parents=True, exist_ok=True)
    for rel, body in bodies.items():
        key = local if rel == "manifest.yaml" else manifest
        _cache_body_path(_remote_url(key, rel)).write_text(body)
    _corpus_state_path().write_text(json.dumps({"ref": ref, "sha": sha, "files": len(bodies)}))
    return CorpusSync(manifest=manifest, sha=sha, files=len(bodies), skipped=False)


# --------------------------------------------------------------------------- #
# Per-command expansive docs (for /describe fusion)
# --------------------------------------------------------------------------- #


def load_command_doc(
    manifest: HelpManifest,
    name: str,
    *,
    allow_remote: bool = True,
) -> Optional[tuple[str, str]]:
    """Expansive prose for a command, for /describe fusion. Returns (body, source)
    or None when there is no doc / it can't be reached offline. Never raises."""
    spec = manifest.commands.get(name)
    if spec is None:
        return None
    for root, label in ((_BUNDLED_DIR, "bundled"), (_DOCS_HELP_DIR, "docs")):
        body = _read_file(root, spec.path)
        if body is not None:
            return body, label
    if allow_remote:
        try:
            body, source = http_get_cached(_remote_url(manifest, spec.path))
            return body.strip(), source
        except (HTTPError, URLError, OSError):
            cached = _read_cached(_remote_url(manifest, spec.path))
            if cached is not None:
                return cached.strip(), "cache (offline)"
    return None


def see_also_for(manifest: HelpManifest, name: str) -> tuple[str, ...]:
    """Graph edges for a command or topic (whichever matches)."""
    if name in manifest.commands:
        return manifest.commands[name].see_also
    tid = manifest.resolve(name)
    if tid and tid in manifest.topics:
        return manifest.topics[tid].see_also
    return ()


# --------------------------------------------------------------------------- #
# Rendering helpers
# --------------------------------------------------------------------------- #


def render_topic_index(manifest: HelpManifest) -> str:
    """Markdown table of attachable topics (for bare /howto). Marks extended
    (GitHub) topics so offline users know they need `/howto latest`."""
    lines = [
        "## Help topics\n",
        (
            "Attach one with `/howto <topic>`. Go deep on any *command* with "
            "`/describe <name>` (live + always-current GitHub detail).\n"
        ),
        "| Topic | Command | Source |",
        "| --- | --- | --- |",
    ]
    for tid in manifest.topic_ids():
        if tid == "index":
            continue
        topic = manifest.topics[tid]
        src = "bundled" if topic.tier == "core" else "GitHub (`/howto latest`)"
        lines.append(f"| {topic.title} | `/howto {tid}` | {src} |")
    lines.append("")
    lines.append("`/howto latest <topic>` always pulls the newest copy from GitHub.")
    return "\n".join(lines)


def format_topic_attachment(
    manifest: HelpManifest,
    topic_id: str,
    body: str,
    *,
    source: str,
) -> str:
    topic = manifest.topics[topic_id]
    header = f"## Help: {topic.title}"
    if source not in ("bundled", "project"):
        header += f" ({source})"
    footer = ""
    if topic.see_also:
        footer = "\n\n[dim]See also:[/dim] " + ", ".join(
            f"/howto {s}" if s in manifest.topics else f"/describe {s}"
            for s in topic.see_also
        )
    return f"{header}\n\n{body.strip()}{footer}"


def list_topic_names(manifest: HelpManifest) -> list[str]:
    """All topic ids plus aliases — for error messages."""
    names: set[str] = set(manifest.topics)
    for topic in manifest.topics.values():
        names.update(topic.aliases)
    return sorted(names)


# --------------------------------------------------------------------------- #
# Corpus search  (seam #2 — the public API /apropos is client #1 of)
# --------------------------------------------------------------------------- #


class SearchHit(NamedTuple):
    """One ranked hit from :func:`search_corpus`. It is a plain 4-tuple
    ``(kind, name, score, description)`` so callers can unpack it positionally;
    higher ``score`` ranks first.

    - ``kind``: ``"command"`` (a live slash command) or ``"topic"`` (a help shard).
    - ``name``: the command name or topic id (address it with ``/<name>`` or
      ``/howto <name>`` respectively).
    - ``score``: relevance, higher is better. Opaque magnitude; only the order
      and the >0 threshold are contractual.
    - ``description``: a one-line blurb (command description or topic title).
    """

    kind: str
    name: str
    score: int
    description: str


_WORD_RE = re.compile(r"[^\w-]+")


def _score_candidate(query: str, words: list[str], name: str, parts: tuple[str, ...]) -> int:
    """Keyword relevance of one candidate (higher is better, 0 = no match).

    Whole-query matches on the *name* dominate; per-word coverage over every
    searchable part adds up; a fuzzy subsequence fallback (reusing the kernel's
    ``_best_fuzzy``) catches typos only when nothing else matched.
    """
    name_l = name.lower()
    parts_l = [p.lower() for p in parts if p]
    score = 0
    if name_l == query:
        score += 100
    elif name_l.startswith(query):
        score += 60
    elif query in name_l:
        score += 40
    for w in words:
        if w == name_l:
            score += 30
        elif w in name_l:
            score += 15
        elif any(w == p for p in parts_l):
            score += 12
        elif any(w in p for p in parts_l):
            score += 8
    if score == 0:
        try:
            from xlii.fuzzy import _best_fuzzy
        except Exception:
            return 0
        raw = _best_fuzzy(query, name, " ".join(parts_l))
        if raw >= 0:
            score = max(1, 12 - raw)  # tighter subsequence → higher (small) score
    return score


def search_corpus(manifest: HelpManifest, query: str) -> list["SearchHit"]:
    """Keyword search over help topics + live slash commands (seam #2).

    Returns :class:`SearchHit` tuples ranked by descending relevance. Topics come
    from ``manifest``; commands come from the live registry (the same source
    ``/describe`` trusts), so results are always accurate for *this* build. Pure
    and side-effect-free; an empty/blank query yields ``[]``.
    """
    q = (query or "").strip().lower()
    if not q:
        return []
    words = [w for w in _WORD_RE.split(q) if w]
    hits: list[SearchHit] = []

    for tid, topic in manifest.topics.items():
        if tid == "index":
            continue
        parts = (tid, topic.title, *topic.aliases, *topic.tags)
        score = _score_candidate(q, words, tid, parts)
        if score > 0:
            hits.append(SearchHit("topic", tid, score, topic.title))

    # Live slash commands — registry is the source of truth (may be empty in a
    # bare import; never let a registry hiccup break corpus search).
    try:
        from xlii.commands import iter_repl_commands

        seen: set[str] = set()
        for cmd in iter_repl_commands():
            if cmd.name in seen:
                continue
            seen.add(cmd.name)
            parts = (cmd.name, *cmd.aliases, cmd.description or "")
            score = _score_candidate(q, words, cmd.name, parts)
            if score > 0:
                hits.append(SearchHit("command", cmd.name, score, cmd.description or ""))
    except Exception as exc:
        # Best-effort command enrichment: keep topic search working even if the
        # live command registry is unavailable or errors during iteration.
        _ = exc

    hits.sort(key=lambda h: (-h.score, h.kind, h.name))
    return hits
