"""The self-wiki — xlii's own semantic self-docs, shipped with the package.

The **vendor tier** of the two-door knowledge map: ``xwiki://`` serves these
pages read-only in *every* project (constant, version-locked to the installed
build), while ``wiki://`` stays each project's own editable semantic memory.
``/howto`` retrieves from this scope (the tool door); ``/askjo`` from the
project's (the project door). Tiers never merge.

The bundle lives at ``xlii/selfwiki/wiki/*.md`` — an xli_dir-shaped root (the
``wiki/`` nesting), so the whole store/retrieval stack (:mod:`xlii.wiki`,
``WikiProvider(root=…)``, :mod:`xlii.wiki_retrieval`) works on it unchanged.
SOURCE is ``docs/selfwiki/`` (``scripts/bundle_selfwiki.py`` copies + checks).
"""

from __future__ import annotations

from pathlib import Path

_BUNDLE_ROOT = Path(__file__).resolve().parent / "selfwiki"


def selfwiki_root() -> "Path | None":
    """The xli_dir-shaped root whose ``wiki/`` child holds the shipped pages —
    what ``WikiProvider(root=…)`` and ``search_pages`` take. ``None`` when the
    bundle is absent (a source checkout that never ran bundle_selfwiki), so
    callers degrade to "no vendor pages" rather than erroring."""
    return _BUNDLE_ROOT if (_BUNDLE_ROOT / "wiki").is_dir() else None


def has_selfwiki() -> bool:
    return selfwiki_root() is not None


__all__ = ["selfwiki_root", "has_selfwiki"]
