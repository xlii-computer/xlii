"""/howto: the conversational mode for asking xlii about itself.

Entering howto mode attaches help topics to the system prompt and flips the
surface to talk-primary — bare input goes straight to the AI, no ``?`` needed,
like chat mode (``_is_shell_primary`` reads the ``howto_mode`` flag). The status
bar shows ``howto`` (blue), and the ``help`` model role is used (cheap by
default — not the persona ``chat`` slot).

``/howto fix <symptom>`` runs ``xlii doctor`` then searches the corpus. When
doctor emits a whitelisted runnable fix, an elevated session (``/admin unlock``)
may confirm and apply it; unelevated stays read-only. A miss reconstructs the
query into a real question and queues it for the AI.

Content layers:

- **Bare ``/howto``** — bundled operator guide (``xlii/prompts/howto.md``,
  overridable via ``.xlii/prompts/howto.md``) + topic index + a compact index of
  the current REPL's live slash-command NAMES. Per-command detail is pulled on
  demand by the ``command_help`` agent tool, not pushed into the prompt: the
  full ``get_repl_help`` block was 17 KB of a 26 KB attachment re-sent every
  turn, and that weight was the latency operators felt.
- **``/howto <topic>``** — attach a focused help shard (install, first-session,
  troubleshoot, …) from the bundled corpus in ``xlii/help/``.
- **``/howto latest [topic]``** — fetch manifest + topic(s) from GitHub
  (``docs/help/`` on the repo's default branch); fall back to bundled on failure.
  Bare ``/howto latest`` first warms the WHOLE corpus cache with one tarball
  fetch (``sync_corpus_cache``), making every topic offline-available; any sync
  failure degrades to the per-topic path.

The guide rides the same attachment seam as ``/doc`` (``attach_doc``), so it is
durable, shows up in ``/attachments``, and can also be removed with ``/undoc howto``.
"""

from __future__ import annotations

from typing import Any, Optional

from xlii.commands import REPLCommand, iter_repl_commands, register_repl_command
from xlii.help_corpus import (
    HelpManifest,
    fetch_manifest,
    fetch_topic_body,
    format_topic_attachment,
    load_manifest,
    load_topic_body,
    render_topic_index,
    search_corpus,
    sync_corpus_cache,
)

# Question words _reconstruct_question preserves when turning a missed query
# into a prompt (used by the /howto fix miss path).
_INTERROGATIVES = (
    "how ", "what ", "why ", "when ", "where ", "which ", "who ",
    "can ", "could ", "should ", "is ", "are ", "do ", "does ", "did ",
)

# Reserved attachment name — also why `/undoc howto` works for free.
_DOC_NAME = "howto"

_LATEST_WORDS = {"latest", "web", "github", "remote", "--latest"}
_OFF_WORDS = {"off", "detach", "unhowto", "--off"}
_WIKI_WORDS = {"wiki"}

_FRAMING = (
    "The operator is asking how to use xlii (this tool) itself. Treat the guide "
    "below as the authority on xlii's commands, flags, and workflows. Be concrete: "
    "name exact slash commands and `xlii` invocations, and prefer an in-session "
    "slash command over a raw shell command where one exists.\n\n"
    "The command list below is NAMES ONLY — the complete vocabulary for this "
    "build, deliberately without usage lines so the guide stays light. When you "
    "need a command's exact usage, flags, aliases, or description, call the "
    "`command_help` tool with its name and quote what comes back. Never "
    "reconstruct a flag from memory; an unnamed command does not exist here.\n\n"
    "Lean hard on `/describe <name>`: it introspects any slash command, agent tool, "
    "or plugin from the LIVE registry (always accurate for THIS build) and appends an "
    "expansive, always-current description pulled from GitHub. Suggest it whenever the "
    "operator wants more depth on a command, and prefer it over reciting flags that "
    "could be stale. Useful entry points: `/describe modes` (the plan/rail/loop "
    "decision tree), `/describe <cmd>` for any command below, and `/howto <topic>` for "
    "task guides. If something isn't covered here, point to `/help`, `/howto "
    "troubleshoot`, or `xlii doctor` rather than guessing.\n\n"
    "Questions about how THIS project has actually been worked — its history, past "
    "decisions, or which files changed and why — live in the project's journal, not "
    "these tool docs: route the operator to `/mojo <question>` (mojo answers with "
    "its own memory fused with this project's journal + wiki)."
)


