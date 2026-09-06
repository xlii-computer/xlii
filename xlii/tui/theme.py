"""Compatibility shim — the Theme palette moved to :mod:`xlii.theme`
(godzilla-mothra V1ab: pure presentation *data* — style strings and icons with
zero rich/textual imports — is kernel-shaped; the rich renderers stay here in
the face). The names below are the same objects. New kernel code imports from
``xlii.theme``; this shim keeps untouched face call sites working until the
Stage-1.5 sweep deletes it.
"""

from __future__ import annotations

from xlii.theme import THEME, Theme

__all__ = ["THEME", "Theme"]
