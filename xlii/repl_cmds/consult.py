"""/consult — cross-vendor "second opinion" slash command (revived xli feature).

Sends an optional slice of the current conversation plus a question to a SECOND,
independently-configured AI provider (anthropic, openai, or xai, via urllib) and prints
the reply under a [consult · <model>] header. Never a tool, never appended to
history — a one-shot outside opinion.

With ``--via <harness>``, routes through an external agent harness instead of
a direct API key — the built-ins (cursor, claude, codex, grok) plus any
community harness the project registers in ``.xlii/harness.local.py`` (e.g.
kimi — multi-model access (Cursor), Claude/Codex headless, ACP CLIs (tier
labeled in the header).

Deliberately a different vendor than the primary (xAI): the value is independence.
The simple path is a BINDING to the gigwork registry — ``/consult --set-to
<gigworker>`` writes ``"judges": {"consult": {"kind": "cross_vendor", "gig":
"<name>"}}``, so the endpoint/key/model stay defined once under
``gigwork.providers`` and any gig preset (Kimi, DeepSeek, Gemini, Ollama, …)
can judge. Inline profiles remain for hand-rolled setups:
    "judges": {"anthropic": {"kind": "cross_vendor", "provider": "anthropic",
               "model": "claude-sonnet-4-6", "api_key_env": "ANTHROPIC_API_KEY"}}
    "consult": {"default_judge": "anthropic"}   # optional; else a judge named "consult"
then `export ANTHROPIC_API_KEY=...`. (The legacy `secondary_ai` block is still
honored but deprecated.)
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any, Optional

from xlii.commands import REPLCommand, register_repl_command
from xlii.harness.detect import list_harness_names

def _consult_usage() -> str:
    """The bare-/consult usage hint. Harness names are rendered live (locals
    from .xlii/harness.local.py are loaded before this prints), so community
    harnesses like kimi show up next to the built-ins."""
    names = "|".join(list_harness_names())
    return (
        f"[dim]usage: /consult [--via {names}] [--model <name>] "
        "[--last N | --turns | --full] [--capture] "
        "[--from-verify | --from-peer | --from <path>] <question>[/dim]\n"
        "[dim]       /consult --set-to <gigworker>  — perma-hire a gigwork provider "
        "as the default judge (binds by name; see /gigwork ls)[/dim]\n"
        "[dim]  default: cross-vendor API (judges profile). --via routes through a "
        "harness CLI (built-ins + community harnesses from .xlii/harness.local.py).[/dim]\n"
        "[dim]  history: default none; --turns = last exchange; --last N; --full.[/dim]\n"
        "[dim]  --capture folds the reply into history (default: one-shot, not kept).[/dim]\n"
        "[dim]  --from-verify / --from-peer / --from attach review or file context.[/dim]"
    )


def _slice_history(agent, n_turns: int) -> list[dict]:
    """Filtered conversation slice as plain {role, content} messages.

    n_turns: 0 = none, -1 = all, k>0 = last k user+assistant exchanges. Tool
    calls / tool results / empty-assistant messages are dropped (token savings)."""
    if n_turns == 0:
        return []
    hist = [
        {"role": m["role"], "content": m["content"]}
        for m in (getattr(agent, "history", []) or [])
        if m.get("role") in ("user", "assistant")
        and isinstance(m.get("content"), str)
        and m["content"].strip()
    ]
    if n_turns < 0:
        return hist
    return hist[-2 * n_turns:]


def _split_capture_flag(rest: str) -> tuple[str, bool]:
    """Pull the boolean ``--capture`` / ``--ephemeral`` flag out of the arg string.

    Handled here (not in :func:`_parse`) so the parser's signature stays put for
    the rest of the flag grammar. ``--capture`` folds the reply into history
    (agent awareness); ``--ephemeral`` is the explicit opposite. Order-independent
    and last-one-wins; everything else is left for ``_parse`` to handle.
    """
    try:
        tokens = shlex.split(rest)
    except ValueError:
        tokens = rest.split()
    if "--capture" not in tokens and "--ephemeral" not in tokens:
        return rest, False  # nothing to strip — leave the arg string byte-for-byte
    capture = False
    kept: list[str] = []
    for tok in tokens:
        if tok == "--capture":
            capture = True
        elif tok == "--ephemeral":
            capture = False
        else:
            kept.append(tok)
    return shlex.join(kept), capture


def _parse(
    rest: str,
) -> tuple[int, Optional[tuple[str, Optional[str]]], Optional[str], Optional[str], str, Optional[str]]:
    """Return (n_turns, from_source, via, model, question, error)."""
    n_turns = 0
    from_source: Optional[tuple[str, Optional[str]]] = None
    via: Optional[str] = None
    model: Optional[str] = None
    try:
        tokens = shlex.split(rest)
    except ValueError:
        tokens = rest.split()

    known_harnesses = set(list_harness_names())

    def _claim_source(src):
        nonlocal from_source
        if from_source is not None:
            return False
        from_source = src
        return True

    while tokens and tokens[0].startswith("--"):
        flag = tokens[0]
        if flag == "--full":
            n_turns, tokens = -1, tokens[1:]
        elif flag == "--turns":
            n_turns, tokens = 1, tokens[1:]
        elif flag == "--last" and len(tokens) >= 2 and tokens[1].isdigit():
            n_turns, tokens = int(tokens[1]), tokens[2:]
        elif flag == "--via" and len(tokens) >= 2:
            via, tokens = tokens[1].lower(), tokens[2:]
            if via not in known_harnesses:
                return (0, None, None, None, "", f"unknown harness {via!r} (choose: {', '.join(sorted(known_harnesses))})")
        elif flag == "--model" and len(tokens) >= 2:
            model, tokens = tokens[1], tokens[2:]
        elif flag == "--from-verify":
            if not _claim_source(("verify", None)):
                return (0, None, None, None, "", "only one --from* source allowed")
            tokens = tokens[1:]
        elif flag == "--from-peer":
            if not _claim_source(("peer", None)):
                return (0, None, None, None, "", "only one --from* source allowed")
            tokens = tokens[1:]
        elif flag == "--from" and len(tokens) >= 2:
            if not _claim_source(("file", tokens[1])):
                return (0, None, None, None, "", "only one --from* source allowed")
            tokens = tokens[2:]
        else:
            return (0, None, None, None, "", f"unknown or malformed flag: {flag}")
    return (n_turns, from_source, via, model, " ".join(tokens).strip(), None)


def _load_context(ctx: dict, from_source: tuple[str, Optional[str]]) -> tuple[Optional[str], Optional[str]]:
    """Resolve a --from* source to (text, error)."""
    state = ctx.get("state")
    project = state.project if state else ctx.get("project")
    kind, path = from_source
    if kind == "verify":
        fp = project.xli_dir / "verify-last.md"
    elif kind == "peer":
        fp = project.xli_dir / "peer-last.md"
    else:
        fp = Path(path).expanduser()
    try:
        return (fp.read_text(errors="replace"), None)
    except OSError:
        hint = (
            f"no {kind} report found — run /{kind} in a code session first"
            if kind in ("verify", "peer") else f"cannot read {fp}"
        )
        return (None, hint)


def _project_root(ctx: dict) -> Path | None:
    state = ctx.get("state")
    project = state.project if state else ctx.get("project")
    if project is None:
        return None
    root = getattr(project, "project_root", None)
    return Path(root) if root else None


def _resolve_api_profile(cfg: Any) -> Optional[dict[str, Any]]:
    """Resolve the judge/secondary profile used by the direct-API /consult path."""
    if hasattr(cfg, "consult_profile"):
        try:
            return cfg.consult_profile()
        except RuntimeError:
            # An unresolvable consult profile falls through to the default resolution below.
            pass
    sec = getattr(cfg, "secondary_ai", {}) or {}
    if sec.get("provider"):
        return sec
    return None


def _set_to_gig(console: Any, agent: Any, name: str) -> None:
    """``/consult --set-to <gigworker>`` — perma-hire a gigwork provider as the
    default consult judge. Writes a BINDING (``judges.consult = {gig: name}``),
    never a copy: the endpoint/key/model stay defined once under
    ``gigwork.providers``, and every later /consult (and any loop judge naming
    the profile) follows the registry."""
    from xlii.chat_backend import GigError, gig_providers
    from xlii.config import GlobalConfig

    if not name:
        console.print(
            "[red]/consult --set-to: pass a gigwork provider name (see /gigwork ls)[/red]"
        )
        return
    cfg = GlobalConfig.load()
    try:
        providers = gig_providers(cfg)
    except GigError as e:
        console.print(f"[red]/consult --set-to: {e}[/red]")
        return
    p = providers.get(name)
    if p is None:
        known = ", ".join(sorted(providers)) or "(none configured)"
        console.print(
            f"[red]/consult --set-to: unknown gig provider {name!r} — configured: "
            f"{known}. Add it first: /gigwork add {name}[/red]"
        )
        return
    judges = dict(cfg.judges or {})
    judges["consult"] = {"kind": "cross_vendor", "mode": "verify", "gig": name}
    cfg.judges = judges
    consult = dict(getattr(cfg, "consult", None) or {})
    consult["default_judge"] = "consult"
    cfg.consult = consult
    cfg.save()
    if agent is not None and getattr(agent, "cfg", None) is not None:
        agent.cfg.judges = judges
        agent.cfg.consult = consult
    console.print(
        f"[green]consult →[/green] gigwork[{name}] · {p.model} · ${p.api_key_env}"
    )


def _api_independence_tier(provider: str, profile: Optional[dict[str, Any]]) -> str:
    """Independence tier for a direct-API consult reply.

    xlii is xAI-powered, so an xAI secondary is ``same_vendor``; other providers
    are ``cross_org``. An explicit ``tier`` on the profile wins so configs stay
    honest about the independence level they actually provide."""
    if profile and profile.get("tier"):
        return str(profile["tier"])
    if (provider or "").lower().strip() == "xai":
        return "same_vendor"
    return "cross_org"


def _record_consult(
    state: Any, text: str, *, label: str, question: str, capture: bool
) -> None:
    """Route a consult reply through Vector B's capture seam (#3).

    The last-output buffer is always set (so ``/replay`` can re-show the reply,
    even after a ``clear``) — that is a re-print cache, not history, so it keeps
    /consult's "one-shot, not in history" contract. ``--capture`` additionally
    folds the reply into history for agent awareness. Best-effort: a missing
    state or capture seam never breaks the consult itself.
    """
    if state is None or not (text or "").strip():
        return
    try:
        from xlii.shell_toolkit import capture_output

        capture_output(
            state,
            text,
            source="harness",
            into_history=capture,
            label=label,
            command=question,
        )
    except Exception:
        # Recording the consult in history is bookkeeping -- the answer was already delivered.
        pass


def _consult_handler(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")
    agent = state.agent if state else ctx.get("agent")

    project = state.project if state else ctx.get("project")
    if project is not None and getattr(project, "xli_dir", None) is not None:
        from xlii.harness.detect import load_local_harnesses

        load_local_harnesses(project.xli_dir)

    parts = line.split(maxsplit=1)
    rest = parts[1].strip() if len(parts) > 1 else ""
    if rest.startswith("--set-to"):
        toks = rest.split()
        _set_to_gig(console, agent, toks[1] if len(toks) > 1 else "")
        return True
    rest, capture = _split_capture_flag(rest)
    n_turns, from_source, via, model, question, err = _parse(rest)
    if err:
        console.print(f"[red]/consult: {err}[/red]")
        return True
    if not question:
        console.print(_consult_usage())
        return True

    messages = _slice_history(agent, n_turns)
    if from_source is not None:
        context_text, ctx_err = _load_context(ctx, from_source)
        if ctx_err:
            console.print(f"[yellow]/consult: {ctx_err}[/yellow]")
            return True
        kind = from_source[0]
        messages = [{"role": "user", "content": f"[context from /{kind}]\n\n{context_text}"}] + messages

    if via:
        from xlii.harness import run_ask
        from xlii.harness.brief import HarnessBrief
        from xlii.harness.detect import harness_meta

        meta = harness_meta(via)
        result = run_ask(
            via,
            HarnessBrief(
                kind="consult",
                tier=str(meta["tier"]),
                question=question,
                messages=messages or None,
                project_root=_project_root(ctx),
            ),
            model=model,
        )
        if result.error:
            console.print(f"[yellow]/consult unavailable:[/yellow] {result.error}")
            return True
        header = f"[consult · {via}/{result.model} · {result.tier}]"
        console.print()
        console.print(f"[bold magenta]{header}[/bold magenta]")
        console.print(result.text)
        _record_consult(
            state,
            result.text,
            label=f"consult · {via}/{result.model}",
            question=question,
            capture=capture,
        )
        return True

    from xlii.config import GlobalConfig
    from xlii.secondary_ai import query_with_profile

    cfg = GlobalConfig.load()
    profile = _resolve_api_profile(cfg)
    if model and profile is not None:
        # Honor the user's --model override on the direct-API path; without this
        # the configured default model was silently used instead.
        profile = {**profile, "model": model}
    try:
        resp = query_with_profile(messages, question, profile=profile)
    except Exception as e:
        console.print(f"[yellow]/consult unavailable:[/yellow] {e}")
        return True

    reply = resp.text
    # A gig-bound judge reports its binding (gigwork[name]); the resolved model
    # rides beside it. Legacy inline profiles keep the model-first label.
    if resp.provider.startswith("gigwork["):
        model_label = f"{resp.provider} {resp.model}"
        tier_label = _api_independence_tier("", profile)
    else:
        model_label = resp.model or resp.provider or "secondary"
        tier_label = _api_independence_tier(resp.provider, profile)
    console.print()
    console.print(f"[bold magenta][consult · {model_label} · {tier_label}][/bold magenta]")
    console.print(reply)
    _record_consult(
        state,
        reply,
        label=f"consult · {model_label}",
        question=question,
        capture=capture,
    )
    return True


def consult_status_line(xli_dir: Path | None = None) -> str:
    """Multi-line /status summary for /consult and harness availability."""
    from xlii.config import GlobalConfig
    from xlii.harness.detect import harness_status_suffixes, load_local_harnesses

    if xli_dir is not None:
        load_local_harnesses(xli_dir)

    cfg = GlobalConfig.load()
    # The judge model no longer gets a /consult: area of its own. When consult is
    # bound to a configured gigwork provider the entry folds into that provider's
    # gig/<name> row (a `· consult judge` marker) — every gigwork model qualifies
    # as a judge, so there is nothing left to break out. Only cases with no gig
    # row to fold into — a dangling binding, an inline API profile, or an
    # unconfigured consult — still print a standalone /consult: line.
    main: str = ""
    resolved = False
    if hasattr(cfg, "consult_profile"):
        try:
            prof = cfg.consult_profile()
            gig = str(prof.get("gig") or "").strip()
            if gig:
                from xlii.chat_backend import GigError, gig_providers

                try:
                    configured = gig in gig_providers(cfg)
                except GigError:
                    configured = False
                if configured:
                    main = ""  # folded into the gig/<gig> row below
                else:
                    main = (
                        f"  /consult:      [yellow]bound to gigwork[{gig}] — not a "
                        f"configured provider (/gigwork add {gig})[/yellow]"
                    )
                resolved = True
            else:
                model = prof.get("model") or "(provider default)"
                env = prof.get("api_key_env") or "?"
                provider = prof.get("provider") or "?"
                tier = _api_independence_tier(provider, prof)
                main = f"  /consult:      [cyan]{provider} / {model}[/cyan] via {env} · {tier}"
                resolved = True
        except RuntimeError:
            # resolved stays False, so the caller falls back to the unconfigured status line.
            pass
    if not resolved:
        sec = getattr(cfg, "secondary_ai", {}) or {}
        if sec.get("provider"):
            model = sec.get("model") or "(provider default)"
            env = sec.get("api_key_env") or "?"
            tier = _api_independence_tier(sec.get("provider", ""), sec)
            main = f"  /consult:      [cyan]{sec['provider']} / {model}[/cyan] via {env} · {tier}"
        else:
            main = (
                "  /consult:      [dim]API not configured — use --via <harness> "
                "or add a judges profile (see /describe consult)[/dim]"
            )
    from xlii.chat_backend import gigwork_status_suffixes

    lines = [main] if main else []
    lines.extend(harness_status_suffixes())
    lines.extend(gigwork_status_suffixes(cfg))
    return "\n".join(lines)


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="consult",
            handler=_consult_handler,
            description="Ask a second model for an independent opinion (API or --via harness)",
            usage="/consult [--via <harness>] [--model <name>] [--last N|--turns|--full] [--capture] <question> | --set-to <gigworker>  (harnesses: built-ins + community from .xlii/harness.local.py)",
            category="knowledge",
            repls=["code", "chat"],
        )
    )
