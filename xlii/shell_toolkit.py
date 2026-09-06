"""Terminal-native shell helpers (Phase 7): NL→command, failure nudge, output post-process.

Captures the last ``run_shell_captured`` / agent ``bash`` event on ``REPLState`` so
post-processing never re-runs a command. NL suggestions are grounded in the Phase-2
``[SYSTEM]`` profile and gated through shellgate like any other shell line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

from xlii.turn_events import ShellRan

if TYPE_CHECKING:
    from rich.console import Console

    from xlii.repl_state import REPLState

# Tail bytes fed to the cheap model for failure nudge / post-process prompts.
_OUTPUT_TAIL = 12_000
_CMD_MAX_LEN = 4000

# Auto-fix retry guard. After a failed command we offer at most this many
# corrected re-runs before leaving it to the user. Combined with a memory of
# already-tried commands, this stops the suggest→fail→suggest loop — e.g. an
# unfixable `sudo apt-get …` cycling sudo / sudo -S / echo|sudo -S forever
# because each suggest_fix() is a fresh one-shot with no history of attempts.
_MAX_FIX_ATTEMPTS = 2


def _norm_cmd(cmd: str) -> str:
    """Whitespace-normalized command, for comparing retry attempts."""
    return " ".join((cmd or "").split())

_FENCE_RE = re.compile(r"^```(?:\w+)?\s*\n?(.*?)\n?```\s*$", re.DOTALL | re.IGNORECASE)


@dataclass
class FailureNudge:
    """A one-shot corrected command offered after a non-zero shell exit."""
    original: str
    suggested: str
    exit_code: int
    stderr_tail: str


# Valid `source` labels for an OutputCapture (the typed last_output buffer).
OUTPUT_SOURCES = ("shell", "harness", "answer")


@dataclass
class OutputCapture:
    """The generalized last-output buffer (interaction-layer-ii Vector B, seam #3).

    One re-printable record of "the last thing that produced output" — a shell
    command, an external harness exchange, or an agent answer. It is the single
    seam three clients share (FINDING-harness-output-ephemeral.md):
      * /replay re-prints ``text`` verbatim (token-free, works after a `clear`),
      * capture-into-history folds it into the conversation as a synthetic tool
        turn so the agent can build on it (``capture_output(into_history=True)``),
      * /sh --explain·/sh --transform keep reading the shell-typed view (``last_shell``).

    ``source`` is one of OUTPUT_SOURCES. ``label`` is a short human header
    (e.g. ``cursor · composer-2.5`` or ``$ git status``); ``command`` is the
    originating command/task/question, when there is one. ``meta`` carries any
    extra provenance a consumer wants to round-trip (harness/model/tier/…).
    """
    text: str
    source: str = "answer"
    label: str = ""
    command: str = ""
    meta: dict = field(default_factory=dict)


def record_last_shell(state: "REPLState", ev: ShellRan) -> None:
    """Retain the latest shell capture for post-process / explain.

    Also mirrors the capture into the generalized ``last_output`` buffer (typed
    ``source="shell"``), so ``/replay`` re-prints the last shell output for free
    while ``/sh --explain``·``/sh --transform``·``?>`` keep reading the ShellRan view here.
    """
    state.last_shell = ev
    try:
        from xlii.desk import listing_from_shell

        note = listing_from_shell(ev)
        if note:
            state.last_listing = note
    except Exception:
        # state.last_shell is already recorded above; the desk listing note is an extra.
        pass
    try:
        record_output(
            state,
            _shell_output_text(ev),
            source="shell",
            label=f"$ {ev.command}",
            command=ev.command,
        )
    except Exception:
        # last_output is a convenience mirror; never let it break shell capture.
        pass


def last_shell_capture(state: "REPLState") -> Optional[ShellRan]:
    return getattr(state, "last_shell", None)


def _shell_output_text(ev: ShellRan) -> str:
    """Verbatim stdout+stderr of a shell capture, for the re-printable buffer."""
    parts = []
    if ev.stdout:
        parts.append(ev.stdout)
    if ev.stderr:
        parts.append(("\n--- stderr ---\n" if ev.stdout else "") + ev.stderr)
    return "".join(parts)


# --------------------------------------------------------------------------- #
#  last_output buffer + capture seam (#3) — one buffer, three consumers.       #
#  See proposals/FINDING-harness-output-ephemeral.md and                      #
#  proposals/interaction-layer-ii-parallel-build.md (Vector B).               #
# --------------------------------------------------------------------------- #

def record_output(
    state: "REPLState",
    text: str,
    *,
    source: str = "answer",
    label: str = "",
    command: str = "",
    meta: Optional[dict] = None,
) -> OutputCapture:
    """Store *text* as the session's last re-printable output and return it.

    The single low-level setter behind the buffer. Lives on SessionState (via the
    ``last_output`` property), so it outlives a screen ``clear``. Higher-level
    callers usually want :func:`capture_output` (which can also fold the output
    into history); :func:`record_last_shell` calls this for shell output.
    """
    cap = OutputCapture(
        text=text or "",
        source=source,
        label=label,
        command=command,
        meta=dict(meta or {}),
    )
    state.last_output = cap
    return cap


def last_output_capture(state: "REPLState") -> Optional[OutputCapture]:
    """The most recent :class:`OutputCapture`, or None if nothing was captured."""
    return getattr(state, "last_output", None)


def capture_output(
    state: "REPLState",
    text: str,
    *,
    source: str = "harness",
    into_history: bool = False,
    label: str = "",
    command: str = "",
    meta: Optional[dict] = None,
    agent: Any = None,
) -> OutputCapture:
    """Capture external/harness output — the published seam (#3).

    Always records the re-printable ``last_output`` buffer (so ``/replay`` can
    re-show it, even after a ``clear``). When ``into_history`` is True, also folds
    the output into the conversation as a synthetic tool turn so the agent is
    aware of it and ``?what did we do?`` works.

    Untrusted ingress (harness · answer · etc.): scans text for injection-class
    Unicode and, when credibility > 0, stamps ``meta["credibility"]``, prints
    a yellow **take note** on the session console, and prefixes the history
    fold with the same note. Never auto-strips.

    Consumers: Vector C (harness output at the ``run_delegate`` chokepoint),
    Vector F (pipe-step slash capture), and ``/consult --capture``.
    """
    meta_out = dict(meta or {})
    note = ""
    try:
        from xlii.text_hygiene import enrich_meta, note_ingress

        note = note_ingress(text or "", source=source)
        meta_out = enrich_meta(meta_out, text or "", source=source)
    except Exception:
        note = ""
    if note:
        console = getattr(state, "console", None)
        if console is not None:
            try:
                console.print(f"[yellow]{note}[/yellow]")
            except Exception:
                # Non-fatal: capture must succeed even if console rendering fails.
                pass
    cap = record_output(
        state, text, source=source, label=label, command=command, meta=meta_out
    )
    if into_history:
        ag = agent if agent is not None else getattr(state, "agent", None)
        _capture_into_history(ag, cap, hygiene_note=note)
    return cap


def _capture_into_history(
    agent: Any, cap: OutputCapture, *, hygiene_note: str = ""
) -> None:
    """Append *cap* to ``agent.history`` as a synthetic tool-use/tool-result pair.

    Mirrors how a real tool round-trip is recorded (an assistant message with a
    ``tool_calls`` entry followed by a ``role: "tool"`` result), so the captured
    exchange reads as work the agent did and can build on. A closed pair — the
    result follows immediately — so it never leaves a dangling unanswered call.

    When *hygiene_note* is set (ingress credibility > 0), it is prefixed so the
    agent sees the same take-note the operator saw — still no auto-strip.
    """
    if agent is None:
        return
    history = getattr(agent, "history", None)
    if history is None or not isinstance(history, list):
        return
    import json
    import uuid

    call_id = f"capture_{uuid.uuid4().hex[:12]}"
    fn_name = cap.source if cap.source in OUTPUT_SOURCES else "capture"
    args: dict[str, str] = {}
    if cap.command:
        args["command"] = cap.command
    if cap.label:
        args["label"] = cap.label
    history.append(
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": fn_name, "arguments": json.dumps(args)},
                }
            ],
        }
    )
    header = f"[{cap.label}]\n" if cap.label else ""
    note_line = f"[{hygiene_note}]\n" if hygiene_note else ""
    history.append(
        {
            "role": "tool",
            "tool_call_id": call_id,
            "content": note_line + header + (cap.text or ""),
        }
    )


def capturing_console(**kwargs: Any) -> "Console":
    """A recording Rich Console — the capturing-console primitive (seam #3).

    Everything printed to it is retained and retrievable via ``export_text()``.
    By default it writes to an in-memory buffer (silent — nothing reaches the
    terminal), so a consumer can scrape a slash handler's prints and take the
    text as a carry. Pass ``file=...`` / ``force_terminal=True`` to also echo.

    Vector F runs each pipe slash step under this and uses ``export_text()`` as
    the step's carry, so B's capture serves harness output *and* pipe steps.
    """
    import io

    from rich.console import Console

    kwargs.setdefault("record", True)
    kwargs.setdefault("file", io.StringIO())
    kwargs.setdefault("force_terminal", False)
    console = Console(**kwargs)
    console.xlii_foreground = False
    return console


def run_capturing(fn: Callable[..., Any], *, console: Optional["Console"] = None) -> str:
    """Run *fn* with a capturing console and return everything it printed.

    *fn* is called with the capturing console as its single argument
    (``fn(console)``) so it can route prints there — e.g.::

        text = run_capturing(lambda con: dispatch_repl_command(line, {**ctx, "console": con}))

    A zero-argument callable that already closes over the console is also
    accepted. Returns the scraped text (``console.export_text()``).
    """
    con = console if console is not None else capturing_console()
    import inspect

    try:
        takes_arg = len(inspect.signature(fn).parameters) >= 1
    except (TypeError, ValueError):
        takes_arg = False
    args = [con] if takes_arg else []
    fn(*args)
    return con.export_text()


def _system_context() -> str:
    try:
        from xlii.system_profile import load_system_profile, summary_line

        profile = load_system_profile()
        if profile is None:
            return ""
        return f"[SYSTEM] {summary_line(profile)}"
    except Exception:
        return ""


def _extract_command(text: str) -> str:
    """Pull a single shell line from a model reply (strip fences / chatter)."""
    raw = (text or "").strip()
    if not raw:
        return ""
    m = _FENCE_RE.match(raw)
    if m:
        raw = m.group(1).strip()
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        # Drop common lead-ins the model sometimes adds.
        for prefix in ("$ ", "> ", "command: ", "Command: "):
            if s.startswith(prefix):
                s = s[len(prefix) :].strip()
                break
        return s[:_CMD_MAX_LEN]
    return raw.splitlines()[0].strip()[:_CMD_MAX_LEN]


def _secondary_query(system: str, user: str) -> str:
    from xlii.secondary_ai import query_with_profile

    resp = query_with_profile(
        None,
        user,
        system=system,
    )
    text = (resp.text or "").strip()
    if text.startswith("(empty response"):
        return ""
    return text


def _confirm_supports_edit() -> bool:
    """True when ``_confirm`` is blocking ``input()`` (inline REPL), not the TUI modal."""
    import builtins

    from xlii import tools

    return tools._confirm is builtins.input


def nl_to_command(nl: str, *, cwd: Path, last_listing: str = "") -> str:
    """Ask the secondary model for one platform-correct shell command."""
    sys_ctx = _system_context()
    system = (
        "You translate a natural-language task into exactly ONE shell command for "
        "the user's machine. Reply with the command only — no explanation, no "
        "markdown unless the command itself requires it. Use the platform hints "
        "below; never assume macOS/BSD flags on GNU systems or vice versa.\n"
    )
    if sys_ctx:
        system += f"\n{sys_ctx}\n"
    try:
        from xlii.desk import desk_context

        desk = desk_context(nl, cwd=cwd, last_listing=last_listing)
        if desk:
            system += f"\n[DESK]\n{desk}\n"
    except Exception:
        system += f"\nWorking directory: {cwd}"
    user = f"Task: {nl.strip()}"
    return _extract_command(_secondary_query(system, user))


def suggest_fix(
    cmd: str,
    *,
    stderr: str,
    exit_code: int,
    stdout: str = "",
    cwd: Optional[Path] = None,
    last_listing: str = "",
) -> str:
    """One-shot corrected command after a failure (generalized git-nudge)."""
    err_blob = (stderr or "").strip()
    if not err_blob and stdout:
        err_blob = (stdout or "").strip()
    err_tail = err_blob[-_OUTPUT_TAIL:] if err_blob else "(no stderr)"
    sys_ctx = _system_context()
    system = (
        "A shell command failed. Reply with exactly ONE corrected shell command "
        "that fixes the typo or obvious mistake. No explanation — command only.\n"
    )
    if sys_ctx:
        system += f"\n{sys_ctx}\n"
    try:
        from xlii.desk import desk_context

        desk = desk_context(cmd, cwd=cwd, last_listing=last_listing)
        if desk:
            system += (
                f"\n[DESK]\n{desk}\n"
                "If the failed command used a bare glob, it searched CWD, "
                "not the drop zone.\n"
            )
    except Exception:
        # The suggestion prompt works without the desk block.
        pass
    user = (
        f"Failed command: {cmd}\n"
        f"Exit code: {exit_code}\n"
        f"stderr/output tail:\n{err_tail}"
    )
    return _extract_command(_secondary_query(system, user))


def post_process_output(instruction: str, ev: ShellRan) -> str:
    """Run an NL instruction over a captured buffer — no re-execution."""
    stdout = ev.stdout or ""
    stderr = ev.stderr or ""
    combined = stdout
    if stderr:
        combined += ("\n--- stderr ---\n" if stdout else "") + stderr
    if not combined.strip():
        return "(no output captured)"
    tail = combined[-_OUTPUT_TAIL:]
    system = (
        "You analyze shell command output for the user. Be concise and direct. "
        "Do not suggest re-running the command unless the output is empty."
    )
    user = (
        f"Command: {ev.command}\n"
        f"Exit code: {ev.returncode}\n"
        f"Captured output (tail):\n{tail}\n\n"
        f"User request: {instruction.strip()}"
    )
    return _secondary_query(system, user)


def explain_last(ev: ShellRan) -> str:
    """Explain what the last command did and summarize its result."""
    return post_process_output(
        "Explain what this command does and summarize the important parts of its output.",
        ev,
    )


def summarize_last(ev: ShellRan, *, extra: str = "") -> str:
    inst = "Summarize this output briefly."
    if extra.strip():
        inst = extra.strip()
    return post_process_output(inst, ev)


def classify_shell(cmd: str, project_root: Path) -> str:
    from xlii.shellgate import classify_command

    return classify_command(cmd, project_root)


def _osc52_copy(text: str) -> bool:
    """Write *text* to the system clipboard via the OSC 52 terminal escape."""
    import base64
    import sys

    try:
        payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
        sys.stdout.write(f"\x1b]52;c;{payload}\a")
        sys.stdout.flush()
        return True
    except Exception:
        return False


def copy_for_terminal(state: "REPLState", text: str) -> bool:
    """Copy *text* to the system clipboard for pasting into a real terminal.

    Prefers a TUI-registered clipboard (Textual routes OSC 52 through its own
    driver); falls back to emitting OSC 52 directly for the inline REPL. Both
    land on the system clipboard in OSC-52-capable terminals (kitty, wezterm,
    iTerm2, …), so no manual selection is needed.
    """
    hook = getattr(state, "_clipboard", None)
    if callable(hook):
        try:
            hook(text)
            return True
        except Exception:
            # The clipboard hook failed -- fall through to the platform paths below.
            pass
    return _osc52_copy(text)


def gate_shell_command(state: "REPLState", cmd: str) -> bool:
    """shellgate + auto-approve + yolo. Returns True to run here.

    On a gated prompt the user may also press ``c`` to copy the command to the
    system clipboard (OSC 52) and run it in their own terminal instead — handy
    for sudo/system commands that can't prompt for a password in xlii's captured
    shell. Copy and cancel both return False (don't run here); the gate prints
    its own outcome, so callers should not add a "cancelled" message.

    Policy matches ``tool_handlers._check_intent_and_gate`` (see
    ``tool_context.shell_command_needs_confirm``).
    """
    from xlii.shellgate import MODIFIES_SYSTEM
    from xlii.tool_context import authorize_shell, default_auto_approve

    auto = getattr(state, "auto_approve", None) or default_auto_approve()
    auth = authorize_shell(
        cmd,
        project_root=state.project.project_root,
        yolo=getattr(state, "yolo", False),
        auto_approve=auto,
    )
    classified = auth.classified
    if not auth.needs_confirm:
        return True
    if classified == MODIFIES_SYSTEM:
        state.console.print(f"[red]⚠ system-level command[/red] [cyan]{cmd}[/cyan]")
    else:
        state.console.print(
            f"[yellow]⚠ {classified} command[/yellow] [cyan]{cmd}[/cyan]"
        )
    from xlii.tools import _confirm

    # Include the command IN the prompt — under Textual the confirm modal floats
    # over the transcript, hiding the line printed above, so the prompt itself
    # must show what's being approved.
    prompt = (
        f"run this command?\n  {cmd}\n"
        "[y] run here · [c] copy for your terminal · [N] cancel: "
    )
    try:
        answer = _confirm(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        state.console.print("[dim]cancelled[/dim]")
        return False
    if answer in ("y", "yes"):
        return True
    if answer in ("c", "copy"):
        if copy_for_terminal(state, cmd):
            state.console.print(
                "[green]✓ copied[/green] — paste it in your terminal:\n"
                f"  [cyan]{cmd}[/cyan]"
            )
        else:
            state.console.print(
                "[yellow]clipboard unavailable[/yellow] — run this in your terminal:\n"
                f"  [cyan]{cmd}[/cyan]"
            )
        return False
    state.console.print("[dim]cancelled[/dim]")
    return False


def _run_with_terminal(state: "REPLState", cmd: str, cwd: Path) -> None:
    """Run a command on a real terminal (sudo password prompt / ncurses app).

    The captured runner closes stdin, so these can't run there. The TUI registers
    a hook (``state._run_interactive``) that suspends Textual and hands over the
    terminal; the inline REPL has no hook and simply inherits the user's TTY.
    """
    hook = getattr(state, "_run_interactive", None)
    if callable(hook):
        try:
            hook(cmd, str(cwd))
        except Exception as e:
            state.console.print(f"[red]could not hand over the terminal: {e}[/red]")
        return
    # Face / wire console is not a TTY. Do not inherit the server's
    # hidden terminal (sudo password prompt sits there, face looks hung).
    con = getattr(state, "console", None)
    have_tty = bool(getattr(con, "is_terminal", False))
    from xlii.interactive import handoff_password_tty

    ok, msg = handoff_password_tty(
        cmd, cwd, cfg=getattr(state, "cfg", None), have_tty=have_tty,
    )
    color = "green" if ok else "red"
    state.console.print(f"[{color}]{msg}[/{color}]")


def run_proposed_command(
    state: "REPLState",
    cmd: str,
    *,
    source: str = "user_shell",
    _tried: Optional[frozenset[str]] = None,
    _depth: int = 0,
) -> Optional[ShellRan]:
    """Gate, capture-run, record, and optionally offer a failure nudge.

    ``_tried``/``_depth`` are threaded through the auto-fix retry chain (see
    ``offer_failure_nudge``) to bound it; callers leave them at the defaults.
    """
    if not cmd.strip():
        state.console.print("[dim]empty command[/dim]")
        return None
    if not gate_shell_command(state, cmd):
        return None  # gate already printed the outcome (cancelled / copied)
    cwd = Path(getattr(state, "shell_cwd", None) or state.project.project_root)
    from xlii.shell_run import run_shell_captured, styled_enabled
    from xlii.repl import _is_clear_command, _is_cd_command, _change_dir

    if _is_cd_command(cmd):
        _change_dir(state, cmd)
        return None
    if _is_clear_command(cmd):
        state.console.clear()
        return None

    # A command that needs a real terminal — sudo's password prompt or a
    # full-screen program — can't run on the captured runner (stdin is DEVNULL),
    # where sudo dies with "a terminal is required to read the password". Hand it
    # the inherited TTY instead: the TUI suspends for it; inline just inherits.
    from xlii.interactive import is_interactive, needs_password_tty
    if is_interactive(cmd) or needs_password_tty(cmd):
        _run_with_terminal(state, cmd, cwd)
        return None

    if not styled_enabled():
        import subprocess

        try:
            rc = subprocess.call(cmd, shell=True, cwd=str(cwd))
        except OSError as e:
            state.console.print(f"[red]shell error: {e}[/red]")
            return None
        if rc != 0:
            state.console.print(f"[dim]exit {rc}[/dim]")
        return None

    try:
        intent = classify_shell(cmd, state.project.project_root)
        ev = run_shell_captured(
            cmd, cwd, source=source, intent=intent  # type: ignore[arg-type]
        )
    except OSError as e:
        state.console.print(f"[red]shell error: {e}[/red]")
        return None

    from xlii.ui import renderer

    renderer.emit(ev)
    record_last_shell(state, ev)
    if ev.returncode != 0:
        offer_failure_nudge(state, ev, tried=_tried, depth=_depth)
    else:
        from xlii.repl import _interactive_hint, _note_shell_activity, _xlii_stateful_heads_up

        _interactive_hint(state.console, cmd, ev)
        _note_shell_activity(state, cwd)
        _xlii_stateful_heads_up(state, cmd)
    return ev


def offer_failure_nudge(
    state: "REPLState",
    ev: ShellRan,
    *,
    tried: Optional[frozenset[str]] = None,
    depth: int = 0,
) -> None:
    """After a failed capture, suggest a corrected command (accept / edit / dismiss).

    ``depth``/``tried`` bound the auto-fix retry chain: stop after
    ``_MAX_FIX_ATTEMPTS`` re-runs, or as soon as a suggestion repeats a command
    already tried in this chain. Without this an unfixable failure (e.g. sudo/apt
    needing a password it cannot get non-interactively) loops forever, since each
    ``suggest_fix`` is a stateless one-shot that cycles the same few variants.
    """
    if depth >= _MAX_FIX_ATTEMPTS:
        state.console.print(
            f"[dim]still failing after {depth} auto-fix attempt(s) — leaving this one to you[/dim]"
        )
        return
    tried = tried or frozenset({_norm_cmd(ev.command)})
    try:
        suggested = suggest_fix(
            ev.command,
            stderr=ev.stderr,
            exit_code=ev.returncode,
            stdout=ev.stdout,
            cwd=getattr(ev, "cwd", None) or getattr(state, "shell_cwd", None),
            last_listing=str(getattr(state, "last_listing", "") or ""),
        )
    except Exception as e:
        state.console.print(f"[dim]fix suggestion unavailable: {e}[/dim]")
        return
    if not suggested or suggested.strip() == ev.command.strip():
        return
    if _norm_cmd(suggested) in tried:
        state.console.print(
            "[dim]suggested fix repeats an earlier attempt — stopping "
            "(this likely needs manual intervention, e.g. an interactive password)[/dim]"
        )
        return
    nudge = FailureNudge(
        original=ev.command,
        suggested=suggested,
        exit_code=ev.returncode,
        stderr_tail=(ev.stderr or "")[-500:],
    )
    _present_failure_nudge(state, nudge, tried=tried, depth=depth)


def _present_failure_nudge(
    state: "REPLState",
    nudge: FailureNudge,
    *,
    tried: frozenset[str] = frozenset(),
    depth: int = 0,
) -> None:
    state.console.print(
        f"[yellow]suggested fix[/yellow] [cyan]{nudge.suggested}[/cyan] "
        f"[dim](exit {nudge.exit_code})[/dim]"
    )
    keys = "[y] run · [e] edit · [n] dismiss" if _confirm_supports_edit() else "[y] run · [n] dismiss"
    state.console.print(f"[dim]{keys}[/dim]")
    from xlii.tools import _confirm

    try:
        choice = _confirm("").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if choice in ("", "n", "no"):
        return
    cmd = nudge.suggested
    if choice.startswith("e") and _confirm_supports_edit():
        try:
            edited = _confirm(f"command [{cmd}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if edited:
            cmd = edited
    elif choice not in ("y", "yes"):
        return
    run_proposed_command(
        state, cmd, _tried=tried | {_norm_cmd(cmd)}, _depth=depth + 1
    )


def nl_command_flow(state: "REPLState", nl: str) -> None:
    """NL → propose → confirm → run."""
    cwd = Path(getattr(state, "shell_cwd", None) or state.project.project_root)
    last_listing = str(getattr(state, "last_listing", "") or "")
    if not last_listing:
        try:
            from xlii.desk import listing_from_shell

            last_listing = listing_from_shell(getattr(state, "last_shell", None))
        except Exception:
            last_listing = ""
    try:
        cmd = nl_to_command(nl, cwd=cwd, last_listing=last_listing)
    except Exception as e:
        state.console.print(f"[red]could not propose command: {e}[/red]")
        return
    if not cmd:
        state.console.print("[red]model returned an empty command[/red]")
        return
    classified = classify_shell(cmd, state.project.project_root)
    state.console.print(f"[bold]proposed[/bold] [{classified}] [cyan]{cmd}[/cyan]")
    from xlii.tools import _confirm

    # The command goes IN the prompt: under Textual the confirm modal floats over
    # the transcript and hides the "proposed" line above it, so the prompt must
    # carry what's being approved.
    keys = (
        "[y] run · [c] copy for your terminal · [e] edit · [N] cancel: "
        if _confirm_supports_edit()
        else "[y] run · [c] copy for your terminal · [N] cancel: "
    )
    run_prompt = f"run this {classified} command?\n  {cmd}\n{keys}"
    try:
        answer = _confirm(run_prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        state.console.print("[dim]cancelled[/dim]")
        return
    if answer in ("", "n", "no"):
        state.console.print("[dim]cancelled[/dim]")
        return
    if answer in ("c", "copy"):
        if copy_for_terminal(state, cmd):
            state.console.print(
                "[green]✓ copied[/green] — paste it in your terminal:\n"
                f"  [cyan]{cmd}[/cyan]"
            )
        else:
            state.console.print(
                "[yellow]clipboard unavailable[/yellow] — run this in your terminal:\n"
                f"  [cyan]{cmd}[/cyan]"
            )
        return
    if answer.startswith("e") and _confirm_supports_edit():
        try:
            edited = _confirm(f"command [{cmd}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            state.console.print("[dim]cancelled[/dim]")
            return
        cmd = edited or cmd
    elif answer not in ("y", "yes"):
        state.console.print("[dim]cancelled[/dim]")
        return
    run_proposed_command(state, cmd)


def post_process_flow(state: "REPLState", instruction: str) -> None:
    ev = last_shell_capture(state)
    if ev is None:
        state.console.print("[yellow]no captured shell output yet[/yellow]")
        return
    try:
        text = post_process_output(instruction, ev)
    except Exception as e:
        state.console.print(f"[red]post-process failed: {e}[/red]")
        return
    state.console.print(text)


def require_last_shell(state: "REPLState") -> Optional[ShellRan]:
    ev = last_shell_capture(state)
    if ev is None:
        state.console.print("[yellow]no captured shell output yet[/yellow]")
    return ev
