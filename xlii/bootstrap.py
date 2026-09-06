"""One-time bootstrap: provision a swarm of worker API keys via the xAI
Management REST API and persist them into ~/.config/xlii/config.json.

Endpoint shape (verified against the live API):
    POST   https://management-api.x.ai/auth/teams/{teamId}/api-keys   (create)
    GET    https://management-api.x.ai/auth/teams/{teamId}/api-keys   (list)
    DELETE https://management-api.x.ai/auth/api-keys/{keyId}          (NOT team-scoped)
    POST   https://management-api.x.ai/auth/api-keys/{keyId}/rotate
    Authorization: Bearer {management_api_key}

Note the asymmetry: create/list are team-scoped collection routes, but per-key
operations (delete, rotate) live under /auth/api-keys/{id} — the team-scoped
per-key path returns 404 "Not Found".

Team ID discovery:
    1. cfg.team_id (from config) takes precedence.
    2. Otherwise, GET /auth/teams and pick the first/only team.
    3. If neither works, ask the user to set team_id in config.
"""

from __future__ import annotations

import fnmatch
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from xlii.atomicio import write_text_atomic
from xlii.config import GLOBAL_CONFIG_FILE, VAULT_CHATKEYS_NS, GlobalConfig
from xlii.minted import record_key_safe

MANAGEMENT_HOST = "https://management-api.x.ai"

DEFAULT_ACLS = ["api-key:model:*", "api-key:endpoint:*"]
DEFAULT_QPS = 30
DEFAULT_QPM = 2000
DEFAULT_TPM: Optional[int] = None
# Sensible expiry: long enough not to annoy, short enough to cap blast radius.
DEFAULT_EXPIRE_DAYS = 180

# Pacing + retry — provisioning N keys at once gets throttled.
INTER_CREATE_DELAY_SEC = 1.5
MAX_429_RETRIES = 5


def _expire_iso(days: Optional[int]) -> Optional[str]:
    if not days or days <= 0:
        return None
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat().replace("+00:00", "Z")


def extract_api_key_id(resp: dict) -> Optional[str]:
    """Pull the api_key_id (server-side identifier) from a create/list/get response.

    The single owner of the management-API id-field shape (``apiKeyId`` /
    ``api_key_id`` / ``id`` — D4). The strict ``isinstance(str)`` guard is
    deliberate: the deleted face copies accepted any truthy value.
    """
    if not isinstance(resp, dict):
        return None
    for k in ("apiKeyId", "api_key_id", "id"):
        v = resp.get(k)
        if isinstance(v, str) and v:
            return v
    return None


class BootstrapError(RuntimeError):
    pass


class MultipleTeamsError(BootstrapError):
    """Raised when the management key has access to more than one team."""
    def __init__(self, teams: list[dict]):
        self.teams = teams
        names = ", ".join(t.get("name", t.get("team_id", "?")) for t in teams)
        super().__init__(f"multiple teams found ({names}). Set `team_id` explicitly in config.")


def _auth_headers(mgmt_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {mgmt_key}",
        "Content-Type": "application/json",
    }


def _request(method: str, url: str, mgmt_key: str, *, json_body: Optional[dict] = None) -> Any:
    try:
        resp = httpx.request(
            method, url,
            headers=_auth_headers(mgmt_key),
            json=json_body,
            timeout=30.0,
        )
    except httpx.RequestError as e:
        # Unreachable management API used to traceback raw out of `xlii keys
        # list` / discover_team_id — fold transport failures into BootstrapError
        # so every caller prints one line and exits nonzero.
        raise BootstrapError(
            f"management API unreachable ({type(e).__name__}: {e})"
        ) from e
    if resp.status_code >= 400:
        raise BootstrapError(
            f"{method} {url} → {resp.status_code}\n{resp.text[:500]}"
        )
    if not resp.content:
        return None
    try:
        return resp.json()
    except json.JSONDecodeError:
        # Not JSON (e.g. plain text error or HTML) — return raw body so caller can log it
        return resp.text


def discover_team_id(cfg: GlobalConfig) -> str:
    """Resolve the team_id to use for key operations.

    cfg.team_id wins if set. Otherwise fetch GET /auth/teams and pick a team
    (single team → use it; multiple → ask user to set team_id explicitly).
    """
    if cfg.team_id:
        return cfg.team_id
    if not cfg.management_api_key:
        raise BootstrapError("management_api_key not set in config.json")

    data = _request("GET", f"{MANAGEMENT_HOST}/auth/teams", cfg.management_api_key)
    # Tolerate either a list response or an envelope { "teams": [...] }.
    teams = data if isinstance(data, list) else (data or {}).get("teams", [])
    if not teams:
        raise BootstrapError(
            "no teams found via GET /auth/teams. Set `team_id` explicitly in your config."
        )
    if len(teams) > 1:
        raise MultipleTeamsError(teams)
    t = teams[0]
    # xAI Management API uses camelCase; tolerate snake_case too just in case.
    return team_id_of(t)


def create_api_key(
    mgmt_key: str,
    team_id: str,
    name: str,
    *,
    qps: int = DEFAULT_QPS,
    qpm: int = DEFAULT_QPM,
    tpm: Optional[int] = DEFAULT_TPM,
    expire_days: Optional[int] = DEFAULT_EXPIRE_DAYS,
) -> dict:
    """Create one API key with 429 backoff. Returns the response dict containing
    the newly-minted secret (xAI shows it once on creation only) plus the
    server-side api_key_id (needed for rotate/expire/delete later).
    """
    body: dict[str, Any] = {
        "name": name,
        "acls": DEFAULT_ACLS,
        "qps": qps,
        "qpm": qpm,
    }
    if tpm is not None:
        body["tpm"] = str(tpm)
    expire_iso = _expire_iso(expire_days)
    if expire_iso:
        body["expireTime"] = expire_iso

    url = f"{MANAGEMENT_HOST}/auth/teams/{team_id}/api-keys"
    headers = _auth_headers(mgmt_key)

    for attempt in range(MAX_429_RETRIES):
        try:
            resp = httpx.post(url, headers=headers, json=body, timeout=30.0)
        except httpx.RequestError as e:
            raise BootstrapError(
                f"management API unreachable ({type(e).__name__}: {e})"
            ) from e
        if resp.status_code == 429:
            wait = 2 ** attempt + 1
            time.sleep(wait)
            continue
        if resp.status_code >= 400:
            raise BootstrapError(
                f"POST {url} → {resp.status_code}\n{resp.text[:500]}"
            )
        return resp.json() if resp.content else {}
    raise BootstrapError(f"create_api_key {name}: 429 after {MAX_429_RETRIES} retries")


