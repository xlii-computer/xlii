"""Gaggles (jams) — named multi-brain presets over the gigwork seam (Phase B, G3).

A **gaggle** is a named preset, not a graph: *who* (members = backend + kit) +
*how many* (max_parallel cap) + *merge policy* + *budget* (the cap itself;
unbounded fan-out is refused). ``write`` is always false — members are
read-only ``WorkerAgent`` passes. Home members run on the xAI plane, gig
members through ``chat_backend``, in parallel, then one merge step combines
the answers.

``/jam`` is the shipped synonym of ``/gaggle``. Config key ``gigwork.jams``
wins; a retired ``gigwork.gaggles`` block still loads.

Merge policies:

* ``synth_conflicts`` — one home-model chat call that surfaces agreements,
  conflicts, and a short verdict. The synthesis must not invent content.
* ``concat_digest`` — no model call; answers formatted under member headers.

Stock gaggles ship as data (config can override or add under
``gigwork.jams``). The ``"gig"`` backend slot means "the default gig":
the first provider in ``gigwork.defaults.allow``, else the sole/first
configured provider — so ``/gaggle second-opinion`` works the moment one
provider exists, with no extra config.

Not swarm (no writers, no worktrees), not fleet (no job kind), no nesting —
gaggle members never dispatch further workers.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from xlii.chat_backend import GigError, gig_allowlist, gig_providers, resolve_gig_backend

_VALID_KITS = frozenset({"explore", "bash", "general"})
_VALID_MERGES = frozenset({"synth_conflicts", "concat_digest"})
_HOME_NAMES = frozenset({"xai", "home"})
_DEFAULT_GIG_SLOT = "gig"

# Hard ceiling regardless of config — a jam is a handful of brains, not a fleet.
MAX_MEMBERS = 6

# Stock presets (proposals/gigwork.md Phase B). Pure data; "gig" binds at
# resolve time to the default gig provider. write is locked false.
STOCK_JAMS: dict[str, dict[str, Any]] = {
    "second-opinion": {
        "members": [
            {"backend": "xai", "kit": "explore"},
            {"backend": "gig", "kit": "explore"},
        ],
        "merge": "synth_conflicts",
        "max_parallel": 2,
        "write": False,
    },
    "debate": {
        "members": [
            {"backend": "gig", "kit": "explore"},
            {"backend": "xai", "kit": "explore"},
        ],
        "merge": "synth_conflicts",
        "max_parallel": 2,
        "write": False,
    },
    "scout": {
        "members": [
            {"backend": "gig", "kit": "explore"},
            {"backend": "gig", "kit": "explore"},
        ],
        "merge": "concat_digest",
        "max_parallel": 3,
        "write": False,
    },
}
STOCK_GAGGLES = STOCK_JAMS

_SYNTH_PROMPT = (
    "You are merging {n} independent answers to the same question. Each was "
    "produced by a different model with no knowledge of the others.\n\n"
    "Question:\n{question}\n\n{answers}\n"
    "Write a synthesis with exactly these sections:\n"
    "## Agreements — claims the answers share (state them once, plainly).\n"
    "## Conflicts — where they disagree or one covers ground the other missed; "
    "be specific about which member said what.\n"
    "## Verdict — a short judgment of which claims to trust and what remains "
    "uncertain.\n"
    "Use only content from the answers; do not add new claims of your own."
)


@dataclass(frozen=True)
class JamMember:
    backend: str          # "xai" (home) or a configured gig provider name
    kit: str = "explore"  # tool palette — the agent-tool API calls this `role`
    model: str = ""       # per-member override; "" = the backend's own model

    @property
    def is_home(self) -> bool:
        return self.backend in _HOME_NAMES

    @property
    def label(self) -> str:
        base = f"{'xai' if self.is_home else self.backend}({self.kit})"
        return f"{base}@{self.model}" if self.model else base

    @property
    def token(self) -> str:
        """The member as ``backend[:kit][@model]`` — the /jam add syntax, so
        a configured jam can round-trip into an editable command line."""
        out = self.backend
        if self.kit != "explore":
            out += f":{self.kit}"
        if self.model:
            out += f"@{self.model}"
        return out


@dataclass(frozen=True)
class JamSpec:
    name: str
    members: tuple[JamMember, ...]
    merge: str
    max_parallel: int  # mandatory cap (the budget): how many brains at once
    write: bool = False  # v1 lock: True is refused at validate time


@dataclass
class MemberResult:
    member: JamMember
    text: str = ""
    error: str = ""
    model: str = ""
    iterations: int = 0
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass
class JamResult:
    spec: JamSpec
    results: list[MemberResult] = field(default_factory=list)
    merged: str = ""
    synth_model: str = ""

    @property
    def ok_count(self) -> int:
        return sum(1 for r in self.results if r.ok)


def _default_gig(cfg: Any) -> str:
    """The provider the ``"gig"`` slot binds to: first allowlisted, else the
    sole/first configured. No provider → GigError whose message is the fix."""
    providers = gig_providers(cfg)
    for name in gig_allowlist(cfg):
        if name in providers:
            return name
    if providers:
        return sorted(providers)[0]
    raise GigError(
        "no gig provider configured — a stock jam needs one foreign brain. "
        "Add one first: /gigwork add <preset>  (see /gigwork presets)"
    )


def _spec_from_raw(name: str, raw: Any) -> JamSpec:
    """Validate one raw jam block into a :class:`JamSpec` (the shared
    path for config-loaded and ``/jam add``-built jams — one validator,
    identical messages)."""
    if not isinstance(raw, dict):
        raise GigError(f"gigwork.jams.{name}: expected an object")
    raw_members = raw.get("members")
    if not isinstance(raw_members, list) or not raw_members:
        raise GigError(f"gigwork.jams.{name}: members must be a non-empty list")
    if len(raw_members) > MAX_MEMBERS:
        raise GigError(
            f"gigwork.jams.{name}: {len(raw_members)} members exceeds the "
            f"cap of {MAX_MEMBERS} — a jam is a handful of brains, not a fleet"
        )
    members: list[JamMember] = []
    for i, m in enumerate(raw_members):
        if not isinstance(m, dict):
            raise GigError(f"gigwork.jams.{name}: members[{i}] must be an object")
        backend = str(m.get("backend", "")).strip()
        if "role" in m and "kit" not in m:
            raise GigError(
                f"gigwork.jams.{name}: members[{i}] key 'role' was renamed "
                "to 'kit' (explore|bash|general) — 'role' means persona "
                "loadouts elsewhere in xlii"
            )
        kit = str(m.get("kit", "explore")).strip().lower() or "explore"
        if not backend:
            raise GigError(f"gigwork.jams.{name}: members[{i}] needs a backend")
        if kit not in _VALID_KITS:
            raise GigError(
                f"gigwork.jams.{name}: members[{i}] kit {kit!r} "
                f"(valid: {', '.join(sorted(_VALID_KITS))})"
            )
        model = str(m.get("model", "")).strip()
        members.append(JamMember(backend=backend, kit=kit, model=model))
    merge = str(raw.get("merge", "synth_conflicts"))
    if merge not in _VALID_MERGES:
        raise GigError(
            f"gigwork.jams.{name}: merge {merge!r} "
            f"(valid: {', '.join(sorted(_VALID_MERGES))})"
        )
    max_parallel = raw.get("max_parallel", 2)
    if not isinstance(max_parallel, int) or isinstance(max_parallel, bool) or max_parallel < 1:
        raise GigError(f"gigwork.jams.{name}: max_parallel must be a positive int")
    write = raw.get("write", False)
    if write is True:
        raise GigError(
            f"gigwork.jams.{name}: write: true is refused — gaggles are "
            "read-only (write: false)"
        )
    if write not in (False, None):
        raise GigError(f"gigwork.jams.{name}: write must be false")
    return JamSpec(
        name=name, members=tuple(members), merge=merge,
        max_parallel=min(max_parallel, MAX_MEMBERS),
        write=False,
    )


def _user_jams(cfg: Any) -> dict[str, Any]:
    """Configured crews. ``jams`` wins; retired ``gaggles`` still loads."""
    block = getattr(cfg, "gigwork", None) or {}
    if not isinstance(block, dict):
        return {}
    out: dict[str, Any] = {}
    old = block.get("gaggles")
    if isinstance(old, dict):
        out.update({str(k): v for k, v in old.items()})
    new = block.get("jams")
    if isinstance(new, dict):
        out.update({str(k): v for k, v in new.items()})
    return out


def jam_specs(cfg: Any) -> dict[str, JamSpec]:
    """Stock + config jams (config wins on name), validated. The ``"gig"``
    slot stays symbolic here; it binds to a provider at run time."""
    merged: dict[str, dict[str, Any]] = dict(STOCK_JAMS)
    merged.update(_user_jams(cfg))
    return {name: _spec_from_raw(name, raw) for name, raw in merged.items()}


def resolve_jam(cfg: Any, name: str) -> JamSpec:
    """One spec with the ``"gig"`` slots bound to a real provider. Unknown
    names and unbindable slots raise GigError with the fix in the message."""
    specs = jam_specs(cfg)
    spec = specs.get(name)
    if spec is None:
        known = ", ".join(sorted(specs)) or "(none)"
        raise GigError(f"unknown jam {name!r} — available: {known}")

    providers = gig_providers(cfg)
    bound: list[JamMember] = []
    for m in spec.members:
        if m.is_home:
            bound.append(m)
        elif m.backend == _DEFAULT_GIG_SLOT:
            bound.append(JamMember(backend=_default_gig(cfg), kit=m.kit, model=m.model))
        elif m.backend in providers:
            bound.append(m)
        else:
            known = ", ".join(sorted(providers)) or "(none configured)"
            raise GigError(
                f"jam {name!r}: member backend {m.backend!r} is not a "
                f"configured gig provider — configured: {known}"
            )
    return JamSpec(
        name=spec.name, members=tuple(bound),
        merge=spec.merge, max_parallel=spec.max_parallel, write=False,
    )


def orchestrator_may_run_gaggle(cfg: Any, name: str) -> JamSpec:
    """Resolve ``name`` and check that every foreign member is on
    ``gigwork.defaults.allow``. The slash command (a human) may hire any
    configured provider; the orchestrator may not. Raises :class:`GigError`
    with the fix in the message. Write stays locked false."""
    spec = resolve_jam(cfg, name)
    allow = set(gig_allowlist(cfg))
    for m in spec.members:
        if m.is_home:
            continue
        if m.backend not in allow:
            allowed = ", ".join(sorted(allow)) or "(none — gigwork.defaults.allow is empty)"
            raise GigError(
                f"gaggle {name!r} member {m.backend!r} is not in "
                f"gigwork.defaults.allow — hireable: {allowed}"
            )
    return spec


# --------------------------------------------------------------------------- #
#  Authoring — /jam add|rm write through here (caller persists cfg)
# --------------------------------------------------------------------------- #

# Verbs of the /jam command — a jam by one of these names could never be
# invoked, so the add is refused instead of writing a dead entry.
_RESERVED_JAM_NAMES = frozenset({"ls", "add", "rm"})


def parse_member_token(token: str) -> dict[str, Any]:
    """``backend[:kit][@model]`` → a raw member dict (the config shape).

    The first ``@`` splits off the model — models may themselves contain ``:``
    (ollama tags like ``llama3:8b``) — then the left side's first ``:`` splits
    backend from kit. Omitted parts are omitted from the dict, so config
    entries stay as terse as hand-written ones.
    """
    spec, _at, model = token.partition("@")
    backend, _colon, kit = spec.partition(":")
    if not backend.strip():
        raise GigError(f"member {token!r}: needs a backend — backend[:kit][@model]")
    out: dict[str, Any] = {"backend": backend.strip()}
    if kit.strip():
        out["kit"] = kit.strip().lower()
    if model.strip():
        out["model"] = model.strip()
    return out


def add_jam_to_config(
    cfg: Any,
    name: str,
    member_tokens: "list[str]",
    *,
    merge: str = "synth_conflicts",
    max_parallel: int = 0,
) -> JamSpec:
    """Build, validate, and write one jam under ``cfg.gigwork['jams']``
    (caller persists via ``cfg.save()``). Members are ``backend[:kit][@model]``
    tokens; ``max_parallel`` 0 means all members at once. Backends must exist
    now — home, the symbolic ``gig`` slot, or a configured provider — so a
    typo is refused at add time, not at first run. Adding under a stock name
    shadows the stock preset (config wins on name)."""
    name = name.strip()
    if not name:
        raise GigError("a jam needs a name")
    if name in _RESERVED_JAM_NAMES:
        raise GigError(f"{name!r} is a /jam verb — pick another name")
    raw: dict[str, Any] = {
        "members": [parse_member_token(t) for t in member_tokens],
        "merge": merge,
        "max_parallel": max_parallel or min(len(member_tokens), MAX_MEMBERS),
        "write": False,
    }
    spec = _spec_from_raw(name, raw)
    providers = gig_providers(cfg)
    for m in spec.members:
        if m.is_home or m.backend == _DEFAULT_GIG_SLOT or m.backend in providers:
            continue
        known = ", ".join(sorted(providers)) or "(none configured)"
        raise GigError(
            f"member backend {m.backend!r} is not a configured gig provider — "
            f"configured: {known}. Add it first: /gigwork add {m.backend}"
        )
    block = dict(getattr(cfg, "gigwork", None) or {})
    jams = _user_jams(cfg)
    jams[name] = raw
    block["jams"] = jams
    cfg.gigwork = block
    return spec


def remove_jam_from_config(cfg: Any, name: str) -> "tuple[bool, bool]":
    """Drop one configured jam (caller persists). Returns ``(existed,
    uncovers_stock)`` — the second is True when the removed entry was shadowing
    a stock preset, which now resurfaces. A stock name with no config entry is
    refused: stock presets are code, not removable."""
    block = dict(getattr(cfg, "gigwork", None) or {})
    jams = _user_jams(cfg)
    if name not in jams:
        if name in STOCK_JAMS:
            raise GigError(
                f"{name!r} is a stock preset (code, not config) — it can't be "
                f"removed, only shadowed: /jam add {name} …"
            )
        return (False, False)
    jams.pop(name)
    block["jams"] = jams
    cfg.gigwork = block
    return (True, name in STOCK_JAMS)


def _run_member(
    member: JamMember,
    question: str,
    *,
    cfg: Any,
    project: Any,
    clients: Any,
    pool: Any,
    context: Optional[str],
    subscribed_plugins: list[str],
) -> MemberResult:
    from xlii.worker_agent import WorkerAgent

    result = MemberResult(member=member)
    try:
        backend = None
        worker_clients = clients
        if member.is_home:
            if pool is not None:
                worker_clients = pool.acquire()
        else:
            backend = resolve_gig_backend(cfg, member.backend)
        worker = WorkerAgent(
            clients=worker_clients,
            project=project,
            cfg=cfg,
            subscribed_plugins=list(subscribed_plugins),
            role=member.kit,  # agent-tool API name for the kit
            chat_backend=backend,
            worker_writes=False,  # gaggles never write (proposals/gigwork.md)
            # A member's @model rides the existing override seam: WorkerAgent
            # prefers self.model over backend.model over the role map.
            model=member.model or None,
        )
        text, call = worker.run(question, context=context)
        result.text = text
        result.model = call.model
        result.iterations = call.iterations
        result.total_tokens = call.total_tokens
        result.prompt_tokens = call.prompt_tokens
        result.completion_tokens = call.completion_tokens
        if member.is_home and pool is not None:
            pool.report_success(worker_clients)
    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
    return result


def _format_answers(results: list[MemberResult]) -> str:
    parts: list[str] = []
    for i, r in enumerate(results, 1):
        head = f"### Answer {i} — {r.member.label}"
        body = r.text if r.ok else f"(member failed: {r.error})"
        parts.append(f"{head}\n{body}")
    return "\n\n".join(parts) + "\n\n"


def _merge_concat(results: list[MemberResult]) -> str:
    return _format_answers(results).strip()


def _merge_synth(
    results: list[MemberResult], question: str, *, cfg: Any, clients: Any,
) -> tuple[str, str]:
    """One home-model chat call over the collected answers. Returns
    ``(synthesis_text, model)``; raises on API failure (caller degrades)."""
    model = cfg.get_model_for_role("worker")
    prompt = _SYNTH_PROMPT.format(
        n=len(results), question=question, answers=_format_answers(results),
    )
    resp = clients.chat.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=cfg.worker_temp(),
    )
    return ((resp.choices[0].message.content or "").strip(), model)


def run_jam(
    name: str,
    question: str,
    *,
    cfg: Any,
    project: Any,
    clients: Any,
    pool: Any = None,
    context: Optional[str] = None,
    subscribed_plugins: Optional[list[str]] = None,
    on_progress: Optional[Callable[[str], None]] = None,
) -> JamResult:
    """Resolve ``name``, fan the members out (capped), merge, return everything.

    Raises :class:`GigError` for config/lookup problems; member API failures
    land in that member's ``error`` (the jam proceeds if any member
    succeeded). A synth-call failure degrades to the concat digest with a note
    — never a crash after paid member passes."""
    spec = resolve_jam(cfg, name)
    emit = on_progress or (lambda _msg: None)

    result = JamResult(spec=spec)
    workers = min(spec.max_parallel, len(spec.members))
    slots: dict[Any, int] = {}
    ordered: list[Optional[MemberResult]] = [None] * len(spec.members)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, member in enumerate(spec.members):
            fut = ex.submit(
                _run_member, member, question,
                cfg=cfg, project=project, clients=clients, pool=pool,
                context=context,
                subscribed_plugins=list(subscribed_plugins or []),
            )
            slots[fut] = i
        for fut in as_completed(slots):
            i = slots[fut]
            ordered[i] = fut.result()
            r = ordered[i]
            emit(f"{r.member.label}: {'done' if r.ok else 'failed — ' + r.error}")
    result.results = [r for r in ordered if r is not None]

    if result.ok_count == 0:
        errors = "; ".join(f"{r.member.label}: {r.error}" for r in result.results)
        raise GigError(f"jam {name!r}: every member failed — {errors}")

    if spec.merge == "concat_digest":
        result.merged = _merge_concat(result.results)
        return result

    try:
        result.merged, result.synth_model = _merge_synth(
            result.results, question, cfg=cfg, clients=clients,
        )
    except Exception as e:
        result.merged = (
            f"(synthesis failed: {type(e).__name__}: {e} — raw answers below)\n\n"
            + _merge_concat(result.results)
        )
    return result


# G3 public name is gaggle (proposals/gigwork.md); jam is the shipped synonym.
gaggle_specs = jam_specs
resolve_gaggle = resolve_jam
run_gaggle = run_jam