def _live_commands(repl: str) -> str:
    """The current REPL's slash-command NAMES — a compact, build-accurate index.

    This used to fence the whole ``get_repl_help(repl)`` block (17 KB of a 26 KB
    attachment) into the system prompt on EVERY howto turn; that prompt weight
    was the sluggishness the operator felt, since time-to-first-token pays for
    it again each turn. Names cost ~1 KB and still make the model's vocabulary
    exact for this build — it cannot name a command that doesn't exist. The
    per-command detail moved to the ``command_help`` agent tool (pull, not
    push), which reads the same registry, so nothing was lost but the weight.
    """
    try:
        commands = [c for c in iter_repl_commands() if repl in c.repls]
    except Exception:
        return ""
    if not commands:
        return ""
    names = sorted(
        f"/{c.name}" + (f" ({', '.join('/' + a for a in c.aliases)})" if c.aliases else "")
        for c in {c.name: c for c in commands}.values()
    )
    label = "chat" if repl == "chat" else "code"
    return (
        f"## Your slash commands (`{label}` REPL, this build)\n\n"
        "This is the COMPLETE vocabulary — aliases in parentheses. For any one "
        "command's exact usage, flags, description, and aliases, call the "
        "`command_help` tool with its name. Do not guess flags from these names.\n\n"
        + ", ".join(names)
    )


def _local_content(repl: str, xli_dir) -> str:
    """Framing + bundled guide + topic index + live command list."""
    from xlii.agent import load_prompt

    try:
        guide = load_prompt(_DOC_NAME, xli_dir)
    except OSError:
        guide = ""
    try:
        index = render_topic_index(load_manifest())
    except Exception:
        index = ""
    parts = [_FRAMING, guide, index, _live_commands(repl)]
    return "\n\n".join(p for p in parts if p).strip()


def _topic_content(
    topic_id: str,
    repl: str,
    xli_dir,
    *,
    remote: bool = False,
) -> tuple[str, str]:
    """Build attachment body for one topic. Returns (content, source_label)."""
    if remote:
        manifest = fetch_manifest()
        body = fetch_topic_body(manifest, topic_id)
        source = f"latest from GitHub ({manifest.repo}@{manifest.ref})"
    else:
        manifest = load_manifest()
        body, source = load_topic_body(manifest, topic_id, xli_dir=xli_dir)
    parts = [
        _FRAMING,
        format_topic_attachment(manifest, topic_id, body, source=source),
        _live_commands(repl),
    ]
    return "\n\n".join(p for p in parts if p).strip(), source


def _attachment_owner(ctx: dict[str, Any]):
    """REPLState (preferred) or the legacy Agent — whatever holds attached_docs."""
    state = ctx.get("state")
    if state is not None and hasattr(state, "attached_docs"):
        return state
    return ctx.get("agent")


def _detach(owner) -> bool:
    if owner is None:
        return False
    if hasattr(owner, "detach_doc"):
        return owner.detach_doc(_DOC_NAME)
    before = len(owner.attached_docs)
    owner.attached_docs = [(n, c) for n, c in owner.attached_docs if n != _DOC_NAME]
    return len(owner.attached_docs) < before


def _reattach(owner, content: str) -> int:
    """(Re)attach, replacing any prior copy so re-running /howto refreshes it."""
    _detach(owner)
    if hasattr(owner, "attach_doc"):
        owner.attach_doc(_DOC_NAME, content)
    else:
        owner.attached_docs.append((_DOC_NAME, content))
    return len(content)


_WARMED = False


def _warm_corpus() -> None:
    """Warm the WHOLE help-corpus cache in the background, once per process.

    Only 8 of the manifest's topics are bundled; the other 13 resolve through
    ``load_topic_body``'s remote leg, so the FIRST ``/howto <topic>`` on an
    extended shard used to pay a GitHub round-trip mid-turn. ``sync_corpus_cache``
    fetches the lot in one tarball, so doing it off the turn path — a daemon
    thread fired when howto mode is entered — turns that latency into nothing
    the operator waits for.

    Best-effort by construction: every failure is swallowed (offline is the
    normal case for this call) and nothing is printed either way — a warm is not
    an event, and the read path already degrades to bundled/cached. Idempotent
    per process so re-entering howto mode never re-fetches.
    """
    global _WARMED
    if _WARMED:
        return
    _WARMED = True

    def _run() -> None:
        try:
            sync_corpus_cache()
        except Exception:
            pass  # offline / rate-limited / no repo — the read path still works

    try:
        import threading

        threading.Thread(target=_run, name="xlii-help-warm", daemon=True).start()
    except Exception:
        pass  # a thread we cannot start is a warm we simply skip


