"""/send — hand a file (or the last output) to an external program.

``/send gimp last`` opens the newest ``/imagine`` artifact in GIMP; ``/send pluma
reply`` opens the last AI answer in an editor; ``/send eog locker://render.png``
opens a named locker file. The bridge from xlii's files/outputs to the desktop.

Design (see proposals / the plan):

* The **program** is a literal name resolved on ``$PATH`` (``shutil.which``) — no
  alias table. With no program named, the target opens in the OS default app
  (``xdg-open`` / ``open`` / ``start``).
* The **target** is one of: ``focus`` (the last face/TUI Focus pin, if it is a
  real file), ``last`` (that Focus file if any, else newest artifact, else newest
  locker file), ``reply`` / ``shell`` (the last-output buffer, materialized to a
  temp file), or an explicit path / ``scheme://`` address (``file://``,
  ``project://``, ``locker://<name>``).
* **Launch** is detached/non-blocking for GUI apps (so the REPL never freezes);
  full-screen/TTY programs (vim, less…) get the terminal via the existing
  ``!!`` handover hook. ``--wait`` blocks until the program exits and re-attaches
  the file into the locker, so an edit round-trips back into context.

No gate: this is an explicit, user-typed command naming its own program (parity
with ``/upload`` / ``/locker add``), and it launches an argv list — not a shell
string — so there is no injection surface.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Optional

from xlii.commands import REPLCommand, register_repl_command
from xlii.interactive import is_interactive

# Bare target words (as opposed to a program name) — used to decide, for a
# single-token ``/send X``, whether X is the target (OS opener) or the program.
_TARGET_WORDS = frozenset({"last", "focus", "reply", "shell"})


# --------------------------------------------------------------------------- #
#  target → a concrete Path (materializing text outputs when needed)
# --------------------------------------------------------------------------- #

def _looks_like_target(tok: str) -> bool:
    """True when a lone token reads as a *target* rather than a program name:
    a target word, a ``scheme://`` address, a path, or a token with a file
    extension (``x.png``). A bare word like ``gimp`` is a program."""
    return (
        tok in _TARGET_WORDS
        or "://" in tok
        or tok.startswith(("/", "./", "../", "~"))
        or "." in tok.rsplit("/", 1)[-1]
    )


def _focus_file(state: Any) -> Optional[Path]:
    """The last Focus pin, when it resolved to an on-disk file."""
    info = getattr(state, "last_focus", None) or {}
    if not isinstance(info, dict):
        return None
    raw = info.get("path") or ""
    if raw:
        p = Path(raw)
        if p.is_file():
            return p
    return None


def _last_file(state: Any) -> Optional[Path]:
    """Focus pin, else newest artifact, else the most-recently-added locker file."""
    focused = _focus_file(state)
    if focused is not None:
        return focused
    root = getattr(getattr(state, "project", None), "project_root", None)
    if root is not None:
        try:
            from xlii.artifacts import read_session, resolve_artifact_path

            sess = read_session(root)
            if sess and sess.paths:
                return resolve_artifact_path(root, sess.paths[-1])
        except (ValueError, FileNotFoundError):
            pass  # fall through to the locker
    for entry in reversed(getattr(state, "attached_files", None) or []):
        p = entry.get("path")
        if p and Path(p).is_file():
            return Path(p)
    return None


def _write_outbox(state: Any, text: str, *, prefix: str, suffix: str) -> Path:
    """Materialize *text* to a file so a program can open it. Prefer a browsable
    ``.xlii/outbox/`` inside the project; fall back to the system temp dir."""
    root = getattr(getattr(state, "project", None), "project_root", None)
    if root is not None:
        outbox = Path(root) / ".xlii" / "outbox"
        try:
            outbox.mkdir(parents=True, exist_ok=True)
            path = outbox / f"{prefix}{uuid.uuid4().hex[:8]}{suffix}"
            path.write_text(text, encoding="utf-8")
            return path
        except OSError:
            pass  # fall back to a system tempfile
    fd, name = tempfile.mkstemp(prefix=prefix, suffix=suffix)
    with open(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return Path(name)


def _materialize_output(state: Any, kind: str, console: Any) -> Optional[Path]:
    """``reply``/``shell`` read the shared last-output buffer (the same one
    ``/replay`` prints) and write it to a temp file. ``reply`` wants an
    answer/harness capture; ``shell`` wants a shell capture."""
    from xlii.shell_toolkit import last_output_capture

    cap = last_output_capture(state)
    text = (getattr(cap, "text", "") or "") if cap else ""
    source = getattr(cap, "source", "") if cap else ""
    if kind == "reply":
        ready, suffix = (bool(text.strip()) and source in ("answer", "harness")), ".md"
    else:  # shell
        ready, suffix = (bool(text.strip()) and source == "shell"), ".txt"
    if not ready:
        console.print(
            f"[yellow]nothing to send yet[/yellow] "
            f"[dim](no last {kind} output — run one first)[/dim]"
        )
        return None
    return _write_outbox(state, text, prefix=f"xlii-{kind}-", suffix=suffix)


def _resolve_target(state: Any, target: str, console: Any) -> Optional[Path]:
    """Resolve a target token to an on-disk file, or None (having printed why)."""
    t = target.strip()
    if t == "focus":
        p = _focus_file(state)
        if p is None:
            console.print(
                "[yellow]nothing focused[/yellow] "
                "[dim](Focus a file in the feed, or F4)[/dim]"
            )
        return p
    if t == "last":
        p = _last_file(state)
        if p is None:
            console.print(
                "[yellow]nothing to send yet[/yellow] "
                "[dim](Focus a file, /imagine, or add one to the locker)[/dim]"
            )
        return p
    if t in ("reply", "shell"):
        return _materialize_output(state, t, console)
    if t.startswith("locker://"):
        # The locker provider is a bytes-VFS (no Resolution.path), so look the
        # name up directly in the session's attached files.
        name = t[len("locker://"):].strip("/")
        entry = next(
            (e for e in (getattr(state, "attached_files", None) or []) if e.get("name") == name),
            None,
        )
        path = entry.get("path") if entry else None
        if path and Path(path).is_file():
            return Path(path)
        console.print(f"[red]no such attached file:[/red] {name}")
        return None
    # Anything else: a path or a scheme:// address, defaulting the scheme to file.
    from xlii.addressing import resolve as resolve_address

    try:
        res = resolve_address(t, default_scheme="file")
    except Exception as e:  # a malformed address should nudge, never crash
        console.print(f"[red]bad target:[/red] {e}")
        return None
    # FileProvider populates .path even on a miss (ok=False), so gate on is_file().
    if res.ok and res.path is not None and Path(res.path).is_file():
        return Path(res.path)
    console.print(f"[red]file not found:[/red] {t}")
    return None


# --------------------------------------------------------------------------- #
#  launch
# --------------------------------------------------------------------------- #

def _os_open_argv(path: Path) -> Optional[list[str]]:
    """The OS default-opener argv for *path*, or None if none is available."""
    if sys.platform == "darwin":
        return ["open", str(path)]
    if sys.platform.startswith("win"):
        return ["cmd", "/c", "start", "", str(path)]
    xdg = shutil.which("xdg-open")
    return [xdg, str(path)] if xdg else None


def _launch(program: Optional[str], path: Path, *, wait: bool, state: Any, console: Any) -> bool:
    """Launch *program* on *path*. Returns True if it launched, False on a lookup
    failure (program/opener missing — a message is printed)."""
    if program is None:
        argv = _os_open_argv(path)
        if argv is None:
            console.print("[red]no OS opener found[/red] [dim](install xdg-open)[/dim]")
            return False
        label = argv[0]
    else:
        exe = shutil.which(program)
        if exe is None:
            console.print(f"[red]program not found:[/red] {program}")
            return False
        argv = [exe, str(path)]
        label = program

    console.print(f"[dim]→ {label} · [/dim][cyan]{path.name}[/cyan]")

    # Full-screen/TTY programs (vim, less…) need a real terminal — route them
    # through the existing handover hook instead of a captured subprocess.
    if program is not None and is_interactive(program):
        runner = getattr(state, "_run_interactive", None)
        cmd_str = " ".join(shlex.quote(a) for a in argv)  # the hook takes a shell string
        if callable(runner):
            runner(cmd_str)
        else:
            subprocess.call(argv)
    elif wait:
        subprocess.call(argv)
    else:
        subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # detached — the REPL never blocks on a GUI app
        )
    return True


# --------------------------------------------------------------------------- #
#  handler
# --------------------------------------------------------------------------- #

_USAGE = (
    "[dim]usage:[/dim] [cyan]/send <program> <target>[/cyan]  "
    "[dim](target: focus · last · reply · shell · path · address; --wait to round-trip)[/dim]"
)


def _send_handler(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")
    if state is None:
        console.print("[red]no session state[/red]")
        return True

    try:
        toks = shlex.split(line)[1:]
    except ValueError as e:
        console.print(f"[red]parse error:[/red] {e}")
        return True

    wait = "--wait" in toks
    toks = [t for t in toks if t != "--wait"]

    if not toks:
        console.print(_USAGE)
        return True

    if len(toks) >= 2:
        program, target = toks[0], toks[1]
    elif _looks_like_target(toks[0]):
        program, target = None, toks[0]         # /send last → OS default opener
    else:
        program, target = toks[0], "last"       # /send gimp → /send gimp last

    resolved = _resolve_target(state, target, console)
    if resolved is None:
        return True  # the resolver already explained the miss

    if not _launch(program, resolved, wait=wait, state=state, console=console):
        return True  # launch failure already reported

    # --wait round-trip: block, then fold the (possibly edited) file back into the
    # locker so the next turn sees the change. attach_file refreshes an existing
    # entry in place.
    if wait and hasattr(state, "attach_file"):
        try:
            entry = state.attach_file(str(resolved))
            name = entry.get("name") if isinstance(entry, dict) else resolved.name
            console.print(f"[dim]↳ synced [/dim][cyan]{name}[/cyan][dim] to the locker[/dim]")
        except Exception:
            pass  # the launch is what mattered; a failed re-attach must not error
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="send",
            handler=_send_handler,
            description="Open a file or the last output in an external program.",
            usage="/send <program> <target>   (target: focus · last · reply · shell · path · address; --wait to round-trip)",
            category="session",
            repls=["code", "chat"],
        )
    )
