"""``plugins://`` — installed plugin catalog as a browseable list (face/TUI panel)."""

from __future__ import annotations

from xlii.addressing import Address, Node, Resolution, ShellExport


class PluginsProvider:
    """``plugins://`` root lists installed plugins; ``plugins://<id>`` is one plugin.

    Read-only address space. Subscribe/unsubscribe and invoke ride the panes
    layer (same split as skills:// + SkillsPane).
    """

    scheme = "plugins"

    def resolve(self, address: Address) -> Resolution:
        name = address.target.strip().split("/", 1)[0]
        if not name:
            return Resolution(ok=True, address=address, kind="plugins")
        from xlii.plugin import Plugin

        ok = Plugin(id=name).exists()
        return Resolution(
            ok=ok, address=address, kind="plugins",
            reason="" if ok else f"no plugin named {name!r}",
        )

    def stat(self, address: Address) -> Node:
        name = address.target.strip()
        if not name:
            return Node(address="plugins://", name="plugins", kind="container",
                        extra={"type": "plugins"})
        parts = [p for p in name.split("/") if p]
        pid = parts[0]
        # plugins://id/source — the written markdown (View, like tasks://name).
        if len(parts) == 2 and parts[1] == "source":
            return Node(
                address=f"plugins://{pid}/source",
                name=f"{pid}.md",
                kind="leaf",
                extra={"type": "plugin_source"},
            )
        # plugins://id or plugins://id/action — container for the actions pane.
        return Node(
            address=f"plugins://{name}",
            name=pid,
            kind="container",
            extra={"type": "plugin"},
        )

    def list(self, address: Address) -> list[Node]:
        from xlii.plugin import Plugin, list_plugins

        target = address.target.strip()
        if not target:
            return [
                Node(address=f"plugins://{p.id}", name=p.id, kind="leaf",
                     extra={"type": "plugin", "brief": p.description() or ""})
                for p in sorted(list_plugins(), key=lambda x: x.id.lower())
            ]
        # Drill: action names as leaves under plugins://id
        pid = target.split("/", 1)[0]
        p = Plugin(id=pid)
        if not p.exists():
            return []
        try:
            from xlii.plugin_manifest import parse_manifest
            m = parse_manifest(p.read_raw())
        except Exception:
            m = None
        if m is None or not m.actions:
            return []
        return [
            Node(address=f"plugins://{pid}/{a.id}", name=a.id, kind="leaf",
                 extra={"type": "plugin_action", "brief": a.description or ""})
            for a in m.actions
        ]

    def read(self, address: Address) -> bytes:
        name = address.target.strip()
        if not name:
            raise IsADirectoryError("plugins://: catalog root — use ls")
        parts = [p for p in name.split("/") if p]
        from xlii.plugin import Plugin

        pid = parts[0]
        p = Plugin(id=pid)
        if not p.exists():
            raise FileNotFoundError(f"plugins://{pid}: no such plugin")
        # Bare id or reserved /source → the written markdown.
        if len(parts) == 1 or (len(parts) == 2 and parts[1] == "source"):
            return (p.read_raw() or "").encode()
        # action — short description
        from xlii.plugin_manifest import parse_manifest

        aid = parts[1]
        m = parse_manifest(p.read_raw())
        if m is None:
            raise FileNotFoundError(f"plugins://{name}")
        a = m.get_action(aid)
        if a is None:
            raise FileNotFoundError(f"plugins://{name}")
        return (a.description or aid).encode()

    def exists(self, address: Address) -> bool:
        name = address.target.strip()
        if not name:
            return True
        from xlii.plugin import Plugin
        pid = name.split("/", 1)[0]
        return Plugin(id=pid).exists()

    def shell_export(self, address: Address) -> ShellExport:
        return ShellExport(kind="address")
