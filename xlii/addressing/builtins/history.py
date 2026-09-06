"""``history://`` — input-line scrollback (``.xlii/repl_history``).

Browse-only scheme for the HistoryPane / face side dock / TUI panel. Lines are
newest-first; selection prefills via the pane layer (never executes).
"""

from __future__ import annotations

from xlii.addressing import Address, Node, Resolution, ShellExport


class HistoryProvider:
    """``history://`` root is a container of submitted input lines."""

    scheme = "history"

    def resolve(self, address: Address) -> Resolution:
        return Resolution(ok=True, address=address, kind="history")

    def stat(self, address: Address) -> Node:
        target = address.target.strip()
        if not target:
            return Node(
                address="history://",
                name="history",
                kind="container",
                extra={"type": "history"},
            )
        return Node(
            address=f"history://{target}",
            name=target,
            kind="leaf",
            extra={"type": "history-line"},
        )

    def exists(self, address: Address) -> bool:
        target = address.target.strip()
        if not target:
            return True
        try:
            idx = int(target.split("/", 1)[0])
        except ValueError:
            return False
        return 0 <= idx < len(self.list(Address(scheme="history")))

    def list(self, address: Address) -> list[Node]:
        del address
        from xlii.active_session import active_session
        from xlii.repl_history_util import load_repl_history_strings

        state = active_session()
        xli = getattr(getattr(state, "project", None), "xli_dir", None) if state else None
        lines = load_repl_history_strings(xli) if xli else []
        out: list[Node] = []
        for i, line in enumerate(lines):
            preview = (line or "").replace("\n", " ").strip()
            if len(preview) > 72:
                preview = preview[:71] + "…"
            out.append(Node(
                address=f"history://{i}",
                name=preview or f"(line {i})",
                kind="leaf",
                extra={"type": "history-line", "index": i, "text": line},
            ))
        return out

    def read(self, address: Address) -> bytes:
        idx_s = address.target.strip()
        if not idx_s:
            raise IsADirectoryError("history://: list of input lines — use ls")
        try:
            idx = int(idx_s.split("/", 1)[0])
        except ValueError as e:
            raise FileNotFoundError(f"history://{idx_s}") from e
        nodes = self.list(Address(scheme="history"))
        if idx < 0 or idx >= len(nodes):
            raise FileNotFoundError(f"history://{idx}")
        text = nodes[idx].extra.get("text") or ""
        return str(text).encode("utf-8")

    def shell_export(self, address: Address) -> ShellExport:
        target = address.target.strip()
        if not target:
            return ShellExport(kind="address")
        return ShellExport(kind="content", content=self.read(address), suffix=".txt")