def rotate_api_key(mgmt_key: str, api_key_id: str) -> dict:
    """Rotate a key's secret in place. Returns response (should contain new secret)."""
    return _request(
        "POST",
        f"{MANAGEMENT_HOST}/auth/api-keys/{api_key_id}/rotate",
        mgmt_key,
    )


def update_api_key_expiration(
    mgmt_key: str, team_id: str, api_key_id: str, expire_days: Optional[int]
) -> dict:
    """Update the expireTime on an existing key. None/0 = no expiry."""
    body: dict[str, Any] = {}
    iso = _expire_iso(expire_days)
    if iso:
        body["expireTime"] = iso
    else:
        body["expireTime"] = None
    return _request(
        "PATCH",
        f"{MANAGEMENT_HOST}/auth/teams/{team_id}/api-keys/{api_key_id}",
        mgmt_key,
        json_body=body,
    )


def list_api_keys(mgmt_key: str, team_id: str) -> list[dict]:
    """Every API key for the team, following pagination.

    The management API returns keys under the camelCase `apiKeys` field with a
    `paginationToken` cursor. Earlier code only read `api_keys`/`keys` and made
    a single request, so it parsed ZERO keys on every call — which made
    `xlii keys list` brand every key "not found on xAI" regardless of validity.
    """
    from urllib.parse import quote

    base = f"{MANAGEMENT_HOST}/auth/teams/{team_id}/api-keys"
    out: list[dict] = []
    token: Optional[str] = None
    while True:
        url = base + (f"?paginationToken={quote(token)}" if token else "")
        data = _request("GET", url, mgmt_key)
        if isinstance(data, list):
            return data
        data = data or {}
        page = data.get("apiKeys") or data.get("api_keys") or data.get("keys") or []
        out.extend(page)
        token = data.get("paginationToken") or None
        if not token or not page:
            break
    return out


def delete_api_key(mgmt_key: str, team_id: str, key_id: str) -> None:
    # Per-key delete is NOT team-scoped. The team-scoped path 404s ("Not Found")
    # for every key — including ones that demonstrably exist — so `keys revoke`,
    # `keys prune`, and `bootstrap --revoke` silently deleted nothing. The real
    # route mirrors rotate: /auth/api-keys/{id}. (Confirmed by probing both with
    # a bogus id: team path → "Not Found"; this path → "Cannot find API key".)
    # team_id stays in the signature for call-site compatibility.
    _request("DELETE", f"{MANAGEMENT_HOST}/auth/api-keys/{key_id}", mgmt_key)


def extract_api_key_string(create_response: dict) -> Optional[str]:
    """Pull the actual secret key out of a create response.

    The exact field name isn't documented to me — try the most likely shapes.
    """
    if not isinstance(create_response, dict):
        return None
    for k in ("api_key", "apiKey", "key", "secret", "token", "value"):
        v = create_response.get(k)
        if isinstance(v, str) and v:
            return v
    # nested envelope: { "api_key": { "value": "..." } } or similar
    nested = create_response.get("api_key")
    if isinstance(nested, dict):
        for k in ("value", "secret", "key"):
            v = nested.get(k)
            if isinstance(v, str) and v:
                return v
    return None


# --------------------------------------------------------------------------- #
#  Provision policy (godzilla-mothra B6) — the key-ops brain, consolidated from
#  xlii/cmds/provision/* (D2–D6, D15) so any body (WS head, Tauri sidecar, node
#  daemon) can provision, reconcile, rotate, and prune keys headless. Nothing
#  below prints or prompts: verbs take callbacks (on_event / confirm_cb) and
#  the faces render.
# --------------------------------------------------------------------------- #


def parse_create_response(resp: dict, label: str) -> Optional[dict]:
    """Compose the ``keys[]`` config entry from a create-key API response.

    Single owner of the response → entry mapping (secret + api_key_id +
    expireTime/expire_time camel/snake fallback) that was triplicated verbatim
    across cmds/journal.py, cmds/provision/_keyops.py, and
    cmds/provision/bootstrap.py (D2). Returns None when the response carries no
    usable secret — the caller renders the raw body.
    """
    if not isinstance(resp, dict):
        return None
    secret = extract_api_key_string(resp)
    if not secret:
        return None
    entry: dict[str, Any] = {"api_key": secret, "label": label}
    kid = extract_api_key_id(resp)
    if kid:
        entry["api_key_id"] = kid
    expire_time = resp.get("expireTime") or resp.get("expire_time")
    if expire_time:
        entry["expire_time"] = expire_time
    return entry


