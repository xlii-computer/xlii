"""Data providers as manifests + ONE runner (typed-workbenches.md, phase S0).

The supply chain for typed workbenches: a *provider* is a declarative TOML
manifest — endpoints, auth slot, params, response shaping, pagination — and
this module is the single engine that executes any of them::

    resolve manifest → inject auth → fetch → shape → store to the project VFS

Results land under ``.xlii/provider-results/<name>/`` (``latest.json`` plus a
stamped copy), inside the project root so they are browsable via addressing
(``project://`` / ``file://``), where panes and the agent can see them. No UI
at S0 — the proofs are the manifests and the runner; quota chips and BYO
authoring flows are S2.

Manifests come from two places, merged by name (project wins — the
``workbench.toml``/``aliases.toml`` precedent), with one credential exception:
project manifests may not shadow a builtin in a way that can make a familiar
provider name send secrets to another host. That means credential-bearing
builtins cannot be shadowed, and auth-bearing project manifests must use a new
provider name rather than a builtin name.

- ``xlii/provider_manifests/*.toml`` — the builtin, shipped set.
- ``.xlii/providers/*.toml`` — project-level bring-your-own.

Manifest sketch::

    name = "hn-algolia"
    version = 1
    summary = "HackerNews search"
    category = "news"
    required_params = ["query"]
    # [defaults]                       # optional param defaults, run params win
    # vs = "usd"

    [request]
    method = "GET"                                 # GET or POST (flat JSON body)
    url = "https://hn.algolia.com/api/v1/search"   # {name} placeholders work
                                                   # in the URL path too

    [request.params]
    query = "{query}"          # {name} placeholders filled from run params
    hitsPerPage = "25"

    # [request.headers]        # optional static headers (e.g. User-Agent)
    # User-Agent = "my-tool/1.0"

    # method = "POST" + [request.body] for POST providers (cohort 2): body
    # values use the same {name} holes; a hole not covered by required_params
    # is OPTIONAL — omitted from the JSON body when unfilled.

    [auth]                     # optional; omit for no-auth providers
    kind = "header"            # "header" | "query"
    key_env = "SOME_API_KEY"   # the env var NAME — never the value
    header = "x-api-key"       # kind=header (or: param = "api_key" for kind=query)
    # prefix = "Token "        # kind=header value prefix ("Token ", "Bearer ")
    required = false           # required=true refuses to run unconfigured

    [pagination]               # optional
    kind = "page"
    param = "page"
    start = 0
    max_pages = 3

    [quota]                    # optional, advisory
    daily = 500                # the account's declared daily budget
    # bucket = "SOME_API_KEY"  # ledger key; defaults to auth.key_env, so
                               # manifests sharing a key share the counter

    [shape]
    records = "hits"           # dotted path to the record list; "" = root
    [shape.fields]             # optional projection; omit = keep whole records
    title = "title"
    url = "url"

The locker read path (S0): a key is named by env var and read from the
process environment at fetch time — the repo's own convention
(``XAI_MANAGEMENT_API_KEY`` et al). The key value is never written to disk,
never stored in results, never logged; "configured / not configured" (the
argus ``[set]`` rule) is all any surface ever sees. A real credential store
behind the same read path is a later S-phase refinement.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

BUILTIN_DIR = Path(__file__).parent / "provider_manifests"
PROJECT_MANIFEST_DIR = "providers"  # under .xlii/
RESULTS_DIR = "provider-results"  # under .xlii/

AUTH_KINDS = ("header", "query")
PAGE_KINDS = ("none", "page")
METHODS = ("GET", "POST")

SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
"""A provider name is also a directory name under .xlii/provider-results/."""

FetchJson = Callable[..., Any]
"""Transport seam: (method, url, *, params, headers[, json_body]) -> parsed
JSON body. ``json_body`` is passed only for POST manifests — GET-era fakes
keep their signature."""


class ProviderError(Exception):
    """A clean, user-presentable provider failure (never carries secrets)."""


@dataclass(frozen=True)
class Auth:
    kind: str = ""  # "" = no auth
    key_env: str = ""
    header: str = ""
    param: str = ""
    prefix: str = ""  # value prefix for kind=header ("Token ", "Bearer ")
    required: bool = False


@dataclass(frozen=True)
class Pagination:
    kind: str = "none"
    param: str = "page"
    start: int = 0
    max_pages: int = 1


@dataclass(frozen=True)
class Quota:
    """Advisory daily-call budget (S2 — the argus lesson: rate limits must
    never look like breakage). The runner RECORDS every fetch and reports
    "used/limit today"; it never refuses — the provider's own 429 is the
    hard wall, this is the dashboard."""
    daily: int = 0  # 0 = no declared limit
    bucket: str = ""  # shared ledger key; "" = derive (see quota_bucket)


USAGE_FILE = "provider-usage.json"  # under .xlii/


def _usage_path(xli_dir: Path) -> Path:
    return Path(xli_dir) / USAGE_FILE


def record_usage(xli_dir: Path, name: str, *, n: int = 1, today: str = "") -> None:
    """Count ``n`` fetches against a bucket's day. Best-effort — a ledger
    hiccup must never fail a run, so a corrupt file (bad JSON *or* a valid
    file of the wrong shape) is treated as an empty ledger and rewritten."""
    try:
        from xlii.atomicio import write_text_atomic

        day = today or datetime.now(timezone.utc).date().isoformat()
        path = _usage_path(xli_dir)
        lock_path = path.with_suffix(path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            data: dict = {}
            if path.is_file():
                try:
                    loaded = json.loads(path.read_text())
                except (json.JSONDecodeError, OSError):
                    loaded = None
                if isinstance(loaded, dict):
                    data = loaded
            day_counts = data.get(name)
            if not isinstance(day_counts, dict):
                day_counts = {}
                data[name] = day_counts
            try:
                previous = int(day_counts.get(day, 0))
            except (TypeError, ValueError):
                previous = 0
            day_counts[day] = previous + n
            # prune to the last 14 days per bucket — the ledger is a dashboard,
            # not an archive
            for stale in sorted(day_counts, key=str)[:-14]:
                del day_counts[stale]
            write_text_atomic(path, json.dumps(data, indent=2) + "\n")
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    except (OSError, TypeError, ValueError, AttributeError):
        # Best-effort ledger update: usage tracking must never fail a run.
        return


def usage_today(xli_dir: Optional[Path], name: str, *, today: str = "") -> int:
    """Fetches recorded for the bucket today (0 when no/unreadable ledger)."""
    if xli_dir is None:
        return 0
    try:
        data = json.loads(_usage_path(xli_dir).read_text())
        if not isinstance(data, dict):
            return 0
        day_counts = data.get(name)
        if not isinstance(day_counts, dict):
            return 0
        day = today or datetime.now(timezone.utc).date().isoformat()
        return int(day_counts.get(day, 0))
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class ProviderManifest:
    """One provider manifest — pure data, no behavior."""

    name: str
    version: int = 1
    summary: str = ""
    category: str = "general"
    method: str = "GET"
    url: str = ""
    request_params: Mapping[str, str] = field(default_factory=dict)
    request_headers: Mapping[str, str] = field(default_factory=dict)  # static
    body: Mapping[str, Any] = field(default_factory=dict)  # POST: JSON body template
    required_params: tuple[str, ...] = ()
    defaults: Mapping[str, str] = field(default_factory=dict)
    auth: Auth = field(default_factory=Auth)
    pagination: Pagination = field(default_factory=Pagination)
    records_path: str = ""
    fields: Mapping[str, str] = field(default_factory=dict)
    quota: Quota = field(default_factory=Quota)
    source: str = "builtin"  # "builtin" | "project"


@dataclass
class RunResult:
    """What one run produced (also the shape of the stored JSON, minus records echo)."""

    provider: str
    fetched_at: str
    params: Mapping[str, Any]
    pages: int
    count: int
    records: list
    latest_path: Optional[Path] = None
    stamped_path: Optional[Path] = None
    quota_note: str = ""  # "47/500 today" (S2) — "" when no limit declared


# --- loading -----------------------------------------------------------------


def _parse_manifest(data: dict, source: str) -> Optional[ProviderManifest]:
    """Dict → manifest, or None when malformed (a bad file degrades, never bricks)."""
    try:
        name = str(data["name"]).strip()
        url = str(data["request"]["url"]).strip()
        if not name or not url:
            return None
        raw_auth = data.get("auth") or {}
        auth = Auth(
            kind=str(raw_auth.get("kind", "")).strip(),
            key_env=str(raw_auth.get("key_env", "")).strip(),
            header=str(raw_auth.get("header", "")).strip(),
            param=str(raw_auth.get("param", "")).strip(),
            prefix=str(raw_auth.get("prefix", "")),
            required=bool(raw_auth.get("required", False)),
        )
        raw_page = data.get("pagination") or {}
        pagination = Pagination(
            kind=str(raw_page.get("kind", "none")).strip() or "none",
            param=str(raw_page.get("param", "page")).strip() or "page",
            start=int(raw_page.get("start", 0)),
            max_pages=int(raw_page.get("max_pages", 1)),
        )
        raw_shape = data.get("shape") or {}
        raw_req = data["request"]
        return ProviderManifest(
            name=name,
            version=int(data.get("version", 1)),
            summary=str(data.get("summary", "")),
            category=str(data.get("category", "general")),
            method=str(raw_req.get("method", "GET")).upper(),
            url=url,
            request_params={str(k): str(v) for k, v in (raw_req.get("params") or {}).items()},
            request_headers={str(k): str(v) for k, v in (raw_req.get("headers") or {}).items()},
            body={str(k): v for k, v in (raw_req.get("body") or {}).items()},
            required_params=tuple(str(p) for p in data.get("required_params", ())),
            defaults={str(k): str(v) for k, v in (data.get("defaults") or {}).items()},
            auth=auth,
            pagination=pagination,
            records_path=str(raw_shape.get("records", "")),
            fields={str(k): str(v) for k, v in (raw_shape.get("fields") or {}).items()},
            quota=Quota(
                daily=int((data.get("quota") or {}).get("daily", 0) or 0),
                bucket=str(
                    (data.get("quota") or {}).get("bucket", "")
                    or (data.get("quota") or {}).get("key", "")
                    or ""
                ).strip(),
            ),
            source=source,
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def _load_dir(path: Path, source: str) -> dict[str, ProviderManifest]:
    out: dict[str, ProviderManifest] = {}
    if not path.is_dir():
        return out
    for f in sorted(path.glob("*.toml")):
        try:
            data = tomllib.loads(f.read_text())
        except (tomllib.TOMLDecodeError, OSError):
            continue
        m = _parse_manifest(data, source)
        if m is not None:
            out[m.name] = m
    return out


# The shipped set is re-read on every face chrome_state (provider readiness
# chips) — ~13 ms of TOML per HUD refresh. Cache it on the directory's file
# signature; the project dir stays uncached (user edits land immediately).
_BUILTIN_CACHE: tuple[tuple, dict[str, ProviderManifest]] | None = None


def _builtin_manifests() -> dict[str, ProviderManifest]:
    global _BUILTIN_CACHE
    if not BUILTIN_DIR.is_dir():
        return {}
    sig = tuple(
        (f.name, st.st_mtime_ns, st.st_size)
        for f in sorted(BUILTIN_DIR.glob("*.toml"))
        for st in (f.stat(),)
    )
    if _BUILTIN_CACHE is not None and _BUILTIN_CACHE[0] == sig:
        return dict(_BUILTIN_CACHE[1])
    loaded = _load_dir(BUILTIN_DIR, "builtin")
    _BUILTIN_CACHE = (sig, loaded)
    return dict(loaded)


def load_manifests(xli_dir: Optional[Path] = None) -> dict[str, ProviderManifest]:
    """Builtin manifests merged with the project's ``.xlii/providers/*.toml``.

    Project rows win by name — except when a builtin/provider-name collision
    could route credentials under a familiar name. A project file may not shadow
    a credential-bearing builtin, and it may not add its own auth slot while
    shadowing an unkeyed builtin. Malformed files are skipped — user-authored
    data must degrade, never break the session."""
    registry = _builtin_manifests()
    if xli_dir is not None:
        for name, m in _load_dir(Path(xli_dir) / PROJECT_MANIFEST_DIR, "project").items():
            builtin = registry.get(name)
            if builtin is not None and (builtin.auth.key_env or m.auth.kind or m.auth.key_env):
                continue  # familiar builtin names must not become credential exfil paths
            registry[name] = m
    return registry


def get_manifest(name: str, xli_dir: Optional[Path] = None) -> Optional[ProviderManifest]:
    return load_manifests(xli_dir).get(name.strip())


def quota_bucket(m: ProviderManifest) -> str:
    """The ledger key a run counts against.

    A daily limit belongs to the *account*, not the manifest: the three
    alpha-vantage manifests share one ``ALPHA_VANTAGE_API_KEY`` and one
    25/day budget, so twenty calls split across two of them is 20/25 once,
    not 20/25 twice. The bucket is an explicit ``[quota] bucket`` when a
    manifest declares one, else the auth env var name (the account identity
    the substrate already knows), else the manifest's own name."""
    return m.quota.bucket or m.auth.key_env or m.name


