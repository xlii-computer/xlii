"""ProjectProvider provider."""
from __future__ import annotations

from pathlib import Path

from xlii import atomicio
from xlii.addressing import (
    Address,
    Node,
    Resolution,
    ShellExport,
)

from xlii.addressing.builtins._helpers import (
    _fs_node,
    _fs_list,
    _safe_join,
    _fs_delete,
    _project_root_from_key,
)

class ProjectProvider:
    """``project://<.|path|name>`` — a project by cwd, root path, or registry name.

    ``resolve`` is path-capable (the commands pass single-token args). The VFS surface uses
    the *key* (``.`` or a registry name) as the project id and the *subpath* as the path
    inside it. This is the single home for the cwd/path/name sniff formerly duplicated in
    ``_resolve_project_target`` and ``_resolve_rm_target``.
    """

    scheme = "project"

    def resolve(self, address: Address) -> Resolution:
        from xlii.project_resolver import resolve_registered_project

        tok = (address.target or ".").strip()
        if tok in (".", ""):
            return self._from_root(address, Path.cwd().resolve())
        p = Path(tok).expanduser()
        if "/" in tok or tok.startswith((".", "~")) or p.is_dir():
            return self._from_root(address, p.resolve())
        res = resolve_registered_project(tok)
        return Resolution(
            ok=res.ok,
            address=address,
            handle=res.project,
            kind="project",
            path=res.path,
            matches=tuple(res.matches),
            reason=res.reason or "",
            detail=res,
        )

    def _from_root(self, address: Address, root: Path) -> Resolution:
        from xlii.config import ProjectConfig

        proj = ProjectConfig.load(root)
        return Resolution(
            ok=proj is not None,
            address=address,
            handle=proj,
            kind="project",
            path=root,
            reason="" if proj is not None else "not an xlii project",
        )

    # VFS — key = project id (name|.), subpath = path within the project root.

    def stat(self, address: Address) -> Node:
        root, target = self._target(address)
        return _fs_node(target, self._addr(address.key, root, target))

    def list(self, address: Address) -> "list[Node]":
        root, target = self._target(address)
        return _fs_list(target, lambda c: self._addr(address.key, root, c))

    def read(self, address: Address) -> bytes:
        _, target = self._target(address)
        return target.read_bytes()

    def exists(self, address: Address) -> bool:
        try:
            _, target = self._target(address)
        except (FileNotFoundError, ValueError):
            return False
        return target.exists()

    def write(self, address: Address, data: bytes) -> None:
        _, target = self._target(address)
        atomicio.write_bytes_atomic(target, data, mode=0o644)

    def delete(self, address: Address, recursive: bool = False) -> None:
        _, target = self._target(address)
        _fs_delete(target, recursive)

    def mkdir(self, address: Address) -> None:
        _, target = self._target(address)
        target.mkdir(parents=True, exist_ok=False)

    def shell_export(self, address: Address) -> ShellExport:
        # The VFS grammar (key = project id, subpath = path inside it) — resolve()'s
        # path-capable sniff can't address a file inside a NAMED project, and its
        # Resolution.path is the root, never the leaf. _safe_join refuses escapes.
        _root, target = self._target(address)
        return ShellExport(kind="path", path=target)

    def _target(self, address: Address) -> "tuple[Path, Path]":
        root = _project_root_from_key(address.key)
        if root is None:
            raise FileNotFoundError(f"project://{address.key}: no such project")
        return root, _safe_join(root, address.subpath)

    @staticmethod
    def _addr(key: str, root: Path, child: Path) -> str:
        rel = child.relative_to(root)
        return f"project://{key}" if str(rel) == "." else f"project://{key}/{rel}"


