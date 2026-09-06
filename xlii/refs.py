"""The attached-refs model — a reference's *target type* (Interaction-II seam #6).

A "ref" is a *named saved turn* (a `/mark`), attached as a live pointer the file
surface (Vector A2) can preview as the marked exchange. Stored entries carry a
``target_type ∈ {collection, bookmark}`` view:

- ``bookmark``   — the ONE live kind: a marked turn as a live pointer.
- ``collection`` — ⚠ tombstone. This used to mean "a persona's xAI Collection,
  fed into search_project" — the passive cross-persona attach BANNED in
  d96c9d35 and banned again in menu-families §5 after the Fold silently
  reactivated it. The vocabulary survives ONLY so :func:`sanitize_refs` can
  recognize legacy persisted entries and drop them; there is deliberately no
  constructor for it, and nothing derives RAG ids from refs anymore
  (``ToolContext.extra_collection_ids`` is deleted).

Why the stored entries stay 2-tuples
-------------------------------------
``SessionState.attached_refs`` is unpacked as ``(name, collection_id)`` in a dozen
places across the codebase (``agent.py``'s RAG call, the loop workers, the judge,
session restore, the toolbar/tab counts). Widening the *stored* tuple to three
elements would break every one of those unpack sites — far outside Vector D's lane.

So storage stays a 2-tuple and the target type is *derived*:

    ("alice", "col-abc123")   → collection ref  (non-empty collection id)
    ("bob:spark", "")         → bookmark ref     (empty collection id)

The empty-id encoding is RAG-inert by construction — nothing reads a collection
id off a ref anymore. This module is the *one* home of that interpretation —
nothing else should hard-code "empty id means bookmark".

This view also accepts a genuine 3-tuple, so if the shared consumers are ever
migrated to store ``(name, target_type, target)`` directly, :func:`ref_view`
already understands them (forward-compatible). Until then, A2's ref-preview
provider dispatches on :func:`ref_target_type` and reads :func:`ref_view`.
"""

from __future__ import annotations

from typing import NamedTuple

# The target_type vocabulary (seam #6). A third party adding a new ref kind adds
# a value here + a preview provider for it (A2's registry) — no edits elsewhere.
COLLECTION = "collection"
BOOKMARK = "bookmark"


class RefView(NamedTuple):
    """The normalized, type-aware view of one attached ref.

    ``name``        — the display label (a persona name, or a mark address).
    ``target_type`` — :data:`COLLECTION` or :data:`BOOKMARK`.
    ``target``      — the resolvable handle: a Collection id (collection) or the
                      mark address to look up (bookmark).
    """

    name: str
    target_type: str
    target: str


def ref_view(entry) -> RefView:
    """Normalize a stored ``attached_refs`` entry into a :class:`RefView`.

    Accepts the live 2-tuple storage (``(name, collection_id)``) and a future
    3-tuple (``(name, target_type, target)``). A 2-tuple with an empty/falsy
    collection id is a bookmark ref; otherwise it is a collection ref.
    """
    if isinstance(entry, RefView):
        return entry
    seq = tuple(entry)
    if len(seq) >= 3:
        name, target_type, target = seq[0], seq[1], seq[2]
        return RefView(str(name), str(target_type), str(target))
    if len(seq) == 2:
        name, target = seq
        if target:
            return RefView(str(name), COLLECTION, str(target))
        return RefView(str(name), BOOKMARK, str(name))
    # Degenerate single-element entry: treat as a nameless collection ref so we
    # never raise while merely *viewing* state.
    name = seq[0] if seq else ""
    return RefView(str(name), COLLECTION, str(name))


def ref_target_type(entry) -> str:
    """The ``target_type`` of a stored ref — the key A2's preview provider
    dispatches on (collection → info card, bookmark → the saved turn)."""
    return ref_view(entry).target_type


def make_bookmark_ref(address: str) -> tuple[str, str]:
    """Build the stored entry for a bookmark ref pointing at a named saved turn.

    The empty collection-id slot is the discriminator (see module docstring) and
    keeps the entry out of the RAG collection set.
    """
    return (address, "")


def sanitize_refs(refs) -> list[tuple[str, str]]:
    """Keep only bookmark refs; drop legacy persona-Collection entries.

    The load-boundary purge (menu-families §5, the d96c9d35 rule): a stored
    non-empty collection id is legacy contamination to drop, never data to
    honor. Old sessions/workspaces/DeepContexts persisted before the ban can
    still carry ``("alice", "col-…")`` entries — they must not re-enter live
    state, where they'd linger in /attach lists and tab counts as dead weight
    (they can no longer reach RAG either way; the plumbing is deleted)."""
    out: list[tuple[str, str]] = []
    for entry in refs or []:
        try:
            v = ref_view(entry)
        except Exception:
            continue
        if v.target_type == BOOKMARK:
            out.append((v.name, ""))
    return out