def validate_manifest(m: ProviderManifest) -> list[str]:
    """Integrity problems in one manifest (empty = sound), as readable strings."""
    problems: list[str] = []
    if not m.name:
        problems.append("manifest needs a name")
    elif not SAFE_NAME.fullmatch(m.name):
        problems.append(
            f"{m.name!r}: name must be one safe segment ([A-Za-z0-9][A-Za-z0-9_-]*) "
            f"— it is also a results directory name"
        )
    if not m.url.startswith(("http://", "https://")):
        problems.append(f"{m.name!r}: url must be http(s)")
    if m.method not in METHODS:
        problems.append(
            f"{m.name!r}: method {m.method!r} unsupported "
            f"({'|'.join(METHODS)})"
        )
    if m.method == "GET" and m.body:
        problems.append(f"{m.name!r}: a [request.body] needs method = \"POST\"")
    if m.auth.kind:
        if m.auth.kind not in AUTH_KINDS:
            problems.append(f"{m.name!r}: auth.kind {m.auth.kind!r} not one of {list(AUTH_KINDS)}")
        if not m.auth.key_env:
            problems.append(f"{m.name!r}: auth needs key_env (the env var NAME)")
        if m.auth.kind == "header" and not m.auth.header:
            problems.append(f"{m.name!r}: auth.kind=header needs a header name")
        if m.auth.kind == "query" and not m.auth.param:
            problems.append(f"{m.name!r}: auth.kind=query needs a param name")
    if m.pagination.kind not in PAGE_KINDS:
        problems.append(f"{m.name!r}: pagination.kind {m.pagination.kind!r} not one of {list(PAGE_KINDS)}")
    if m.pagination.max_pages < 1:
        problems.append(f"{m.name!r}: pagination.max_pages must be >= 1")
    return problems


