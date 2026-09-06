"""``taskmake://`` — face/TUI Task+ author pane root."""

from __future__ import annotations

from xlii.addressing import Address, Node, Resolution, ShellExport


class TaskMakeProvider:
    scheme = "taskmake"

    def resolve(self, address: Address) -> Resolution:
        return Resolution(ok=True, address=address, kind="taskmake")

    def stat(self, address: Address) -> Node:
        # Keep the task name. Collapsing every address to taskmake:// made
        # Edit-in-maker mount a blank "echo hello" form.
        raw = address.raw or (
            f"taskmake://{address.target}" if address.target else "taskmake://"
        )
        name = address.key or "task maker"
        return Node(
            address=raw,
            name=name,
            kind="container",
            extra={"type": "taskmake", "task": address.key},
        )

    def exists(self, address: Address) -> bool:
        return True

    def list(self, address: Address) -> list[Node]:
        del address
        return []

    def read(self, address: Address) -> bytes:
        raise IsADirectoryError("taskmake://: open as a panel")

    def shell_export(self, address: Address) -> ShellExport:
        del address
        return ShellExport(kind="address")
