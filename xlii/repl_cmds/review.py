"""Cold-context review subagents: /peer and /verify (revived xli features).

Both spawn a fresh-context WorkerAgent with the read-only tool palette and print
the result; nothing goes back into chat history. They differ by what intent the
reviewer is allowed to see:

- /verify (was the old /debug verifier) DOES get the brief: the last turn's task
  + a `git diff HEAD` of uncommitted work. "Did this do what was asked?"
- /peer DOES NOT get the brief: only the commit log + diff for a committed range.
  A blind PR reviewer that must reconstruct intent from the artifact alone.

The old /debug name now belongs to a different command (live state dump), so the
verifier is exposed as /verify.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Optional

from xlii.commands import REPLCommand, register_repl_command


PEER_SYSTEM_PROMPT = """You are a brutal cold-context peer reviewer.

You did NOT write this code. You have NEVER seen it before. You have NO
conversation with the author. NO chat thread. NO design discussion. NO
prompts. NO stated intent. None of the reasoning that produced this change
is available to you, by design — that is the entire point of this review.

What you DO have:
1. The git log (commit messages) for the changes under review.
2. The full git diff.
3. The list of files touched.
4. Read-only access to the entire codebase via read_file, grep, glob,
   search_project, and read-only bash (git show, git log, git blame, etc.).

Use the codebase access. The point of this review is that you can
investigate the artifact in its surroundings — not just stare at the diff.
Cross-reference. Check whether the change is consistent with how things
are done elsewhere. Read the files being modified in full to see what the
diff is sitting inside of. Be a real reviewer.

Your job: judge whether the change is correct, coherent, and consistent.
Reconstruct what the change is for from the commits and the code alone.
If you can't tell what a change is supposed to do from the artifacts, that
is itself a finding — flag it as incoherent.

Distinguish between:
- [correctness] — what the code does is wrong (off-by-one, wrong types,
  swapped args, missing branches, broken error paths, dead code paths,
  security regressions).
- [coherence] — the commit message doesn't match what the diff actually
  does, or claims verification that isn't actually verified, or the
  diff's intent can't be reconstructed at all.
- [consistency] — the change conflicts with existing patterns elsewhere
  in the codebase (use search_project / grep to check).

Be brutal. You are not the author's friend. Praise is forbidden. Restating
the diff is forbidden. "Looks good to me" is forbidden — if it looks good,
write the one-line PASS instead.

Output format:
- If clean: one line — "PASS: <one-sentence summary of what changed and
  why it's correct>".
- If issues: "FAIL" then a numbered list. Each item: file:line, one
  sentence, tagged [correctness], [coherence], or [consistency].

Do not propose fixes unless asked. Do not modify any files. Do not ask
for the author's intent — the absence of it is the point."""


VERIFIER_SYSTEM_PROMPT = """You are a code verifier. You did NOT write the code under review — you are examining it cold, with no memory of the decisions that produced it.

You will be given:
1. The original user task.
2. A git diff of all changes made in response to that task.

Your job: find specific bugs and scope violations. Not style. Not "could be cleaner." Real defects.

Anchored checklist:
- Does the diff parse / import cleanly? (Use bash to verify if uncertain — e.g. `python -c "import xlii.foo"`.)
- Does the scope of changes match the user's request, or did unrelated files get touched?
- Are there verification claims in commit messages or comments that aren't actually verified by the diff?
- Are there obvious correctness bugs: off-by-one, wrong types, swapped args, missing branches, broken error paths, dead code paths?
- Are there security regressions: unredacted secrets, removed validation, widened permissions, new subprocess calls without escaping?

Output format:
- If clean: one line — "PASS: <one-sentence summary of what changed>".
- If issues: "FAIL" then a numbered list. Each item: file:line, one sentence describing the specific defect. No suggestions for new features. No praise. No restating the diff.

Do not propose fixes unless asked. Do not modify any files. You have read-only investigation tools."""


