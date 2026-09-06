"""Code-level classification of shell commands.

The model declares an `intent` on every bash call, but the model is not a
security boundary. This module independently classifies the command text and
the executor escalates whenever the classification is stronger than the
declared intent. Workers ("read-only") are enforced here, not by politeness.

Severity order (weakest → strongest):
    read-only < modifies-project < network < modifies-system
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Optional

READ_ONLY = "read-only"
MODIFIES_PROJECT = "modifies-project"
NETWORK = "network"
MODIFIES_SYSTEM = "modifies-system"

SEVERITY = {READ_ONLY: 0, MODIFIES_PROJECT: 1, NETWORK: 2, MODIFIES_SYSTEM: 3}

# Binaries that escalate straight to modifies-system.
SYSTEM_BINARIES = {
    "sudo", "su", "doas", "pkexec",
    "apt", "apt-get", "dpkg", "dnf", "yum", "pacman", "zypper", "brew",
    "snap", "flatpak",
    "systemctl", "service", "init", "telinit", "reboot", "shutdown", "halt",
    "poweroff", "mount", "umount", "mkfs", "fdisk", "parted", "dd",
    "useradd", "userdel", "usermod", "groupadd", "passwd", "chpasswd",
    "crontab", "at", "modprobe", "insmod", "rmmod", "sysctl",
    "iptables", "nft", "ufw", "firewall-cmd",
    "kill", "killall", "pkill",
}

# Binaries whose primary job is the network.
NETWORK_BINARIES = {
    "curl", "wget", "ssh", "scp", "sftp", "rsync", "ftp", "nc", "ncat",
    "netcat", "telnet", "ping", "dig", "nslookup", "host", "nmap",
}

# Package managers: network when installing, system-ish either way — treat as
# network (their install dirs are usually user-local in this workflow).
PKG_MANAGERS = {"pip", "pip3", "npm", "yarn", "pnpm", "cargo", "gem", "go", "uv", "poetry", "composer"}
PKG_NET_VERBS = {"install", "uninstall", "add", "remove", "update", "upgrade", "publish", "download", "get"}

# Binaries that mutate the filesystem.
MUTATING_BINARIES = {
    "rm", "mv", "cp", "mkdir", "rmdir", "touch", "ln", "chmod", "chown",
    "chgrp", "tee", "truncate", "shred", "patch", "rename", "unzip", "tar",
    "install",
}

# Interpreters can do anything; classify as project mutation so a declared
# read-only `python -c "..."` mismatches and gets surfaced.
INTERPRETERS = {"python", "python3", "sh", "bash", "zsh", "dash", "fish",
                "node", "deno", "bun", "perl", "ruby", "php", "lua"}

# Wrappers whose first non-flag operand is the real command. Peeled so
# `env curl`, `nice wget`, `timeout 5 sudo` classify as the inner binary.
_WRAPPERS = {"env", "command", "exec", "nice", "nohup", "time", "stdbuf",
             "ionice", "catchsegv", "eatmydata", "timeout", "busybox"}
_SOURCE_BUILTINS = {".", "source"}

# Interpreter flags whose next argument is a script body to classify recursively.
_INTERPRETER_SCRIPT_FLAGS = {
    "python": {"-c"},
    "python3": {"-c"},
    "bash": {"-c"},
    "sh": {"-c"},
    "zsh": {"-c"},
    "dash": {"-c"},
    "fish": {"-c"},
    "node": {"-e", "-p"},
    "deno": {"-e", "eval"},
    "bun": {"-e", "-p"},
    "perl": {"-e", "-E"},
    "ruby": {"-e"},
    "php": {"-r"},
    "lua": {"-e"},
}

# Build drivers run arbitrary recipe/rule shells — same "can do anything"
# posture as interpreters. Without this a read-only worker could run `make`.
BUILD_TOOLS = {"make", "gmake", "ninja", "cmake"}

# find's exec-family flags carry a command of their own; the body is classified
# recursively so `find -exec grep …` stays read-only while `find -exec rm …`
# escalates. `-delete` mutates outright.
_FIND_EXEC_FLAGS = {"-exec", "-execdir", "-ok", "-okdir"}

# xargs flag shapes, needed to locate the wrapped command (`xargs -n 5 rm`
# must classify `rm`, not `5`). Unknown flags fail toward mutation.
_XARGS_ARG_FLAGS = {"-a", "-d", "-E", "-e", "-I", "-i", "-L", "-l", "-n", "-P", "-s"}
_XARGS_LONG_ARG_FLAGS = {"--arg-file", "--delimiter", "--eof", "--max-lines",
                         "--max-args", "--max-chars", "--max-procs",
                         "--process-slot-var", "--replace"}
_XARGS_BOOL_FLAGS = {"-0", "-o", "-p", "-r", "-t", "-x",
                     "--exit", "--interactive", "--no-run-if-empty", "--null",
                     "--open-tty", "--show-limits", "--verbose"}

GIT_NETWORK_VERBS = {"push", "pull", "fetch", "clone", "remote", "submodule"}
GIT_READONLY_VERBS = {"status", "log", "diff", "show", "blame", "grep",
                      "ls-files", "branch", "tag", "describe", "rev-parse",
                      "shortlog", "reflog", "remote"}  # bare listing forms

_REDIRECT_RE = re.compile(r"(?:&>\s*\S|(?<![\d&<>])>{1,2}\s*\S)")

# Interpreter -c/-e bodies can reach the network without invoking curl/wget.
_NETWORK_SCRIPT_RE = re.compile(
    r"\b(urllib|urllib3|requests|httpx|aiohttp|http\.client|socket)\b",
    re.IGNORECASE,
)
_NETWORK_SUBPROC_RE = re.compile(
    r"\b(subprocess|os\.system|os\.popen)\b.*\b(curl|wget|nc|ncat|netcat|ssh|scp)\b",
    re.IGNORECASE | re.DOTALL,
)


def _find_process_substitution_bodies(cmd: str) -> tuple[list[str], bool]:
    """Return process-substitution bodies and whether parsing was complete."""
    bodies: list[str] = []
    i = 0
    n = len(cmd)
    in_single = in_double = in_backtick = escaped = False
    while i < n:
        ch = cmd[i]
        if escaped:
            escaped = False
            i += 1
            continue
        if ch == "\\" and not in_single:
            escaped = True
            i += 1
            continue
        if ch == "'" and not in_double and not in_backtick:
            in_single = not in_single
            i += 1
            continue
        if ch == '"' and not in_single and not in_backtick:
            in_double = not in_double
            i += 1
            continue
        if ch == "`" and not in_single and not in_double:
            in_backtick = not in_backtick
            i += 1
            continue
        # Process substitution supports both input (<(...)) and output (>(...)).
        if not (in_single or in_double or in_backtick) and ch in "<>" and i + 1 < n and cmd[i + 1] == "(":
            start = i + 2
            j = start
            depth = 1
            sub_single = sub_double = sub_backtick = sub_escaped = False
            while j < n:
                c = cmd[j]
                if sub_escaped:
                    sub_escaped = False
                    j += 1
                    continue
                if c == "\\" and not sub_single:
                    sub_escaped = True
                    j += 1
                    continue
                if c == "'" and not sub_double and not sub_backtick:
                    sub_single = not sub_single
                    j += 1
                    continue
                if c == '"' and not sub_single and not sub_backtick:
                    sub_double = not sub_double
                    j += 1
                    continue
                if c == "`" and not sub_single and not sub_double:
                    sub_backtick = not sub_backtick
                    j += 1
                    continue
                if not (sub_single or sub_double or sub_backtick):
                    if c == "(":
                        depth += 1
                    elif c == ")":
                        depth -= 1
                        if depth == 0:
                            break
                j += 1
            if depth != 0:
                return [], False
            bodies.append(cmd[start:j])
            i = j + 1
            continue
        i += 1
    return bodies, True


def _strip_wrapping_group_parens(segment: str) -> str:
    """Strip full-command grouping parens: ( cmd ) -> cmd."""
    s = segment.strip()
    while s.startswith("(") and s.endswith(")"):
        depth = 0
        in_single = in_double = in_backtick = escaped = False
        wraps = True
        for idx, ch in enumerate(s):
            if escaped:
                escaped = False
                continue
            if ch == "\\" and not in_single:
                escaped = True
                continue
            if ch == "'" and not in_double and not in_backtick:
                in_single = not in_single
                continue
            if ch == '"' and not in_single and not in_backtick:
                in_double = not in_double
                continue
            if ch == "`" and not in_single and not in_double:
                in_backtick = not in_backtick
                continue
            if in_single or in_double or in_backtick:
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0 and idx != len(s) - 1:
                    wraps = False
                    break
                if depth < 0:
                    wraps = False
                    break
        if not wraps or depth != 0:
            break
        s = s[1:-1].strip()
    return s


def _severity_max(a: str, b: str) -> str:
    return a if SEVERITY[a] >= SEVERITY[b] else b


def _split_segments(cmd: str) -> list[str]:
    """Split shell command into top-level segments without splitting inside quotes."""
    segments: list[str] = []
    buf: list[str] = []
    in_single = False
    in_double = False
    escaped = False
    i = 0
    n = len(cmd)

    def flush() -> None:
        if buf:
            segments.append("".join(buf))
            buf.clear()

    while i < n:
        ch = cmd[i]

        if escaped:
            buf.append(ch)
            escaped = False
            i += 1
            continue
        if ch == "\\" and not in_single:
            buf.append(ch)
            escaped = True
            i += 1
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            buf.append(ch)
            i += 1
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            buf.append(ch)
            i += 1
            continue

        if not in_single and not in_double:
            if ch in ";\n":
                flush()
                i += 1
                continue
            if ch == "|":
                flush()
                i += 2 if i + 1 < n and cmd[i + 1] == "|" else 1
                continue
            if ch == "&":
                if i + 1 < n and cmd[i + 1] == "&":
                    flush()
                    i += 2
                    continue
                prev = cmd[i - 1] if i > 0 else ""
                nxt = cmd[i + 1] if i + 1 < n else ""
                if prev not in "<>&" and nxt not in ">&":
                    flush()
                    i += 1
                    continue

        buf.append(ch)
        i += 1

    flush()
    return segments


def _outside_root(token: str, project_root: Optional[Path]) -> bool:
    """True if a path-looking token points outside the project root.

    Absolute and home paths fail closed when the root is unknown — inbox
    tests and other callers that omit a root must not treat ``rm /etc`` as
    an in-tree edit.
    """
    if not (token.startswith("~") or token.startswith("/")):
        return False
    if project_root is None:
        return True
    try:
        p = Path(token).expanduser().resolve()
    except (OSError, RuntimeError):
        return True
    return not (p == project_root or project_root in p.parents)


def _sed_in_place(rest: list[str]) -> bool:
    """True if a sed invocation edits files in place (a mutation).

    GNU sed's -i takes an OPTIONAL backup suffix glued to the flag, so -i,
    -i.bak and -iBACKUP are all in-place — including -in (suffix 'n'), which an
    earlier `not startswith('-in')` guard wrongly let through as read-only. The
    long form is --in-place[=SUFFIX]. Read-only flags (-n/-e/-E/-r/-s/-z) do not
    start with -i, so a simple prefix test is both correct and safe.
    """
    return any(t.startswith("-i") or t.startswith("--in-place") for t in rest)


def _find_class(rest: list[str], project_root: Optional[Path]) -> str:
    """Classify a `find` invocation: `-delete` mutates; each exec-family body
    is a command in its own right and classifies recursively."""
    cls = MODIFIES_PROJECT if "-delete" in rest else READ_ONLY
    i = 0
    while i < len(rest):
        if rest[i] in _FIND_EXEC_FLAGS:
            j = i + 1
            body: list[str] = []
            while j < len(rest) and rest[j] not in (";", "+"):
                if rest[j] != "{}":
                    body.append(rest[j])
                j += 1
            if body:
                cls = _severity_max(cls, _classify_segment(shlex.join(body), project_root))
            i = j
        i += 1
    return cls


def _xargs_class(rest: list[str], project_root: Optional[Path]) -> str:
    """Classify an `xargs` invocation by the command it wraps. Flags are walked
    so arg-taking ones don't shift the command start; an unrecognized flag means
    the command can't be located reliably — fail toward mutation."""
    i, n = 0, len(rest)
    while i < n:
        t = rest[i]
        if t == "--":
            i += 1
            break
        if not t.startswith("-"):
            break
        if t in _XARGS_ARG_FLAGS or t in _XARGS_LONG_ARG_FLAGS:
            i += 2
            continue
        if t in _XARGS_BOOL_FLAGS or (t.startswith("--") and "=" in t):
            i += 1
            continue
        if not t.startswith("--") and len(t) > 2 and t[:2] in _XARGS_ARG_FLAGS:
            i += 1  # glued short form: -n5, -I{}
            continue
        return MODIFIES_PROJECT
    body = [t for t in rest[i:] if t != "{}"]
    if not body:
        return READ_ONLY  # no wrapped command → xargs defaults to echo
    return _classify_segment(shlex.join(body), project_root)


