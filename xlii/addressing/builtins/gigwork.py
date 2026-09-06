"""GigworkProvider provider."""
from __future__ import annotations

from typing import Any

from xlii.addressing import (
    Address,
    Node,
    Resolution,
    ShellExport,
)


def ambient_gig_cfg() -> Any:
    """The config the gigwork surfaces read: the live agent's (mirrored on
    every /gigwork · /jam write) through the ambient session, else the
    persisted global config (headless / no session)."""
    from xlii.active_session import active_session

    state = active_session()
    agent = getattr(state, "agent", None) if state is not None else None
    cfg = getattr(agent, "cfg", None)
    if cfg is not None:
        return cfg
    from xlii.config import GlobalConfig

    return GlobalConfig.load()


class GigworkProvider:
    """``gigwork://`` — configured gig providers + jam presets as a browseable list (the
    Panel-menu "Gigwork" doorway's destination).

    ``gigwork://`` lists every configured provider (``gigwork://provider/<name>``) and every
    stock + configured jam (``gigwork://jam/<name>``); a leaf reads as a dry detail
    sheet (model, endpoint, key state, allow state, cache marks / members, merge, cap) so
    "view" works and the sheet grabs as AI context. Read-only through the VFS — mutation is
    the ``/gigwork`` and ``/jam`` commands' job (the pane seeds them, review-before-run).
    Bad gigwork config lists empty rather than erroring (the graceful-empty rule); the
    commands carry the actionable message.
    """

    scheme = "gigwork"

    def resolve(self, address: Address) -> Resolution:
        kind, name = self._split(address)
        if not kind:
            return Resolution(ok=True, address=address, kind="gigwork")  # the browseable root
        if kind not in ("provider", "jam"):
            return Resolution(ok=False, address=address, kind="gigwork",
                              reason=f"no gigwork://{kind} — provider/<name> or jam/<name>")
        ok = self._has(kind, name)
        return Resolution(ok=ok, address=address, kind="gigwork",
                          reason="" if ok else f"no configured {kind} {name!r}")

    def stat(self, address: Address) -> Node:
        kind, name = self._split(address)
        return Node(address=str(address), name=name or "gigwork",
                    kind="container" if not kind else "leaf",
                    extra={"type": "gig-provider" if kind == "provider" else
                           ("jam" if kind == "jam" else "gigwork")})

    def list(self, address: Address) -> "list[Node]":
        if address.target.strip():
            return []
        out: "list[Node]" = []
        for name in sorted(self._providers()):
            out.append(Node(address=f"gigwork://provider/{name}", name=name,
                            kind="leaf", extra={"type": "gig-provider"}))
        for name in sorted(self._jams()):
            out.append(Node(address=f"gigwork://jam/{name}", name=name,
                            kind="leaf", extra={"type": "jam"}))
        return out

    def read(self, address: Address) -> bytes:
        kind, name = self._split(address)
        if not kind:
            raise IsADirectoryError("gigwork://: a gigwork root — use ls")
        if kind == "provider":
            return self._read_provider(name)
        if kind == "jam":
            return self._read_jam(name)
        raise FileNotFoundError(f"gigwork://{kind}: provider/<name> or jam/<name>")

    def exists(self, address: Address) -> bool:
        kind, name = self._split(address)
        return True if not kind else self._has(kind, name)

    def shell_export(self, address: Address) -> ShellExport:
        # Providers and jams are config entries, not files — address-kind only.
        return ShellExport(kind="address")

    # --- internals -----------------------------------------------------------

    @staticmethod
    def _split(address: Address) -> "tuple[str, str]":
        return (address.key.strip(), address.subpath.strip())

    def _has(self, kind: str, name: str) -> bool:
        if not name:
            return False
        pool = self._providers() if kind == "provider" else self._jams()
        return name in pool

    @staticmethod
    def _providers() -> "dict[str, Any]":
        from xlii.chat_backend import GigError, gig_providers

        try:
            return gig_providers(ambient_gig_cfg())
        except GigError:
            return {}

    @staticmethod
    def _jams() -> "dict[str, Any]":
        from xlii.chat_backend import GigError
        from xlii.jam import jam_specs

        try:
            return jam_specs(ambient_gig_cfg())
        except GigError:
            return {}

    def _read_provider(self, name: str) -> bytes:
        from xlii.chat_backend import gig_allowlist

        p = self._providers().get(name)
        if p is None:
            raise FileNotFoundError(f"gigwork://provider/{name}: no configured provider")
        cfg = ambient_gig_cfg()
        if p.key_optional:
            key = "none needed (local endpoint)" if p.is_local else "none needed"
        else:
            key = f"${p.api_key_env} ({'set' if p.key_set else 'unset'})"
        agent = ("hireable via dispatch (on gigwork.defaults.allow)"
                 if name in gig_allowlist(cfg) else "not agent-hireable (/gigwork only)")
        lines = [
            f"# gig provider: {name}",
            f"model:     {p.model}",
            f"endpoint:  {p.base_url}",
            f"key:       {key}",
            f"agent:     {agent}",
            f"cache:     {'marks on' if p.cache_effective else 'no marks'} ({p.cache})",
        ]
        if p.temperature is not None:
            lines.append(f"temperature: pinned {p.temperature}")
        lines.append(f"hire:      /gigwork {name} <task>")
        return ("\n".join(lines) + "\n").encode()

    def _read_jam(self, name: str) -> bytes:
        from xlii.jam import STOCK_JAMS, _user_jams

        s = self._jams().get(name)
        if s is None:
            raise FileNotFoundError(f"gigwork://jam/{name}: no such jam")
        cfg = ambient_gig_cfg()
        origin = "config" if name in _user_jams(cfg) else "stock"
        if origin == "config" and name in STOCK_JAMS:
            origin = "config (shadows stock)"
        tokens = " ".join(m.token for m in s.members)
        lines = [
            f"# jam: {name} ({origin})",
            f"members:   {' + '.join(m.label for m in s.members)}",
            f"merge:     {s.merge}",
            f"parallel:  ≤{s.max_parallel}",
            f"run:       /jam {name} <question>",
            f"edit:      /jam add {name} {tokens} --merge {s.merge} --cap {s.max_parallel}",
        ]
        return ("\n".join(lines) + "\n").encode()
