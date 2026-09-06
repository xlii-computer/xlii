"""REPL runtime primitives for xlii.

This module introduces REPLState — the single source of truth for everything
that lives for the duration of an interactive session (whether `xlii code`
or `xlii chat`).

It is the foundation for:
- Durable /ref + /doc attachments (now persisted in .xlii/session.json)
- Clean unified REPL loop
- Pluggable command system (commands can inspect and mutate state)
"""

from __future__ import annotations

import glob as globmod
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

from xlii.repl_state import REPLState
from xlii.ui import renderer
from xlii.shell_run import run_shell_captured, styled_enabled
from xlii.ui import console, format_turn_line

if TYPE_CHECKING:

    pass


class _QuitSession(BaseException):
    """Fire-alarm signal that the whole session must end *now* (A2 / OQ5).

    Raised once ``state.quit_requested`` is set (by /exit·/quit anywhere — the
    inline prompt, or a /tui nested inside it) and caught at a single point in the
    top-level command (cmd_code / cmd_chat), which exits the process. Subclasses
    BaseException so the codebase's many ``except Exception`` blocks (command
    dispatch, hook runners, turn execution) can't swallow it — it unwinds past
    every nested layer untouched, never "backing out" to a lower one.
    """


def _run_raw_shell(cmd: str, cwd: Path, printer) -> None:
    """Inherited-TTY passthrough — stdout/stderr go straight to the terminal so
    pagers, `clear`, `vim`, and `ls --color` work naturally. The escape hatch
    capture can't replace. `printer` is the console to report errors/exit on."""
    try:
        rc = subprocess.call(cmd, shell=True, cwd=str(cwd))
    except (OSError, subprocess.SubprocessError) as e:
        printer.print(f"[red]shell error: {e}[/red]")
        return
    if rc != 0:
        printer.print(f"[dim]exit {rc}[/dim]")


def _session_renderer(state: "Optional[REPLState]"):
    """The renderer that owns THIS session's surface.

    The agent's renderer when a session is in hand — a remote face (serve
    --face) replaces that with its wire renderer, so a live-shell ShellRan
    reaches the browser instead of the server's terminal. State-less callers
    (bare CLI helpers, old tests) keep the module singleton."""
    agent = getattr(state, "agent", None) if state is not None else None
    if agent is not None:
        try:
            return agent._renderer()
        except Exception:
            # No renderer on this agent -- fall through to the default renderer below.
            pass
    return renderer


def _surface_has_terminal(state: "Optional[REPLState]") -> bool:
    """Can this session hand over a real TTY (raw `!!`, vim/htop, pagers)?

    A remote face's console is not a terminal — handing over the tty would
    silently run the program in the SERVER's terminal (the served page just
    sees nothing). State-less callers default to True (old behavior)."""
    if state is None:
        return True
    return bool(getattr(getattr(state, "console", None), "is_terminal", True))


def _open_elsewhere(state: "REPLState", cmd: str, cwd: Path) -> None:
    """Face/Tauri has no TTY — open the full-screen program in a real terminal."""
    from xlii.interactive import launch_in_external_terminal

    pref = str(getattr(getattr(state, "cfg", None), "tui_terminal", "") or "")
    ok, msg = launch_in_external_terminal(cwd, run=cmd, preferred=pref)
    printer = getattr(state, "console", None) or console
    color = "green" if ok else "yellow"
    printer.print(f"[{color}]{msg}[/{color}]")


def _run_shell_passthrough(user_input: str, cwd: Path, state: Optional["REPLState"] = None) -> bool:
    """Handle `!<command>` (and `!!<command>`) shell escape at the REPL prompt.

    `!cmd` runs locally in `cwd`, no chat turn / history / tokens. With styled
    capture on (XLII_SHELL_STYLE=styled) it renders as a ShellBlock; otherwise
    it inherits the TTY as before. `!!cmd` always inherits the TTY — the raw
    escape hatch for pagers and full-screen apps. Returns True if the input was
    a `!` command and was handled (caller should `continue` the loop).
    """
    if not user_input.startswith("!"):
        return False
    raw = False
    body = user_input[1:]
    if body.startswith("!"):  # `!!` → raw inherited-TTY escape
        raw = True
        body = body[1:]
    cmd = body.strip()
    if not cmd:
        console.print("[dim]usage: ![/dim][cyan]<shell command>[/cyan]   "
                      "[dim](runs locally; [/dim][cyan]!![/cyan][dim] for a raw terminal)[/dim]")
        return True
    if state is not None:
        from xlii.shell_toolkit import gate_shell_command
        if not gate_shell_command(state, cmd):
            return True
    # `!!`, raw mode, OR a known full-screen program → hand over the real
    # terminal (capture can't host an ncurses app; the allowlist means a user
    # never has to know `!!` exists for the common tools like mc/vim/htop).
    from xlii.interactive import is_interactive, needs_password_tty
    if raw or not styled_enabled() or is_interactive(cmd) or needs_password_tty(cmd):
        if not _surface_has_terminal(state):
            _open_elsewhere(state, cmd, cwd)
            return True
        _run_raw_shell(cmd, cwd, console)
        return True
    if _is_clear_command(cmd):
        # Captured `clear` would only print escape codes into the ShellBlock;
        # clear the real surface instead (`!!clear` above keeps the raw TTY).
        console.clear()
        return True
    try:
        ev = run_shell_captured(cmd, cwd, source="user_bang")
    except OSError as e:
        console.print(f"[red]shell error: {e}[/red]")
        return True
    _session_renderer(state).emit(ev)
    if state is not None:
        _after_shell_capture(state, cmd, ev)
    else:
        _interactive_hint(console, cmd, ev)
    return True


def _after_shell_capture(state: "REPLState", cmd: str, ev) -> None:
    """Record capture, hint on interactive misuse, offer fix on failure."""
    from xlii.shell_toolkit import offer_failure_nudge, record_last_shell

    record_last_shell(state, ev)
    _interactive_hint(state.console, cmd, ev)
    if ev.returncode != 0:
        offer_failure_nudge(state, ev)


