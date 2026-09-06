"""ResultsProvider provider."""
from __future__ import annotations

import json
from pathlib import Path

from xlii.addressing import (
    Address,
    Node,
    Resolution,
    ShellExport,
)


class ResultsProvider:
    """``results://`` — stored provider runs (``.xlii/provider-results/``) as a
    browseable tree (typed-workbenches F2: the provider results viewer).

    ``results://`` lists every provider with a stored ``latest.json`` (one
    container per provider); ``results://<name>`` reads the latest run's
    records (the table pane renders them). Read-only through the VFS — runs
    are written by the runner (`xlii.providers`), never through here. Reaches
    the project through the ambient session (``active_xli_dir``); outside a
    project it lists empty."""

    scheme = "results"

    @staticmethod
    def _dir() -> "Path | None":
        from xlii.active_session import active_xli_dir

        xli = active_xli_dir()
        return (Path(xli) / "provider-results") if xli else None

    def _latest(self, name: str) -> "Path | None":
        if not self._valid_provider(name):
            return None
        base = self._dir()
        if base is None:
            return None
        if (
            not name
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
            or any(part in {".", ".."} for part in Path(name).parts)
        ):
            return None
        p = base / name / "latest.json"
        return p if p.is_file() else None

    @staticmethod
    def _valid_provider(name: str) -> bool:
        name = name.strip()
        if not name or name in {".", ".."}:
            return False
        p = Path(name)
        return not p.is_absolute() and "/" not in name and "\\" not in name

    def _has_record(self, provider: str, idx: str) -> bool:
        latest = self._latest(provider)
        if latest is None:
            return False
        try:
            i = int(idx)
        except ValueError:
            return False
        try:
            data = json.loads(latest.read_text())
        except (json.JSONDecodeError, OSError):
            return False
        records = data.get("records", [])
        return 0 <= i < len(records)

    def _providers(self) -> "list[str]":
        base = self._dir()
        if base is None or not base.is_dir():
            return []
        return sorted(
            d.name for d in base.iterdir()
            if d.is_dir() and (d / "latest.json").is_file()
        )

    def resolve(self, address: Address) -> Resolution:
        name = address.target.strip()
        if not name:
            return Resolution(ok=True, address=address, kind="results")
        provider, _, subpath = name.partition("/")
        ok = self._latest(provider) is not None if not subpath else self._has_record(provider, subpath)
        return Resolution(ok=ok, address=address, kind="results",
                          reason="" if ok else f"no stored run for {name!r}")

    def stat(self, address: Address) -> Node:
        name = address.target.strip()
        kind = "container" if "/" not in name else "leaf"
        return Node(address=str(address), name=name or "results",
                    kind=kind, extra={"type": "results"})

    def exists(self, address: Address) -> bool:
        name = address.target.strip()
        if not name:
            return True
        provider, _, subpath = name.partition("/")
        return self._latest(provider) is not None if not subpath else self._has_record(provider, subpath)

    def list(self, address: Address) -> "list[Node]":
        name = address.target.strip()
        if not name:
            return [
                Node(address=f"results://{p}", name=p, kind="container",
                     extra={"type": "results"})
                for p in self._providers()
            ]
        if "/" in name:
            return []
        latest = self._latest(name)
        if latest is None:
            return []
        try:
            data = json.loads(latest.read_text())
        except (json.JSONDecodeError, OSError):
            return []
        # Records as addressable leaves — one per row, index-addressed.
        return [
            Node(address=f"results://{name}/{i}", name=str(i), kind="leaf",
                 extra={"type": "record"})
            for i in range(len(data.get("records", [])))
        ]

    def read(self, address: Address) -> bytes:
        name = address.target.strip()
        if not name:
            raise IsADirectoryError("results:// is a list, not a file")
        provider, _, idx = name.partition("/")
        latest = self._latest(provider)
        if latest is None:
            raise FileNotFoundError(f"results://{provider}: no stored run")
        data = json.loads(latest.read_text())
        if idx:
            records = data.get("records", [])
            i = int(idx)
            if not (0 <= i < len(records)):
                raise FileNotFoundError(f"results://{name}: no such record")
            return (json.dumps(records[i], indent=2) + "\n").encode()
        return latest.read_bytes()

    def shell_export(self, address: Address) -> ShellExport:
        name = address.target.strip()
        if not name:
            return ShellExport(kind="address")  # the root is a virtual list
        provider = name.partition("/")[0]
        latest = self._latest(provider)
        if latest is None:
            raise FileNotFoundError(f"results://{provider}: no stored run")
        return ShellExport(kind="path", path=latest)
