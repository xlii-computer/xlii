"""Curated registry of interactive / full-screen terminal programs.

xlii's "substrate" look comes from CAPTURING a command's output into clean blocks
(pipes, not a PTY). That is fundamentally incompatible with a full-screen ncurses
program (mc, vim, htop, less, …): such a program needs a real controlling
terminal to address the cursor and read keystrokes live. Two safeguards keep that
from biting a user who doesn't know the difference:

1. The captured runner closes stdin (DEVNULL, see xlii.tui.shell.capture), so a
   mis-captured interactive program hits EOF and exits instead of *hanging the
   session* — turning the worst case (a silent freeze) into a clean quick exit.
2. This registry lets the REPL recognize a known interactive program by name and
   route it to the raw inherited-TTY path (the `!!` handover) automatically — so
   the common tools "just work" without the user knowing `!!` exists.

The baked-in DEFAULTS are always active; a user file (one program basename per
line, at `~/.config/xlii/interactive.txt`) ADDS to them and grows as the user
works (the reactive "looks interactive — add it?" hint points at
`/interactive add`).
"""

from __future__ import annotations

import shlex
from collections.abc import Iterable
from typing import Optional

from xlii.config import GLOBAL_CONFIG_DIR

USER_LIST_FILE = GLOBAL_CONFIG_DIR / "interactive.txt"

# Command wrappers to peel so we inspect the REAL program: `sudo vim`, `env X=1
# htop`, `command less`, `nice -n10 top` all launch a full-screen program.
_WRAPPERS = frozenset({
    "sudo", "doas", "env", "command", "nice", "nohup", "stdbuf", "time", "exec",
})

# Wrapper options that consume the FOLLOWING token as their value (separated
# form, e.g. `nice -n 10 top`). The joined forms (`-n10`, `--adjustment=10`)
# carry their value in the same token and are handled by the generic flag skip.
# We whitelist rather than guess, so we never skip past the real program.
_WRAPPER_OPT_TAKES_ARG = {
    "sudo": {"-u", "--user", "-g", "--group", "-h", "--host", "-p", "--prompt",
             "-C", "-r", "--role", "-t", "--type", "-U", "--other-user"},
    "doas": {"-u", "-C"},
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
    "nice": {"-n", "--adjustment"},
    "stdbuf": {"-i", "--input", "-o", "--output", "-e", "--error"},
    "time": {"-o", "--output", "-f", "--format"},
    "exec": {"-a"},
}

# Baked-in defaults: programs that take over the screen / raw keyboard regardless
# of their arguments, so auto-routing them to a real terminal is always right.
DEFAULTS: frozenset = frozenset({
    # file managers
    "mc", "ranger", "nnn", "lf", "vifm", "yazi", "broot",
    # editors
    "vi", "vim", "nvim", "nano", "emacs", "helix", "hx", "micro", "kak", "joe",
    # pagers
    "less", "most", "moar", "man",
    # monitors
    "top", "htop", "btop", "btm", "glances", "gtop", "bpytop", "nvtop",
    "iotop", "iftop", "s-tui",
    # multiplexers / TUIs
    "tmux", "screen", "zellij", "lazygit", "lazydocker", "gitui", "tig", "k9s",
    "ncdu", "dialog", "whiptail", "fzf", "cmus", "ncmpcpp", "newsboat",
    "mutt", "neomutt", "weechat", "irssi", "aerc", "calcurse", "vit",
    # interactive clients whose batch form is an explicit flag (their dominant
    # use is the interactive prompt: `psql db`, `sqlite3 f.db`, `gdb ./prog`)
    "psql", "mysql", "sqlite3", "redis-cli", "mongo", "mongosh", "gdb", "lldb",
    "ssh", "telnet", "ftp", "sftp", "nmtui", "bluetoothctl", "visudo",
})

# REPLs that are interactive ONLY when invoked bare (no script/positional arg) —
# `python` is a REPL but `python script.py` is a batch run that should capture.
_REPL_WHEN_BARE: frozenset = frozenset({
    "python", "python3", "ipython", "node", "irb", "ghci", "bpython",
})


def _read_user_list() -> set[str]:
    """Program basenames added by the user (one per line; `#` comments allowed)."""
    try:
        text = USER_LIST_FILE.read_text()
    except OSError:
        return set()
    out: set[str] = set()
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.add(s)
    return out


def interactive_programs() -> set[str]:
    """The active set: baked-in DEFAULTS ∪ the user's additions."""
    return set(DEFAULTS) | _read_user_list()


