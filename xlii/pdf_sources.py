"""Resolve PDF render sources — aliases and explicit paths (document-pdf P1)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from xlii.loop import PLAN_LAST_FILE
from xlii.pdf_render import PdfRenderError, extract_title
from xlii.transcript import load_recent_turns


@dataclass(frozen=True)
class SourceContext:
    project_root: Path
    xli_dir: Path
    turns_dir: Path | None
    repl: str = "code"  # "code" | "chat" — affects ``last``


class SourceNotFound(PdfRenderError):
    pass


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        raise SourceNotFound(f"cannot read {path}: {e}") from e


def _newest_md(directory: Path, pattern: str = "*.md") -> Path | None:
    if not directory.is_dir():
        return None
    files = [p for p in directory.glob(pattern) if p.is_file()]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def resolve_source(alias_or_path: str, *, ctx: SourceContext) -> tuple[str, str]:
    """Resolve ``alias_or_path`` to ``(markdown_text, title)``."""
    key = (alias_or_path or "").strip()
    if not key:
        raise SourceNotFound("source is required")

    lowered = key.lower()
    xli = Path(ctx.xli_dir)

    if lowered == "last":
        td = ctx.turns_dir
        if td is None:
            raise SourceNotFound("last: no active turns directory in this session")
        turns = load_recent_turns(td, 1)
        if not turns:
            raise SourceNotFound(f"last: no turns in {td}")
        turn = turns[-1]
        text = turn.to_markdown()
        return text, extract_title(text, fallback="last")

    if lowered == "verify":
        path = xli / "verify-last.md"
        if not path.is_file():
            raise SourceNotFound("verify: no verify-last.md — run /verify first")
        text = _read_text(path)
        return text, extract_title(text, fallback="verify")

    if lowered == "peer":
        path = xli / "peer-last.md"
        if not path.is_file():
            raise SourceNotFound("peer: no peer-last.md — run /peer first")
        text = _read_text(path)
        return text, extract_title(text, fallback="peer")

    if lowered == "loop":
        path = _newest_md(xli, "loop-verdict-*.md")
        if path is None:
            fallback = xli / "verify-last.md"
            if fallback.is_file():
                text = _read_text(fallback)
                return text, extract_title(text, fallback="loop")
            raise SourceNotFound("loop: no loop-verdict-*.md (or verify-last.md fallback)")
        text = _read_text(path)
        return text, extract_title(text, fallback=path.stem)

    if lowered == "plan":
        current = xli / "plans" / "current.md"
        if current.is_file() and current.read_text(encoding="utf-8").strip():
            text = _read_text(current)
            return text, extract_title(text, fallback="plan")
        plan_last = xli / PLAN_LAST_FILE
        if plan_last.is_file():
            text = _read_text(plan_last)
            return text, extract_title(text, fallback="plan")
        raise SourceNotFound("plan: no plans/current.md or plan-last.md")

    # Explicit path — project-relative or absolute
    p = Path(key).expanduser()
    if not p.is_absolute():
        p = Path(ctx.project_root) / p
    p = p.resolve()
    if not p.is_file():
        raise SourceNotFound(f"not found: {alias_or_path}")
    text = _read_text(p)
    return text, extract_title(text, fallback=p.stem)


def source_context_from_state(state) -> SourceContext:
    """Build a resolver context from a REPL ``state`` object."""
    project = getattr(state, "project", None)
    if project is None:
        raise SourceNotFound("no active project")
    root = Path(project.project_root)
    xli_dir = Path(project.xli_dir)
    repl = getattr(getattr(state, "profile", None), "name", None) or "code"
    turns_dir = None
    mem = getattr(getattr(state, "profile", None), "memory", None)
    if mem is not None:
        turns_dir = getattr(mem, "turns_dir", None)
    if turns_dir is None:
        from xlii.repl_cmds.chat import _active_turns_dir

        turns_dir = _active_turns_dir(state)
    return SourceContext(
        project_root=root,
        xli_dir=xli_dir,
        turns_dir=Path(turns_dir) if turns_dir else None,
        repl=str(repl),
    )
