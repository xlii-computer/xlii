"""AI-authored wiki pages — the **distillation action** and the **skeptical-editor verify pass**.

Two model-powered steps of the wiki's ``write → refute → promote`` lifecycle
(kernel-rebuild north-star §"Wiki anti-confabulation"). Both are pure functions over an
**injected** ``complete(messages) -> str`` callable, so the logic is unit-testable without a
live model and the REPL/CLI wiring stays a thin shell that only has to build that callable
from the ambient session's client pool + model.

* :func:`distill` — turn a set of source *addresses* (``file://``, ``conv://``, ``wiki://`` …)
  into a page, born ``verified: false``, with ``sources:`` set to exactly what it read. The
  episode→semantic compression the wiki exists to do.
* :func:`verify` — the **promote gate**. Re-read the page's own ``sources:`` through the VFS and
  ask a skeptical editor whether every claim is supported. Only a clean ``VERIFIED`` verdict
  promotes (:func:`xlii.wiki.mark_verified`); a page with no provenance is *never* auto-verified.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from xlii import wiki as W

# A completion is any callable taking OpenAI-style messages and returning the reply text.
Complete = Callable[[list], str]


class WikiAuthorError(RuntimeError):
    """A distill/verify step could not produce a usable result (empty model reply, etc.)."""


_DISTILL_SYSTEM = (
    "You are the expert distiller of a project's semantic memory (its wiki). You compress raw "
    "episodes — code, notes, conversation turns — into one durable, human-readable markdown page. "
    "Rules: (1) State conclusions, not narration. (2) Write ONLY what the provided sources "
    "support — never invent facts, numbers, or file paths. (3) If the sources are thin, write a "
    "short page and say what's unknown rather than padding. (4) Start with a single '# Title' "
    "heading; use '## ' sub-sections. (5) Output the page markdown only — no preamble, no fences."
)

_VERIFY_SYSTEM = (
    "You are a skeptical fact-checking editor for a project's wiki. You are given a page and the "
    "full text of the sources it cites. Your only job is to decide whether EVERY substantive claim "
    "on the page is supported by those sources. Be adversarial: a plausible-but-unsupported claim "
    "must fail. Respond with 'VERIFIED' on the first line if all claims are supported, or "
    "'REFUTED' on the first line otherwise. Then, on following lines, briefly list each unsupported "
    "or contradicted claim (empty if VERIFIED)."
)


def _read_sources(addresses: "list[str]") -> "list[tuple[str, str]]":
    """Resolve each source address through the VFS to its text. Unreadable sources are kept in the
    corpus as an explicit error line (so the model sees the gap) rather than silently dropped."""
    from xlii.addressing import vfs_read

    out: "list[tuple[str, str]]" = []
    for a in addresses:
        try:
            out.append((a, vfs_read(a).decode("utf-8", errors="replace")))
        except Exception as e:  # noqa: BLE001 — a bad source is data, not a crash
            out.append((a, f"(could not read this source: {type(e).__name__})"))
    return out


def _corpus(read: "list[tuple[str, str]]") -> str:
    return "\n\n".join(f"### {addr}\n{text}" for addr, text in read) or "(no sources)"


def _readable(read: "list[tuple[str, str]]") -> "list[str]":
    return [a for a, t in read if not t.startswith("(could not read this source")]


def distill(
    xli_dir: "Path | str",
    name: str,
    sources: "list[str]",
    complete: Complete,
    *,
    intent: str = "",
) -> "list[str]":
    """Write ``wiki://<name>`` from ``sources`` using ``complete``. Born unverified; ``sources:``
    records exactly the addresses that resolved. Returns the recorded source list. Raises
    :class:`WikiAuthorError` on an empty model reply."""
    if not W.is_valid_name(name):
        raise ValueError(f"invalid wiki page name: {name!r} (letters/digits/._- only)")
    read = _read_sources(list(sources))
    recorded = _readable(read)
    ask = [f"Write the wiki page '{name}'."]
    if intent:
        ask.append(f"Focus: {intent}")
    ask.append("Distil it strictly from these sources:\n\n" + _corpus(read))
    body = complete([
        {"role": "system", "content": _DISTILL_SYSTEM},
        {"role": "user", "content": "\n\n".join(ask)},
    ]).strip()
    if not body:
        raise WikiAuthorError("the model returned an empty page")
    W.write_page(xli_dir, name, body, sources=recorded, verified=False)
    return recorded


@dataclass
class Verdict:
    """The skeptical-editor outcome: whether the page was promoted, and the editor's reasoning."""

    verified: bool
    reasons: str
    promoted: bool = False  # whether this pass actually flipped the page to verified


