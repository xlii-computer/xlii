"""The wiki — the system's **semantic memory** (kernel-rebuild north-star §"The wiki").

Pages are plain markdown files under the project's ``.xlii/wiki/`` — distilled, linked,
durable knowledge, the third tier of the memory hierarchy (``conv://`` episodic ·
``xlii://`` working · ``wiki://`` semantic). This module is the **store**; the address
surface is ``WikiProvider`` (``xlii/addressing/_builtin_providers.py``) and the browse
surface is :class:`~xlii.panes.wiki.WikiPane`.

The two anti-confabulation guards from the design doc are *data* here, so every later
consumer (the journal routine, retrieval, the skeptical-editor pass) shares one source:

* **Provenance** — every page carries ``sources:``, a list of *addresses* (``file://…#L4``,
  ``conv://…``, ``job://…``) its claims came from. The ``#anchor`` grammar is the wiki's
  precise-provenance unit.
* **Verify-before-trust** — a page is born ``verified: false`` and is only **promoted**
  (:func:`mark_verified`) after a skeptical pass checks it against its sources. Write →
  refute → *then* promote. Unverified pages still browse/attach, but wear the flag loudly.

Front matter is a tiny hand-rolled ``key: value`` block (no YAML dependency)::

    ---
    sources: file://notes.md#L4-10, conv://proj/20260630-1200
    verified: false
    ---
    # Page title

    body…
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from xlii.atomicio import write_text_atomic

WIKI_DIRNAME = "wiki"          # under .xlii/
_NAME_RE = re.compile(r"[A-Za-z0-9._-]+")

DEFAULT_WIKI_TEMPLATE = """# <page title>

Distilled, durable knowledge about this project — the *semantic* tier (what you'd want
to read months from now), not a raw log. Keep it tight and linkable:

- State conclusions, not narration. Link related pages with [[other-page]].
- Every claim should trace to a source — list them in the `sources:` front matter as
  addresses (`file://path#L4-10`, `conv://./turn.md`, `wiki://other#section`).
- This page is born **unverified**. Run `/wiki verify <name>` to promote it once its
  claims check out against those sources.
