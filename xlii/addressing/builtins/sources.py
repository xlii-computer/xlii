"""SourcesProvider provider."""
from __future__ import annotations

from pathlib import Path

from xlii.addressing import (
    Address,
    Node,
    Resolution,
    ShellExport,
)

_DIR = "sources"  # under .xlii/


class SourcesProvider:
    """``sources://`` — the project's typed source registry (typed-workbenches
    F3; the research row's fifth pane, argus's sources/ idea).

    One TOML card per source in ``.xlii/sources/*.toml`` — person, informant,
    rss-feed, api, filing, dataset… human- or agent-authored (the
    BYO-data idiom: cards are data, like provider manifests)::

        name = "Jane Doe"
        type = "person"            # person | informant | rss-feed | api | filing | …
        url = "https://…"          # optional
        notes = "covers the port authority beat"
        reliability = "high"       # optional, free text

    ``sources://`` lists every card; ``sources://<name>`` reads it. Read-only
    through the VFS — cards are authored by the agent or by hand, never
    through the pane. Reaches the project through the ambient session;
    outside a project it lists empty."""

    scheme = "sources"

    @staticmethod
    def _dir() -> "Path | None":
        from xlii.active_session import active_xli_dir

        xli = active_xli_dir()
        return (Path(xli) / _DIR) if xli else None

    def _cards(self) -> "list[Path]":
        d = self._dir()
        if d is None or not d.is_dir():
            return []
        return sorted(d.glob("*.toml"))

    @staticmethod
    def _slug(path: Path) -> str:
        return path.stem

    def resolve(self, address: Address) -> Resolution:
        name = address.target.strip()
        if not name:
            return Resolution(ok=True, address=address, kind="sources")
        ok = any(self._slug(p) == name for p in self._cards())
        return Resolution(ok=ok, address=address, kind="sources",
                          reason="" if ok else f"no source {name!r}")

    def stat(self, address: Address) -> Node:
        name = address.target.strip()
        return Node(address=str(address), name=name or "sources",
                    kind="container" if not name else "leaf",
                    extra={"type": "source"})

    def exists(self, address: Address) -> bool:
        name = address.target.strip()
        if not name:
            return bool(self._cards())
        return any(self._slug(p) == name for p in self._cards())

    def list(self, address: Address) -> "list[Node]":
        import tomllib

        nodes = []
        for p in self._cards():
            stype = "source"
            try:
                stype = tomllib.loads(p.read_text()).get("type", "source")
            except Exception:
                # A malformed card keeps the default "source" type set above.
                pass
            nodes.append(Node(address=f"sources://{self._slug(p)}",
                              name=self._slug(p), kind="leaf",
                              extra={"type": "source", "source_type": stype}))
        return nodes

    def read(self, address: Address) -> bytes:
        name = address.target.strip()
        for p in self._cards():
            if self._slug(p) == name:
                return p.read_bytes()
        raise FileNotFoundError(f"sources://{name}: no such source")

    def shell_export(self, address: Address) -> ShellExport:
        name = address.target.strip()
        if not name:
            return ShellExport(kind="address")  # the root spans every card
        for p in self._cards():
            if self._slug(p) == name:
                return ShellExport(kind="path", path=p)
        raise FileNotFoundError(f"sources://{name}: no such source")