def _set_mode(ctx: dict[str, Any], on: bool) -> None:
    """Flip the howto overlay flag on REPLState or Agent (same SessionState).

    Entering the mode also kicks the off-turn corpus warm (see _warm_corpus) —
    this is THE entry chokepoint every /howto path funnels through, so the warm
    can't be forgotten by a new subcommand."""
    if on:
        _warm_corpus()
    for obj in (ctx.get("state"), ctx.get("agent")):
        if obj is not None and hasattr(obj, "howto_mode"):
            obj.howto_mode = on
            return


def _print_topic_list(console, manifest: HelpManifest) -> None:
    console.print("[dim]topics:[/dim] " + ", ".join(
        f"[cyan]{tid}[/cyan]" for tid in manifest.topic_ids() if tid != "index"
    ))


def _resolve_topic_arg(manifest: HelpManifest, arg: str) -> Optional[str]:
    return manifest.resolve(arg)


def _get_arg(parts: list[str], index: int, default: str = "") -> str:
    return parts[index] if index < len(parts) else default


def _journal_nudge(ctx: dict[str, Any]) -> str:
    """A one-line pointer to ``/mojo`` — shown only when the project journal is live.

    ``/howto`` teaches how the *tool* works; the journal records how *this project*
    has actually been worked. When both are on we surface that second self-model so
    the operator can cross over to ``/mojo`` (the journal, its memory FUSED with this
    project's journal + wiki — the retired ``/askjo`` door). Read-only on
    ``state.journal`` (xlii's published seam) and silent otherwise: ``None`` in chat
    (no journal) and in the code REPL with journaling off, so the banner is
    byte-for-byte unchanged whenever the journal isn't recording."""
    state = ctx.get("state")
    journal = getattr(state, "journal", None) if state is not None else None
    if journal is None:
        return ""
    try:
        recording = journal.is_recording()
    except Exception:
        recording = bool(getattr(journal, "code_on", False))
    if not recording:
        return ""
    return (
        "[dim]📓 journal on · [/dim][cyan]/mojo <q>[/cyan]"
        "[dim] — how this project's actually been worked.[/dim]"
    )