def add_program(name: str) -> bool:
    """Append `name` to the user list. Returns True if newly added; False if it's
    blank or already covered (by DEFAULTS or the user file)."""
    name = name.strip()
    if not name or name in interactive_programs():
        return False
    USER_LIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    with USER_LIST_FILE.open("a", encoding="utf-8") as f:
        f.write(name + "\n")
    return True


def remove_program(name: str) -> bool:
    """Remove `name` from the USER list (a baked-in DEFAULT can't be removed).
    Returns True if something was removed."""
    name = name.strip()
    user = _read_user_list()
    if name not in user:
        return False
    user.discard(name)
    USER_LIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    USER_LIST_FILE.write_text("".join(f"{n}\n" for n in sorted(user)), encoding="utf-8")
    return True


# Shell metacharacters that make a line a pipeline / redirection rather than a
# single program launch. Those capture fine (a lone interactive program never
# needs them to run), so we don't try to hand them the terminal.
_SHELL_OPS = ("|", "&", ";", ">", "<", "`", "$(", "&&", "||")


def _parse_command(cmd: str) -> tuple[Optional[str], list[str]]:
    """`(program_basename, args_after_program)` for a command line, peeling
    wrappers (sudo/env/nice/…) and leading `VAR=val` assignments. Returns
    `(None, [])` when the line is a pipeline/redirection (a shell operator is
    present) or can't be parsed — those are left to the capture path."""
    s = cmd.strip()
    if not s or any(op in s for op in _SHELL_OPS):
        return None, []
    try:
        toks = shlex.split(s)
    except ValueError:
        return None, []
    i = 0
    while i < len(toks):
        t = toks[i]
        # leading `VAR=val` env assignment before the program
        if "=" in t and not t.startswith("-") and "/" not in t.split("=", 1)[0]:
            i += 1
            continue
        base = t.rsplit("/", 1)[-1]
        if base in _WRAPPERS:
            takes_arg = _WRAPPER_OPT_TAKES_ARG.get(base, frozenset())
            i += 1
            # skip the wrapper's own flags + (for env) VAR=val pairs, plus the
            # separate-token value of any flag known to take one (`nice -n 10`)
            while i < len(toks) and (toks[i].startswith("-") or "=" in toks[i]):
                opt = toks[i]
                i += 1
                if opt in takes_arg and i < len(toks):
                    i += 1
            continue
        return base, toks[i + 1:]
    return None, []


def program_token(cmd: str) -> Optional[str]:
    """The real program basename a command line launches (wrappers peeled)."""
    return _parse_command(cmd)[0]


def is_interactive(cmd: str, *, extra: Iterable[str] = ()) -> bool:
    """True when `cmd` launches a full-screen / interactive program, so it should
    get the raw-TTY handover (the `!!` path) instead of capture. Always-full-screen
    programs (DEFAULTS + user additions + `extra`) match regardless of args; a
    `_REPL_WHEN_BARE` program matches only when invoked with no positional argument
    (so `python` → REPL, but `python script.py` → a batch run that captures)."""
    tok, args = _parse_command(cmd)
    if tok is None:
        return False
    if tok in interactive_programs() or tok in set(extra):
        return True
    if tok in _REPL_WHEN_BARE and not any(not a.startswith("-") for a in args):
        return True
    return False


# Privilege tools whose OWN password prompt needs a real terminal. Unlike the
# full-screen programs above, the inner command (`sudo apt …`) isn't interactive —
# but sudo still must read a password from a TTY, so on the captured runner (stdin
# = DEVNULL) it dies with "sudo: a terminal is required to read the password". So
# these route to the inherited-TTY (suspend) path too.
_PRIVILEGE_TTY_CMDS = frozenset({"sudo", "doas", "su", "pkexec"})


def needs_password_tty(cmd: str) -> bool:
    """True when the line invokes sudo/doas/su in a command position — they may
    prompt for a password and so need the inherited-TTY path, not the captured
    (DEVNULL-stdin) runner. Detected across shell operators (`sudo a && sudo b`)."""
    try:
        toks = shlex.split(cmd)
    except ValueError:
        toks = cmd.split()
    at_cmd_pos = True
    for t in toks:
        if t in ("&&", "||", "|", ";", "&"):
            at_cmd_pos = True
            continue
        if at_cmd_pos:
            if t.rsplit("/", 1)[-1] in _PRIVILEGE_TTY_CMDS:
                return True
            at_cmd_pos = False
    return False


def installed_defaults() -> list[str]:
    """The DEFAULTS actually present on this machine (for the setup report)."""
    import shutil
    return sorted(p for p in DEFAULTS if shutil.which(p))


# --------------------------------------------------------------------------- #
#  External terminal (Tauri / no-TTY surfaces)
# --------------------------------------------------------------------------- #