def validate_all(xli_dir: Optional[Path] = None) -> list[str]:
    out: list[str] = []
    for m in load_manifests(xli_dir).values():
        out.extend(validate_manifest(m))
    return out


# --- runner -------------------------------------------------------------------


def _dig(obj: Any, path: str) -> Any:
    """Dotted-path lookup ("a.b.0.c"); "" returns obj itself."""
    if not path:
        return obj
    cur = obj
    for part in path.split("."):
        if isinstance(cur, Mapping):
            cur = cur[part]
        elif isinstance(cur, list):
            cur = cur[int(part)]
        else:
            raise KeyError(path)
    return cur


def _dig_or_none(obj: Any, path: str) -> Any:
    """Lenient projection lookup — a field an API omits is None, not a crash."""
    try:
        return _dig(obj, path)
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _fill(template: str, run_params: Mapping[str, Any], mname: str) -> str:
    """Fill {name} placeholders in one template; unfilled = a clean error."""
    rendered = template
    for pk, pv in run_params.items():
        rendered = rendered.replace("{" + pk + "}", str(pv))
    if "{" in rendered:
        raise ProviderError(f"provider {mname!r}: unfilled placeholder in {template!r}")
    return rendered


def _render_params(m: ProviderManifest, run_params: Mapping[str, Any]) -> tuple[str, dict[str, str]]:
    """Fill {name} placeholders — in the URL path *and* the request params —
    from run params. Returns (rendered_url, rendered_params)."""
    missing = [p for p in m.required_params if p not in run_params]
    if missing:
        raise ProviderError(
            f"provider {m.name!r} needs params: {', '.join(missing)} "
            f"(required: {', '.join(m.required_params) or '—'})"
        )
    return _fill(m.url, run_params, m.name), {
        k: _fill(v, run_params, m.name) for k, v in m.request_params.items()
    }