_REDIRECT_TARGET_RE = re.compile(r"(?:&>|>>?)\s*(\S+)")

# env(1) flags that take a following argument.
_ENV_ARG_FLAGS = {"-u", "--unset", "-C", "--chdir"}
_ENV_BOOL_FLAGS = {"-i", "--ignore-environment", "-0", "--null", "-v", "--debug",
                   "--list-signal-handling"}
# timeout(1) / nice(1) / command(1) arg-taking flags.
_TIMEOUT_ARG_FLAGS = {"-k", "--kill-after", "-s", "--signal"}
_TIMEOUT_BOOL_FLAGS = {"-v", "--verbose", "--preserve-status", "--foreground"}
_NICE_ARG_FLAGS = {"-n", "--adjustment"}
_COMMAND_BOOL_FLAGS = {"-p", "-v", "-V"}


def _redirect_targets(segment: str) -> list[str]:
    return [m.group(1) for m in _REDIRECT_TARGET_RE.finditer(segment)]


def _skip_flags(rest: list[str], *, arg_flags: set[str], bool_flags: set[str],
                eq_prefixes: tuple[str, ...] = ()) -> tuple[list[str] | None, bool]:
    """Walk leading flags. Returns (remaining, ok). ok is False when an
    unrecognized flag means we cannot locate the wrapped command."""
    i, n = 0, len(rest)
    while i < n:
        t = rest[i]
        if t == "--":
            return rest[i + 1:], True
        if not t.startswith("-") or t == "-":
            return rest[i:], True
        if t in bool_flags:
            i += 1
            continue
        if t in arg_flags:
            i += 2
            continue
        if any(t.startswith(p) and "=" in t for p in eq_prefixes):
            i += 1
            continue
        return None, False
    return rest[i:], True