"""


def wiki_dir(xli_dir: "Path | str") -> Path:
    return Path(xli_dir) / WIKI_DIRNAME


def is_valid_name(name: str) -> bool:
    return bool(name) and _NAME_RE.fullmatch(name) is not None


def page_path(xli_dir: "Path | str", name: str) -> Path:
    return wiki_dir(xli_dir) / f"{name}.md"


@dataclass
class WikiPage:
    """One page: its name, the provenance/trust front matter, and the markdown body."""

    name: str
    body: str = ""
    sources: list[str] = field(default_factory=list)
    verified: bool = False

    @property
    def title(self) -> str:
        """The first heading (or first non-empty line) — the browse-row label."""
        for line in self.body.splitlines():
            s = line.strip()
            if s:
                return s.lstrip("#").strip() or self.name
        return self.name


# --------------------------------------------------------------------------- #
#  Front matter (hand-rolled key: value block — provenance + trust only)
# --------------------------------------------------------------------------- #

def _looks_like_front_matter(head: str) -> bool:
    """True only for a real ``key: value`` provenance block (what ``render_page``
    writes) — NOT a markdown body that merely opens with a ``---`` thematic break.
    Every non-empty line must be a ``key: value`` pair and at least one key must be
    a recognized wiki field, so plain prose between ``---`` rules is left as body."""
    lines = [ln for ln in head.splitlines() if ln.strip()]
    if not lines:
        return False
    recognized = False
    for line in lines:
        key, sep, _ = line.partition(":")
        key = key.strip()
        if not sep or not key or " " in key:
            return False  # bare prose / heading — not front matter
        if key.lower() in ("sources", "verified"):
            recognized = True
    return recognized


def parse_page(name: str, text: str) -> WikiPage:
    """Split a page file into front matter + body. A file with no front-matter block is a
    valid page (unverified, no sources) — the wiki never rejects plain markdown."""
    sources: list[str] = []
    verified = False
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        head = text[4:end] if end != -1 else ""
        # Only consume a leading `---…---` block when it's genuinely front matter.
        # A body that opens with a `---` thematic break (or has an early `---` line)
        # must not have its content silently eaten as a bogus header.
        if end != -1 and _looks_like_front_matter(head):
            body = text[end + 4:].lstrip("\n")
            for line in head.splitlines():
                key, _, value = line.partition(":")
                key, value = key.strip().lower(), value.strip()
                if key == "sources" and value:
                    sources = [s.strip() for s in value.split(",") if s.strip()]
                elif key == "verified":
                    verified = value.lower() in ("true", "yes", "1")
    return WikiPage(name=name, body=body, sources=sources, verified=verified)


def render_page(page: WikiPage) -> str:
    """The on-disk form: front matter (always written, so trust state is never implicit)
    followed by the body."""
    lines = ["---"]
    if page.sources:
        lines.append("sources: " + ", ".join(page.sources))
    lines.append(f"verified: {'true' if page.verified else 'false'}")
    lines.append("---")
    return "\n".join(lines) + "\n\n" + page.body.rstrip("\n") + "\n"


# --------------------------------------------------------------------------- #
#  Store operations
# --------------------------------------------------------------------------- #

def list_pages(xli_dir: "Path | str") -> "list[WikiPage]":
    d = wiki_dir(xli_dir)
    if not d.exists():
        return []
    pages = []
    for p in sorted(d.glob("*.md")):
        try:
            pages.append(parse_page(p.stem, p.read_text()))
        except OSError:
            continue
    return pages


def page_exists(xli_dir: "Path | str", name: str) -> bool:
    return is_valid_name(name) and page_path(xli_dir, name).exists()


def read_page(xli_dir: "Path | str", name: str) -> WikiPage:
    path = page_path(xli_dir, name)
    if not path.exists():
        raise FileNotFoundError(f"wiki://{name}: no such page")
    return parse_page(name, path.read_text())


def write_page(
    xli_dir: "Path | str",
    name: str,
    body: str,
    *,
    sources: "list[str] | tuple[str, ...]" = (),
    verified: bool = False,
) -> Path:
    """Create/overwrite a page (atomic). A rewrite resets ``verified`` unless the caller
    re-asserts it — changed claims need a fresh skeptical pass."""
    if not is_valid_name(name):
        raise ValueError(f"invalid wiki page name: {name!r} (letters/digits/._- only)")
    path = page_path(xli_dir, name)
    page = WikiPage(name=name, body=body, sources=list(sources), verified=verified)
    write_text_atomic(path, render_page(page), mode=0o644)
    return path


def mark_verified(xli_dir: "Path | str", name: str, verified: bool = True) -> WikiPage:
    """The **promote** step of write → refute → promote (or its revocation)."""
    page = read_page(xli_dir, name)
    page.verified = verified
    write_text_atomic(page_path(xli_dir, name), render_page(page), mode=0o644)
    return page


def delete_page(xli_dir: "Path | str", name: str) -> bool:
    path = page_path(xli_dir, name)
    if not path.exists():
        return False
    path.unlink()
    return True


# --------------------------------------------------------------------------- #
#  Retrieval — the floor that lets the xlii *find* its own pages
# --------------------------------------------------------------------------- #

def search_pages(
    xli_dir: "Path | str", query: str, limit: int = 10
) -> "list[tuple[str, str, float, bool]]":
    """BM25 search over the project's wiki pages: ``[(name, snippet, score, verified)]``.

    Verified and unverified pages both match — the caller tags trust (mirroring the attach path,
    which rides unverified pages with a warning rather than hiding them). **Always fresh**: an
    ephemeral in-memory FTS5 index is built from the *current* pages every call, so a page is
    searchable the instant it's written — no rebuild step to drift out of date (the wiki corpus is
    small, so this is cheap). Returns ``[]`` on an empty/operator-only query or no pages."""
    import sqlite3

    terms = re.findall(r"[A-Za-z0-9_]+", query)
    if not terms:
        return []
    pages = list_pages(xli_dir)
    if not pages:
        return []
    con = sqlite3.connect(":memory:")
    try:
        con.execute(
            "CREATE VIRTUAL TABLE docs USING fts5(name UNINDEXED, verified UNINDEXED, content)"
        )
        con.executemany(
            "INSERT INTO docs(name, verified, content) VALUES (?, ?, ?)",
            [(p.name, "1" if p.verified else "0", f"{p.title}\n{p.body}") for p in pages],
        )
        match = " OR ".join(f'"{t}"' for t in terms)
        cur = con.execute(
            "SELECT name, verified, snippet(docs, 2, '', '', ' … ', 40), bm25(docs) "
            "FROM docs WHERE docs MATCH ? ORDER BY bm25(docs) LIMIT ?",
            (match, limit),
        )
        # bm25() is a cost (lower = better); negate so higher = better, like every other score.
        return [(name, snip, -score, verified == "1") for name, verified, snip, score in cur.fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


# --------------------------------------------------------------------------- #
#  Sections — the ``wiki://page#section`` anchor unit
# --------------------------------------------------------------------------- #

def heading_slug(heading: str) -> str:
    """A heading's anchor slug (``## The event seam`` → ``the-event-seam``)."""
    s = re.sub(r"[^a-z0-9]+", "-", heading.strip().lstrip("#").strip().lower())
    return s.strip("-")


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def extract_section(body: str, anchor: str) -> Optional[str]:
    """The span a ``#<slug>`` anchor denotes: from the matching heading up to (not including)
    the next heading of the same or higher level. ``None`` when no heading matches — the
    caller decides whether that's an error (``vfs_read``) or a fallthrough."""
    want = anchor.strip().lstrip("#")
    lines = body.splitlines()
    start = level = None
    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m and heading_slug(m.group(2)) == heading_slug(want):
            start, level = i, len(m.group(1))
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        m = _HEADING_RE.match(lines[j])
        if m and len(m.group(1)) <= (level or 6):
            end = j
            break
    return "\n".join(lines[start:end]).rstrip("\n")


__all__ = [
    "WIKI_DIRNAME", "DEFAULT_WIKI_TEMPLATE", "WikiPage",
    "wiki_dir", "is_valid_name", "page_path", "page_exists",
    "parse_page", "render_page",
    "list_pages", "read_page", "write_page", "mark_verified", "delete_page",
    "search_pages",
    "heading_slug", "extract_section",
]