def _git(cwd, args: list[str], timeout: int = 10) -> tuple[str, Optional[str]]:
    """Return (stdout, error). error is None on success."""
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(cwd),
            capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError:
        return ("", "git not installed")
    except subprocess.TimeoutExpired:
        return ("", f"git {' '.join(args)} timed out after {timeout}s")
    if proc.returncode != 0:
        return ("", proc.stderr.strip() or f"git {' '.join(args)} failed")
    return (proc.stdout, None)


def _uncommitted_diff(cwd) -> tuple[str, str, Optional[str]]:
    """`git diff HEAD` PLUS untracked new files — which `git diff` omits entirely,
    so a turn that only created new files would otherwise look like "no changes".
    Returns (diff, newline-joined names, error)."""
    diff, err = _git(cwd, ["diff", "HEAD"])
    if err:
        return ("", "", err)
    names, _ = _git(cwd, ["diff", "HEAD", "--name-only"])
    files = [f for f in (names or "").splitlines() if f.strip()]
    untracked, _ = _git(cwd, ["ls-files", "--others", "--exclude-standard"])
    for rel in [u for u in (untracked or "").splitlines() if u.strip()]:
        fp = Path(cwd) / rel
        if not fp.is_file():
            continue
        try:
            body = fp.read_text(errors="replace")
        except OSError:
            continue
        files.append(rel)
        diff += (
            f"\ndiff --git a/{rel} b/{rel}\nnew file mode 100644\n"
            f"--- /dev/null\n+++ b/{rel}\n"
        )
        diff += "".join(f"+{ln}\n" for ln in body.splitlines())
    return (diff, "\n".join(files), None)


def _files_block(name_only: str) -> str:
    files = [f for f in name_only.splitlines() if f.strip()]
    return "\n".join(f"  - {f}" for f in files) if files else "  (none enumerated)"


def _last_user_prompt(agent) -> Optional[str]:
    for msg in reversed(getattr(agent, "history", []) or []):
        if msg.get("role") == "user":
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                return content
    return None


def _spawn_reviewer(ctx: dict[str, Any], brief: str, system_prompt: str,
                    label: str, style: str, sink: str) -> None:
    """Spawn a cold-context WorkerAgent with the given system prompt and print
    its review. Mirrors the modern dispatch_subagent recipe (pool health, cost).
    The review text is saved to <project>/.xlii/<sink> so `/consult --from-*`
    can pull it in as context for a cross-vendor second opinion."""
    console = ctx["console"]
    state = ctx.get("state")
    agent = state.agent if state else ctx.get("agent")
    project = state.project if state else ctx.get("project")

    from xlii.cost import format_cost, format_tokens
    from xlii.loop_judge import save_review_report, spawn_worker_review

    try:
        text, call = spawn_worker_review(
            agent=agent,
            project=project,
            brief=brief,
            system_prompt=system_prompt,
        )
    except Exception as e:
        console.print(f"[red]{label}: reviewer crashed: {type(e).__name__}: {e}[/red]")
        return

    rule = getattr(console, "rule", None)
    console.print()
    if rule:
        rule(f"[bold]{label}[/bold]", style=style)
    # markup=False: the reviewer's text is free-form LLM output that routinely
    # contains literal brackets (e.g. "[0]", "list[int]"). With markup on, Rich
    # would try to parse those as style tags and raise MarkupError — which, on the
    # TUI worker thread, surfaces as a crash. Render it verbatim instead.
    console.print(text, markup=False)
    if rule:
        rule(style=style)

    cost_part = f" · {format_cost(call.cost_usd)}" if call.cost_usd is not None else ""
    console.print(
        f"[dim]{label} · {call.model} · {call.iterations} iter · "
        f"{format_tokens(call.total_tokens)}{cost_part}[/dim]"
    )

    save_review_report(project.xli_dir, label=label, text=text, sink=sink)


