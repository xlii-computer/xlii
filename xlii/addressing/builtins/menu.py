"""MenuProvider provider."""
from __future__ import annotations

from xlii.addressing import (
    Address,
    Node,
    Resolution,
    ShellExport,
)


class MenuProvider:
    """``menu://`` — the REPL's slash commands as a browseable menu
    (typed-workbenches F3; the godzilla-mothra *menus-do-don't-type* rule as
    a scheme). ``menu://`` lists the registered commands (the same registry
    ``/help`` reads), ``menu://<name>`` reads one command's card. The listing
    is scoped to the **active REPL surface** (``code``/``chat``) the way
    dispatch is — chat's ``/status`` and code's ``/status`` are two commands,
    and only the live one is listed. Role commands keep their namespace in the
    address (``menu://architect:review``). Read-only — commands register at
    boot; nothing here mutates them."""

    scheme = "menu"

    @staticmethod
    def _scope() -> str:
        """The active command surface (``code``/``chat``) — the same axis
        ``find_repl_command`` dispatches on. Outside a session: ``code``."""
        from xlii.active_session import active_session

        state = active_session()
        return (getattr(state, "command_scope", None) or "code") if state is not None else "code"

    @classmethod
    def _commands(cls):
        from xlii.commands import iter_repl_commands

        scope = cls._scope()
        try:
            cmds = [c for c in iter_repl_commands() if scope in c.repls]
            if scope == "chat":
                from xlii.mode_contract import get_mode

                policy = get_mode("chat").capabilities.slash_commands
                cmds = [c for c in cmds if policy.permits(c.name)]
            return sorted(cmds, key=lambda c: (c.category, c.name))
        except Exception:
            return []

    @staticmethod
    def _token(cmd) -> str:
        """How the command is typed — role commands keep their namespace
        (``/architect:review``), so the menu address is unique per command."""
        return f"{cmd.role}:{cmd.name}" if cmd.role else cmd.name

    def _find(self, token: str):
        for c in self._commands():
            if self._token(c) == token:
                return c
        return None

    def resolve(self, address: Address) -> Resolution:
        name = address.target.strip().lstrip("/")
        if not name:
            return Resolution(ok=True, address=address, kind="menu")
        ok = self._find(name) is not None
        return Resolution(ok=ok, address=address, kind="menu",
                          reason="" if ok else f"no command /{name}")

    def stat(self, address: Address) -> Node:
        name = address.target.strip().lstrip("/")
        return Node(address=str(address), name=name or "menu",
                    kind="container" if not name else "leaf",
                    extra={"type": "command"})

    def exists(self, address: Address) -> bool:
        name = address.target.strip().lstrip("/")
        return True if not name else self._find(name) is not None

    def list(self, address: Address) -> "list[Node]":
        return [
            Node(address=f"menu://{self._token(c)}", name=self._token(c), kind="leaf",
                 extra={"type": "command", "category": c.category,
                        "usage": c.usage, "description": c.description})
            for c in self._commands()
        ]

    def read(self, address: Address) -> bytes:
        name = address.target.strip().lstrip("/")
        c = self._find(name)
        if c is None:
            raise FileNotFoundError(f"menu://{name}: no such command")
        token = self._token(c)
        lines = [
            f"/{token}",
            f"category: {c.category}",
            f"usage: {c.usage or '/' + token}",
            f"repls: {', '.join(c.repls)}",
            "",
            c.description or "(no description)",
        ]
        return ("\n".join(lines) + "\n").encode()

    def shell_export(self, address: Address) -> ShellExport:
        return ShellExport(kind="address")  # commands are not files
