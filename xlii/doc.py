"""Reference document management for `/doc` slash command.

A doc is a named piece of static knowledge — a GROK.md-style guide or project convention, a
framework's coding conventions, a project spec — that any session can
attach via `/doc <name>`. Attached docs are inlined into the agent's
system prompt for the rest of the session, so they're ALWAYS in context
(no RAG, no retrieval cost). Best for small-to-medium docs (rules,
conventions, glossaries) — for big reference material that needs
chunking, use a persona + `/ref` instead.

Storage: `~/.config/xlii/docs/<name>.md` — same shape as personas, just
different content type. Hand-editable, shareable as text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from xlii.config import GLOBAL_CONFIG_DIR

DOCS_DIR = GLOBAL_CONFIG_DIR / "docs"

# Soft cap for inline mode. If a doc is bigger than this, we still attach
# but warn the user — at some point you want a Collection instead of
# every-turn context bloat. Tune later based on real usage.
INLINE_SOFT_CAP_BYTES = 20_000

DEFAULT_DOC_TEMPLATE = """# <doc title>

Replace this with your reference content. Anything you put here gets inlined
into the agent's system prompt every turn it's attached, so:

- Be concise — every byte costs tokens, every turn.
- Use it for *rules and conventions*, not big reference material. (For big
  docs, build a persona instead and `/ref` it.)
- Markdown headings/lists/code-fences all work — the model reads it as text.

Examples of what a good doc looks like:
- "Always use httpx, never requests."
- "Use snake_case for filenames; PascalCase for classes."
- "Run `pytest -x` after every change to src/."
- "When writing async code, prefer `anyio` over `asyncio` directly."
"""


# Names go on disk as filenames; reuse the same validation pattern as personas.
_VALID_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def is_valid_name(name: str) -> bool:
    return bool(_VALID_NAME.match(name))


@dataclass
class Doc:
    name: str

    @property
    def path(self) -> Path:
        return DOCS_DIR / f"{self.name}.md"

    def exists(self) -> bool:
        return self.path.exists()

    def read(self) -> str:
        return self.path.read_text()

    def write(self, text: str) -> None:
        DOCS_DIR.mkdir(parents=True, exist_ok=True)
        self.path.write_text(text)

    def first_line(self) -> str:
        """One-line summary for `--list`. Skips markdown headings and blank
        lines so the user sees actual content."""
        try:
            for line in self.read().splitlines():
                stripped = line.strip().lstrip("#").strip()
                if stripped and not stripped.startswith("<"):
                    return stripped[:100]
        except OSError:
            return "(unreadable)"
        return "(empty)"

    def size_bytes(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0


def list_docs() -> list[Doc]:
    if not DOCS_DIR.exists():
        return []
    return [Doc(name=p.stem) for p in sorted(DOCS_DIR.glob("*.md"))]



def open_in_editor(path: Path) -> int:
    """Open `path` in $EDITOR (or vi as fallback). Returns exit status."""
    from xlii.editor import open_for_edit

    return open_for_edit(path)


def create_doc(name: str, *, content: Optional[str] = None) -> Doc:
    if not is_valid_name(name):
        raise ValueError(
            f"invalid doc name: {name!r}. Use letters, digits, _ . - only "
            "(start with letter/digit; max 64 chars)."
        )
    d = Doc(name)
    if d.exists():
        raise FileExistsError(f"doc {name!r} already exists at {d.path}")
    d.write(content if content is not None else DEFAULT_DOC_TEMPLATE)
    return d


def delete_doc(name: str) -> bool:
    """Remove the doc file. Returns True if removed, False if not found."""
    d = Doc(name)
    if not d.exists():
        return False
    d.path.unlink()
    return True
