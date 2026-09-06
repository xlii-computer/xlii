"""Heavy tier — the deep-search coordinator (chat-tiers Vector C).

``run_deep_search`` is the ``heavy`` tier's executor: a coordinator decomposes a
question into sub-queries, fans them out in parallel (most are direct
``web_search`` / ``x_search`` server calls; a few tagged ``investigate`` run a
read-only :class:`~xlii.worker_agent.WorkerAgent`), optionally runs a second
bounded wave to fill coverage gaps, then synthesizes a cited answer. This is the
deterministic coordinator from ``proposals/chat-tiers.md`` (LOCKED), not a
free-running ``dispatch_subagent`` batch.

Every model / search / agent interaction is a **injectable seam** (``plan_fn``,
``search_fn``, ``investigate_fn``, ``synth_fn``), defaulting to the real
implementation but replaced by fakes in tests — so the fan-out is exercised with
**zero network and zero subprocess**. The fan-out uses a bounded ThreadPool
(mirroring ``agent_dispatch._run_worker``), capped by ``cfg.max_parallel_workers``.

The turn layer wraps ``run_deep_search`` in a ``KIND_FLEET`` job and feeds the
``on_progress`` callback into the job's ``detail`` / ``progress`` fields; this
module stays free of ``jobs.py`` so it is trivially testable.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# Sub-query kinds.
KIND_SEARCH = "search"          # a direct web/x_search server call
KIND_INVESTIGATE = "investigate"  # a read-only WorkerAgent (search + reason + read)

_CITATIONS_MARKER = "\n\n--- citations ---\n"

# Default budget knobs — all breaches are surfaced via on_log (no silent cap).
DEFAULT_MAX_SUBQUERIES = 6      # breadth per wave
DEFAULT_MAX_ROUNDS = 2          # depth (wave 1 + one gap-filling wave)


# --------------------------------------------------------------------------- #
#  Data
# --------------------------------------------------------------------------- #

@dataclass
class SubQuery:
    """One planned sub-question and how to answer it."""

    query: str
    kind: str = KIND_SEARCH        # KIND_SEARCH | KIND_INVESTIGATE
    channel: str = "web"           # "web" | "x" (search kind only)

    @property
    def label(self) -> str:
        tag = "x" if (self.kind == KIND_SEARCH and self.channel == "x") else \
            ("agent" if self.kind == KIND_INVESTIGATE else "web")
        return f"{tag}:{self.query[:24]}"


@dataclass
class Finding:
    """The result of one sub-query."""

    subquery: SubQuery
    text: str = ""
    citations: list[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class DeepSearchProgress:
    """A progress snapshot handed to ``on_progress`` as sub-queries resolve."""

    done: int
    total: int
    items: list[tuple[str, str]] = field(default_factory=list)  # (label, status)


@dataclass
class DeepSearchResult:
    """The heavy tier's turn output."""

    question: str
    answer: str = ""
    findings: list[Finding] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)  # deduped, ordered
    rounds: int = 0
    partial: bool = False           # ≥1 sub-query failed
    cancelled: bool = False


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #

def split_citations(text: str) -> tuple[str, list[str]]:
    """Split a ``server_tools`` result into (body, citations).

    ``web_search`` / ``x_search`` append ``\\n\\n--- citations ---\\n`` + a
    newline-joined URL list; a result without the marker has no citations.
    """
    if _CITATIONS_MARKER in text:
        body, _, cites = text.partition(_CITATIONS_MARKER)
        urls = [ln.strip() for ln in cites.splitlines() if ln.strip()]
        return body.strip(), urls
    return text.strip(), []


