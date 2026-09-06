"""PlanProvider provider."""
from __future__ import annotations

from xlii.addressing import (
    Address,
    Node,
    Resolution,
    ShellExport,
)


class PlanProvider:
    """``plan://`` — the project's plans (``.xlii/plans/*.md``) as a browseable
    list (the Panel-menu "Plan" doorway's destination).

    ``plan://`` lists the working file (``current``, always first when present)
    plus the christened named plans; ``plan://<name>`` reads that plan's text.
    Read-only through the VFS — plans are written by plan mode / plan_check /
    plan_amend, never by a pane. Reaches the project's ``.xlii`` dir through
    the ambient session (:mod:`xlii.active_session`); outside a project it
    lists empty. Nodes carry ``type=plan`` so the Panel opens the PlanPane
    (whose actions PREFILL ``/plan …`` commands for review-before-run).
    """

    scheme = "plan"

    def resolve(self, address: Address) -> Resolution:
        name = address.key.strip()
        if not name:
            return Resolution(ok=True, address=address, kind="plan")  # the browseable root
        ok = self._has(name)
        return Resolution(ok=ok, address=address, kind="plan",
                          reason="" if ok else f"no plan {name!r}")

    def stat(self, address: Address) -> Node:
        name = address.key.strip()
        return Node(address=str(address), name=name or "plan",
                    kind="container" if not name else "leaf", extra={"type": "plan"})

    def list(self, address: Address) -> "list[Node]":
        if address.key.strip():
            return []
        return [
            Node(address=f"plan://{name}", name=name, kind="leaf", extra={"type": "plan"})
            for name, _path in self._plan_files()
        ]

    def read(self, address: Address) -> bytes:
        name = address.key.strip()
        if not name:
            raise IsADirectoryError("plan://: a plans root — use ls")
        plans_dir = self._plans_dir()
        if plans_dir is None:
            raise FileNotFoundError("plan://: no active project")
        from xlii.plan_ops import PlanOpError, resolve_plan_file

        try:
            # resolve_plan_file's bare-name + containment rules guard the key.
            f = resolve_plan_file(plans_dir, name)
        except PlanOpError as e:
            raise FileNotFoundError(f"plan://{name}: {e}") from e
        return f.read_bytes()

    def exists(self, address: Address) -> bool:
        name = address.key.strip()
        return True if not name else self._has(name)

    def shell_export(self, address: Address) -> ShellExport:
        plans_dir = self._plans_dir()
        if plans_dir is None:
            raise FileNotFoundError("plan://: no active project")
        name = address.key.strip()
        if not name:
            if not plans_dir.is_dir():
                raise FileNotFoundError("plan://: no plans yet")
            return ShellExport(kind="path", path=plans_dir)
        from xlii.plan_ops import PlanOpError, resolve_plan_file

        try:
            f = resolve_plan_file(plans_dir, name)
        except PlanOpError as e:
            raise FileNotFoundError(f"plan://{name}: {e}") from e
        if not f.is_file():
            raise FileNotFoundError(f"plan://{name}: no such plan")
        return ShellExport(kind="path", path=f)

    def _has(self, name: str) -> bool:
        return any(n == name for n, _p in self._plan_files())

    def _plan_files(self) -> "list[tuple[str, object]]":
        """(name, path) for every plan file — ``current`` first when present,
        then named plans newest-first. Graceful-empty outside a project."""
        plans_dir = self._plans_dir()
        if plans_dir is None or not plans_dir.is_dir():
            return []
        current = []
        named = []
        for p in plans_dir.glob("*.md"):
            if not p.is_file():
                continue
            if p.stem.casefold() == "current":
                current.append((p.stem, p))
            else:
                named.append((p.stem, p))
        named.sort(key=lambda t: t[1].stat().st_mtime, reverse=True)
        return current + named

    def _plans_dir(self):
        xli_dir = self._xli_dir()
        return None if xli_dir is None else xli_dir / "plans"

    @staticmethod
    def _xli_dir():
        from xlii.active_session import active_xli_dir

        return active_xli_dir()