def has_graphical_session() -> bool:
    import os

    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def launch_in_external_terminal(
    cwd,
    *,
    run: str = "",
    preferred: str = "",
    popen=None,
    which=None,
) -> tuple[bool, str]:
    """Open a real terminal window in *cwd*, optionally running *run* (``mc``).

    The face/Tauri webview is not a TTY. Full-screen tools stay in a terminal
    emulator — xlii does not become a file manager. ``preferred`` is
    ``cfg.tui_terminal`` (``{cwd}`` / ``{cmd}`` tokens). Returns ``(ok, message)``.
    """
    import os
    import shutil
    import subprocess

    if popen is None:
        popen = subprocess.Popen
    if which is None:
        which = shutil.which

    here = str(cwd or os.getcwd())
    cmd = (run or "").strip()
    pref = (preferred or "").strip()

    if not has_graphical_session():
        return False, "no graphical session (DISPLAY / WAYLAND_DISPLAY) — cannot open a terminal"

    if pref:
        return _launch_preferred(pref, here, cmd, popen=popen, which=which)

    # (exe, cwd-args, args inserted before `run` when a command is given)
    candidates = [
        ("x-terminal-emulator", [], ["-e"] if cmd else []),
        ("gnome-terminal", ["--working-directory", here], ["--"] if cmd else []),
        ("konsole", ["--workdir", here], ["-e"] if cmd else []),
        ("xfce4-terminal", ["--working-directory", here], ["-e"] if cmd else []),
        ("kitty", ["--directory", here], []),
        ("alacritty", ["--working-directory", here], ["-e"] if cmd else []),
        ("wezterm", ["start", "--cwd", here], [] if not cmd else []),
        ("xterm", [], ["-e"] if cmd else []),
    ]
    run_argv = shlex.split(cmd) if cmd else []
    for exe, cwd_args, mid in candidates:
        path = which(exe)
        if not path:
            continue
        argv = [path, *cwd_args, *mid, *run_argv]
        try:
            popen(
                argv,
                cwd=here,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception:
            continue
        label = cmd.split()[0] if cmd else exe
        return True, f"opened {label} in a new terminal"
    return False, (
        "no terminal emulator found "
        "(x-terminal-emulator, gnome-terminal, konsole, kitty, …)"
    )


def _launch_preferred(pref: str, cwd: str, cmd: str, *, popen, which) -> tuple[bool, str]:
    import subprocess

    filled = pref.replace("{cwd}", cwd)
    if "{cmd}" in filled:
        filled = filled.replace("{cmd}", cmd)
        argv = shlex.split(filled)
    else:
        argv = shlex.split(filled)
        if cmd:
            extra = shlex.split(cmd)
            # gnome-style long command strings already end at the emulator;
            # append `--` then the program so `kitty -d {cwd}` + mc works.
            if argv and argv[-1] != "--":
                argv.append("--")
            argv.extend(extra)
    if not argv:
        return False, f"couldn't parse terminal command: {pref}"
    argv[0] = which(argv[0]) or argv[0]
    try:
        popen(
            argv,
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        return False, f"couldn't launch your configured terminal: {pref} ({e})"
    label = cmd.split()[0] if cmd else (shlex.split(pref)[0] if pref else "terminal")
    return True, f"opened {label} in a new terminal"


def handoff_password_tty(
    cmd: str,
    cwd,
    *,
    cfg: object | None = None,
    hook=None,
    have_tty: bool = False,
) -> tuple[bool, str]:
    """Run a sudo/doas/su line where a password may be required.

    Never capture (DEVNULL stdin / no TTY → hang or 'terminal is required').
    Prefer a new emulator window (face). Else the TUI suspend hook. Else
    inherit this process's TTY. Never dump the prompt onto a hidden server tty.
    """
    if callable(hook):
        hook(cmd, str(cwd))
        return True, "handed to the terminal for the password prompt"
    if has_graphical_session():
        pref = str(getattr(cfg, "tui_terminal", "") or "") if cfg is not None else ""
        ok, msg = launch_in_external_terminal(cwd, run=cmd, preferred=pref)
        if ok:
            return True, (
                f"{msg} — type the sudo password there. "
                "I cannot enter it from here."
            )
        return False, msg
    if have_tty:
        import subprocess

        try:
            rc = subprocess.call(cmd, shell=True, cwd=str(cwd))
        except OSError as e:
            return False, f"shell error: {e}"
        return rc == 0, f"exit {rc}"
    return False, (
        "this command needs a password in a real terminal "
        "(sudo / doas / su). I cannot type it. Run it in a terminal yourself."
    )
