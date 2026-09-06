"""Wiki retrieval — search a project's semantic-memory wiki and resolve hits to
precise ``wiki://page#section`` addresses. Scope-neutral (any door can use it):
today ``/askjo`` (the project-knowledge door) reads the project wiki here; a
future ``/howto`` vendor tier will read xlii's own shipped wiki through the same
helpers.

The wiki (``.xlii/wiki/``) is the project's semantic tier — distilled, source-
cited knowledge, the read-end of the journal's own auto-distillation
(:meth:`xlii.journal.Journal._autobuild_wiki` → :func:`xlii.wiki_author.propose_pages`
drafts pages on flush; the operator promotes; retrieval reads). BM25 search is
:func:`xlii.wiki.search_pages`; this module adds section-anchor resolution so a
citation points at one heading's span, not a whole page.

Pure functions over :mod:`xlii.wiki` only (no TUI import), so ranking and anchor
selection are unit-testable without a running app.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from xlii.wiki import extract_section, heading_slug, read_page, search_pages

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


@dataclass(frozen=True)
class WikiHit:
    """One retrieved wiki page: its name, the best-matching section anchor (or
    ``""`` for the page as a whole), a snippet, the BM25 score, trust, and the
    scope's scheme (``wiki`` = project tier · ``xwiki`` = shipped vendor tier)."""

    name: str
    anchor: str
    snippet: str
    score: float
    verified: bool
    scheme: str = "wiki"

    @property
    def address(self) -> str:
        """The precise ``<scheme>://page#section`` (or ``<scheme>://page``) citation."""
        base = f"{self.scheme}://{self.name}"
        return f"{base}#{self.anchor}" if self.anchor else base


def _query_terms(query: str) -> list[str]:
    return [t.lower() for t in _WORD_RE.findall(query) if len(t) > 1]


def wiki_search_address(question: str, *, scheme: str = "wiki") -> str:
    """The ``<scheme>://?q=…`` results address for a question — the search "URL"
    the Dock mounts WikiPane in results mode against (project or vendor scope).
    Sanitized to word chars + spaces so the query survives ``Address.parse`` (a
    stray ``?``/``#`` in the question would otherwise be read as query/anchor
    punctuation)."""
    terms = " ".join(re.findall(r"[\w-]+", question))
    return f"{scheme}://?q={terms}"


def best_section(body: str, query: str) -> str:
    """The heading slug whose section best matches ``query`` — the ``#anchor`` a
    citation should point at. Scores each section by how many distinct query
    terms appear in it (heading text counts double, so a section *titled* for the
    question wins). Returns ``""`` when nothing beats the page as a whole, so the
    caller cites ``wiki://page`` rather than a spurious anchor."""
    terms = set(_query_terms(query))
    if not terms:
        return ""
    lines = body.splitlines()
    heads: list[tuple[str, int]] = []
    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m:
            heads.append((heading_slug(m.group(2)), i))
    if not heads:
        return ""
    best_slug, best_score = "", 0
    for idx, (slug, line_i) in enumerate(heads):
        end = heads[idx + 1][1] if idx + 1 < len(heads) else len(lines)
        heading_words = set(_query_terms(lines[line_i]))
        section_words = set(_query_terms("\n".join(lines[line_i:end])))
        score = len(terms & section_words) + len(terms & heading_words)
        if score > best_score:
            best_slug, best_score = slug, score
    return best_slug


def search_self_wiki(xli_dir, query: str, limit: int = 4, *, scheme: str = "wiki") -> list[WikiHit]:
    """Rank a wiki scope's pages for ``query`` and resolve each to its best
    ``<scheme>://page#section`` anchor. ``xli_dir`` is any xli_dir-shaped root —
    the ambient project's ``.xlii`` (scheme ``wiki``) or the shipped bundle
    (:func:`xlii.selfwiki.selfwiki_root`, scheme ``xwiki``). ``[]`` when there is
    no wiki, no match, or an operator-only query. Never raises — a broken page or
    missing dir yields no hits rather than failing the caller's turn."""
    if xli_dir is None:
        return []
    try:
        raw = search_pages(xli_dir, query, limit=limit)
    except Exception:
        return []
    hits: list[WikiHit] = []
    for name, snippet, score, verified in raw:
        anchor = ""
        try:
            page = read_page(xli_dir, name)
            anchor = best_section(page.body, query)
        except Exception:
            anchor = ""
        hits.append(WikiHit(name=name, anchor=anchor, snippet=snippet.strip(),
                            score=score, verified=verified, scheme=scheme))
    return hits


def section_text(xli_dir, hit: "WikiHit") -> Optional[str]:
    """The markdown a hit's address denotes — the anchored section, or the whole
    body when the hit has no anchor. ``None`` if the page can't be read."""
    try:
        page = read_page(xli_dir, hit.name)
    except Exception:
        return None
    if hit.anchor:
        span = extract_section(page.body, hit.anchor)
        if span is not None:
            return span
    return page.body


def wiki_context_block(xli_dir, hits: list["WikiHit"], *, limit: int = 3) -> str:
    """The anchored section text of the top hits, fenced by address — what an
    advisor (``/askjo``) folds into its prompt to ground an answer in the wiki.
    ``""`` when nothing resolves, so callers can concatenate unconditionally."""
    shards: list[str] = []
    for h in hits[:limit]:
        body = section_text(xli_dir, h)
        if body:
            if h.scheme == "xwiki":
                tag = "shipped self-doc"
            else:
                tag = "verified" if h.verified else "unverified — draft"
            shards.append(f"### {h.address} ({tag})\n\n{body}")
    return "\n\n".join(shards)


def format_citations(hits: list["WikiHit"]) -> str:
    """The AI-framing block naming the wiki addresses to cite for deeper reading.
    Scope-aware: project hits carry their trust state; vendor (``xwiki``) hits are
    shipped truth and carry none. ``""`` when there are no hits, so callers can
    concatenate unconditionally."""
    if not hits:
        return ""
    vendor = all(h.scheme == "xwiki" for h in hits)
    if vendor:
        lines = [
            "xlii's shipped self-wiki (tool knowledge, version-locked to this "
            "build) has pages relevant to this question. Cite the most relevant "
            "as `xwiki://page#section` addresses so the operator can open them "
            "for deeper detail. Relevant pages:",
        ]
    else:
        lines = [
            "The project's own wiki (semantic memory) has pages relevant to this "
            "question. Cite the most relevant as `wiki://page#section` addresses so "
            "the operator can open them for deeper, project-specific detail. "
            "Unverified pages (marked) are drafts; say so when you lean on one. "
            "Relevant pages:",
        ]
    for h in hits:
        if h.scheme == "xwiki":
            tag = "shipped"
        else:
            tag = "verified" if h.verified else "unverified"
        lines.append(f"- `{h.address}` ({tag})"
                     + (f" — {h.snippet}" if h.snippet else ""))
    return "\n".join(lines)


__all__ = [
    "WikiHit", "best_section", "search_self_wiki", "section_text",
    "wiki_context_block", "wiki_search_address", "format_citations",
]