def _dedupe(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for s in seq:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _noop_log(_msg: str) -> None:
    pass


# --------------------------------------------------------------------------- #
#  The coordinator
# --------------------------------------------------------------------------- #

def run_deep_search(
    question: str,
    *,
    clients: Any = None,
    cfg: Any = None,
    project: Any = None,
    pool: Any = None,
    # Injectable seams (default to the real implementations below).
    plan_fn: Optional[Callable[[str, list[Finding]], list[SubQuery]]] = None,
    search_fn: Optional[Callable[[SubQuery], Finding]] = None,
    investigate_fn: Optional[Callable[[SubQuery], Finding]] = None,
    synth_fn: Optional[Callable[[str, list[Finding]], tuple[str, list[str]]]] = None,
    # Budget + control.
    max_subqueries: int = DEFAULT_MAX_SUBQUERIES,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    max_parallel: Optional[int] = None,
    synthesize: bool = True,
    warm_start: Optional[list[str]] = None,
    on_progress: Optional[Callable[[DeepSearchProgress], None]] = None,
    on_log: Optional[Callable[[str], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
    gaggle: Optional[str] = None,
    gig: Optional[str] = None,
) -> DeepSearchResult:
    """Run a coordinated deep search for *question*; return a cited answer.

    *warm_start* seeds the first wave with sub-query strings (the escalation
    warm-start from :func:`xlii.chat_router.maybe_escalate`). *cancelled* is
    polled between waves for cooperative cancellation (matching the fleet job's
    ``cancel_requested``); the fan-out already in flight is not force-killed.
    *gig* / *gaggle* pin investigate hops (call-site wins over
    ``gigwork.defaults.investigate_gig`` / ``investigate_gaggle``).
    """
    log = on_log or _noop_log
    is_cancelled = cancelled or (lambda: False)
    plan_fn = plan_fn or _make_default_plan(clients, cfg)
    search_fn = search_fn or _make_default_search(clients, cfg)
    investigate_fn = investigate_fn or _make_default_investigate(
        clients, cfg, project, pool, gig=gig, gaggle=gaggle,
    )
    synth_fn = synth_fn or _make_default_synth(clients, cfg)
    parallel = max(1, int(max_parallel or getattr(cfg, "max_parallel_workers", 4) or 4))

    result = DeepSearchResult(question=question)

    # Wave 1 plan — seeded by the escalation warm-start if present.
    if warm_start:
        subqueries = [SubQuery(q, KIND_SEARCH, "web") for q in warm_start if q.strip()]
    else:
        subqueries = list(plan_fn(question, []))

    for round_no in range(1, max_rounds + 1):
        if is_cancelled():
            result.cancelled = True
            break
        if not subqueries:
            break
        if len(subqueries) > max_subqueries:
            log(f"deep-search: wave {round_no} planned {len(subqueries)} "
                f"sub-queries; capping to {max_subqueries}")
            subqueries = subqueries[:max_subqueries]

        wave = _fan_out(
            subqueries, search_fn, investigate_fn, parallel,
            base_done=len(result.findings),
            grand_total=len(result.findings) + len(subqueries),
            on_progress=on_progress,
            should_abort=is_cancelled,
        )
        result.findings.extend(wave)
        result.rounds = round_no

        # Plan the next (gap-filling) wave from everything gathered so far; an
        # empty list means "coverage is sufficient — stop". No extra wave on the
        # final round.
        if round_no < max_rounds and not is_cancelled():
            subqueries = list(plan_fn(question, result.findings))
        else:
            subqueries = []

    result.partial = any(not f.ok for f in result.findings)
    result.citations = _dedupe([c for f in result.findings for c in f.citations])

    # Synthesis — even a partial / cancelled run synthesizes from what landed.
    # synthesize=False leaves answer empty for a caller that will synthesize
    # itself from result.findings (the escalation/tool path, where the turn's own
    # model writes the final answer — no second coordinator synth call).
    ok_findings = [f for f in result.findings if f.ok]
    if not synthesize:
        result.answer = ""
    elif ok_findings:
        answer, _gaps = synth_fn(question, result.findings)
        result.answer = answer
    elif result.cancelled:
        result.answer = "(deep search cancelled before any results returned)"
    else:
        result.answer = "(deep search returned no usable results)"
    return result


def _fan_out(
    subqueries: list[SubQuery],
    search_fn: Callable[[SubQuery], Finding],
    investigate_fn: Callable[[SubQuery], Finding],
    parallel: int,
    *,
    base_done: int,
    grand_total: int,
    on_progress: Optional[Callable[[DeepSearchProgress], None]],
    should_abort: Optional[Callable[[], bool]] = None,
) -> list[Finding]:
    """Run one wave of sub-queries concurrently; a raised error becomes a failed
    Finding (never crashes the wave). Results preserve input order."""

    def _run_one(sq: SubQuery) -> Finding:
        try:
            fn = investigate_fn if sq.kind == KIND_INVESTIGATE else search_fn
            return fn(sq)
        except Exception as e:  # a single sub-query failing must not sink the wave
            return Finding(subquery=sq, error=f"{type(e).__name__}: {e}")

    findings: list[Optional[Finding]] = [None] * len(subqueries)
    statuses: list[str] = ["running"] * len(subqueries)

    def _emit(done: int) -> None:
        if on_progress is not None:
            items = [(sq.label, statuses[i]) for i, sq in enumerate(subqueries)]
            on_progress(DeepSearchProgress(done=base_done + done, total=grand_total, items=items))

    _emit(0)
    # No `with`: the context manager's exit is shutdown(wait=True), which would
    # block an abort behind every in-flight investigation. On abort we shut
    # down WITHOUT waiting — queued sub-queries are cancelled; the ones already
    # running finish in the background and are discarded. That is the honest
    # cost of a mid-wave stop today (a worker agent has no kill switch); the
    # TURN gets control back in ≤ the poll interval instead of minutes.
    ex = ThreadPoolExecutor(max_workers=min(parallel, len(subqueries)),
                            thread_name_prefix="xlii-deepsearch")
    try:
        futs = {ex.submit(_run_one, sq): i for i, sq in enumerate(subqueries)}
        done = 0
        for fut in list(futs):
            i = futs[fut]
            while True:
                if should_abort is not None and should_abort():
                    for pending in futs:
                        pending.cancel()
                    return [f for f in findings if f is not None]
                try:
                    f = fut.result(timeout=0.5)
                    break
                except FuturesTimeoutError:
                    continue
            findings[i] = f
            statuses[i] = "done" if f.ok else "error"
            done += 1
            _emit(done)
        return [f for f in findings if f is not None]
    finally:
        ex.shutdown(wait=False, cancel_futures=True)


# --------------------------------------------------------------------------- #
#  Default (production) seam implementations — model / server-tool / agent glue.
#  Untouched by tests, which inject fakes for every seam.
# --------------------------------------------------------------------------- #

def _coordinator_model(cfg: Any) -> Optional[str]:
    """The reasoning model the coordinator plans + synthesizes with."""
    try:
        from xlii.model_profiles import get_model_profile
        return get_model_profile(cfg, "reason").get("chat")
    except Exception:
        get = getattr(cfg, "get_model_for_role", None)
        return get("chat") if callable(get) else None


def resolve_investigate_hire(
    cfg: Any,
    *,
    gig: Optional[str] = None,
    gaggle: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Pick the investigate brain: call-site ``gig=``/``gaggle=`` wins, else config.

    Returns ``(gig_name, gaggle_name)``. Both None means the home worker.
    Raises :class:`~xlii.chat_backend.GigError` if both pins are set at the
    call site, or both ``gigwork.defaults.investigate_*`` keys are set when
    the call site is unset. Call-site pins skip the defaults entirely, so a
    single explicit ``gig=`` still wins over a conflicting defaults pair.
    """
    from xlii.chat_backend import GigError, gigwork_investigate_defaults

    gig_name = (gig or "").strip() or None
    gaggle_name = (gaggle or "").strip() or None
    if gig_name and gaggle_name:
        raise GigError("pass gig= or gaggle=, not both")
    if gig_name or gaggle_name:
        return gig_name, gaggle_name
    d_gig, d_gaggle = gigwork_investigate_defaults(cfg)
    if d_gig and d_gaggle:
        raise GigError(
            "gigwork.defaults: set investigate_gig or investigate_gaggle, not both"
        )
    return (d_gig or None, d_gaggle or None)


def _make_default_search(clients: Any, cfg: Any) -> Callable[[SubQuery], Finding]:
    from xlii import server_tools

    def _search(sq: SubQuery) -> Finding:
        if sq.channel == "x":
            raw = server_tools.x_search(clients, cfg, sq.query)
        else:
            raw = server_tools.web_search(clients, cfg, sq.query)
        body, cites = split_citations(raw)
        return Finding(subquery=sq, text=body, citations=cites)

    return _search


def _make_default_investigate(
    clients: Any, cfg: Any, project: Any, pool: Any,
    *, gig: Optional[str] = None, gaggle: Optional[str] = None,
):
    from xlii.chat_backend import GigError, gig_allowlist, resolve_gig_backend
    from xlii.worker_agent import WorkerAgent

    hire_error: Optional[str] = None
    gig_name: Optional[str] = None
    gaggle_name: Optional[str] = None
    gig_backend = None
    try:
        gig_name, gaggle_name = resolve_investigate_hire(cfg, gig=gig, gaggle=gaggle)
        if gig_name:
            allow = gig_allowlist(cfg)
            if gig_name not in allow:
                allowed = ", ".join(sorted(allow)) or "(none — gigwork.defaults.allow is empty)"
                raise GigError(
                    f"gig {gig_name!r} is not in gigwork.defaults.allow "
                    f"— hireable: {allowed}"
                )
            gig_backend = resolve_gig_backend(cfg, gig_name)
        elif gaggle_name:
            from xlii.jam import orchestrator_may_run_gaggle
            orchestrator_may_run_gaggle(cfg, gaggle_name)
    except GigError as e:
        hire_error = str(e)

    def _investigate(sq: SubQuery) -> Finding:
        if hire_error:
            return Finding(subquery=sq, error=hire_error)
        if gaggle_name:
            from xlii.jam import run_jam

            try:
                result = run_jam(
                    gaggle_name, sq.query,
                    cfg=cfg, project=project, clients=clients, pool=pool,
                )
            except GigError as e:
                return Finding(subquery=sq, error=str(e))
            except Exception as e:
                return Finding(subquery=sq, error=f"{type(e).__name__}: {e}")
            return Finding(subquery=sq, text=result.merged)

        if gig_backend is not None:
            # Foreign brain: explore kit, read-only, no xAI pool draw, and a
            # failed hire never silently becomes a home worker.
            try:
                worker = WorkerAgent(
                    clients=clients,
                    project=project,
                    cfg=cfg,
                    role="explore",
                    worker_writes=False,
                    chat_backend=gig_backend,
                )
                text, _call = worker.run(sq.query)
            except Exception as e:
                return Finding(subquery=sq, error=f"{type(e).__name__}: {e}")
            body, cites = split_citations(text)
            return Finding(subquery=sq, text=body, citations=cites)

        # Home plane — a read-only general worker (search + reason + read).
        # Draw a client from the pool when one is available, else reuse the
        # coordinator's.
        wclients = pool.acquire() if pool is not None else clients
        try:
            worker = WorkerAgent(clients=wclients, project=project, cfg=cfg, role="general")
            text, _call = worker.run(sq.query)
        except Exception as e:
            if pool is not None:
                from xlii.pool import is_auth_failure
                if is_auth_failure(e):
                    pool.report_auth_failure(wclients)
            raise
        if pool is not None:
            pool.report_success(wclients)
        body, cites = split_citations(text)
        return Finding(subquery=sq, text=body, citations=cites)

    return _investigate


def _make_default_plan(clients: Any, cfg: Any):
    """Coordinator plan turn → sub-queries. Defensive: any failure or unparyable
    output degrades to a single web search of the raw question."""
    import json

    from xlii.turn_prompt import load_prompt

    def _plan(question: str, prior: list[Finding]) -> list[SubQuery]:
        model = _coordinator_model(cfg)
        if model is None or clients is None:
            return [SubQuery(question, KIND_SEARCH, "web")] if not prior else []
        try:
            directive = load_prompt("deep-search-coordinator")
        except Exception:
            directive = ""
        if prior:
            gathered = "\n\n".join(
                f"- {f.subquery.query}: {f.text[:400]}" for f in prior if f.ok
            )
            user = (
                f"Question: {question}\n\nFindings so far:\n{gathered}\n\n"
                "List ONLY the remaining coverage gaps as more sub-queries "
                '(JSON array of {"query","kind","channel"}). Empty array if the '
                "findings already cover the question."
            )
        else:
            user = (
                f"Question: {question}\n\nDecompose into focused sub-queries as a "
                'JSON array of {"query","kind"(search|investigate),"channel"(web|x)}.'
            )
        try:
            resp = clients.chat.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": directive},
                          {"role": "user", "content": user}],
                temperature=0.3,
            )
            raw = resp.choices[0].message.content or "[]"
            data = json.loads(raw[raw.find("["): raw.rfind("]") + 1] or "[]")
        except Exception:
            return [SubQuery(question, KIND_SEARCH, "web")] if not prior else []
        out: list[SubQuery] = []
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict) or not str(item.get("query", "")).strip():
                continue
            kind = item.get("kind") if item.get("kind") in (KIND_SEARCH, KIND_INVESTIGATE) else KIND_SEARCH
            channel = "x" if item.get("channel") == "x" else "web"
            out.append(SubQuery(str(item["query"]).strip(), kind, channel))
        return out

    return _plan


def _make_default_synth(clients: Any, cfg: Any):
    """Coordinator synthesis turn → (answer, gaps). Defensive fallback = a plain
    concatenation of findings when the model is unavailable."""
    from xlii.turn_prompt import load_prompt

    def _synth(question: str, findings: list[Finding]) -> tuple[str, list[str]]:
        ok = [f for f in findings if f.ok]
        model = _coordinator_model(cfg)
        if model is None or clients is None:
            joined = "\n\n".join(f"{f.subquery.query}: {f.text}" for f in ok)
            return joined, []
        try:
            directive = load_prompt("deep-search-coordinator")
        except Exception:
            directive = ""
        numbered_sources: list[str] = []
        for f in ok:
            for c in f.citations:
                if c not in numbered_sources:
                    numbered_sources.append(c)
        src_block = "\n".join(f"[{i+1}] {u}" for i, u in enumerate(numbered_sources))
        body = "\n\n".join(f"### {f.subquery.query}\n{f.text}" for f in ok)
        user = (
            f"Question: {question}\n\nFindings:\n{body}\n\nSources:\n{src_block}\n\n"
            "Write a clear, cited answer to the question. Cite inline as [n] "
            "mapping to the sources above. Flag conflicts and thin coverage."
        )
        try:
            resp = clients.chat.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": directive},
                          {"role": "user", "content": user}],
                temperature=0.5,
            )
            return (resp.choices[0].message.content or "").strip(), []
        except Exception:
            joined = "\n\n".join(f"{f.subquery.query}: {f.text}" for f in ok)
            return joined, []

    return _synth
