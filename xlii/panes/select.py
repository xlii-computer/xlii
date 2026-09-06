"""Restore a pane selection from a full ``scheme://target`` or a bare name.

``FaceDeck._refresh`` remounts every pane with ``select=selection().address``.
Panes that compared that string to a bare name snapped back to row 0 on every
click — the skills panel's "only the top skill stays selected" bug.
"""

from __future__ import annotations

from typing import Optional

from xlii.addressing import Address


def select_target(select: Optional[str], address: "str | Address") -> str:
    """The leaf/name to restore: ``skills://foo`` → ``foo``, else the address target."""
    raw = (select or "").strip()
    if "://" in raw:
        try:
            raw = Address.parse(raw).target.strip()
        except Exception:
            raw = raw.split("://", 1)[-1].strip()
    if raw:
        return raw
    addr = address if isinstance(address, Address) else Address.parse(str(address or ""))
    return (addr.target or "").strip()