def _howto_handler(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    owner = _attachment_owner(ctx)
    if owner is None:
        console.print("[red]/howto needs an active session[/red]")
        return True

    repl = ctx.get("command_scope") or ("chat" if ctx.get("persona") else "code")
    xli_dir = getattr(ctx.get("project"), "xli_dir", None)

    parts = line.split(maxsplit=2)
    arg = _get_arg(parts, 1).strip().lower()
    topic_arg = _get_arg(parts, 2).strip().lower() if arg in _LATEST_WORDS else ""

    # /howto fix [symptom] — diagnose then point at help (Vector A self-repair).
    # Shadows the legacy "fix" topic alias; the troubleshoot guide is still at
    # /howto troubleshoot.
    if arg == "fix":
        symptom = _get_arg(parts, 2).strip()
        return _howto_fix(ctx, symptom)

    # /howto wiki [question] — the TOOL door's wiki: xlii's shipped self-docs
    # (xwiki://, the read-only vendor tier — constant in every project). The
    # project's own wiki answers through /askjo, never here.
    if arg in _WIKI_WORDS:
        return _howto_xwiki(ctx, _get_arg(parts, 2).strip())

    # /howto project [list|new|edit|rm <topic>] — author per-project help
    # overrides at .xlii/help/topics/<topic>.md (Phase 3). These win over the
    # bundled corpus in the read path (help_corpus load order: project → bundle).
    if arg == "project":
        return _howto_project(ctx, parts, xli_dir)

    # /howto off — leave howto mode + detach the guide
    if arg in _OFF_WORDS:
        was_on = getattr(ctx.get("state") or owner, "howto_mode", False)
        removed = _detach(owner)
        _set_mode(ctx, False)
        if was_on or removed:
            console.print("[green]✓[/green] [bold #5f9bff]howto mode[/bold #5f9bff] off — "
                          "[dim]system prompt back to normal; type as usual.[/dim]")
        else:
            console.print("[dim](howto mode wasn't on)[/dim]")
        return True

    # /howto latest [topic] — fetch from GitHub
    if arg in _LATEST_WORDS:
        synced = None
        if not topic_arg:
            # Bare `/howto latest`: one tarball fetch warms the WHOLE corpus
            # cache first (sync_corpus_cache), so the index attach below is
            # served from the just-warmed slots and every topic is now
            # offline-available. On ANY failure fall through to today's
            # per-topic path unchanged — degradation, not failure.
            try:
                synced = sync_corpus_cache()
            except Exception:
                synced = None
        if synced is not None:
            manifest = synced.manifest
            if synced.skipped:
                console.print(
                    f"[green]✓[/green] help corpus already current (@{synced.sha[:7]})"
                )
            else:
                console.print(
                    f"[green]✓[/green] help corpus synced — {synced.files} files @ "
                    f"{manifest.repo}@{synced.sha[:7]} "
                    "(whole corpus now offline-available)"
                )
        else:
            try:
                manifest = fetch_manifest()
            except Exception as e:
                console.print(f"[red]fetch failed: {e}[/red]")
                console.print("[dim]falling back — run [/dim][cyan]/howto[/cyan]"
                              "[dim] or [/dim][cyan]/howto <topic>[/cyan]"
                              "[dim] for bundled help[/dim]")
                return True

        tid = _resolve_topic_arg(manifest, topic_arg) if topic_arg else None
        if topic_arg and tid is None:
            console.print(f"[red]unknown help topic: {topic_arg!r}[/red]")
            _print_topic_list(console, manifest)
            return True

        if synced is None:
            console.print("[dim]fetching help from GitHub…[/dim]")
        try:
            if tid:
                content, source = _topic_content(tid, repl, xli_dir, remote=True)
                label = manifest.topics[tid].title
            else:
                index_body = fetch_topic_body(manifest, "index")
                content = "\n\n".join([
                    _FRAMING,
                    format_topic_attachment(manifest, "index", index_body, source="latest from GitHub"),
                    render_topic_index(manifest),
                    _live_commands(repl),
                ]).strip()
                label = "help index"
                source = "GitHub"
        except Exception as e:
            console.print(f"[red]fetch failed: {e}[/red]")
            console.print("[dim]try [/dim][cyan]/howto[/cyan][dim] for bundled help[/dim]")
            return True

        size = _reattach(owner, content)
        _set_mode(ctx, True)
        console.print(
            f"[green]✓[/green] [bold #5f9bff]howto mode[/bold #5f9bff] on with "
            f"[cyan]{label}[/cyan] [dim]({source}, ~{size // 1000} KB in the system "
            f"prompt while on) — just type your question. "
            f"[/dim][cyan]/howto off[/cyan][dim] to leave.[/dim]"
        )
        return True

    # /howto <topic> — bundled help shard
    if arg:
        try:
            manifest = load_manifest()
        except Exception as e:
            console.print(f"[red]help corpus unavailable: {e}[/red]")
            return True

        full = " ".join(parts[1:]).strip().lower()
        tid = _resolve_topic_arg(manifest, full)
        if tid is None and full != arg:
            tid = _resolve_topic_arg(manifest, arg)
        if tid is None:
            console.print(f"[dim]unknown /howto option: {arg!r}[/dim]")
            console.print("[dim]usage: [/dim][cyan]/howto[/cyan][dim] | "
                          "[/dim][cyan]/howto <topic>[/cyan][dim] | "
                          "[/dim][cyan]/howto latest [topic][/cyan][dim] | "
                          "[/dim][cyan]/howto off[/cyan]")
            _print_topic_list(console, manifest)
            return True

        try:
            content, _ = _topic_content(tid, repl, xli_dir, remote=False)
        except (KeyError, FileNotFoundError) as e:
            console.print(f"[red]topic unavailable: {e}[/red]")
            return True

        size = _reattach(owner, content)
        _set_mode(ctx, True)
        title = manifest.topics[tid].title
        console.print(
            f"[green]✓[/green] [bold #5f9bff]howto mode[/bold #5f9bff] on — "
            f"[cyan]{title}[/cyan] attached [dim](~{size // 1000} KB while on)[/dim]. "
            f"Just type your question (no [cyan]?[/cyan] needed). "
            f"[cyan]/howto latest {tid}[/cyan][dim] for GitHub's newest · "
            f"[/dim][cyan]/howto off[/cyan][dim] to leave.[/dim]"
        )
        return True

    # bare /howto — bundled guide + topic index + live command list
    size = _reattach(owner, _local_content(repl, xli_dir))
    _set_mode(ctx, True)
    try:
        manifest = load_manifest()
        topic_hint = ", ".join(tid for tid in manifest.topic_ids() if tid != "index")
    except Exception:
        topic_hint = "install, first-session, troubleshoot"
    console.print(
        "[green]✓[/green] [bold #5f9bff]howto mode[/bold #5f9bff] on — just type your "
        "question (no [cyan]?[/cyan] needed); answers come from the xlii guide "
        f"[dim](~{size // 1000} KB in the system prompt while on)[/dim]. "
        f"[dim]Topics: {topic_hint}. [/dim]"
        "[cyan]/howto <topic>[/cyan][dim] for a focused guide · "
        "[/dim][cyan]/howto wiki <q>[/cyan][dim] to ask xlii's built-in self-docs · "
        "[/dim][cyan]/describe <cmd>[/cyan][dim] to go deep on any command · "
        "[/dim][cyan]/howto off[/cyan][dim] to leave.[/dim]"
    )
    nudge = _journal_nudge(ctx)
    if nudge:
        console.print(nudge)
    return True


def _print_xwiki_citations(console, hits) -> None:
    """The openable ``xwiki://page#section`` citations — vendor rows wear ⌂
    (shipped/read-only), no trust ladder."""
    console.print("[bold]xwiki[/bold] [dim](⌂ shipped self-docs, this build — open with the "
                  "address)[/dim]")
    for h in hits:
        line = f"  [cyan]⌂ {h.address}[/cyan]"
        if h.snippet:
            line += f"  [dim]{h.snippet}[/dim]"
        console.print(line)


def _open_xwiki_results(question: str) -> bool:
    """Open the vendor scope's ranked results in a side panel (xwiki://?q=… →
    WikiPane results mode; every row selectable). TUI only; False inline."""
    try:
        from xlii.tui import panels
        from xlii.wiki_retrieval import wiki_search_address

        host = panels.current_panel_host()
        if host is None:
            return False
        return bool(host.open_address(wiki_search_address(question, scheme="xwiki")))
    except Exception:
        return False


def _xwiki_retrieval_content(repl: str, hits, root) -> str:
    """The per-question bundle for the AI: framing + citations + the anchored
    vendor sections + the live command list — scoped retrieval instead of the
    whole ~23 KB guide."""
    from xlii.wiki_retrieval import format_citations, wiki_context_block

    shards = wiki_context_block(root, hits)
    parts = [
        _FRAMING,
        format_citations(hits),
        ("## Relevant xlii self-docs (shipped, this build)\n\n" + shards) if shards else "",
        _live_commands(repl),
    ]
    return "\n\n".join(p for p in parts if p).strip()


def _howto_xwiki(ctx: dict[str, Any], question: str) -> bool:
    """/howto wiki [question] — retrieve from xlii's shipped self-wiki.

    Bare form opens the ``xwiki://`` browse pane. With a question: search the
    vendor pages, print openable ``xwiki://page#section`` citations, open the
    ranked results to the side, attach just those sections as the turn's
    context, and queue the question for the AI to answer citing them. Falls back
    to the general guide when nothing matches (or no bundle is present)."""
    from xlii.selfwiki import selfwiki_root
    from xlii.wiki_retrieval import search_self_wiki, wiki_context_block

    console = ctx["console"]
    owner = _attachment_owner(ctx)
    repl = ctx.get("command_scope") or ("chat" if ctx.get("persona") else "code")

    root = selfwiki_root()
    if root is None:
        console.print("[dim]no shipped self-wiki in this build — using the guide instead.[/dim]")
        if question:
            return _queue_ai_question(ctx, _reconstruct_question(question))
        return True

    if not question:
        opened = False
        try:
            from xlii.tui import panels

            host = panels.current_panel_host()
            if host is not None:
                opened = bool(host.open_doorway("xwiki"))
        except Exception:
            opened = False
        if opened:
            console.print("[green]✓[/green] xwiki panel docked — [dim]xlii's shipped "
                          "self-docs; select a page, or ask [/dim]"
                          "[cyan]/howto wiki <question>[/cyan]")
        else:
            console.print("[dim]ask xlii's self-wiki: [/dim][cyan]/howto wiki <question>[/cyan]")
        return True

    hits = search_self_wiki(root, question, limit=4, scheme="xwiki")
    wiki_context = wiki_context_block(root, hits) if hits else ""
    if not wiki_context:
        console.print(f"[dim]no self-doc matches [/dim][cyan]{question}[/cyan]"
                      "[dim] — asking with the general guide instead.[/dim]")
        return _queue_ai_question(ctx, _reconstruct_question(question))

    _print_xwiki_citations(console, hits)
    opened = _open_xwiki_results(question)

    if owner is not None:
        _reattach(owner, _xwiki_retrieval_content(repl, hits, root))
        _set_mode(ctx, True)

    state = ctx.get("state")
    q = _reconstruct_question(question)
    if state is not None:
        from xlii.repl_state import queue_pending_input

        queue_pending_input(state, q, replace=False)
        tail = (" · [dim]results opened to the side — select any to read[/dim]"
                if opened else "")
        console.print(
            f"[green]✓[/green] queued: [cyan]{q}[/cyan]{tail}\n"
            "[dim]press Enter to ask — the answer cites the self-docs above "
            "([/dim][cyan]/howto off[/cyan][dim] to leave).[/dim]"
        )
    else:
        console.print(f"[dim]ask directly: [/dim][cyan]?{q}[/cyan]")
    return True


def _run_doctor():
    """Run ``xlii doctor`` (offline) — health + fixes. Returns a DoctorReport
    (or None on failure). Isolated so /howto fix reuses the CLI doctor and
    tests can stub it."""
    from types import SimpleNamespace

    from xlii.cmds.diag import run_doctor

    return run_doctor(SimpleNamespace(online=False, migrate_legacy=False, dry_run=False))


def _filter_runnable(findings, symptom: str):
    """Runnable findings, optionally filtered by symptom tokens in message/fix."""
    from xlii.doctor import DoctorFinding, is_runnable_fix

    runnable = [
        f for f in findings
        if isinstance(f, DoctorFinding)
        and f.severity in ("warn", "bad")
        and is_runnable_fix(f.runnable_fix)
    ]
    if not symptom:
        return runnable
    tokens = [t.lower() for t in symptom.split() if len(t) > 1]
    if not tokens:
        return runnable

    def matches(f) -> bool:
        hay = f"{f.message} {f.fix} {f.fix_cmd}".lower()
        return any(t in hay for t in tokens)

    matched = [f for f in runnable if matches(f)]
    return matched if matched else runnable


def _confirm_fix(prompt: str) -> bool:
    from xlii.tools import _confirm
    try:
        return _confirm(prompt).strip().lower() == "y"
    except Exception:
        return False


def _offer_apply_fixes(ctx: dict[str, Any], findings, symptom: str) -> None:
    """Elevated + confirm → apply whitelisted doctor fixes; else read-only hint."""
    from xlii.doctor import apply_doctor_fix
    from xlii.commands import session_is_elevated

    console = ctx["console"]
    runnable = _filter_runnable(findings, symptom)
    if not runnable:
        return

    console.print(f"\n[bold]runnable fixes[/bold] [dim]({len(runnable)})[/dim]")
    for f in runnable:
        console.print(f"  [yellow]•[/yellow] {f.message}")
        if f.fix and f.fix != f.runnable_fix:
            console.print(f"      [dim]{f.fix}[/dim]")
        console.print(f"      [cyan]{f.runnable_fix}[/cyan]")

    if not session_is_elevated(ctx):
        console.print(
            "[dim]read-only — unlock with [/dim][cyan]/admin unlock[/cyan]"
            "[dim] then re-run [/dim][cyan]/howto fix[/cyan]"
            "[dim] to apply (per-fix confirm).[/dim]"
        )
        return

    project = ctx.get("project")
    cwd = getattr(project, "project_root", None)
    for f in runnable:
        console.print(f"\n[dim]would run:[/dim] [cyan]{f.runnable_fix}[/cyan]")
        if not _confirm_fix("  apply this fix? [y/N] "):
            console.print("[dim]skipped[/dim]")
            continue
        console.print(f"[dim]$ {f.runnable_fix}[/dim]")
        try:
            summary = apply_doctor_fix(f.runnable_fix, cwd=cwd)
            console.print(f"[green]✓[/green] {summary}")
        except Exception as e:
            console.print(f"[red]fix failed: {e}[/red]")


def _howto_fix(ctx: dict[str, Any], symptom: str) -> bool:
    """/howto fix [symptom] = doctor + optional gated apply + corpus search.

    Diagnoses (prints fixes), offers elevated confirm-to-run for whitelisted
    config/CLI repairs, then surfaces matching topics/commands for the symptom.
    If the corpus has nothing, restores the question and queues it for the AI.
    """
    console = ctx["console"]
    console.print("[bold]xlii doctor[/bold] [dim]— health check + fixes[/dim]")
    report = None
    try:
        report = _run_doctor()
    except Exception as e:  # doctor must never crash the REPL
        console.print(f"[dim](doctor unavailable: {e})[/dim]")

    if report is not None:
        _offer_apply_fixes(ctx, report.findings, symptom)

    if not symptom:
        console.print(
            "[dim]name a symptom for targeted help, e.g. [/dim]"
            "[cyan]/howto fix sync fails[/cyan][dim] · or load the guide with [/dim]"
            "[cyan]/howto troubleshoot[/cyan][dim].[/dim]"
        )
        return True

    try:
        hits = search_corpus(load_manifest(), symptom)
    except Exception:
        hits = []
    if hits:
        topic_hits = [h for h in hits if h.kind == "topic"]
        cmd_hits = [h for h in hits if h.kind == "command"]
        console.print(f"\n[bold]related help for[/bold] [cyan]{symptom}[/cyan]")
        for h in (topic_hits + cmd_hits)[:6]:
            if h.kind == "command":
                console.print(f"  [cyan]/{h.name}[/cyan]  [dim]{h.description}[/dim]")
            else:
                console.print(
                    f"  [magenta]{h.name}[/magenta] [dim]topic[/dim]  "
                    f"[dim]([/dim][cyan]/howto {h.name}[/cyan][dim])[/dim]"
                )
        return True

    return _queue_ai_question(ctx, _reconstruct_question(f"fix {symptom}"))


def _reconstruct_question(query: str) -> str:
    """Turn a bare query into a real question so the AI sees one.

    'reset keys' → 'how do I reset keys'; 'to undo' → 'how to undo';
    an already-formed question is left untouched."""
    q = query.strip()
    low = q.lower()
    if low.startswith(_INTERROGATIVES):
        return q
    if low.startswith("to "):
        return "how " + q
    if low.startswith("i "):
        q = q[2:].strip()
    return f"how do I {q}"


def _queue_ai_question(ctx: dict[str, Any], question: str) -> bool:
    """Enter howto mode (guide attached, talk-primary) and queue ``question`` for
    the next prompt — both the inline loop and the TUI consume
    ``state.pending_input``, so Enter sends it straight to the AI."""
    console = ctx["console"]
    owner = _attachment_owner(ctx)
    repl = ctx.get("command_scope") or ("chat" if ctx.get("persona") else "code")
    xli_dir = getattr(ctx.get("project"), "xli_dir", None)
    if owner is not None:
        _reattach(owner, _local_content(repl, xli_dir))
        _set_mode(ctx, True)
    state = ctx.get("state")
    if state is not None:
        from xlii.repl_state import queue_pending_input

        queue_pending_input(state, question, replace=False)
        console.print(
            f"[green]✓[/green] no matching topic — queued: [cyan]{question}[/cyan]\n"
            "[dim]press Enter to ask (howto mode on; [/dim][cyan]/howto off[/cyan]"
            "[dim] to leave).[/dim]"
        )
    else:
        console.print(
            f"[dim]no matching topic — ask directly: [/dim][cyan]?{question}[/cyan]"
        )
    return True


_PROJECT_TOPIC_RE = __import__("re").compile(r"^[a-z0-9][a-z0-9_-]*$")


def _project_topics_dir(xli_dir):
    from pathlib import Path
    return Path(xli_dir) / "help" / "topics"


def _list_project_topics(xli_dir) -> list[str]:
    d = _project_topics_dir(xli_dir)
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.md"))


def _project_topic_template(name: str) -> str:
    return (
        f"# {name}\n\n"
        f"<!-- Project-local help topic. This overrides any bundled `{name}` topic\n"
        "     for THIS project only; /howto reads it before the shipped corpus.\n"
        "     Delete this file (or /howto project rm) to fall back to the bundle. -->\n\n"
        "Write the guidance a teammate would want when they ask about this — "
        "commands, conventions, gotchas specific to this project.\n"
    )


def _howto_project(ctx: dict[str, Any], parts: list[str], xli_dir) -> bool:
    """Author per-project help overrides (.xlii/help/topics/<name>.md)."""
    console = ctx["console"]
    if xli_dir is None:
        console.print("[red]/howto project needs a project (no .xlii dir)[/red]")
        return True

    # `parts` came from split(maxsplit=2), so `new <name>` rides in parts[2].
    sub = _get_arg(parts, 2).strip()
    sub_parts = sub.split(maxsplit=1)
    verb = sub_parts[0].lower() if sub_parts else ""
    name = sub_parts[1].strip().lower() if len(sub_parts) > 1 else ""

    if not verb or verb == "list":
        topics = _list_project_topics(xli_dir)
        if topics:
            console.print("[bold]project help topics[/bold] "
                          "[dim](.xlii/help/topics/)[/dim]")
            for t in topics:
                console.print(f"  [magenta]{t}[/magenta]  "
                              f"[dim](/howto {t} · /howto project edit {t})[/dim]")
        else:
            console.print("[dim]no project help topics yet — "
                          "[/dim][cyan]/howto project new <name>[/cyan]"
                          "[dim] to author one (overrides the bundled topic here).[/dim]")
        return True

    if verb in ("new", "edit"):
        if not name or not _PROJECT_TOPIC_RE.match(name):
            console.print("[red]topic name must be lowercase "
                          "letters/digits/-/_[/red] [dim](e.g. deploy, local-setup)[/dim]")
            return True
        from xlii.atomicio import write_text_atomic
        path = _project_topics_dir(xli_dir) / f"{name}.md"
        created = not path.exists()
        if created:
            if verb == "edit":
                console.print(f"[dim]no project topic {name!r} yet — creating it[/dim]")
            path.parent.mkdir(parents=True, exist_ok=True)
            write_text_atomic(path, _project_topic_template(name))
        from xlii.editor import open_for_edit
        try:
            open_for_edit(path)
        except Exception as e:
            console.print(f"[yellow]couldn't open $EDITOR ({e})[/yellow] — "
                          f"edit [cyan]{path}[/cyan] directly")
        verb_txt = "created" if created else "opened"
        console.print(f"[green]✓[/green] {verb_txt} project topic [magenta]{name}[/magenta] — "
                      f"[cyan]/howto {name}[/cyan][dim] loads it (overrides the bundle here).[/dim]")
        return True

    if verb in ("rm", "remove", "delete"):
        if not name:
            console.print("[dim]usage: /howto project rm <name>[/dim]")
            return True
        path = _project_topics_dir(xli_dir) / f"{name}.md"
        if not path.exists():
            console.print(f"[dim]no project topic {name!r}[/dim]")
            return True
        try:
            path.unlink()
            console.print(f"[green]✓[/green] removed project topic [magenta]{name}[/magenta] "
                          f"[dim](the bundled topic, if any, applies again)[/dim]")
        except OSError as e:
            console.print(f"[red]couldn't remove: {e}[/red]")
        return True

    console.print("[dim]usage: [/dim][cyan]/howto project[/cyan][dim] [list | "
                  "new <name> | edit <name> | rm <name>][/dim]")
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="howto",
            handler=_howto_handler,
            description="Enter howto mode — ask xlii how to use itself (bare input, /howto off to leave)",
            usage="/howto [topic | wiki [question] | fix [symptom] | latest [topic] | project [new|edit|rm <name>] | off]",
            category="session",
        )
    )