def next_free_label(existing_entries: list, label: str) -> str:
    """First collision-free label derived from ``label``.

    One allocator for both create paths (D3) — the two previous ones DIVERGED:
    ``_keyops._create_one_key`` parsed the numeric suffix and kept bumping from
    it, while ``provision/bootstrap._bootstrap_create`` counted up from the
    loop index and gap-filled. With ``worker-10`` already taken they allocated
    differently for the same config (``worker-11`` vs ``worker-1``).

    Kept semantics: continuation, not gap-fill. A number at or below one we
    have seen taken may still exist server-side (server names derive from
    labels and can outlive local config entries after a prune), so re-issuing
    it risks a server-name collision. Requesting ``worker-10`` when it is taken
    yields ``worker-11``, never ``worker-1``.
    """
    def taken(candidate: str) -> bool:
        return any(
            (e.get("label") if isinstance(e, dict) else None) == candidate
            for e in existing_entries
        )

    base = label
    parts = label.rsplit("-", 1)
    n = 0
    if len(parts) == 2:
        try:
            n = int(parts[1])
        except ValueError:
            n = 0
    while taken(label):
        n += 1
        label = f"{base.rsplit('-', 1)[0]}-{n}"
    return label


def provisioned_key_names(label: str) -> tuple[str, str]:
    """Server-side names of an xlii-provisioned key: the current ``xlii-`` form
    plus the legacy ``xli-`` form (keys created before the rename). Single
    owner of a convention previously scattered across 7 face sites (D6)."""
    return (f"xlii-{label}", f"xli-{label}")


def is_provisioned_name(name: str) -> bool:
    """True when a server-side key name looks xlii-provisioned (new or legacy)."""
    return name.startswith(("xlii-", "xli-"))


def require_management_key(cfg: GlobalConfig) -> str:
    """Return the management API key, or raise BootstrapError with the canonical
    remediation text (one owner for what were 6 divergent face gates — D15).
    Faces print the exception and exit 1."""
    if cfg.management_api_key:
        return cfg.management_api_key
    raise BootstrapError(
        "XAI_MANAGEMENT_API_KEY not set — the management key is env-only "
        "(never written to config.json, so it cannot leak via git, backups, or "
        "screen-shares). Add `export XAI_MANAGEMENT_API_KEY=xai-...` to your "
        "shell rc (~/.bashrc, ~/.zshrc), re-source it, and re-run."
    )