def _env_class(rest: list[str], project_root: Optional[Path]) -> str:
    """Classify `env [opts] [NAME=VAL...] [command]`. `-S` is opaque — fail closed."""
    i, n = 0, len(rest)
    while i < n:
        t = rest[i]
        if t in ("--", "-"):
            i += 1
            break
        if t.startswith("-S") or t.startswith("--split-string"):
            return MODIFIES_PROJECT
        if t in _ENV_BOOL_FLAGS:
            i += 1
            continue
        if t in _ENV_ARG_FLAGS:
            i += 2
            continue
        if t.startswith("--unset=") or t.startswith("--chdir=") or t.startswith("--block-signal=") \
                or t.startswith("--default-signal=") or t.startswith("--ignore-signal="):
            i += 1
            continue
        if t.startswith("-") and not re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t):
            return MODIFIES_PROJECT
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t):
            i += 1
            continue
        break
    body = rest[i:]
    if not body:
        return READ_ONLY
    return _classify_segment(shlex.join(body), project_root)


def _wrapper_class(binary: str, rest: list[str], project_root: Optional[Path]) -> str:
    """Peel one wrapper and classify the inner command."""
    if binary == "env":
        return _env_class(rest, project_root)
    if binary == "busybox":
        if not rest:
            return READ_ONLY
        return _classify_segment(shlex.join(rest), project_root)
    if binary == "timeout":
        remaining, ok = _skip_flags(
            rest, arg_flags=_TIMEOUT_ARG_FLAGS, bool_flags=_TIMEOUT_BOOL_FLAGS,
            eq_prefixes=("--kill-after=", "--signal="),
        )
        if not ok or remaining is None:
            return MODIFIES_PROJECT
        # next token is DURATION
        if remaining:
            remaining = remaining[1:]
        if not remaining:
            return READ_ONLY
        return _classify_segment(shlex.join(remaining), project_root)
    if binary == "nice":
        remaining, ok = _skip_flags(
            rest, arg_flags=_NICE_ARG_FLAGS, bool_flags=set(),
            eq_prefixes=("--adjustment=",),
        )
        if not ok or remaining is None:
            return MODIFIES_PROJECT
        if not remaining:
            return READ_ONLY
        return _classify_segment(shlex.join(remaining), project_root)
    if binary == "command":
        remaining, ok = _skip_flags(
            rest, arg_flags=set(), bool_flags=_COMMAND_BOOL_FLAGS,
        )
        if not ok or remaining is None:
            return MODIFIES_PROJECT
        if not remaining:
            return READ_ONLY
        return _classify_segment(shlex.join(remaining), project_root)
    # exec / nohup / time / stdbuf / ionice / catchsegv / eatmydata:
    # skip leading flags, classify the rest. Unknown flags fail toward mutation.
    remaining, ok = _skip_flags(
        rest, arg_flags=set(), bool_flags=set(),
    )
    if not ok or remaining is None:
        # stdbuf/ionice take required options before the command (`stdbuf -oL cmd`).
        # Treat a leading-flag token as skippable only when a later non-flag exists.
        i = 0
        while i < len(rest) and rest[i].startswith("-") and rest[i] != "--":
            i += 1
        if i < len(rest) and rest[i] == "--":
            i += 1
        remaining = rest[i:]
        if not remaining:
            return MODIFIES_PROJECT
    if not remaining:
        return READ_ONLY
    return _classify_segment(shlex.join(remaining), project_root)


