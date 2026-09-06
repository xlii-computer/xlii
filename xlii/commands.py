"""REPL slash command registry for xlii.

This module provides a declarative way to define interactive slash commands
used inside the `xlii code` and `xlii chat` REPLs.

Goals:
- Eliminate the giant if/elif chains in the REPL loops.
- Make help text self-documenting and grouped.
- Provide a clean extension point for future user-contributed or plugin
  commands (the "user composes the system" thesis).
- Keep dispatch simple and the transition seamless.

Current focus: REPL slash commands only. Top-level CLI subcommands (via argparse)
remain as-is for now; we may unify help generation later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


@dataclass
class REPLCommand:
    """Declarative definition of one slash command (e.g. /plan, /ref, /yolo)."""

    name: str
    """Primary name without the leading slash, e.g. "plan"."""

    handler: Callable[[str, dict[str, Any]], bool]
    """Handler function: (full_user_input_line, context_dict) -> handled?

    The handler should return True if it completely handled the command
    (the REPL should `continue` the loop). Return False (or fall off the end)
    to let the input be treated as a normal prompt to the model (rare, used
    by /get for intent rewriting).
    """

    aliases: list[str] = field(default_factory=list)
    """Alternative names, e.g. ["quit"] for "exit"."""

    description: str = ""
    """One-line description shown in /help."""

    usage: str = ""
    """Example usage, e.g. "/temp <0.0..2.0>"."""

    category: str = "general"
    """Grouping for help output: "session", "mode", "knowledge", "admin", etc."""

    repls: list[str] = field(default_factory=lambda: ["code", "chat"])
    """Which REPLs this command is available in: subset of {"code", "chat"}."""

    source: str = "builtin"
    """Where the command came from: "builtin", "project", "plugin", etc.
    Used for /help grouping and diagnostics.
    """

    role: Optional[str] = None
    """Role command-namespace that owns this command (roles R4), e.g. "architect".
    None = global/flat (today's behavior). A role-owned command is addressed
    /<role>:<name> and resolves ONLY while that role is active (REPLState.active_role).
    A *peer* of `repls`: a second resolve-time scope axis, not a routing mode."""

    capability: Optional[str] = None
    """Capability gate (Vector E / merge seam #1). None = ungated (today's
    behavior, every existing command). A non-None value names the privilege a
    session must hold before the command may run; `dispatch_repl_command` refuses
    it centrally unless the session is *elevated* (see `/admin unlock`).

    Declarative by design (Option B, NOT role-reuse): register commands with the
    existing API exactly as before; to gate one, set `capability="admin"`. The
    field is optional + additive so it is safe to land day 1 and rebase onto."""

    conversational: Optional[bool] = False
    """Presentation hint: surfaces frame this command's invocation as a user
    *question* — pin/persist the ask while the answer streams (TUI-only)."""

    human_only: bool = False
    """Foreground-human gate (Vector G). When True, dispatch refuses unless
    ``is_foreground_console(context['console'])`` — ``context['elevated']`` is
    not sufficient proof of a human at the desk."""

    def matches(self, token: str) -> bool:
        """Does this command match the token (name or alias, without /)?"""
        return token == self.name or token in self.aliases


# Global registry (populated at import time by register_repl_command calls).
# _INDEX maps each name/alias to the commands claiming it — at most one per
# REPL scope ("code" / "chat"); overlap within a scope is a registration error.
_REPL_COMMANDS: list[REPLCommand] = []
_INDEX: dict[str, list[REPLCommand]] = {}

# Track which command names came from the current project (for reload support)
_PROJECT_COMMAND_NAMES: set[str] = set()

# Collect load errors so users can inspect them with /commands errors
_COMMAND_LOAD_ERRORS: dict[Path, str] = {}  # path -> full traceback


def register_repl_command(cmd: REPLCommand) -> None:
    """Register a command (call at module import time).

    Duplicate registration of a name/alias in an overlapping REPL scope is a
    hard error — silent shadowing is how chat's /status got eaten by code's.
    Exception: a project-sourced command may override a builtin (the user is
    explicitly customizing); two commands from the same source may not collide.
    """
    for key in [cmd.name] + cmd.aliases:
        claimants = _INDEX.setdefault(key, [])
        for other in list(claimants):
            if not (set(other.repls) & set(cmd.repls)):
                continue  # disjoint surface scope — both can own the name
            if other.role != cmd.role:
                continue  # disjoint role scope (R4) — both can own the name
            if cmd.source == "project" and other.source == "builtin":
                claimants.remove(other)  # explicit user override wins
                continue
            raise ValueError(
                f"duplicate slash command /{key}: already registered by "
                f"{other.source} command /{other.name} for repl(s) "
                f"{sorted(set(other.repls) & set(cmd.repls))}"
            )
        claimants.append(cmd)

    _REPL_COMMANDS.append(cmd)

    # Track project commands for reload support
    if cmd.source == "project":
        _PROJECT_COMMAND_NAMES.add(cmd.name)
        for alias in cmd.aliases:
            _PROJECT_COMMAND_NAMES.add(alias)


def unregister_repl_command(name: str) -> bool:
    """Remove command(s) by name or alias. Returns True if something was removed."""
    cmds = _INDEX.get(name)
    if not cmds:
        return False

    for cmd in list(cmds):
        _REPL_COMMANDS[:] = [c for c in _REPL_COMMANDS if c is not cmd]
        for key in [cmd.name] + cmd.aliases:
            entry = _INDEX.get(key)
            if entry and cmd in entry:
                entry.remove(cmd)
            if entry == []:
                _INDEX.pop(key, None)
        _PROJECT_COMMAND_NAMES.discard(cmd.name)
        for alias in cmd.aliases:
            _PROJECT_COMMAND_NAMES.discard(alias)

    return True


def iter_repl_commands() -> list[REPLCommand]:
    """Return the live registered-command list (for help generation)."""
    return _REPL_COMMANDS


def _split_ns(token: str) -> tuple[Optional[str], str]:
    """Split a role-namespaced token: 'architect:review-design' -> ('architect',
    'review-design'); 'plan' -> (None, 'plan')."""
    ns, sep, name = token.partition(":")
    return (ns, name) if sep else (None, token)


def find_repl_command(
    user_input: str, repl: str = "code", active_role: Optional[str] = None
) -> Optional[REPLCommand]:
    """Find a registered command, scoped on two resolve-time axes (roles R4):
    the REPL surface (`repl`: "code"/"chat") and the role namespace (`active_role`).

    A global command is addressed bare (`/plan`); a role command by its prefix
    (`/architect:review-design`) and resolves ONLY while that role is active —
    inactive it resolves to nothing (an unknown command), never a routing change.
    Returns None for plain text or unknown/parked commands.
    """
    if not user_input or not user_input.startswith("/"):
        return None
    parts = user_input[1:].split(maxsplit=1)     # strip leading / and take first word
    if not parts:
        return None                              # bare "/" (or "/   ") — not a command
    token = parts[0]
    ns, name = _split_ns(token)                  # ns = the *typed* role qualifier, or None
    for cmd in _INDEX.get(name, []):             # index is keyed by bare name (+ aliases)
        if repl not in cmd.repls:
            continue                             # axis 1 — surface scope (unchanged)
        if cmd.role != ns:
            continue                             # axis 2 — address a role cmd by its prefix,
                                                 #          a global by no prefix
        if cmd.role is not None and cmd.role != active_role:
            continue                             # role-owned: only while its role is active
        return cmd
    return None


def _closest_command(name: str, repl: str, active_role: Optional[str]) -> Optional[str]:
    """Best fuzzy match for a mistyped command name, among the commands actually
    available in this surface/role (so we never suggest a parked or other-REPL one)."""
    import difflib

    avail = [
        key
        for key, cmds in _INDEX.items()
        if any(repl in c.repls and (c.role is None or c.role == active_role) for c in cmds)
    ]
    matches = difflib.get_close_matches(name, avail, n=1, cutoff=0.6)
    return matches[0] if matches else None


def session_is_elevated(context: dict[str, Any]) -> bool:
    """Has this session cleared the capability gate (merge seam #1)?

    True when `/admin unlock` elevated the session — it sets `state.elevated` on
    the live REPLState — or when a caller passes an explicit `elevated=True` in
    the context (the headless / test path). Fail-closed: anything we cannot read
    counts as *not* elevated, so a gated command never runs unprivileged."""
    if context.get("elevated"):
        return True
    state = context.get("state")
    return bool(getattr(state, "elevated", False))


def dispatch_repl_command(
    user_input: str, context: dict[str, Any]
) -> bool:
    """Dispatch a slash command if one matches.

    Returns True if a command was found and handled (REPL should continue).
    Returns False if no command matched (fall through to model or error).
    """
    # RP2: the live command scope is explicit on REPLState. Fall back to
    # persona-presence so pre-RP2 callers (and raw as_context_dict()) stay correct.
    repl = context.get("command_scope") or ("chat" if context.get("persona") else "code")
    active_role = context.get("active_role")  # roles R4 — set on /role activation
    cmd = find_repl_command(user_input, repl=repl, active_role=active_role)
    if cmd is not None and repl == "chat":
        # V3b: chat's capability profile GATES the slash surface (not just
        # describes it) — only conversation-local verbs + read-only meta reach
        # the handler; everything else is refused at dispatch.
        from xlii.mode_contract import get_mode

        policy = get_mode("chat").capabilities.slash_commands
        if not policy.permits(cmd.name):
            console = context.get("console")
            msg = (
                f"/{cmd.name} is not available in the chat REPL (safe mode) — "
                "/code for the full surface"
            )
            if console:
                console.print(f"[dim]{msg}[/dim]")
            else:
                print(msg)
            return True
        if cmd.name == "plan":
            # Catalog-legal in talk so Face can offer the gateway. Never
            # start PlanController on a chat desk — Face intercepts /plan
            # and flips to [$] first; the inline chat REPL has no lab flip.
            console = context.get("console")
            msg = (
                "/plan is a lab mode — talk never starts it. "
                "Face: /plan from [M] flips to [$]; "
                "/plan --from-mojo carries recent talk. "
                "Here: /code, then /plan."
            )
            if console:
                console.print(f"[dim]{msg}[/dim]")
            else:
                print(msg)
            return True
    if cmd is None:
        _p = user_input[1:].split(maxsplit=1) if user_input.startswith("/") else []
        token = _p[0] if _p else ""
        _, name = _split_ns(token)
        console = context.get("console")

        def _say(msg: str) -> None:
            if console:
                console.print(f"[dim]{msg}[/dim]")
            else:
                print(msg)

        # Bare "/" (or "/   ") — nothing to route. Nudge instead of crashing or
        # spending an agent turn on a lone slash.
        if not token:
            _say("type a command after / (type / for the list)")
            return True

        # Parked role command (R4): the name exists but its owning role isn't
        # active — nudge instead of silently sending it to the model.
        gated = [c for c in _INDEX.get(name, []) if c.role and c.role != active_role]
        if gated:
            owner = gated[0].role
            _say(f"/{token} belongs to role '{owner}' — /role {owner} to activate it")
            return True
        # The name exists only in the other REPL surface.
        if any(c.role is None and repl not in c.repls for c in _INDEX.get(name, [])):
            _say(f"/{token} is not available in the {repl} REPL")
            return True
        # Truly unknown. If it LOOKS like a command typo (a /word token, no path
        # separators), reject it with a suggestion — a misspelled command must
        # NEVER fall through to an agent turn (it could spend tokens or edit files).
        import re

        if re.fullmatch(r"[A-Za-z][\w-]*", name or ""):
            suggestion = _closest_command(name, repl, active_role)
            if console:
                hint = f" — did you mean [cyan]/{suggestion}[/cyan]?" if suggestion else ""
                console.print(
                    f"[yellow]unknown command[/yellow] /{token}{hint}"
                    "  [dim](type / for the list)[/dim]"
                )
            else:
                tail = f" — did you mean /{suggestion}?" if suggestion else ""
                print(f"unknown command /{token}{tail}")
            return True
        return False

    # Capability gate (merge seam #1). A command may declare a required
    # capability; refuse it centrally unless the session is elevated. One
    # chokepoint so every gated command — built-in, role-owned, or plugin —
    # is enforced identically, and an unprivileged gated command can never reach
    # its handler (which could spend tokens, run a daemon, or edit files).
    if cmd.capability and not session_is_elevated(context):
        console = context.get("console")
        msg = (
            f"/{cmd.name} needs the '{cmd.capability}' capability — "
            "run /admin unlock to elevate this session"
        )
        if console:
            console.print(f"[yellow]{msg}[/yellow]")
        else:
            print(msg)
        return True

    if cmd.human_only:
        from xlii.human_gate import is_foreground_console

        console = context.get("console")
        if not is_foreground_console(console):
            msg = f"/{cmd.name} requires a foreground human at the desk"
            printer = getattr(console, "print", None)
            if callable(printer):
                printer(f"[dim]{msg}[/dim]")
            else:
                print(msg)
            return True

    try:
        handled = cmd.handler(user_input, context)
        return bool(handled)
    except Exception as e:
        # Show the FULL traceback. A one-line message here masked several
        # session-killing NameErrors — silent failure is worse than noise.
        import traceback
        tb = traceback.format_exc()
        console = context.get("console")
        if console:
            # Escape both: an exception message containing markup-shaped text
            # (e.g. a literal "[/path]") must not crash the crash handler.
            from rich.markup import escape

            console.print(f"[red]command /{cmd.name} crashed: {escape(str(e))}[/red]")
            console.print(f"[dim]{escape(tb)}[/dim]")
        else:
            print(f"command /{cmd.name} crashed:\n{tb}")
        return True  # keep the REPL alive after surfacing the error


# ------------------------------------------------------------------
# Pluggability hooks (making the registry itself extensible)
# ------------------------------------------------------------------

def register_commands_from_callable(loader: Callable[[], list[REPLCommand]]) -> None:
    """Allow external code (plugins, per-project files, etc.) to contribute commands.

    Example usage later:
        def my_commands():
            return [REPLCommand(name="foo", handler=..., ...)]
        register_commands_from_callable(my_commands)
    """
    for cmd in loader():
        register_repl_command(cmd)


def load_project_commands(xli_dir: Path) -> None:
    """
    Load user-provided REPL commands for this project.

    This is the primary extensibility mechanism for the "user composes the system"
    philosophy of xlii.

    See `examples/project-commands.py` in the repository for a complete, working
    example with several useful patterns.

    Supported locations:

    1. <xli_dir>/commands.py
       Must define `def get_commands() -> list[REPLCommand]`

    2. <xli_dir>/commands/ (directory, supports subdirectories)
       Recursively loads all .py files (except those starting with _ or in __pycache__).
       Any file defining `get_commands()` contributes commands.

    Commands with `source="project"` appear in their own "PROJECT COMMANDS"
    section in `/help`.
    """
    # First unload any previously loaded project commands (important for reload)
    _unload_project_commands()

    loaded_any = False

    # 1. Single file: .xlii/commands.py
    single = xli_dir / "commands.py"
    if single.exists():
        loaded_any |= _load_commands_module(single, source="project")

    # 2. Directory tree: .xlii/commands/** (richer support)
    commands_dir = xli_dir / "commands"
    if commands_dir.is_dir():
        for pyfile in _walk_project_command_files(commands_dir):
            loaded_any |= _load_commands_module(pyfile, source="project")

    # 3. Declarative task aliases (.xlii/aliases.toml) — task-args P2. They register
    #    as source="project", so this ride the same load/unload/reload lifecycle.
    try:
        from xlii.repl_cmds.alias import load_task_aliases
        loaded_any |= load_task_aliases(xli_dir)
    except Exception:
        pass  # a broken alias file must never break project-command loading

    if loaded_any:
        pass  # quiet by default


def _unload_project_commands() -> None:
    """Remove all previously registered project commands (used by reload)."""
    # Copy because we're modifying while iterating
    for name in list(_PROJECT_COMMAND_NAMES):
        unregister_repl_command(name)
    _PROJECT_COMMAND_NAMES.clear()


def _walk_project_command_files(root: Path):
    """Recursively yield .py files under a commands directory, skipping junk."""
    for path in sorted(root.rglob("*.py")):
        if path.name.startswith("_"):
            continue
        if "__pycache__" in path.parts:
            continue
        yield path


def reload_project_commands(xli_dir: Path) -> int:
    """
    Reload all project commands for the current REPL.

    Returns the number of commands loaded after reload.
    """
    load_project_commands(xli_dir)
    # Count how many project commands are currently registered
    return sum(1 for c in _REPL_COMMANDS if c.source == "project")


def _load_commands_module(path: Path, source: str = "project") -> bool:
    """Safely import a module and register any commands it exposes."""
    try:
        import importlib.util
        module_name = f"xlii_project_cmd_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            return False

        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        if hasattr(mod, "get_commands"):
            for cmd in mod.get_commands():
                if isinstance(cmd, REPLCommand):
                    # Force nice source label
                    if cmd.source == "builtin":
                        cmd.source = source
                    register_repl_command(cmd)
            return True
    except Exception as e:
        import traceback

        from xlii.ui import console

        tb = traceback.format_exc().strip()
        # Show a short, useful error
        msg = (
            f"[yellow]Warning:[/yellow] failed to load commands from {path}\n"
            f"  {type(e).__name__}: {e}\n"
            f"  (use /commands errors to see full traceback)"
        )

        # Store detailed error for later inspection
        _COMMAND_LOAD_ERRORS[path] = tb

        console.print(msg)
    return False


from xlii.commands_help import get_repl_help, render_repl_help  # noqa: E402, F401 — re-export façade