def parse_xai_timestamp(ts: Optional[str]) -> Optional[datetime]:
    """Parse an ISO8601 timestamp (xAI uses a trailing 'Z'); None if unparseable."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def has_key_labeled(cfg: GlobalConfig, label: str) -> bool:
    """True when keys[] already holds an entry with exactly this label."""
    return any(isinstance(e, dict) and e.get("label") == label for e in cfg.keys)


def team_id_of(team: dict) -> str:
    """The id of a team dict from the management API (camelCase first, snake
    tolerated) — exported so faces rendering MultipleTeamsError stop knowing
    the API shape."""
    return team.get("teamId") or team.get("team_id") or team.get("id") or ""


def select_keys(cfg: GlobalConfig, label: Optional[str]) -> list[dict]:
    """Return keys[] entries matching `label` (exact), or all dict entries if None."""
    out: list[dict] = []
    for e in cfg.keys:
        if not isinstance(e, dict):
            continue
        if label is None or e.get("label") == label:
            out.append(e)
    return out


def key_is_active(k: dict, active_ids: set, active_names: set) -> bool:
    """A server key belongs to the local pool. Match by id (precise, so stale
    duplicates of a re-created label are NOT protected); fall back to name only
    for local entries that have no stored id."""
    kid = extract_api_key_id(k)
    if kid and kid in active_ids:
        return True
    return (k.get("name") or "") in active_names


def active_pool_sets(cfg_keys: list, server_keys: list[dict]) -> tuple[set, set]:
    """Return ids/names that prune must protect from the local key pool.

    If a local entry's stored id is stale (not present on the server), protect
    the provisioned server name as a conservative fallback. When the stored id
    is present, id matching stays precise so same-name stale duplicates prune.
    """
    server_ids = {kid for k in server_keys if (kid := extract_api_key_id(k))}
    active_ids: set = set()
    active_names: set = set()
    for e in cfg_keys:
        if not isinstance(e, dict):
            continue
        kid = e.get("api_key_id")
        label = e.get("label")
        if kid:
            active_ids.add(kid)
        if label and (not kid or kid not in server_ids):
            active_names.update(provisioned_key_names(label))
    return active_ids, active_names


def select_prune_candidates(
    server_keys: list[dict],
    *,
    active_ids: set,
    active_names: set,
    name_ok,
    older_than_days: Optional[int],
    now,
    include_active: bool,
) -> list[dict]:
    """Pure selection: which server keys should be pruned. Split out of
    _keys_prune so the safety filtering is unit-testable without the network.
    `name_ok(name) -> bool` is the name gate (the default keeps prune scoped to
    xlii-provisioned keys so a shared management key's other keys are safe)."""
    out: list[dict] = []
    for k in server_keys:
        if not include_active and key_is_active(k, active_ids, active_names):
            continue
        if not name_ok(k.get("name") or ""):
            continue
        if older_than_days is not None:
            created = parse_xai_timestamp(k.get("createTime") or k.get("create_time"))
            if created is None or (now - created).days < older_than_days:
                continue
        out.append(k)
    return out


def required_worker_creates(existing: int, workers: int) -> int:
    """How many worker keys must still be created for a target pool of
    ``workers`` workers behind one primary (the setup pool-sizing math)."""
    return max(0, workers - max(0, existing - 1))


@dataclass
class ProvisionResult:
    """Outcome of one key creation (provision_labeled_key). Never printed
    kernel-side; the face maps it to its own success/error lines."""
    ok: bool
    label: str
    name_on_server: str
    entry: Optional[dict] = None
    error_kind: Optional[str] = None  # "api" | "missing_secret"
    error: Optional[str] = None
    raw: Any = None


def provision_labeled_key(
    cfg: GlobalConfig,
    team_id: str,
    *,
    label: str,
    expire_days: Optional[int] = None,
    bump: bool = True,
) -> ProvisionResult:
    """Create one API key on xAI and register it in config.json.

    The single create→parse→append path (was _keyops._create_one_key plus the
    inline copy in cmds/journal.py — D2/D4). ``bump=True`` (pool paths) derives
    a collision-free label via next_free_label; ``bump=False`` uses the exact
    label (dedicated keys like the journal's — idempotency/force policy stays
    with the caller).
    """
    if expire_days is None:
        expire_days = DEFAULT_EXPIRE_DAYS
    if bump:
        label = next_free_label(cfg.keys, label)
    name_on_server = provisioned_key_names(label)[0]
    try:
        resp = create_api_key(cfg.management_api_key, team_id, name_on_server,
                              expire_days=expire_days)
    except BootstrapError as e:
        return ProvisionResult(ok=False, label=label, name_on_server=name_on_server,
                               error_kind="api", error=str(e))
    entry = parse_create_response(resp, label)
    if entry is None:
        return ProvisionResult(ok=False, label=label, name_on_server=name_on_server,
                               error_kind="missing_secret", raw=resp)
    try:
        disk_entry = seal_new_key_entry(entry)
    except Exception as e:
        return ProvisionResult(ok=False, label=label, name_on_server=name_on_server,
                               error_kind="vault", error=str(e))
    append_keys_to_config([disk_entry])
    entry = disk_entry
    kid = entry.get("api_key_id") or extract_api_key_id(entry)
    if kid:
        record_key_safe(key_id=kid, team_id=team_id, label=label)
    return ProvisionResult(ok=True, label=label, name_on_server=name_on_server, entry=entry)


def provision_worker_keys(
    cfg: GlobalConfig,
    team_id: str,
    *,
    prefix: str,
    count: int,
    expire_days: Optional[int],
    force: bool,
    on_event=None,
) -> int:
    """Batch-provision ``count`` keys labeled ``prefix-N`` (was
    cmds/provision/bootstrap._bootstrap_create). Emits progress via
    ``on_event(kind, **payload)``; the face renders. Salvage policy preserved:
    keys created before an API failure are written to config before aborting.
    """
    emit = on_event or (lambda kind, **payload: None)
    existing_pool_size = len(cfg.key_pairs())
    existing_with_prefix = [
        e for e in cfg.keys
        if isinstance(e, dict) and (e.get("label") or "").startswith(f"{prefix}-")
    ]
    if existing_with_prefix and not force:
        emit("prefix_exists", existing=len(existing_with_prefix), prefix=prefix, count=count)
        return 0

    emit("creating", count=count)
    new_entries: list[dict] = []
    new_secrets: list[tuple[str, str]] = []  # (label, secret) for warning print
    for i in range(1, count + 1):
        # Choose a label that doesn't collide with an existing one in config.
        label = next_free_label(list(cfg.keys) + new_entries, f"{prefix}-{i}")
        name_on_server = provisioned_key_names(label)[0]
        try:
            resp = create_api_key(cfg.management_api_key, team_id, name_on_server,
                                  expire_days=expire_days)
        except BootstrapError as e:
            emit("key_failed", label=label, error=str(e), raw=None)
            if new_entries:
                append_keys_to_config(new_entries)
            emit("aborted", saved=len(new_entries))
            return 1
        entry = parse_create_response(resp, label)
        if entry is None:
            emit("key_failed", label=label, error=None, raw=resp)
            if new_entries:
                append_keys_to_config(new_entries)
            emit("aborted", saved=len(new_entries))
            return 1
        try:
            entry = seal_new_key_entry(entry)
        except Exception as e:
            emit("key_failed", label=label, error=f"vault: {e}", raw=None)
            if new_entries:
                append_keys_to_config(new_entries)
            emit("aborted", saved=len(new_entries))
            return 1
        new_entries.append(entry)
        new_secrets.append((label, entry.get("api_key_id") or entry.get("vault_ref") or label))
        kid = entry.get("api_key_id") or extract_api_key_id(entry)
        if kid:
            record_key_safe(key_id=kid, team_id=team_id, label=label)
        emit("key_created", label=label, name_on_server=name_on_server,
             expire_days=expire_days)
        # Pace creates so we don't get throttled batch-wide
        if i < count:
            time.sleep(INTER_CREATE_DELAY_SEC)

    path = append_keys_to_config(new_entries)
    emit("written", count=len(new_entries), path=path,
         pool_total=existing_pool_size + len(new_entries), secrets=new_secrets)
    return 0


def auto_detect_models(
    cfg: GlobalConfig,
    team_id: str,
    *,
    chat_key: Optional[str] = None,
    on_event=None,
) -> None:
    """Query xAI for available models and persist the best pair — unless the
    user already pinned both orchestrator and worker. Events: models_pinned /
    models_discovering / models_unavailable / models_unclassified /
    models_detected."""
    emit = on_event or (lambda kind, **payload: None)
    if cfg.orchestrator_model and cfg.worker_model:
        emit("models_pinned", orchestrator=cfg.orchestrator_model, worker=cfg.worker_model)
        return
    emit("models_discovering")
    # Resolve via key_pairs() so vault-backed refs work (plaintext api_key is
    # gone after `xlii keys migrate`). Fall back to the just-provisioned key.
    try:
        chat_keys = [kp.api_key for kp in cfg.key_pairs() if kp.api_key]
    except RuntimeError:
        chat_keys = []
    if not chat_keys and chat_key:
        chat_keys = [chat_key]
    available = discover_models(cfg.management_api_key, team_id, chat_keys=chat_keys)
    if not available:
        emit("models_unavailable")
        return
    orch, worker = pick_best_models(available)
    if not orch and not worker:
        emit("models_unclassified", count=len(available))
        return
    set_models_in_config(orch, worker, auto_detected=True)
    emit("models_detected", count=len(available), orchestrator=orch, worker=worker)


def run_setup(
    cfg: GlobalConfig,
    *,
    workers: int,
    expire_days: Optional[int],
    force: bool,
    on_event=None,
) -> int:
    """The idempotent first-time setup ritual as a kernel state machine:
    team discovery → pool sizing → primary → model auto-detect → workers.

    Emits ``(kind, **payload)`` events for the face to render and returns the
    exit code. Raises MultipleTeamsError / BootstrapError only from the team
    discovery step (faces render those with their own wording). The
    interactive companion ritual and the closing report stay face-side — they
    prompt and print.
    """
    emit = on_event or (lambda kind, **payload: None)

    # team_id discovery + cache
    team_id = discover_team_id(cfg)
    if not cfg.team_id:
        set_team_id_in_config(team_id)
        emit("team_cached", team_id=team_id)
        cfg = GlobalConfig.load()
    else:
        emit("team_already_cached", team_id=team_id)

    # pool sizing / idempotent skip
    existing = len(cfg.key_pairs())
    if existing >= 1 + workers and not force:
        emit("pool_sufficient", existing=existing)
        return 0

    emit("proceeding")
    if existing == 0:
        # primary first — auto-detect needs a chat key to query with
        emit("creating_primary", expire_days=expire_days)
        result = provision_labeled_key(cfg, team_id, label="primary-1",
                                       expire_days=expire_days)
        if not result.ok:
            emit("key_failed", label=result.label, error=result.error, raw=result.raw)
            return 1
        emit("key_created", label=result.label, name_on_server=result.name_on_server,
             expire_days=expire_days)
        cfg = GlobalConfig.load()
        # The just-minted primary secret is the fallback query key (its
        # plaintext rides the entry we just wrote, no raw config re-read).
        chat_key = result.entry.get("api_key") if result.entry else None
        auto_detect_models(cfg, team_id, chat_key=chat_key, on_event=on_event)
        cfg = GlobalConfig.load()

    needed = required_worker_creates(existing, workers)
    if needed:
        emit("creating_workers", count=needed, expire_days=expire_days)
        for i in range(1, needed + 1):
            result = provision_labeled_key(cfg, team_id, label=f"worker-{i}",
                                           expire_days=expire_days)
            if not result.ok:
                emit("key_failed", label=result.label, error=result.error, raw=result.raw)
                return 1
            emit("key_created", label=result.label, name_on_server=result.name_on_server,
                 expire_days=expire_days)
            cfg = GlobalConfig.load()
            if i < needed:
                time.sleep(INTER_CREATE_DELAY_SEC)
    return 0


@dataclass
class LocalKeyStatus:
    """One local keys[] entry reconciled against the server pool (`keys list`).
    Pure data — the face owns colors/markup."""
    label: str
    found: bool
    name: Optional[str] = None
    expire_str: str = ""
    days_left: Optional[int] = None
    parse_failed: bool = False
    disabled: bool = False


def reconcile_local_keys(
    cfg_keys: list,
    remote_keys: list[dict],
    now: Optional[datetime] = None,
) -> list[LocalKeyStatus]:
    """Match local keys[] entries to server keys: by stored id (precise), else
    by provisioned server name (``xlii-`` then legacy ``xli-``) when no id is
    stored locally. Extracted from keys.py's _keys_list."""
    by_id = {extract_api_key_id(k): k for k in remote_keys}
    by_name = {k.get("name"): k for k in remote_keys}
    if now is None:
        now = datetime.now(timezone.utc)
    out: list[LocalKeyStatus] = []
    for entry in cfg_keys:
        if not isinstance(entry, dict):
            continue
        label = entry.get("label", "?")
        kid = entry.get("api_key_id")
        if kid:
            rk = by_id.get(kid)
        else:
            new_name, legacy_name = provisioned_key_names(label)
            rk = by_name.get(new_name) or by_name.get(legacy_name)
        if rk is None:
            out.append(LocalKeyStatus(label=label, found=False))
            continue
        exp_str = rk.get("expireTime") or entry.get("expire_time") or ""
        expires = parse_xai_timestamp(exp_str) if exp_str else None
        out.append(LocalKeyStatus(
            label=label,
            found=True,
            name=rk.get("name", "?"),
            expire_str=exp_str,
            days_left=(expires - now).days if expires is not None else None,
            parse_failed=bool(exp_str) and expires is None,
            disabled=bool(rk.get("disabled")),
        ))
    return out


def seal_new_key_entry(entry: dict, *, vault=None) -> dict:
    """Move a freshly provisioned ``api_key`` into the vault; return a
    ``vault_ref`` entry with the secret stripped. New keys never land on disk
    as plaintext. Raises the vault error when the vault cannot be opened."""
    secret = entry.get("api_key") if isinstance(entry, dict) else None
    if not secret or entry.get("vault_ref"):
        return entry
    if vault is None:
        from xlii.vault import Vault
        vault = Vault.unlock()
    label = str(entry.get("label") or "key")
    ref, n = label, 1
    while vault.has(VAULT_CHATKEYS_NS, ref):
        n += 1
        ref = f"{label}#{n}"
    vault.set(VAULT_CHATKEYS_NS, ref, str(secret))
    return {**{k: v for k, v in entry.items() if k != "api_key"}, "vault_ref": ref}


def store_chat_key_secret(entry: dict, secret: str, *, vault=None) -> None:
    """Persist a (rotated) secret: into the vault when the entry is
    vault-backed, else the plaintext config slot (the keys.py:137-140 policy).
    """
    vault_ref = entry.get("vault_ref")
    if vault_ref:
        if vault is None:
            from xlii.vault import Vault
            vault = Vault.unlock(create_if_missing=False)
        vault.set(VAULT_CHATKEYS_NS, str(vault_ref), secret)
    else:
        update_key_in_config(entry.get("label"), api_key=secret)


def rotate_keys(
    cfg: GlobalConfig,
    team_id: str,
    *,
    label: Optional[str] = None,
    on_event=None,
) -> int:
    """Rotate the secret of one or all pooled keys in place (same key id, new
    value — was keys.py's _keys_rotate loop). Events: no_targets / rotating /
    skip_no_id / vault_error / failed / rotated."""
    emit = on_event or (lambda kind, **payload: None)
    targets = select_keys(cfg, label)
    if not targets:
        emit("no_targets")
        return 0
    emit("rotating", count=len(targets))
    vault = None
    for entry in targets:
        entry_label = entry.get("label", "?")
        kid = entry.get("api_key_id")
        if not kid:
            emit("skip_no_id", label=entry_label)
            continue
        if entry.get("vault_ref") and vault is None:
            from xlii.vault import Vault, VaultError
            try:
                vault = Vault.unlock(create_if_missing=False)
            except VaultError as e:
                emit("vault_error", label=entry_label, error=str(e))
                continue
        try:
            resp = rotate_api_key(cfg.management_api_key, kid)
        except BootstrapError as e:
            emit("failed", label=entry_label, error=str(e))
            continue
        new_secret = extract_api_key_string(resp) if isinstance(resp, dict) else None
        if not new_secret:
            emit("failed", label=entry_label,
                 error=f"response missing new secret. raw: {resp}")
            continue
        store_chat_key_secret(entry, new_secret, vault=vault)
        emit("rotated", label=entry_label)
    return 0


def set_keys_expiration(
    cfg: GlobalConfig,
    team_id: str,
    *,
    days: int,
    label: Optional[str] = None,
    on_event=None,
) -> int:
    """Set expireTime on one or all pooled keys (was keys.py's _keys_expire
    loop). Events: no_targets / setting / skip_no_id / failed / updated."""
    emit = on_event or (lambda kind, **payload: None)
    targets = select_keys(cfg, label)
    if not targets:
        emit("no_targets")
        return 0
    emit("setting", count=len(targets), days=days)
    for entry in targets:
        entry_label = entry.get("label", "?")
        kid = entry.get("api_key_id")
        if not kid:
            emit("skip_no_id", label=entry_label)
            continue
        try:
            resp = update_api_key_expiration(cfg.management_api_key, team_id, kid, days)
        except BootstrapError as e:
            emit("failed", label=entry_label, error=str(e))
            continue
        new_exp = (resp or {}).get("expireTime") if isinstance(resp, dict) else None
        if new_exp:
            update_key_in_config(entry_label, expire_time=new_exp)
        emit("updated", label=entry_label)
    return 0


@dataclass
class PrunePlan:
    """What `keys prune` would delete — the plan half of the plan/execute split."""
    candidates: list[dict] = field(default_factory=list)
    server_count: int = 0
    protected: int = 0


def plan_prune(
    cfg: GlobalConfig,
    team_id: str,
    *,
    name_glob: Optional[str] = None,
    any_name: bool = False,
    older_than: Optional[int] = None,
    include_active: bool = False,
    now: Optional[datetime] = None,
) -> PrunePlan:
    """Select orphaned server keys for deletion: pool-protection sets plus the
    name gate (default: xlii-provisioned names only, so a shared management
    key's OTHER keys are never touched without opting in)."""
    server_keys = list_api_keys(cfg.management_api_key, team_id)
    if not server_keys:
        return PrunePlan()
    active_ids, active_names = active_pool_sets(cfg.keys, server_keys)
    if name_glob:
        name_ok = lambda n: fnmatch.fnmatch(n, name_glob)
    elif any_name:
        name_ok = lambda n: True
    else:
        name_ok = is_provisioned_name
    if now is None:
        now = datetime.now(timezone.utc)
    candidates = select_prune_candidates(
        server_keys,
        active_ids=active_ids,
        active_names=active_names,
        name_ok=name_ok,
        older_than_days=older_than,
        now=now,
        include_active=include_active,
    )
    protected = sum(1 for k in server_keys if key_is_active(k, active_ids, active_names))
    return PrunePlan(candidates=candidates, server_count=len(server_keys),
                     protected=protected)


def execute_prune(
    cfg: GlobalConfig,
    team_id: str,
    candidates: list[dict],
    *,
    on_event=None,
) -> int:
    """Delete the planned candidates server-side and drop any tracked entries
    whose ids were confirmed deleted (was the second half of _keys_prune).
    Events: skip_no_id / deleted / failed / config_removed / done."""
    emit = on_event or (lambda kind, **payload: None)
    deleted_ids: set = set()
    ok = 0
    for k in candidates:
        kid = extract_api_key_id(k)
        name = k.get("name", "?")
        if not kid:
            emit("skip_no_id", name=name)
            continue
        try:
            delete_api_key(cfg.management_api_key, team_id, kid)
        except BootstrapError as e:
            emit("failed", name=name, error=str(e))
            continue
        deleted_ids.add(kid)
        ok += 1
        emit("deleted", name=name)

    # If --include-active deleted any tracked keys, drop them from the config too.
    if deleted_ids:
        _, n = remove_keys_from_config(
            lambda e: isinstance(e, dict) and e.get("api_key_id") in deleted_ids
        )
        if n:
            emit("config_removed", count=n)
    emit("done", deleted=ok, total=len(candidates))
    return 0


def revoke_keys_by_prefix(
    cfg: GlobalConfig,
    team_id: str,
    prefix: str,
    *,
    confirm_cb=None,
    on_event=None,
) -> int:
    """Delete every server key named ``xlii-{prefix}-*`` (or legacy
    ``xli-{prefix}-*``) and strip matching local entries (was
    _keyops._bootstrap_revoke, reached by `bootstrap --revoke` and
    `keys revoke`).

    ``confirm_cb(matches) -> bool`` gates the deletion — a headless body
    confirms through its own surface; None means "cannot confirm" and cancels.
    Partial-failure policy preserved: if any delete fails or is skipped, only
    local entries whose server id was confirmed deleted are stripped.
    """
    emit = on_event or (lambda kind, **payload: None)
    name_prefixes = provisioned_key_names(f"{prefix}-")
    emit("listing", prefixes=name_prefixes)
    try:
        keys = list_api_keys(cfg.management_api_key, team_id)
    except BootstrapError as e:
        emit("error", error=str(e))
        return 1

    matches = [k for k in keys if (k.get("name") or "").startswith(name_prefixes)]
    deleted_ids: set[str] = set()
    incomplete = False
    if not matches:
        emit("none_found", prefixes=name_prefixes)
        emit("config_removed", count=0, path=GLOBAL_CONFIG_FILE)
        return 0
    else:
        emit("will_delete", matches=matches)
        if confirm_cb is None or not confirm_cb(matches):
            emit("cancelled")
            return 0
        for k in matches:
            kid = extract_api_key_id(k)
            name = k.get("name", "?")
            if not kid:
                emit("skip_no_id", name=name)
                incomplete = True
                continue
            try:
                delete_api_key(cfg.management_api_key, team_id, kid)
            except BootstrapError as e:
                emit("failed", name=name, error=str(e))
                incomplete = True
                continue
            deleted_ids.add(kid)
            emit("deleted", name=name)

    # Strip matching entries from local config too
    if matches and incomplete:
        # Preserve secrets for keys that may still be live server-side. With a
        # partial revoke we can only safely remove entries whose server id was
        # confirmed deleted.
        matcher = (
            lambda e: isinstance(e, dict) and e.get("api_key_id") in deleted_ids
        )
    else:
        matcher = (
            lambda e: isinstance(e, dict)
            and (e.get("label") or "").startswith(f"{prefix}-")
        )
    path, n_removed = remove_keys_from_config(matcher)
    emit("config_removed", count=n_removed, path=path)
    return 1 if incomplete else 0


def _chat_keys(chat_key: Optional[str], chat_keys: Optional[list[str]]) -> list[str]:
    keys: list[str] = []
    if chat_keys:
        keys.extend(k for k in chat_keys if k)
    elif chat_key:
        keys.append(chat_key)
    return keys


def _request_models_json(
    keys: list[str], mgmt_key: str
) -> tuple[Any, Optional[str]]:
    """GET /v1/models. Returns ``(payload, None)`` or ``(None, last_error)``."""
    last_error: Optional[str] = None
    # Region-aware (cfg.region / XAI_REGION); GlobalConfig.load() tolerates a
    # missing file (setup-time), returning the global edge.
    models_url = f"{GlobalConfig.load().api_base_url()}/models"

    for k in keys:
        try:
            resp = httpx.get(
                models_url,
                headers={"Authorization": f"Bearer {k}"},
                timeout=15.0,
            )
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            last_error = f"{type(e).__name__}: {e}"
            continue
        if resp.status_code < 400:
            return resp.json(), None
        last_error = f"{resp.status_code}: {resp.text[:120]}"

    if mgmt_key:
        try:
            resp = httpx.get(
                models_url,
                headers={"Authorization": f"Bearer {mgmt_key}"},
                timeout=15.0,
            )
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            last_error = f"{type(e).__name__}: {e}"
        else:
            if resp.status_code < 400:
                return resp.json(), None
            last_error = f"{resp.status_code}: {resp.text[:120]}"
    return None, last_error


def discover_models(
    mgmt_key: str,
    team_id: str,
    chat_key: Optional[str] = None,
    chat_keys: Optional[list[str]] = None,
) -> list[str]:
    """Return the list of model IDs available via the OpenAI-compatible
    /v1/models endpoint.

    Tries every key in `chat_keys` (or just `chat_key` if that's all we have)
    until one returns a 2xx — pools often have a dead key or two and we
    shouldn't fail discovery just because keys[0] is stale.

    Returns [] if no key works — discovery is a nice-to-have, not load-bearing.
    """
    keys = _chat_keys(chat_key, chat_keys)
    data, last_error = _request_models_json(keys, mgmt_key)
    if data is not None:
        return _normalize_model_list(data)

    import sys as _sys
    if keys:
        print(
            f"[xlii] model discovery: all {len(keys)} chat key(s) rejected. "
            f"last error: {last_error}",
            file=_sys.stderr,
        )
    elif last_error:
        print(
            f"[xlii] model discovery: no chat keys available; "
            f"management-key fallback failed: {last_error}",
            file=_sys.stderr,
        )
    return []


def extract_context_windows(data: Any) -> dict[str, int]:
    """``id`` / alias → ``context_length`` from a ``/v1/models`` payload."""
    if not data:
        return {}
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("data") or data.get("models") or data.get("items") or []
    else:
        return {}
    out: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        raw = item.get("context_length")
        if raw is None:
            continue
        try:
            n = int(raw)
        except (TypeError, ValueError):
            continue
        if n <= 0:
            continue
        mid = item.get("id") or item.get("name") or item.get("model")
        if isinstance(mid, str) and mid:
            out[mid] = n
        aliases = item.get("aliases") or []
        if isinstance(aliases, list):
            for alias in aliases:
                if isinstance(alias, str) and alias:
                    out[alias] = n
    return out


def discover_model_windows(
    mgmt_key: str,
    team_id: str,
    chat_key: Optional[str] = None,
    chat_keys: Optional[list[str]] = None,
) -> dict[str, int]:
    """Live ``context_length`` map from ``/v1/models``. Empty if no key works."""
    data, _err = _request_models_json(_chat_keys(chat_key, chat_keys), mgmt_key)
    return extract_context_windows(data)


def _normalize_model_list(data: Any) -> list[str]:
    """Pull model IDs out of any plausible response shape."""
    if not data:
        return []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("data") or data.get("models") or data.get("items") or []
    else:
        return []
    out: list[str] = []
    for item in items:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            mid = item.get("id") or item.get("name") or item.get("model")
            if isinstance(mid, str) and mid:
                out.append(mid)
    return out


def pick_best_models(available: list[str]) -> tuple[Optional[str], Optional[str]]:
    """Heuristic best-of-class picker. Returns (orchestrator, worker).

    Strategy: walk explicit preference tiers, take the first non-empty tier,
    then pick the lexically-latest entry within that tier.

    Tiers err strongly toward STABLE models — an experimental flagship is
    worse than a stable fast model for production use. Users can always
    override via `xlii models set`.
    """
    def _is_stable(m: str) -> bool:
        return "experimental" not in m and "beta" not in m

    def _is_reasoning_only(m: str) -> bool:
        return "reasoning" in m and "non-reasoning" not in m

    def _is_non_reasoning(m: str) -> bool:
        return "non-reasoning" in m

    def _is_fast(m: str) -> bool:
        return "fast" in m

    def _is_flagship(m: str) -> bool:
        # Bare model name like `grok-4`, `grok-4.1` — no fast/reasoning suffix
        return not _is_fast(m) and "reasoning" not in m

    # Orchestrator preference — stable non-fast reasoning → stable flagship →
    # stable fast reasoning → any reasoning → any stable non-flagship.
    # Reasoning beats bare flagship: an orchestrator's job is planning/tool
    # selection, where chain-of-thought matters more than raw breadth. (e.g.
    # grok-4.20-reasoning should beat a bare grok-4 for the main agent role.)
    orch_tiers = [
        [m for m in available if _is_stable(m) and _is_reasoning_only(m) and not _is_fast(m)],
        [m for m in available if _is_stable(m) and _is_flagship(m)],
        [m for m in available if _is_stable(m) and _is_reasoning_only(m)],
        [m for m in available if _is_reasoning_only(m)],
        [m for m in available if _is_stable(m) and not _is_non_reasoning(m)],
    ]
    orch = next((sorted(tier, reverse=True)[0] for tier in orch_tiers if tier), None)

    # Worker preference — stable fast non-reasoning → any fast non-reasoning →
    # stable non-reasoning → any non-reasoning.
    worker_tiers = [
        [m for m in available if _is_stable(m) and _is_fast(m) and _is_non_reasoning(m)],
        [m for m in available if _is_fast(m) and _is_non_reasoning(m)],
        [m for m in available if _is_stable(m) and _is_non_reasoning(m)],
        [m for m in available if _is_non_reasoning(m)],
    ]
    worker = next((sorted(tier, reverse=True)[0] for tier in worker_tiers if tier), None)

    return orch, worker


def set_models_in_config(
    orchestrator_model: Optional[str],
    worker_model: Optional[str],
    *,
    chat_model: Optional[str] = None,
    help_model: Optional[str] = None,
    auto_detected: bool = False,
) -> Path:
    """Persist model picks. If auto_detected, also stamp models_detected_at."""
    raw = json.loads(GLOBAL_CONFIG_FILE.read_text())
    if orchestrator_model:
        raw["orchestrator_model"] = orchestrator_model
    if worker_model:
        raw["worker_model"] = worker_model
    if chat_model:
        raw["chat_model"] = chat_model
    if help_model:
        raw["help_model"] = help_model
    if auto_detected:
        raw["models_detected_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    write_text_atomic(GLOBAL_CONFIG_FILE, json.dumps(raw, indent=2), mode=0o600)
    return GLOBAL_CONFIG_FILE


def set_team_id_in_config(team_id: str) -> Path:
    """Cache a discovered team_id back into config.json so future bootstrap
    calls skip the discovery round-trip."""
    raw = json.loads(GLOBAL_CONFIG_FILE.read_text())
    raw["team_id"] = team_id
    write_text_atomic(GLOBAL_CONFIG_FILE, json.dumps(raw, indent=2), mode=0o600)
    return GLOBAL_CONFIG_FILE


def append_keys_to_config(new_keys: list[dict]) -> Path:
    """Append `new_keys` to `keys[]` in config.json without disturbing any
    other fields (preserves `_comment`, pricing, model fields, etc.)."""
    raw = json.loads(GLOBAL_CONFIG_FILE.read_text())
    keys = raw.get("keys") or []
    keys.extend(new_keys)
    raw["keys"] = keys
    write_text_atomic(GLOBAL_CONFIG_FILE, json.dumps(raw, indent=2), mode=0o600)
    return GLOBAL_CONFIG_FILE


def update_key_in_config(label: str, *, api_key: Optional[str] = None,
                         api_key_id: Optional[str] = None,
                         expire_time: Optional[str] = None) -> bool:
    """Patch the entry whose label == label, in place. Returns True if updated."""
    raw = json.loads(GLOBAL_CONFIG_FILE.read_text())
    keys = raw.get("keys") or []
    found = False
    for entry in keys:
        if isinstance(entry, dict) and entry.get("label") == label:
            if api_key is not None:
                entry["api_key"] = api_key
            if api_key_id is not None:
                entry["api_key_id"] = api_key_id
            if expire_time is not None:
                entry["expire_time"] = expire_time
            found = True
            break
    if found:
        raw["keys"] = keys
        write_text_atomic(GLOBAL_CONFIG_FILE, json.dumps(raw, indent=2), mode=0o600)
    return found


def remove_keys_from_config(matcher) -> tuple[Path, int]:
    """Remove every entry in keys[] for which matcher(entry) is True.

    matcher receives the raw dict from the file. Returns (file_path, n_removed).
    """
    raw = json.loads(GLOBAL_CONFIG_FILE.read_text())
    keys = raw.get("keys") or []
    kept = [k for k in keys if not matcher(k)]
    n_removed = len(keys) - len(kept)
    raw["keys"] = kept
    write_text_atomic(GLOBAL_CONFIG_FILE, json.dumps(raw, indent=2), mode=0o600)
    return GLOBAL_CONFIG_FILE, n_removed
