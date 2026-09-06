"""ConvProvider provider."""
from __future__ import annotations

from pathlib import Path

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
    _project_root_from_key,
)
from xlii.conversation import INFLIGHT_LEAF


def _live_conv_for(turns_dir: Path):
    """Ambient session Conversation when it owns this turns_dir, else None."""
    try:
        from xlii.active_session import active_session
        from xlii.conversation import ensure_conversation
    except Exception:
        return None
    state = active_session()
    if state is None:
        return None
    conv = ensure_conversation(state)
    if conv is None:
        return None
    try:
        if Path(conv.turns_dir).resolve() != Path(turns_dir).resolve():
            return None
    except Exception:
        return None
    return conv


def _inflight_markdown(conv) -> bytes:
    infl = conv.in_flight
    if infl is None:
        return b""
    body = (
        f"# {infl.started_at or 'in-flight'}\n\n"
        f"## user\n{infl.user}\n\n"
        f"## assistant\n{infl.assistant_so_far}\n"
    )
    return body.encode("utf-8")


def _inflight_node(key: str, conv) -> Node:
    infl = conv.in_flight
    addr = f"conv://{key}/{INFLIGHT_LEAF}"
    preview = (infl.assistant_so_far if infl else "") or ""
    return Node(
        address=addr,
        name=INFLIGHT_LEAF,
        kind="leaf",
        size=len(preview.encode("utf-8")),
        extra={"live": True, "type": "turn", "mtime_ns": None},
    )


class ConvProvider:
    """``conv://<.|name>/<turn>`` — a project's conversation (its ``.xlii/turns/`` dir).

    Browseable read-only: you list and read turns, but don't write them through ``cp``.
    When the ambient session has an in-flight turn for this turns_dir, a synthetic
    ``__inflight__.md`` leaf is listed (Phase 6 live streaming into Conversation).
    """

    scheme = "conv"

    def resolve(self, address: Address) -> Resolution:
        td = self._turns_dir(address.key)
        ok = td is not None and td.is_dir()
        if not ok and address.subpath == INFLIGHT_LEAF:
            # In-flight leaf can resolve even when the turns dir is empty/missing
            # as long as the ambient Conversation owns this project key.
            conv = _live_conv_for(td) if td is not None else None
            if conv is not None and conv.has_in_flight():
                ok = True
        return Resolution(
            ok=ok,
            address=address,
            handle=td,
            kind="conv",
            path=td,
            reason="" if ok else "no conversation (turns) for this project",
        )

    def stat(self, address: Address) -> Node:
        td, target = self._target(address)
        if address.subpath == INFLIGHT_LEAF or target.name == INFLIGHT_LEAF:
            conv = _live_conv_for(td)
            if conv is not None and conv.has_in_flight():
                return _inflight_node(address.key, conv)
            raise FileNotFoundError(str(address))
        return _fs_node(target, self._addr(address.key, td, target))

    def list(self, address: Address) -> "list[Node]":
        td, target = self._target(address)
        nodes = _fs_list(target, lambda c: self._addr(address.key, td, c))
        # Live in-flight leaf only on the conversation root (not nested dirs).
        if target == td:
            conv = _live_conv_for(td)
            if conv is not None and conv.has_in_flight():
                nodes = [n for n in nodes if n.name != INFLIGHT_LEAF]
                nodes.append(_inflight_node(address.key, conv))
        return nodes

    def read(self, address: Address) -> bytes:
        td, target = self._target(address)
        if address.subpath == INFLIGHT_LEAF or target.name == INFLIGHT_LEAF:
            conv = _live_conv_for(td)
            if conv is None or not conv.has_in_flight():
                raise FileNotFoundError(str(address))
            return _inflight_markdown(conv)
        return target.read_bytes()

    def exists(self, address: Address) -> bool:
        try:
            td, target = self._target(address)
        except (FileNotFoundError, ValueError):
            return False
        if address.subpath == INFLIGHT_LEAF or target.name == INFLIGHT_LEAF:
            conv = _live_conv_for(td)
            return bool(conv is not None and conv.has_in_flight())
        return target.exists()

    def shell_export(self, address: Address) -> ShellExport:
        td, target = self._target(address)
        if address.subpath == INFLIGHT_LEAF or target.name == INFLIGHT_LEAF:
            # The live turn exists only in the ambient Conversation — snapshot it.
            return ShellExport(kind="content", content=self.read(address), suffix=".md")
        # Resolution.path is the DIR, never the leaf — compute the leaf the same way
        # the VFS ops do. Read-only scheme: a missing target is a miss, never a
        # destination (mirroring resolve()/exists(), unlike writable file://).
        if not target.exists():
            if target == td:
                raise FileNotFoundError(f"{address}: no conversation (turns) for this project")
            raise FileNotFoundError(f"{address}: no such turn")
        return ShellExport(kind="path", path=target)

    @staticmethod
    def _turns_dir(key: str) -> "Path | None":
        from xlii.config import PROJECT_DIR_NAME

        root = _project_root_from_key(key)
        return None if root is None else root / PROJECT_DIR_NAME / "turns"

    def _target(self, address: Address) -> "tuple[Path, Path]":
        td = self._turns_dir(address.key)
        if td is None:
            raise FileNotFoundError(f"conv://{address.key}: no such project")
        return td, _safe_join(td, address.subpath)

    @staticmethod
    def _addr(key: str, td: Path, child: Path) -> str:
        rel = child.relative_to(td)
        return f"conv://{key}" if str(rel) == "." else f"conv://{key}/{rel}"