def verify(xli_dir: "Path | str", name: str, complete: Complete) -> Verdict:
    """Run the skeptical-editor pass on ``wiki://<name>`` and, on a clean verdict, promote it.

    A page with **no recorded sources** is never verified — there is nothing to check it against,
    and auto-trusting unprovenanced text is exactly the confabulation the guard exists to stop."""
    page = W.read_page(xli_dir, name)
    if not page.sources:
        return Verdict(verified=False,
                       reasons="no sources recorded — add provenance before verifying "
                               "(a page can't be trusted against nothing).")
    read = _read_sources(page.sources)
    raw = complete([
        {"role": "system", "content": _VERIFY_SYSTEM},
        {"role": "user", "content": f"PAGE:\n{page.body}\n\nSOURCES:\n{_corpus(read)}"},
    ]).strip()
    verified = _first_line_says_verified(raw)
    if verified:
        W.mark_verified(xli_dir, name, True)
    return Verdict(verified=verified, reasons=raw, promoted=verified)


_PROPOSE_SYSTEM = (
    "You maintain a project's wiki from its work journal. Given recent activity and the list of "
    "pages that ALREADY exist, propose wiki pages for STABLE, durable, page-worthy topics — how a "
    "subsystem works, an architecture, a decision and its why. Rules: (1) Do NOT propose a topic "
    "that an existing page already covers. (2) Skip day-to-day churn, fixes-in-progress, and "
    "anything volatile — a page is semantic memory, not a log. (3) Write ONLY what the journal "
    "supports; never invent. (4) Often the right answer is NONE yet — return an empty list freely. "
    "Output a JSON array (and nothing else) of objects: "
    '{"name": "<simple-slug>", "body": "# Title\\n\\n<markdown>", "sources": ["file://…", "conv://…"]}. '
    "sources are the addresses the claims came from (cite the files the journal shows as touched)."
)


@dataclass
class ProposedPage:
    """One page the journalist proposes: its name, markdown body, and cited source addresses."""

    name: str
    body: str
    sources: "list[str]"


def _parse_json_array(raw: str) -> list:
    """Best-effort extraction of a JSON array from a model reply (tolerates ```json fences and
    surrounding prose). Returns [] on anything unparseable — the autobuilder is best-effort."""
    import json

    if not raw:
        return []
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(raw[start:end + 1])
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def propose_pages(
    existing_names: "list[str]", corpus: str, complete: Complete, *, max_pages: int = 3
) -> "list[ProposedPage]":
    """Ask ``complete`` for up to ``max_pages`` new wiki pages distilled from ``corpus`` (the
    journal's summary + recent entries), avoiding topics already covered by ``existing_names``.
    Pure + injectable; returns validated proposals (empty when nothing is page-worthy). The caller
    (the journalist) decides what to actually write — and writes create-only, never clobbering."""
    raw = complete([
        {"role": "system", "content": _PROPOSE_SYSTEM},
        {"role": "user", "content":
            f"Existing pages (do not duplicate): {', '.join(existing_names) or '(none)'}\n\n"
            f"Recent project activity:\n\n{corpus}"},
    ])
    out: "list[ProposedPage]" = []
    for item in _parse_json_array(raw)[:max_pages]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        body = str(item.get("body", "")).strip()
        if not name or not body:
            continue
        sources = [str(s).strip() for s in (item.get("sources") or []) if str(s).strip()]
        out.append(ProposedPage(name=name, body=body, sources=sources))
    return out


def _first_line_says_verified(raw: str) -> bool:
    head = (raw.splitlines()[0] if raw else "").strip().upper()
    # 'VERIFIED' must be the verdict, not merely appear — guard against 'REFUTED: ... not verified'.
    return head.startswith("VERIFIED") and not head.startswith("REFUTED")


def session_completer(state, *, temperature: float = 0.2) -> "Optional[Complete]":
    """Build a :data:`Complete` from a live session's client pool + conversational model, or
    ``None`` when no model is reachable. The one seam the REPL/CLI wiring uses so the AI steps
    bill and route exactly like the rest of the session."""
    pool = getattr(state, "pool", None)
    cfg = getattr(state, "cfg", None)
    if pool is None or cfg is None:
        return None
    try:
        clients = pool.primary()
        model = cfg.chat()
    except Exception:  # noqa: BLE001
        return None
    if clients is None or not model:
        return None

    def complete(messages: list) -> str:
        resp = clients.chat.chat.completions.create(
            model=model, messages=messages, temperature=temperature,
        )
        try:
            return (resp.choices[0].message.content or "").strip()
        except (AttributeError, IndexError):
            return ""

    return complete


__all__ = ["Complete", "WikiAuthorError", "Verdict", "ProposedPage",
           "distill", "verify", "propose_pages", "session_completer"]