def _verify_handler(line: str, ctx: dict[str, Any]) -> bool:
    """/verify — cold verifier on uncommitted work vs the last turn's task."""
    console = ctx["console"]
    state = ctx.get("state")
    agent = state.agent if state else ctx.get("agent")
    project = state.project if state else ctx.get("project")

    last = _last_user_prompt(agent)
    if not last:
        console.print("[dim]/verify: no prior turn this session — nothing to verify[/dim]")
        return True

    diff, names, err = _uncommitted_diff(project.project_root)
    if err:
        console.print(f"[red]/verify: {err}[/red]")
        return True
    if not diff.strip():
        console.print(
            "[dim]/verify: no uncommitted changes vs HEAD — nothing to verify "
            "(use [/dim][cyan]/peer[/cyan][dim] to review committed work)[/dim]"
        )
        return True

    brief = (
        f"Original task:\n{last}\n\n"
        f"Files changed (uncommitted vs HEAD):\n{_files_block(names)}\n\n"
        f"Diff:\n{diff}"
    )
    console.print("[dim]/verify: spawning verifier…[/dim]")
    _spawn_reviewer(ctx, brief, VERIFIER_SYSTEM_PROMPT, "verifier", "cyan", "verify-last.md")
    return True


def _peer_handler(line: str, ctx: dict[str, Any]) -> bool:
    """/peer — blind reviewer on a committed range (default HEAD~1..HEAD)."""
    console = ctx["console"]
    state = ctx.get("state")
    project = state.project if state else ctx.get("project")
    cwd = project.project_root

    parts = line.split()
    since = None
    if len(parts) > 1:
        if parts[1] == "--since" and len(parts) == 3:
            since = parts[2]
        else:
            console.print(f"[red]/peer: usage: /peer [--since <ref>]  (got: {' '.join(parts[1:])})[/red]")
            return True

    ref_spec = since or "HEAD~1"
    base, err = _git(cwd, ["rev-parse", ref_spec])
    if err:
        if since is not None:
            console.print(f"[red]/peer: cannot resolve --since {since}: {err}[/red]")
        elif "unknown revision" in err.lower() or "ambiguous argument" in err.lower():
            # Specifically the single-commit case — give the actionable remedies.
            console.print(
                "[dim]/peer: this looks like the first commit — no HEAD~1 to review. Try "
                "[/dim][cyan]/peer --since <ref>[/cyan][dim], commit an empty baseline "
                "([/dim][cyan]git commit --allow-empty[/cyan][dim]), or [/dim]"
                "[cyan]/verify[/cyan][dim] for uncommitted work.[/dim]"
            )
        else:
            console.print(f"[red]/peer: {err}[/red]")  # genuine git error, surfaced as-is
        return True
    baseline = base.strip()
    rng = f"{baseline}..HEAD"

    diff, err = _git(cwd, ["diff", rng])
    if err:
        console.print(f"[red]/peer: {err}[/red]")
        return True
    if not diff.strip():
        console.print(f"[dim]/peer: no changes in {baseline[:8]}..HEAD — nothing to review[/dim]")
        return True

    log, _ = _git(cwd, ["log", "--format=%h %s%n%b", rng])
    names, _ = _git(cwd, ["diff", rng, "--name-only"])
    # NOTE: deliberately NO original-task brief here — that structural ignorance
    # is what distinguishes /peer from /verify. Don't add it back as "polish".
    brief = (
        f"Range under review: {baseline[:12]}..HEAD\n\n"
        f"Files changed:\n{_files_block(names)}\n\n"
        f"Commit log:\n{log.strip() or '(no commits)'}\n\n"
        f"Full diff:\n{diff}"
    )
    console.print("[dim]/peer: spawning cold reviewer…[/dim]")
    _spawn_reviewer(ctx, brief, PEER_SYSTEM_PROMPT, "peer review", "magenta", "peer-last.md")
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="verify",
            handler=_verify_handler,
            description="Cold-context verifier on uncommitted work vs the last turn's task",
            category="general",
            repls=["code"],
        )
    )
    register_repl_command(
        REPLCommand(
            name="peer",
            handler=_peer_handler,
            description="Blind peer review of a committed range (no author intent)",
            usage="/peer [--since <ref>]",
            category="general",
            repls=["code"],
        )
    )
