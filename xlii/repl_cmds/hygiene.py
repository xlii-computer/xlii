"""/hygiene — text hygiene + credibility scan for untrusted ingress.

scan  — always safe; report newlines, BOM, encoding, one credibility counter
strip — opt-in default pack (LF · drop BOM · strip injection-class Unicode)

Not a lab-watermark eraser. Not a language formatter. See xlii/text_hygiene.py.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any, Optional

from xlii.commands import REPLCommand, register_repl_command
from xlii.text_hygiene import (
    HygieneReport,
    StripOptions,
    format_report,
    scan_path,
    scan_text,
    strip_path,
)

_USAGE = (
    "/hygiene scan [path…] | strip [path…] [--newlines lf|crlf|keep] [--keep-bom]"
)


def _project_root(ctx: dict[str, Any]) -> Optional[Path]:
    state = ctx.get("state")
    project = ctx.get("project") or getattr(state, "project", None)
    root = getattr(project, "project_root", None) if project is not None else None
    if root:
        return Path(root)
    cwd = getattr(state, "shell_cwd", None) if state is not None else None
    return Path(cwd) if cwd else None


def _resolve_path(raw: str, root: Optional[Path]) -> Path:
    p = Path(raw).expanduser()
    if not p.is_absolute() and root is not None:
        cand = (root / p)
        if cand.exists():
            return cand.resolve()
    try:
        return p.resolve()
    except OSError:
        return p


def _parse_strip_opts(flags: list[str]) -> tuple[StripOptions, list[str]]:
    newlines = "lf"
    strip_bom = True
    unknown: list[str] = []
    i = 0
    while i < len(flags):
        tok = flags[i]
        if tok == "--keep-bom":
            strip_bom = False
        elif tok == "--newlines" and i + 1 < len(flags):
            i += 1
            val = flags[i].lower()
            if val in ("lf", "crlf", "keep"):
                newlines = val
            else:
                unknown.append(f"--newlines {flags[i]}")
        elif tok.startswith("--newlines="):
            val = tok.split("=", 1)[1].lower()
            if val in ("lf", "crlf", "keep"):
                newlines = val
            else:
                unknown.append(tok)
        else:
            unknown.append(tok)
        i += 1
    return StripOptions(newlines=newlines, strip_bom=strip_bom), unknown


def _split_paths_flags(rest: list[str]) -> tuple[list[str], list[str]]:
    paths_raw: list[str] = []
    flags: list[str] = []
    i = 0
    while i < len(rest):
        t = rest[i]
        if t == "--newlines" and i + 1 < len(rest):
            flags.extend([t, rest[i + 1]])
            i += 2
            continue
        if t.startswith("-"):
            flags.append(t)
        else:
            paths_raw.append(t)
        i += 1
    return paths_raw, flags


def _handler(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    parts = line.split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    if not arg or arg in ("help", "-h", "--help"):
        console.print(
            "[dim]Text hygiene — portability + credibility (injection-class Unicode).[/dim]"
        )
        console.print(_USAGE)
        console.print(
            "[dim]scan = report only · strip = LF + drop BOM + strip stego/bidi junk[/dim]"
        )
        console.print(
            "[dim]credibility = hidden/control Unicode only "
            "(CRLF/BOM are portability notes, not danger)[/dim]"
        )
        return True

    try:
        tokens = shlex.split(arg)
    except ValueError as e:
        console.print(f"[yellow]bad quoting: {e}[/yellow]")
        return True

    verb = tokens[0].lower()
    rest = tokens[1:]
    if verb not in ("scan", "strip", "normalize", "check"):
        rest = tokens
        verb = "scan"
    if verb == "normalize":
        verb = "strip"
    if verb == "check":
        verb = "scan"

    paths_raw, flags = _split_paths_flags(rest)
    opts, unknown = _parse_strip_opts(flags)
    if unknown:
        console.print(
            f"[yellow]unknown flag(s): {' '.join(unknown)}[/yellow] — "
            f"known: --newlines lf|crlf|keep · --keep-bom"
        )
        return True

    if not paths_raw:
        console.print("[yellow]pass one or more paths[/yellow]")
        console.print(_USAGE)
        return True

    root = _project_root(ctx)
    for raw in paths_raw:
        path = _resolve_path(raw, root)
        if verb == "scan":
            if not path.is_file():
                console.print(format_report(HygieneReport(
                    path=str(path),
                    error="not a file" if not path.exists() else "not a regular file",
                )))
                continue
            rep = scan_path(path)
            console.print(format_report(rep))
            if rep.ok and rep.credibility > 0:
                console.print(
                    f"[yellow]take note · credibility {rep.credibility} "
                    f"— /hygiene strip {path}[/yellow]"
                )
            continue

        # strip
        if not path.is_file():
            console.print(f"[yellow]hygiene strip: not a file: {path}[/yellow]")
            continue
        before, result, err = strip_path(path, opts, write=True)
        console.print(format_report(before))
        if err == "binary-suspect":
            console.print("[yellow]skipped binary-suspect file[/yellow]")
            continue
        if err:
            console.print(f"[red]strip failed: {err}[/red]")
            continue
        if not result.changed:
            console.print("[dim]already clean (default pack)[/dim]")
        else:
            applied = " · ".join(result.applied) or "normalized"
            console.print(f"[green]stripped[/green] · {applied} → {path}")
            after = scan_text(result.text, path=str(path))
            console.print(
                f"[dim]credibility now {after.credibility} "
                f"(was {before.credibility})[/dim]"
            )
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="hygiene",
            handler=_handler,
            aliases=["sanitize"],
            description=(
                "Text hygiene: scan portability + credibility counter; "
                "strip LF/BOM/invisible Unicode (harness/paste ingress)"
            ),
            usage="/hygiene scan|strip [path…] [--newlines lf|crlf|keep] [--keep-bom]",
            category="console",
            repls=["code"],
        )
    )
