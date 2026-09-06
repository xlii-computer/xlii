"""Content-class (``type``) facet — a semantic class for a :class:`~xlii.addressing.Node`,
distinct from ``Node.kind`` (container/leaf).

Where ``kind`` answers *"directory or leaf?"*, ``type`` answers *"what kind of thing is it?"* —
``image`` / ``pdf`` / ``doc`` / ``dir`` / ``file`` / … — the facet that (1) picks a view renderer (an
image leaf opens in the image viewer, a PDF in the PDF viewer, not as text) and (2) buckets the tab strip
(``[image 5]`` / ``[docs 2]``). One source of truth feeds both.

It is a small **registry** so new classes are appendages, never core edits: a plugin — or a
later feature like marks/skills — registers ``(name, predicate)`` and every surface that groups
or renders by type picks it up. Resolution order, high to low:

1. an explicit ``node.extra["type"]`` (a provider that already knows its class wins),
2. the registered predicates, in order (``first=True`` to outrank the built-ins),
3. the structural default — ``dir`` for a container, ``file`` for a leaf.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Callable

from xlii.addressing import Node

Classifier = Callable[[Node], bool]

_CONTENT_TYPES: "list[tuple[str, Classifier]]" = []


def register_content_type(name: str, predicate: Classifier, *, first: bool = False) -> None:
    """Register content-class ``name``, recognised by ``predicate(node) -> bool``.

    Re-registering a name replaces it in place. ``first=True`` gives it priority over the
    built-ins (e.g. a scheme-specific class that should outrank an extension guess)."""
    unregister_content_type(name)
    entry = (name, predicate)
    if first:
        _CONTENT_TYPES.insert(0, entry)
    else:
        _CONTENT_TYPES.append(entry)


def unregister_content_type(name: str) -> None:
    """Drop a registered class (no-op if absent) — plugin teardown / test isolation."""
    _CONTENT_TYPES[:] = [(n, p) for (n, p) in _CONTENT_TYPES if n != name]


def content_types() -> "list[str]":
    """Registered class names, in classification order (built-ins first)."""
    return [n for (n, _) in _CONTENT_TYPES]


def classify(node: Node) -> str:
    """The content-class of ``node`` — see the module docstring for the resolution order."""
    explicit = node.extra.get("type") if node.extra else None
    if explicit:
        return str(explicit)
    for name, pred in _CONTENT_TYPES:
        try:
            if pred(node):
                return name
        except Exception:
            continue  # a bad predicate can never break classification
    return "dir" if node.kind == "container" else "file"


def _ext(name: str) -> str:
    return PurePosixPath(name).suffix.lower()


_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico", ".tif", ".tiff"}
_PDF_EXT = {".pdf"}
_DOC_EXT = {".md", ".markdown", ".txt", ".rst", ".org"}


def register_builtin_content_types() -> None:
    """The built-in classes (client #1): image, pdf, and doc, by leaf extension."""
    register_content_type("image", lambda n: n.kind == "leaf" and _ext(n.name) in _IMAGE_EXT)
    register_content_type("pdf", lambda n: n.kind == "leaf" and _ext(n.name) in _PDF_EXT)
    register_content_type("doc", lambda n: n.kind == "leaf" and _ext(n.name) in _DOC_EXT)


register_builtin_content_types()