def _apply_auth(
    m: ProviderManifest,
    params: dict[str, str],
    headers: dict[str, str],
    environ: Mapping[str, str],
) -> None:
    """Inject the key from the environment. The value touches the live request
    only — it is never returned, stored, or included in any error."""
    auth = m.auth
    if not auth.kind:
        return
    key = environ.get(auth.key_env, "").strip()
    if not key:
        if auth.required:
            raise ProviderError(
                f"provider {m.name!r} is not configured — set {auth.key_env} "
                f"in the environment (the locker read path: env var, never a file)"
            )
        return  # optional key, run unauthenticated
    if auth.kind == "header":
        headers[auth.header] = auth.prefix + key
    else:
        params[auth.param] = key


def _requests_fetch(method: str, url: str, *, params: dict, headers: dict,
                    json_body: Optional[dict] = None) -> Any:
    """The stock transport (lazy import — the cmds/notify.py pattern).

    Every transport failure is normalized to :class:`ProviderError` so the
    surfaces render one clean line instead of a traceback. The messages carry
    the status and the exception *type* only — never the URL, params, or
    headers, which hold the injected key."""
    import requests

    try:
        resp = requests.request(method, url, params=params, headers=headers,
                                json=json_body, timeout=20)
    except requests.exceptions.Timeout:
        raise ProviderError("provider timed out after 20s") from None
    except requests.exceptions.RequestException as e:
        raise ProviderError(f"could not reach the provider ({type(e).__name__})") from None
    if resp.status_code >= 400:
        hint = {
            401: " — the key is missing or rejected",
            403: " — the key is not entitled to this endpoint",
            429: " — rate limited; the daily/minute budget is spent, try later",
        }.get(resp.status_code, " — the key may be missing, or the endpoint may have moved")
        raise ProviderError(f"provider refused the request (HTTP {resp.status_code}){hint}")
    try:
        return resp.json()
    except requests.exceptions.JSONDecodeError:
        raise ProviderError(
            f"response was not JSON (HTTP {resp.status_code}, "
            f"{resp.headers.get('content-type', '?')}) — the API may have "
            f"moved or be HTML-gated"
        ) from None


