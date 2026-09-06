"""Storage backends for project search.

The strategic seam (roadmap 5.1): xlii's durable value — what search_project
can recall — must never be trapped in a single vendor API. This module gives
every project a LOCAL full-text index (SQLite FTS5, stdlib, zero deps) that
is maintained alongside the remote Collection at sync time. It is:

  * the offline mode  — search works with the network cable pulled,
  * the local_only mode — `xlii init --local` projects get search_project,
  * the test double  — no network needed to exercise the search path,
  * the escape hatch — your index is a file you own.

Remote (xAI Collections) remains the primary backend when reachable: its
server-side hybrid RAG outranks bare BM25. The local index is the floor,
not the ceiling.
"""

from __future__ import annotations

import re
import sqlite3
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from xlii.config import GlobalConfig, ProjectConfig

INDEX_FILENAME = "local_index.db"


class LocalIndex:
    """SQLite FTS5 full-text index over a project's tracked text files."""

    def __init__(self, project: "ProjectConfig"):
        self.project = project
        self.path = project.xli_dir / INDEX_FILENAME

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path)
        con.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(rel, content)"
        )
        return con

    def exists(self) -> bool:
        return self.path.exists()

    def rebuild(self, cfg: "GlobalConfig") -> int:
        """Re-index every tracked text file. Returns the number of docs.

        Full rebuild in one transaction — simpler and less drift-prone than
        incremental updates, and fast enough for any tree the sync ignore
        rules let through (content is capped at cfg.max_file_bytes per file).
        """
        from xlii.ignore import load_ignore_spec, walk_project

        spec = load_ignore_spec(self.project.project_root, self.project.extra_ignores)
        rows: list[tuple[str, str]] = []
        for path in walk_project(
            self.project.project_root, spec, max_bytes=cfg.max_file_bytes
        ):
            rel = path.relative_to(self.project.project_root).as_posix()
            try:
                rows.append((rel, path.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                continue

        con = self._connect()
        try:
            with con:
                con.execute("DELETE FROM docs")
                con.executemany("INSERT INTO docs(rel, content) VALUES (?, ?)", rows)
        finally:
            con.close()
        return len(rows)

    def search(self, query: str, limit: int = 10) -> list[tuple[str, str, float]]:
        """BM25-ranked search. Returns [(relpath, snippet, score)].

        The raw query is reduced to bare terms (FTS5 operators like NEAR/"/*
        in model-written queries would otherwise be syntax errors) and OR-ed,
        so partial matches still rank.
        """
        terms = re.findall(r"[A-Za-z0-9_]+", query)
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in terms)
        if not self.exists():
            return []
        con = self._connect()
        try:
            cur = con.execute(
                "SELECT rel, snippet(docs, 1, '', '', ' … ', 40), bm25(docs) "
                "FROM docs WHERE docs MATCH ? ORDER BY bm25(docs) LIMIT ?",
                (match, limit),
            )
            # bm25() is a cost (lower = better); negate so higher = better
            # like every other score the model sees.
            return [(rel, snip, -score) for rel, snip, score in cur.fetchall()]
        except sqlite3.OperationalError:
            return []
        finally:
            con.close()


def local_search_text(
    project: "ProjectConfig", query: str, limit: int = 10
) -> Optional[str]:
    """Format a local-index search like the Collections result block.

    Returns None when there is no usable index (caller decides the message).
    """
    idx = LocalIndex(project)
    if not idx.exists():
        return None
    hits = idx.search(query, limit)
    if not hits:
        return "(no results — local index)"
    out = []
    for i, (rel, snippet, score) in enumerate(hits, 1):
        clean = " ".join(snippet.split())
        out.append(f"[{i}] {rel}  (local bm25={score:.2f})\n{clean}")
    return "\n\n---\n\n".join(out) + "\n\n[searched the LOCAL index — offline/local mode]"
