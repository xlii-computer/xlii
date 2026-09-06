"""Built-in address providers — thin facade (grades Phase 5).

Implementations: :mod:`xlii.addressing.builtins`.
"""
from __future__ import annotations

from xlii.addressing.builtins.register import register_builtins
from xlii.addressing.builtins.wiki import WikiProvider

__all__ = ["register_builtins", "WikiProvider"]