def _interpreter_script_body(binary: str, rest: list[str]) -> Optional[str]:
    """Return the `-c`/`-e` script body for an interpreter invocation, if any."""
    flags = _INTERPRETER_SCRIPT_FLAGS.get(binary, {"-c", "-e"})
    i = 0
    while i < len(rest):
        t = rest[i]
        if t in flags and i + 1 < len(rest):
            return rest[i + 1]
        i += 1
    return None


def _script_uses_network(script: str) -> bool:
    """True when an inline interpreter script performs network I/O."""
    if _NETWORK_SCRIPT_RE.search(script):
        return True
    return bool(_NETWORK_SUBPROC_RE.search(script))


def _classify_segment(segment: str, project_root: Optional[Path]) -> str:
    segment = _strip_wrapping_group_parens(segment)
    if not segment:
        return READ_ONLY
    try:
        tokens = shlex.split(segment)
    except ValueError:
        # Unparseable quoting — be conservative.
        return MODIFIES_PROJECT
    if not tokens:
        return READ_ONLY

    # Skip leading env assignments (FOO=bar cmd ...)
    while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
        tokens = tokens[1:]
    if not tokens:
        return READ_ONLY

    binary = Path(tokens[0]).name or tokens[0]
    rest = tokens[1:]
    cls = READ_ONLY

    if binary == "eval":
        return classify_command(" ".join(rest), project_root) if rest else READ_ONLY
    if binary in _SOURCE_BUILTINS:
        return MODIFIES_PROJECT
    if binary in _WRAPPERS:
        return _wrapper_class(binary, rest, project_root)

    if binary in SYSTEM_BINARIES:
        return MODIFIES_SYSTEM
    if binary in NETWORK_BINARIES:
        cls = _severity_max(cls, NETWORK)
    if binary in PKG_MANAGERS and any(t in PKG_NET_VERBS for t in rest):
        cls = _severity_max(cls, NETWORK)
    if binary in INTERPRETERS or binary in BUILD_TOOLS:
        cls = _severity_max(cls, MODIFIES_PROJECT)
        if binary in INTERPRETERS:
            body = _interpreter_script_body(binary, rest)
            if body:
                cls = _severity_max(cls, classify_command(body, project_root))
                if _script_uses_network(body):
                    cls = _severity_max(cls, NETWORK)
    if binary == "find":
        cls = _severity_max(cls, _find_class(rest, project_root))
    if binary == "xargs":
        cls = _severity_max(cls, _xargs_class(rest, project_root))
    if binary == "git":
        verbs = [t for t in rest if not t.startswith("-")]
        verb = verbs[0] if verbs else ""
        if verb in GIT_NETWORK_VERBS:
            cls = _severity_max(cls, NETWORK)
        elif verb and verb not in GIT_READONLY_VERBS:
            cls = _severity_max(cls, MODIFIES_PROJECT)
    if binary == "sed" and _sed_in_place(rest):
        cls = _severity_max(cls, MODIFIES_PROJECT)
    if binary in MUTATING_BINARIES:
        cls = _severity_max(cls, MODIFIES_PROJECT)
        # rm/mv/cp/chmod/tee touching paths outside the project → system-level
        if binary in {"rm", "mv", "cp", "chmod", "chown", "ln", "shred", "truncate", "tee", "install"}:
            if any(_outside_root(t, project_root) for t in rest if not t.startswith("-")):
                cls = MODIFIES_SYSTEM

    # Output redirection writes a file; an absolute/home target outside the
    # project is system-level (echo secret > /etc/cron.d/x).
    if _REDIRECT_RE.search(segment):
        cls = _severity_max(cls, MODIFIES_PROJECT)
        for target in _redirect_targets(segment):
            if _outside_root(target, project_root):
                cls = MODIFIES_SYSTEM
                break

    return cls


def classify_command(cmd: str, project_root: Optional[Path] = None) -> str:
    """Return the strongest intent class found anywhere in the command line."""
    root = project_root.resolve() if project_root is not None else None
    cls = READ_ONLY
    # Command/process substitution can hide anything — pull out bodies and
    # classify them as commands in their own right.
    for inner in re.findall(r"\$\(([^()]*)\)|`([^`]*)`", cmd):
        for body in inner:
            if body.strip():
                cls = _severity_max(cls, classify_command(body, root))
    process_bodies, parsed_all_process_subs = _find_process_substitution_bodies(cmd)
    if not parsed_all_process_subs:
        cls = _severity_max(cls, MODIFIES_PROJECT)
    else:
        for body in process_bodies:
            if body.strip():
                cls = _severity_max(cls, classify_command(body, root))
    for segment in _split_segments(cmd):
        cls = _severity_max(cls, _classify_segment(segment, root))
    return cls