def _interactive_hint(console_, cmd: str, ev) -> None:
    """If a CAPTURED command turned out to be a full-screen program, point the
    user at the raw-terminal escape + the curation command — so the allowlist
    grows the next time they hit one it didn't already know about."""
    from xlii.shell_run import looks_interactive
    if not looks_interactive(getattr(ev, "stdout", ""), getattr(ev, "stderr", "")):
        return
    from xlii.interactive import program_token
    tok = program_token(cmd)
    msg = ("[yellow]that looked like a full-screen program[/yellow] "
           "[dim]— it needs a real terminal; re-run with [/dim][cyan]!!<cmd>[/cyan]")
    if tok:
        msg += f"[dim], or make it automatic: [/dim][cyan]/interactive add {tok}[/cyan]"
    console_.print(msg)


# ---------------------------------------------------------------------------
# Shell-primary ("flip mode") input model — see proposals/done/flipmode.md
#
# In the `code` REPL, bare input is a live shell command run in a tracked
# shell_cwd (cd moves it); the AI is summoned with `?`, meta-commands with `/`,
# and `!` forces a command at the project root. Conversational modes (chat, or
# `code` while in plan mode) stay talk-primary: bare input goes to the model,
# except desk nav (`cd`/`ls`/`pwd`) and `!` which share the live desk cwd.
# ---------------------------------------------------------------------------

# Common shell builtins that are not on $PATH but are still "real commands"
# (so the prose guard doesn't mistake them for natural language).
_SHELL_BUILTINS = {
    "cd", "export", "unset", "alias", "unalias", "source", "set", "pushd",
    "popd", "echo", "printf", "eval", "exec", "read", "local", "declare",
    "return", "true", "false", "test", "jobs", "fg", "bg", "wait", "type",
    "which", "command", ":", ".",
}

# Operators that mark a line as unambiguously a shell command line.
_SHELL_OPS_RE = re.compile(r"[|&;<>$`]")


def _shell_primary_enabled() -> bool:
    """Whether the `code` REPL treats bare input as a live shell command.

    Default ON. Set XLII_SHELL_PRIMARY to a falsey value (0/false/no/off) to
    fall back to the old AI-first model. This toggle is dogfooding scaffolding;
    the long-term model is mode-scoped (see proposals/done/flipmode.md), not a flag.
    """
    val = os.environ.get("XLII_SHELL_PRIMARY", "1").strip().lower()
    return val not in ("0", "false", "no", "off", "")


def _is_shell_primary(state: "REPLState") -> bool:
    """True when bare input should run as a live shell command for this state."""
    if not _shell_primary_enabled():
        return False
    if getattr(state, "ask_primary", False):
        return False  # the [$]/[M] flipmode button — user chose ask-first
    if getattr(state, "persona", None) is not None:
        return False  # chat REPL is conversational — bare input talks to the persona
    if getattr(state, "howto_mode", False):
        return False  # /howto is a talk-primary mode — bare input asks the guide
    try:
        if state.plan_mode:
            return False  # /plan is a talk-primary dialogue ("tit-for-tat")
    except AttributeError:
        # A state without plan_mode is not in a talk-primary mode; carry on with the checks below.
        pass
    if getattr(state, "discovery_mode", False):
        return False  # discovery is a "talk about the code" mode — bare input asks the agent
    if getattr(state, "ops_mode", False):
        return False  # ops is a host-diagnostics mode — bare input asks the agent
    agent = getattr(state, "agent", None)
    if agent is not None:
        # rail/debug are staged coding modes whose banners invite prose ("type your
        # task to begin stage 0" / "describe the bug to begin"). Like their sibling
        # modes above, bare input starts/continues the staged turn — otherwise a
        # multi-word task is routed to the live shell or rejected as "looks like a task".
        if getattr(agent, "rail", None) is not None:
            return False
        if getattr(agent, "debug", None) is not None:
            return False
    return True


# Talk-primary modes (howto, plan, chat): bare input goes to the AI, but lines
# starting with `/` still hit dispatch first. Naming an *action* command while
# asking about it (`/loop how does it work?`) must not start the command — use
# `/describe loop` for docs, or ask in plain words. Meta subcommands still run
# (`/loop status`, `/loop off`, …).
_TALK_PRIMARY_DEFER_SUBS: dict[str, frozenset[str]] = {
    "loop": frozenset({"status", "?", "resume", "pause", "cancel", "off"}),
    "rail": frozenset({"off", "next", "back", "status", "?"}),
    "plan": frozenset({"off"}),
    "execute": frozenset(),
    "verify": frozenset(),
    "peer": frozenset(),
    "consult": frozenset(),
    "yolo": frozenset(),
    "safe": frozenset(),
}

# Plan mode is a *work* mode, not a docs mode like /howto or chat: these are
# the actions you take on the pending plan, so they must dispatch even when
# bare (`/execute`, `/rail`). Deferring them would route `/execute` to the
# model as an ordinary read-only plan turn — leaving you stuck in plan mode.
_PLAN_CONTROL_CMDS = frozenset({"execute", "rail", "plan", "cancel"})

# Discovery mode is talk-primary, but switching *out* of it into another mode is
# an action, not a question about that command — these must dispatch even when
# bare (`/plan`, `/rail`) instead of being deferred to the agent as prose.
_DISCOVERY_CONTROL_CMDS = frozenset(
    {"discovery", "research", "plan", "rail", "debug", "execute", "cancel", "ops"}
)

# Ops mode is talk-primary, but switching *out* into another mode is an action.
_OPS_CONTROL_CMDS = frozenset(
    {"ops", "discovery", "research", "plan", "rail", "debug", "execute", "cancel"}
)


def _slash_token_rest(user_input: str) -> tuple[str, str]:
    """First slash token (no leading /) and the remainder, lowercased."""
    body = user_input[1:].strip()
    if not body:
        return "", ""
    parts = body.split(maxsplit=1)
    token = parts[0].lower()
    rest = parts[1].strip().lower() if len(parts) > 1 else ""
    return token, rest