def _records_from(body: Any, records_path: str) -> list:
    try:
        node = _dig(body, records_path)
    except (KeyError, IndexError, TypeError, ValueError):
        raise ProviderError(
            f"no records at {records_path or '<root>'!r} in the response "
            f"— the manifest's shape.records does not match this API"
        ) from None
    if isinstance(node, list):
        return node
    if isinstance(node, Mapping):
        return [node]
    raise ProviderError(f"records at {records_path or '<root>'!r} are not a list/object")


def run_provider(
    name: str,
    xli_dir: Optional[Path] = None,
    params: Optional[Mapping[str, Any]] = None,
    *,
    fetch_json: Optional[FetchJson] = None,
    environ: Optional[Mapping[str, str]] = None,
    store: bool = True,
) -> RunResult:
    """Execute one provider manifest: auth → fetch (pages) → shape → store.

    ``fetch_json`` and ``environ`` are the test seams; production uses
    requests and os.environ. With ``store=False`` nothing touches disk.
    """
    m = get_manifest(name, xli_dir)
    if m is None:
        valid = ", ".join(sorted(load_manifests(xli_dir)))
        raise ProviderError(f"unknown provider {name!r} — available: {valid}")
    problems = validate_manifest(m)
    if problems:
        raise ProviderError(f"provider {m.name!r} manifest is unsound: {'; '.join(problems)}")

    run_params = dict(params or {})
    env = os.environ if environ is None else environ
    fetch = _requests_fetch if fetch_json is None else fetch_json

    eff_params = {**m.defaults, **run_params}
    url, req_params = _render_params(m, eff_params)
    headers: dict[str, str] = dict(m.request_headers)
    _apply_auth(m, req_params, headers, env)
    json_body = None
    if m.method == "POST" and m.body:
        # Body holes not covered by required_params are OPTIONAL (the argus
        # `params.x || undefined` idiom): omit the key when unfilled.
        json_body = {}
        for k, v in m.body.items():
            if isinstance(v, str):
                rendered = v
                for pk, pv in eff_params.items():
                    rendered = rendered.replace("{" + pk + "}", str(pv))
                if "{" not in rendered:
                    json_body[k] = rendered
            elif isinstance(v, (bool, int, float)):
                json_body[k] = v
            else:
                raise ProviderError(
                    f"provider {m.name!r}: request.body {k!r} must be a string/number/bool"
                )

    page = m.pagination
    n_pages = 1 if page.kind == "none" else page.max_pages
    records: list = []
    pages_fetched = 0
    bucket = quota_bucket(m)
    try:
        for i in range(n_pages):
            call_params = dict(req_params)
            if page.kind == "page":
                call_params[page.param] = str(page.start + i)
            # count the ATTEMPT, before the call: a request that times out or
            # 429s has already spent its slice of the budget, and the ledger
            # must not under-report exactly when a provider starts refusing.
            pages_fetched += 1
            if m.method == "POST":
                body = fetch(m.method, url, params=call_params, headers=headers,
                             json_body=json_body)
            else:
                body = fetch(m.method, url, params=call_params, headers=headers)
            batch = _records_from(body, m.records_path)
            if not batch:
                break  # an empty page is the natural stop
            records.extend(batch)
    finally:
        # the ledger is written even when a later page fails — a partial run
        # still consumed quota (and record_usage is best-effort by contract).
        if xli_dir is not None and pages_fetched:
            record_usage(xli_dir, bucket, n=pages_fetched)

    if m.fields:
        records = [
            {out_key: _dig_or_none(rec, path) for out_key, path in m.fields.items()}
            for rec in records
        ]

    result = RunResult(
        provider=m.name,
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        params=eff_params,
        pages=pages_fetched,
        count=len(records),
        records=records,
    )
    if xli_dir is not None and m.quota.daily > 0:
        used = usage_today(xli_dir, bucket)
        note = f"{used}/{m.quota.daily} today"
        result.quota_note = (
            f"⚠ over daily limit: {note}" if used > m.quota.daily else note
        )
    if store:
        if xli_dir is None:
            raise ProviderError("store=True needs xli_dir (the project state home)")
        result.latest_path, result.stamped_path = _store_result(xli_dir, result)
    return result


def _store_result(xli_dir: Path, result: RunResult) -> tuple[Path, Path]:
    """Write latest.json + a stamped copy under .xlii/provider-results/<name>/.

    Stored params are the *run* params only — auth material never reaches this
    function (it is injected into the live request, not the record)."""
    from xlii.atomicio import write_text_atomic

    out_dir = Path(xli_dir) / RESULTS_DIR / result.provider
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "provider": result.provider,
            "fetched_at": result.fetched_at,
            "params": result.params,
            "pages": result.pages,
            "count": result.count,
            "records": result.records,
        },
        indent=2,
    ) + "\n"
    stamp = result.fetched_at.replace(":", "").replace("+", "Z")
    stamped = out_dir / f"{stamp}.json"
    latest = out_dir / "latest.json"
    write_text_atomic(stamped, payload)
    write_text_atomic(latest, payload)
    return latest, stamped