def _slash_deferred_in_talk_primary(state: "REPLState", user_input: str) -> bool:
    """True when a slash line should become an agent turn instead of dispatch."""
    if _is_shell_primary(state):
        return False
    if not user_input.startswith("/"):
        return False
    token, rest = _slash_token_rest(user_input)
    if not token:
        return False
    # In plan mode the plan-control commands are actions, not docs queries —
    # always dispatch them (incl. bare /execute, /execute rail, /rail). Other
    # talk-primary modes (howto, chat) stay docs-first and defer as before.
    if getattr(state, "plan_mode", False) and token in _PLAN_CONTROL_CMDS:
        return False
    if getattr(state, "discovery_mode", False) and token in _DISCOVERY_CONTROL_CMDS:
        return False
    if getattr(state, "ops_mode", False) and token in _OPS_CONTROL_CMDS:
        return False
    safe_subs = _TALK_PRIMARY_DEFER_SUBS.get(token)
    if safe_subs is None:
        return False  # discovery/meta commands (/help, /describe, /howto, …)
    if not rest:
        return True  # bare /loop, /execute, … — asking about the command
    sub = rest.split(maxsplit=1)[0]
    return sub not in safe_subs


def _on_path(token: str) -> bool:
    return token in _SHELL_BUILTINS or bool(shutil.which(token))


def _looks_like_prose(line: str) -> bool:
    """Heuristic guard: a multi-word line that isn't a real command line.

    Catches the muscle-memory / paste foot-gun (a task typed at a shell-primary
    prompt, e.g. `implement the login page`) without taxing real commands:
    single tokens, lines with shell operators, and lines whose first token is a
    known binary / path all run normally.
    """
    s = line.strip()
    tokens = s.split()
    if len(tokens) < 2:
        return False
    if _SHELL_OPS_RE.search(s):
        return False
    first = tokens[0]
    if "/" in first or _on_path(first):
        return False
    return True


def _is_cd_command(line: str) -> bool:
    """A *pure* leading `cd` (no chaining) — the only form that persists.

    `cd a && ls` runs in a subshell like any other command; only a bare leading
    `cd` mutates the REPL's tracked cwd (subprocess can never move the parent).
    """
    s = line.strip()
    if not (s == "cd" or s.startswith("cd ")):
        return False
    return not re.search(r"[&;|]", s)


def _bang_cwd(state: "REPLState") -> Path:
    """Where ``!cmd`` runs.

    Shell-primary: project root (escape from a roam). Talk-primary: the live
    desk — ``!`` is the run prefix, not a re-root.
    """
    if _is_shell_primary(state):
        return Path(state.project.project_root)
    return Path(getattr(state, "shell_cwd", None) or state.project.project_root)


def _is_clear_command(line: str) -> bool:
    """A *pure* `clear`/`cls` — the screen-clear a captured subprocess can't do.

    Like `cd`, the live UI must handle this in-process: a subprocess `clear`
    only writes terminal escape codes into a captured buffer / append-only log,
    which clears nothing and leaves the next line at the old vertical offset (it
    just scrolls). Compound forms (`clear && ls`) fall through to a real shell.
    """
    return line.strip() in ("clear", "cls")


def _tilde(p: Path) -> str:
    try:
        return "~/" + str(p.relative_to(Path.home()))
    except ValueError:
        return str(p)


def shell_roam_root(state: "REPLState") -> Path:
    """Root for shell outside-checks and bare ``cd``.

    Real code projects: ``project_root``. Home desk (``scratch/home``): the
    user home — config lives under ``~/.xlii/scratch/home`` but roam is ``~``.
    """
    try:
        from xlii.project_paths import is_home_desk_project

        if is_home_desk_project(getattr(state, "project", None)):
            return Path.home().resolve()
    except Exception:
        # Can't tell whether this is the home desk -- fall through to the project root below.
        pass
    return Path(state.project.project_root).resolve()


def format_shell_cwd(state: "REPLState") -> str:
    """Compact prompt-prefix representation of the live shell cwd."""
    cwd = getattr(state, "shell_cwd", None)
    if cwd is None:
        return ""
    cwd = Path(cwd).resolve()
    root = shell_roam_root(state)
    try:
        from xlii.project_paths import is_home_desk_project

        home_desk = is_home_desk_project(getattr(state, "project", None))
    except Exception:
        home_desk = False
    if home_desk:
        # Product labels: ~ and ~/, not "scratch/home" / outside.
        home = Path.home().resolve()
        if cwd == home:
            return "~"
        if home in cwd.parents:
            return f"~/{cwd.relative_to(home)}"
        return f"(outside {_tilde(cwd)})"
    if cwd == root:
        return state.project.name
    if root in cwd.parents:
        return f"{state.project.name}/{cwd.relative_to(root)}"
    return f"(outside {_tilde(cwd)})"


def shell_cwd_is_outside(state: "REPLState") -> bool:
    """True when the live shell cwd has left the roam root.

    Home desk: outside means outside ``~``, not outside the config desk path.
    """
    cwd = getattr(state, "shell_cwd", None)
    if cwd is None:
        return False
    cwd = Path(cwd).resolve()
    root = shell_roam_root(state)
    return cwd != root and root not in cwd.parents


def terminal_title_enabled() -> bool:
    """Whether to drive the terminal title with the live cwd. On when stdout is
    a TTY and XLII_NO_TITLE isn't set. When off, the cwd falls back to the
    prompt prefix so the information is never lost."""
    if os.environ.get("XLII_NO_TITLE", "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    try:
        return bool(sys.stdout.isatty())
    except Exception:
        return False


def set_terminal_title(text: str) -> None:
    """Set the terminal/window title via OSC (kitty, gnome-terminal, xterm,
    iTerm, …). No-op when not on a TTY or disabled. Pass "" to clear."""
    if not terminal_title_enabled():
        return
    try:
        sys.stdout.write(f"\x1b]0;{text}\x07")
        sys.stdout.flush()
    except Exception:
        # A terminal that rejects the OSC sequence simply keeps its old title.
        pass


def _title_for(state: "REPLState") -> str:
    """Compact title: the live cwd in `code`, or the persona in `chat`."""
    label = format_shell_cwd(state)
    if not label:
        persona = getattr(state, "persona", None)
        label = getattr(persona, "name", None) or "code"
    return f"xlii · {label}"


def _write_exit_cwd(state: "REPLState") -> None:
    """Opt-in shell integration: if a wrapper set XLII_CWD_FILE, write the final
    live shell cwd so the parent shell can `cd` there on exit (see
    `xlii shell-init`). Only writes when the user actually navigated away from
    the project root — never moves the caller's shell just for opening xlii.
    A no-op without the env var or a tracked cwd (so chat is unaffected)."""
    path = os.environ.get("XLII_CWD_FILE")
    cwd = getattr(state, "shell_cwd", None)
    if not path or cwd is None:
        return
    try:
        cwd = Path(cwd).resolve()
        if cwd == state.project.project_root.resolve():
            return  # stayed at the root — leave the parent shell where it is
        Path(path).write_text(str(cwd))
    except OSError:
        # The parent shell just stays where it is.
        pass


_GLOB_META = re.compile(r"[*?[]")


def _has_glob(s: str) -> bool:
    return bool(_GLOB_META.search(s or ""))


def _cd_glob_hits(cwd: Path, expanded: str) -> list[Path]:
    """Pathname-expand *expanded* against *cwd*, bash-style.

    A directory actually named ``foo*`` wins over the glob. Zero matches
    fall through to the literal (bash without ``nullglob``).
    """
    literal = Path(expanded) if os.path.isabs(expanded) else cwd / expanded
    if literal.exists():
        return [literal]
    if not _has_glob(expanded):
        return [literal]
    pattern = expanded if os.path.isabs(expanded) else str(cwd / expanded)
    hits = sorted(Path(p) for p in globmod.glob(pattern))
    return hits or [literal]


def _change_dir(state: "REPLState", line: str) -> None:
    """Resolve a `cd` target against shell_cwd and update the tracked cwd."""
    roam = shell_roam_root(state)
    cwd = Path(getattr(state, "shell_cwd", None) or roam)
    try:
        parts = shlex.split(line)
    except ValueError:
        parts = line.split()
    target = parts[1] if len(parts) > 1 else None

    if target is None:
        # bare `cd` → roam root (project root, or ~ on the home desk)
        dest = roam
    elif target == "-":
        dest = Path(getattr(state, "_prev_shell_cwd", None) or cwd)
    elif target == "~":
        dest = Path.home()
    else:
        expanded = os.path.expanduser(os.path.expandvars(target))
        hits = _cd_glob_hits(cwd, expanded)
        if len(hits) > 1:
            names = " ".join(p.name for p in hits[:12])
            extra = " …" if len(hits) > 12 else ""
            state.console.print(
                f"[red]cd: too many arguments[/red] [dim]{names}{extra}[/dim]"
            )
            return
        dest = hits[0]

    try:
        dest = dest.resolve()
    except (OSError, RuntimeError) as e:
        state.console.print(f"[red]cd: {e}[/red]")
        return
    if not dest.is_dir():
        state.console.print(f"[red]cd: not a directory: {dest}[/red]")
        return

    state._prev_shell_cwd = cwd.resolve()
    state.shell_cwd = dest
    if dest == roam or roam in dest.parents:
        state.console.print(f"[dim]{format_shell_cwd(state)}[/dim]")
    else:
        state.console.print(
            f"[yellow]⚠ outside project[/yellow] [dim]{dest}[/dim] "
            f"[dim]— AI tools stay scoped to[/dim] [cyan]{state.project.name}[/cyan]"
        )


def _confirm_if_catastrophic(state: "REPLState", line: str) -> bool:
    """Gate system-level commands (sudo/rm-outside-root/dd/mkfs/…).

    Project-level mutations (git, sed -i, normal rm inside the project) run
    freely — like any real terminal — to keep the substrate honest. Yolo
    does not waive ``modifies-system``. Returns True to proceed, False to cancel.
    """
    try:
        from xlii.shellgate import classify_command, MODIFIES_SYSTEM
    except Exception:
        return True
    if classify_command(line, state.project.project_root) != MODIFIES_SYSTEM:
        return True
    state.console.print(f"[red]⚠ system-level command[/red] [cyan]{line}[/cyan]")
    try:
        answer = input("  run it? [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


def _handle_live_shell(state: "REPLState", line: str) -> None:
    """Run bare input as a live shell command in state.shell_cwd (with cd)."""
    cwd = Path(getattr(state, "shell_cwd", None) or state.project.project_root)

    if _is_cd_command(line):
        _change_dir(state, line)
        return

    if _is_clear_command(line):
        # Clear the live terminal in-process — a captured (or even inherited)
        # subprocess `clear` can't reliably reset the surface the REPL owns.
        state.console.clear()
        return

    if _looks_like_prose(line):
        state.console.print(
            "[yellow]that looks like a task, not a command[/yellow] "
            "[dim]— try[/dim] [cyan]/sh <task>[/cyan] [dim]for a command, or[/dim] "
            "[cyan]?<text>[/cyan] [dim]for the AI[/dim]"
        )
        return

    if not _confirm_if_catastrophic(state, line):
        state.console.print("[dim]cancelled[/dim]")
        return

    # A known full-screen program (mc/vim/htop/…) gets the real terminal even in
    # styled mode — capture can't host it. `!!` is the explicit override; this is
    # the automatic path so a user needn't know `!!` exists for common tools.
    from xlii.interactive import is_interactive, needs_password_tty
    if not styled_enabled() or is_interactive(line) or needs_password_tty(line):
        if not _surface_has_terminal(state):
            _open_elsewhere(state, line, cwd)
            return
        try:
            rc = subprocess.call(line, shell=True, cwd=str(cwd))
        except (OSError, subprocess.SubprocessError) as e:
            state.console.print(f"[red]shell error: {e}[/red]")
            return
        if rc != 0:
            state.console.print(f"[dim]exit {rc}[/dim]")
    else:
        try:
            ev = run_shell_captured(line, cwd, source="user_shell")
        except OSError as e:
            state.console.print(f"[red]shell error: {e}[/red]")
            return
        _session_renderer(state).emit(ev)
        rc = ev.returncode
        _after_shell_capture(state, line, ev)
    _note_shell_activity(state, cwd)
    if rc == 0:
        _xlii_stateful_heads_up(state, line)


# ---------------------------------------------------------------------------
# Phase 3 — the payoff: re-init-to-re-root + `xlii …` awareness.
# (proposals/done/flipmode.md §5, §6). All suggest-only; nothing here auto-acts.
# ---------------------------------------------------------------------------

# How many commands a user must run in an outside-root directory before the
# re-root nudge is allowed to fire (once they then ask the AI about it).
_REROOT_MIN_ACTIVITY = 2

# Top-level `xlii` subcommands that only READ state — running them in-session
# is harmless, so they get no heads-up.
_XLII_READONLY = {"status", "projects", "help", "doctor", "shell-init", "export"}
# (cmd, subcmd) pairs with a tailored heads-up (more specific than the generic).
_XLII_STATEFUL = {("models", "set")}


def _note_shell_activity(state: "REPLState", cwd: Path) -> None:
    """Count commands run in each outside-root directory (re-root heuristic)."""
    if not shell_cwd_is_outside(state):
        return
    activity = getattr(state, "_shell_activity", None)
    if activity is None:
        activity = {}
        state._shell_activity = activity
    key = str(Path(cwd).resolve())
    activity[key] = activity.get(key, 0) + 1


def _maybe_offer_reroot(state: "REPLState") -> None:
    """Single-shot, suggest-only: when the user is clearly working in a
    non-project directory outside the root, suggest `xlii init` to make it its
    own project (which re-locks the project-root invariant around the new root).
    Never auto-acts; never nags — one quiet line per cwd, only with prior
    activity, and only if the dir isn't already an xlii project.
    """
    if not shell_cwd_is_outside(state):
        return
    cwd = Path(state.shell_cwd).resolve()
    key = str(cwd)
    offered = getattr(state, "_reroot_offered", None)
    if offered is None:
        offered = set()
        state._reroot_offered = offered
    if key in offered:
        return
    if (cwd / ".xlii").is_dir():
        return  # already a project — re-rooting is a different, explicit action
    if getattr(state, "_shell_activity", {}).get(key, 0) < _REROOT_MIN_ACTIVITY:
        return
    offered.add(key)
    state.console.print(
        f"[dim]you've been working in[/dim] [cyan]{cwd}[/cyan][dim]; run[/dim] "
        f"[bold]xlii init[/bold] [dim]here to make it its own project — its files "
        f"aren't part of[/dim] [cyan]{state.project.name}[/cyan][dim].[/dim]"
    )


def _xlii_stateful_heads_up(state: "REPLState", line: str) -> None:
    """A bare `xlii …` ran as a subprocess inside the live session. A child
    process can't mutate the running Agent's loaded config/state, so warn that
    its effect won't be visible here — and that stateful commands can conflict
    with what's running. Suggest-only (flipmode.md §6); never blocks."""
    try:
        toks = [t for t in shlex.split(line) if not t.startswith("-")]
    except ValueError:
        return
    if len(toks) < 2 or Path(toks[0]).name != "xlii":
        return
    cmd = toks[1]
    sub = toks[2] if len(toks) > 2 else None

    if (cmd, sub) in _XLII_STATEFUL:
        state.console.print(
            "[dim]heads-up: that changed model config on disk, but this session "
            "keeps its current models until you restart[/dim] [bold]xlii code[/bold]"
            "[dim] — use[/dim] [cyan]/models[/cyan] [dim]to check.[/dim]"
        )
        return

    # Read-only invocations change nothing the session caches — stay quiet.
    if cmd in _XLII_READONLY or sub == "list" or (cmd == "models" and sub is None):
        return

    state.console.print(
        f"[dim]heads-up: [bold]xlii {cmd}[/bold] ran in a subprocess — any "
        "config/key/project/collection changes it made won't reach this live "
        "session until you restart[/dim] [bold]xlii code[/bold][dim]; stateful "
        "commands (sync/init/setup) can also conflict with what's running here.[/dim]"
    )


def process_repl_input(state: "REPLState", user_input: str) -> tuple[Optional[str], bool]:
    """
    Core input processor used by the unified REPL loop.

    Dispatch precedence (proposals/done/flipmode.md): /exit → `!` force-shell →
    `/` meta (incl. rewrite-markers + persona switch) → `?` AI → mode default
    (live shell in `code`, or an agent turn in conversational modes). The
    rewrite-marker check stays AHEAD of the live-shell branch so an approved
    /execute runs as an agent turn, never as a shell command named 'Approved.'.

    Returns (rewritten_or_None, should_continue):
      (None, True)  -> handled; loop continues, no turn.
      (None, False) -> run an agent turn with the original input.
      (text, False) -> run an agent turn with `text`.
    """
    from xlii.commands import dispatch_repl_command

    if not user_input:
        return None, True

    if user_input in ("/exit", "/quit"):
        state.save()  # persist attachments etc. before leaving
        return None, True

    # `!<command>` — explicit, ungated shell escape.
    # Shell-primary: at the project root (roam escape). Talk-primary: at the
    # live desk cwd, and `!cd` persists — `!` is the only run prefix there.
    if user_input.startswith("!"):
        body = user_input[1:]
        if body.startswith("!"):
            body = body[1:]
        cmd = body.strip()
        if cmd and (not _is_shell_primary(state)) and _is_cd_command(cmd):
            _change_dir(state, cmd)
            return None, True
        _run_shell_passthrough(user_input, _bang_cwd(state), state)
        return None, True

    # `/<command>` — meta-commands win over the shell. Unknown slashes fall
    # through to the agent (preserving prior behavior), never to the shell.
    if user_input.startswith("/"):
        if _slash_deferred_in_talk_primary(state, user_input):
            return None, False  # talk-primary: name-check an action cmd → ask the guide
        ctx = state.as_context_dict()
        handled = dispatch_repl_command(user_input, ctx)

        for marker in ("_execute_rewritten", "_get_rewritten", "_rail_rewritten",
                       "_debug_rewritten", "_loop_rewritten", "_freeball_rewritten"):
            if marker in ctx:
                return ctx.pop(marker), False

        if "_switch_persona" in ctx:
            # ctx is a throwaway dict — move the marker onto state to survive.
            state.pending_persona_switch = ctx["_switch_persona"]
            state.save()
            return None, True

        if handled:
            read_only = {"help", "status", "cost", "models", "projects", "personas"}
            _parts = user_input[1:].split(maxsplit=1)  # bare "/" → [] (don't index blindly)
            token = _parts[0] if _parts else ""
            if token and token not in read_only:
                state.save()
            return None, True
        return None, False  # unknown slash → agent turn (old behavior)

    # `?><text>` — post-process the last captured shell output (no re-run).
    if user_input.startswith("?>"):
        remainder = user_input[2:].strip()
        if not remainder:
            state.console.print(
                "[dim]usage: ?>[/dim][cyan]<instruction>[/cyan]   "
                "[dim](analyze last shell output)[/dim]"
            )
            return None, True
        from xlii.shell_toolkit import post_process_flow

        post_process_flow(state, remainder)
        return None, True

    # `?<text>` — summon the AI. Mode-agnostic: works in code and chat alike.
    if user_input.startswith("?"):
        remainder = user_input[1:].strip()
        if not remainder:
            state.console.print(
                "[dim]usage: ?[/dim][cyan]<text>[/cyan]   [dim](send to the AI)[/dim]"
            )
            return None, True
        # The user is asking the AI while their shell may be elsewhere — the
        # right moment to (quietly, once) suggest re-rooting if they've been
        # working in a non-project directory. Self-gates; no-op otherwise.
        _maybe_offer_reroot(state)
        return remainder, False

    # Harness-as-a-mode (Vector C, Tier 1.5): when a harness is the foreground
    # mode, bare input drives that live session instead of xlii's own agent.
    # `!`/`/`/`?` above still escape (shell, commands, ask-xlii); only plain
    # lines route to the harness. Returns False (falls through) when no mode is
    # active, so this is inert outside harness mode.
    if getattr(state, "harness_foreground", None):
        from xlii.harness.session import drive_foreground_session

        if drive_foreground_session(state, user_input):
            return None, True

    # Mode default.
    if _is_shell_primary(state):
        _handle_live_shell(state, user_input)
        return None, True
    # Face / TUI [M] (ask_primary): `cd` / `ls` / `pwd` still move the live
    # desk so `/sh` sees where they just went. Plan/howto stay talk — a bare
    # `ls` there is a question, not a navigator. Prose (`cd to the downloads
    # folder`) is not desk-nav.
    if getattr(state, "ask_primary", False):
        from xlii.desk import is_desk_nav

        if is_desk_nav(user_input):
            _handle_live_shell(state, user_input)
            return None, True
    return None, False  # conversational: bare input is an agent turn


def repair_interrupted_history(history: list[dict]) -> None:
    """Make history API-valid after a mid-turn interrupt.

    An interrupt can leave a trailing assistant message whose tool_calls have
    no matching tool results — the API rejects that on the next turn. Drop
    trailing tool results without a preceding assistant entry, then drop a
    trailing assistant entry that still has unanswered tool_calls.
    """
    # Drop trailing orphaned tool results (interrupt between tool appends).
    while history and history[-1].get("role") == "tool":
        # tool results are only orphaned if their assistant entry was dropped;
        # here they're fine as long as the assistant entry above has matching
        # ids — simplest safe repair: drop the whole trailing tool-call group.
        history.pop()
    if history and history[-1].get("role") == "assistant" and history[-1].get("tool_calls"):
        history.pop()


def _checkpoint_begin(state: "REPLState") -> Optional[str]:
    try:
        from xlii.checkpoints import begin_turn

        tree, warn = begin_turn(state.project.project_root)
        if warn:
            try:
                state.console.print(f"[yellow]{warn}[/yellow]")
            except Exception:
                # The checkpoint already began; only the warning line is lost.
                pass
        return tree
    except Exception:
        # Checkpoints are best-effort — never break a turn.
        return None


def _checkpoint_end(state: "REPLState", tree: Optional[str], dirty: set[str]) -> None:
    try:
        from xlii.checkpoints import end_turn

        end_turn(state.project.xli_dir, state.project.project_root, tree, dirty)
    except Exception:
        # Best-effort: a failed ledger write must not mask turn output.
        pass


def _session_meter_begin(state: "REPLState") -> None:
    """Warn when the soft budget is already exceeded (before the next turn)."""
    from xlii.session_meter import budget_warning

    msg = budget_warning(state.agent.session)
    if msg:
        state.console.print(msg)
    try:
        from xlii.context_compact import maybe_auto_compact

        maybe_auto_compact(state)
    except Exception:
        # Auto-compaction is opportunistic -- the turn proceeds with the current context.
        pass


def _session_meter_end(state: "REPLState", turn_stats: Any) -> None:
    """Roll per-turn stats into the session meter."""
    from xlii.session_meter import record_turn

    record_turn(state.agent.session, turn_stats)


def _sync_loop_session(state: "REPLState") -> None:
    ctrl = getattr(state, "loop", None)
    if ctrl is None:
        state.agent.session.loop_lock_tests = False
        return
    ctrl.sync_test_lock(state.agent.session)


def _drive_loop_continuation(
    state: "REPLState",
    run_turn: Callable[[str], tuple[str, set[str], Any]],
    *,
    render: Callable[[Any, str], None],
    on_error: Callable[[Exception], None],
) -> None:
    """After a turn, advance an active autonomous loop and run follow-up turns.

    Phase 5b: each continuation rides THE spine (:func:`drive_turn` with
    ``continue_loop=False`` — this loop owns the advance; the spine's tail
    would double-advance). Continuations therefore get the full lifecycle a
    main turn gets — persistence, journal, receipt — which the legacy
    ``post_turn`` lane only sometimes provided."""
    ctrl = getattr(state, "loop", None)
    if ctrl is None or not ctrl.is_active:
        return

    from xlii.conversation import drive_turn
    from xlii.loop import LoopJudgeContext

    while ctrl.is_active:
        result = ctrl.advance_after_build(
            state.project.project_root,
            judge_ctx=LoopJudgeContext(agent=state.agent, console=state.console),
        )
        if result.message:
            state.console.print(result.message)
        if result.status in ("done", "failed", "capped"):
            state.loop = None
            if result.status == "done":
                ctrl.clear()
            break
        if result.status != "continue" or not result.next_prompt:
            break

        continuation = result.next_prompt
        try:
            res = drive_turn(
                state, continuation, run_turn,
                render=render, on_error=on_error,
                hook_extra={"loop": True},
                fold_attachments=False,   # staged files ride the ORIGINAL turn
                continue_loop=False,
            )
        except KeyboardInterrupt:
            repair_interrupted_history(state.agent.history)
            ctrl.mark_interrupted()
            state.console.print(
                "\n[yellow]loop turn interrupted[/yellow] — /loop resume to continue"
            )
            break
        if res is None:                   # turn failed — on_error already rendered
            ctrl.mark_interrupted()
            break
        # Between-cycle observability on every surface (was TUI-only): the
        # loop panel with cycle/phase/verdicts, straight to the live console.
        if ctrl.is_active:
            try:
                for line in ctrl.status_lines():
                    state.console.print(line)
            except Exception:
                # Loop status lines are diagnostic; the loop keeps driving either way.
                pass


def _control_hooks_enabled(state: "REPLState") -> bool:
    """Effective control-hook gate (B1). The session override (`/hook-control
    on|off`) wins; otherwise the project.json `hooks.control` default. Off unless
    explicitly enabled — control hooks can drive turns, so opt-in is the point."""
    from xlii.hooks import control_enabled_for_project

    override = getattr(state.agent.session, "hook_control", None)
    if override is not None:
        return bool(override)
    try:
        return control_enabled_for_project(state.project.xli_dir)
    except Exception:
        return False


def _drive_policy_hooks(
    state: "REPLState",
    run_turn: Callable[..., tuple[str, set[str], Any]],
    render: Callable[[Any, str], None],
    last_input: str,
    last_dirty: set[str],
    last_stats: Any,
) -> None:
    """B1: after an interactive turn, let `on-turn-stop` control hooks drive
    capped follow-up turns ('keep going until tests pass').

    No-op unless control is enabled. The cap is hard (POLICY_HOOK_HARD_CAP); a
    hook may request a smaller/larger `max_loops` but it is clamped — there is no
    unbounded loop. Skipped while an autonomous /loop drives its own continuation
    (the caller gates on that)."""
    from xlii.hooks import (
        POLICY_HOOK_DEFAULT_MAX,
        POLICY_HOOK_HARD_CAP,
        run_control_hooks,
    )

    base_data = {
        "user_input": last_input,
        "dirty": sorted(last_dirty),
        "tool_calls": getattr(last_stats, "tool_calls", 0),
    }
    if not _control_hooks_enabled(state):
        # Contract: on-turn-stop hooks still run as observers when control is off
        # (their followup is ignored), so a hook is harmless until opt-in.
        run_control_hooks(state.project.xli_dir, base_data,
                           console=state.console, control_enabled=False)
        return

    budget = POLICY_HOOK_DEFAULT_MAX
    used = 0
    user_input, dirty, stats = last_input, last_dirty, last_stats
    while used < budget:
        directive = run_control_hooks(
            state.project.xli_dir,
            {"user_input": user_input, "dirty": sorted(dirty),
             "tool_calls": getattr(stats, "tool_calls", 0), "followups_used": used},
            console=state.console, control_enabled=True,
        )
        followup = (directive or {}).get("followup")
        if not followup:
            break
        req = (directive or {}).get("max_loops")
        if isinstance(req, int) and req > 0:
            budget = min(req, POLICY_HOOK_HARD_CAP)
        if used >= budget:
            break
        used += 1
        followup = str(followup)
        state.console.print(
            f"[cyan]policy hook → follow-up {used}/{budget}[/cyan] "
            f"[dim]{followup[:80]}[/dim]"
        )
        # Phase 5b: policy follow-ups ride THE spine (full lifecycle —
        # persistence, journal, receipt — like any other turn).
        from xlii.conversation import drive_turn
        try:
            res = drive_turn(
                state, followup, run_turn,
                render=render, on_error=lambda e, _f=followup: state.console.print(
                    f"[red]policy follow-up failed: {e}[/red]"),
                hook_extra={"policy": True},
                fold_attachments=False,
                continue_loop=False,
            )
        except KeyboardInterrupt:
            repair_interrupted_history(state.agent.history)
            state.console.print("\n[yellow]policy follow-up interrupted[/yellow]")
            return
        if res is None:
            return
        user_input = followup
        dirty, stats = res.dirty, res.stats

    if used and used >= budget:
        state.console.print(
            f"[dim]policy hooks: hit the {budget}-follow-up cap — stopping[/dim]"
        )


def run_repl_loop(
    state: "REPLState",
    *,
    session: Any,
    get_prompt_prefix: Callable[[], str],
    run_turn: Callable[[str], tuple[str, set[str], Any]],
    render_turn: Optional[Callable[[Any, str], None]] = None,
    on_exit: Optional[Callable[[], None]] = None,
) -> None:
    """
    Full unified REPL loop helper.

    Both `xlii code` and `xlii chat` can now delegate their main while-loop to this.
    This eliminates the massive duplication between the two REPLs.

    The caller is responsible for:
    - Creating the REPLState
    - Providing a prompt prefix generator (can include persona name, attachments, plan/yolo tags)
    - The actual run_turn (usually agent.run_turn)
    - *render_turn(result, prompt)* — render-ONLY turn delivery for the
      kernel spine (:func:`xlii.conversation.drive_turn`), which owns
      persistence/sync/hooks/journal/receipt itself and serves main, loop-
      continuation, and policy turns alike (Phase 5b: the post_turn legacy
      lane is gone). Default: print reply + turn line.
    """
    # Load per-project commands (if any)
    try:
        from xlii.commands import load_project_commands
        load_project_commands(state.project.xli_dir)
    except Exception:
        # No per-project commands -- the built-in command set still loads.
        pass

    # Load per-project tools that the *agent* can call (extensible tool surface)
    try:
        from xlii.tools import load_project_tools
        load_project_tools(state.project.xli_dir)
    except Exception:
        # No per-project tools -- the built-in tool surface still loads.
        pass

    # === Durable session state (the feature we're building) ===
    restored = state.load()
    from xlii.session_meter import init_budget_from_env

    init_budget_from_env(state.agent.session)
    from xlii.repl_cmds.compact import init_compact_auto_from_env

    init_compact_auto_from_env(state.agent.session)
    # The inline REPL is a real mouth too (tui-media-delivery P0; VM↔local
    # parity is CAPABILITY parity — the plain terminal is a body). Grant a
    # local outbox; each turn drains it through the stdout preview cascade.
    # Release at process exit (the loop's exits route through graceful-exit
    # helpers, and one process runs one loop — atexit is the honest teardown).
    try:
        from xlii.outbox import grant_local_outbox, release_local_outbox
        if grant_local_outbox(state.agent.session) is not None:
            import atexit
            atexit.register(release_local_outbox, state.agent.session)
    except Exception:
        # Without the grant there is nothing to release, so skipping the atexit hook is correct.
        pass
    if restored and (state.attached_refs or state.attached_docs):
        ref_names = [n for n, _ in state.attached_refs]
        doc_names = [n for n, _ in state.attached_docs]

        parts = []
        if ref_names:
            shown = ", ".join(ref_names[:3])
            if len(ref_names) > 3:
                shown += "..."
            parts.append(f"refs: {shown}")
        if doc_names:
            shown = ", ".join(doc_names[:3])
            if len(doc_names) > 3:
                shown += "..."
            parts.append(f"docs: {shown}")

        state.console.print(f"[dim]restored session: {' | '.join(parts)}[/dim]")

    from xlii.tui import input_chrome
    from xlii.paste_collapse import PasteStore, attach_prompt_toolkit_paste_collapse

    # Large clipboard pastes → one-line placeholders in the editable buffer so a
    # traceback paste can't scroll the prior answer out of the terminal. Expanded
    # back to full text before slash/agent routing so behavior is unchanged.
    paste_store = getattr(session, "_xlii_paste_store", None)
    if not isinstance(paste_store, PasteStore):
        try:
            paste_store = attach_prompt_toolkit_paste_collapse(session)
        except Exception:
            paste_store = PasteStore()  # expand is still a no-op without stashes

    while True:
        prompt_prefix = get_prompt_prefix()
        set_terminal_title(_title_for(state))
        try:
            message = input_chrome.prompt_message(state, f"\n{prompt_prefix}")
            # A command may queue text for the next prompt (e.g. /browse --reference
            # inserts picked paths into the editable buffer). Consume it once.
            default_text = getattr(state, "pending_input", "") or ""
            if default_text:
                state.pending_input = ""
            user_input = session.prompt(
                message, bottom_toolbar=input_chrome.toolbar(state), default=default_text
            ).strip()
        except KeyboardInterrupt:
            # Ctrl-C at the prompt clears the line, never kills the session
            continue
        except EOFError:
            _write_exit_cwd(state)
            set_terminal_title("")  # let the shell reclaim the title
            state.console.print()   # break off the ^D line
            from xlii.exit_sequence import run_graceful_exit
            run_graceful_exit(state, printer=state.console.print, on_exit=on_exit)
            return

        if not user_input:
            continue

        user_input = paste_store.expand(user_input)

        if user_input in ("/exit", "/quit"):
            state.quit_requested = True  # A2: record the disposition (fire alarm)
            _write_exit_cwd(state)
            set_terminal_title("")  # let the shell reclaim the title
            from xlii.exit_sequence import run_graceful_exit
            run_graceful_exit(state, printer=state.console.print, on_exit=on_exit)
            return

        rewritten, should_continue = process_repl_input(state, user_input)

        # A2: a handled command (e.g. /tui → the nested TUI's /exit·/quit, or any
        # future surface) may have set quit_requested. Honor it here so the quit
        # unwinds past every layer instead of resuming the loop — no backing out.
        if getattr(state, "quit_requested", False):
            _write_exit_cwd(state)
            set_terminal_title("")
            # A nested /tui's /exit already ran the announced sequence; the
            # idempotent guard makes this a no-op (no double "bye"). A quit set
            # by another surface runs it fully here.
            from xlii.exit_sequence import run_graceful_exit
            run_graceful_exit(state, printer=state.console.print, on_exit=on_exit)
            raise _QuitSession()

        if rewritten:
            user_input = rewritten

        if getattr(state, "pending_persona_switch", None):
            return  # caller (chat) restarts the session with the new persona

        if should_continue:
            continue

        # Phase 2 (proposals/done/flipmode.md): stamp the live shell cwd onto the
        # session so run_turn can tell the model where the user physically is —
        # only when the shell has wandered outside the project root.
        try:
            state.agent.session.user_shell_cwd = (
                state.shell_cwd if shell_cwd_is_outside(state) else None
            )
        except AttributeError:
            # A session stub without the field just doesn't carry the shell-cwd hint.
            pass

        # Execute turn — through THE kernel spine (convergence Phase 4). The
        # spine owns sync/meter/hooks/attachments/checkpoints/Conversation/
        # persistence/journal/loop-continuation/policy-hooks; this loop keeps
        # only what is genuinely the inline surface's: interrupt recovery.
        from xlii.conversation import drive_turn

        def _default_render(result: Any, prompt: str) -> None:
            if result.reply:
                state.console.print()
                state.console.print(result.reply)
            state.console.print(format_turn_line(result.stats))

        def _on_turn_error(e: Exception) -> None:
            state.console.print(f"[red]turn failed: {e}[/red]")

        try:
            try:
                drive_turn(
                    state, user_input, run_turn,
                    render=render_turn if render_turn is not None else _default_render,
                    on_error=_on_turn_error,
                    policy_hooks=True,
                )
            finally:
                # Media-out drain — once per turn, error turns included, so a
                # queued file surfaces now instead of leaking into the next turn.
                from xlii.outbox import drain_outbox
                try:
                    drain_outbox(state.agent.session, state.console)
                except Exception:
                    # Undelivered files ride the next turn instead.
                    pass
        except KeyboardInterrupt:
            repair_interrupted_history(state.agent.history)
            if getattr(state, "loop", None) is not None:
                try:
                    state.loop.mark_interrupted()
                except Exception:
                    # The interrupt is already handled; marking the loop is bookkeeping.
                    pass
            state.console.print(
                "\n[yellow]turn interrupted[/yellow] — history repaired, back at the prompt"
            )
            continue
