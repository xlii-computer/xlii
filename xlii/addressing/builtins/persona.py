"""PersonaProvider provider."""
from __future__ import annotations


from xlii.addressing import (
    Address,
    Node,
    Resolution,
    ShellExport,
)


class PersonaProvider:
    """``persona://`` — personas/roles: browse the list, read a persona's prompt.

    ``persona://`` lists every persona (F6 / the roles doorway); ``persona://<name>`` reads that
    persona's system prompt. Read-only — created via ``xlii chat --new`` / F7. *Switching to* a
    persona is a surface action (a later slice); this provider is the browse address. The
    bootstrap/last-used policy stays in ``_resolve_persona_to_load``.
    """

    scheme = "persona"

    def resolve(self, address: Address) -> Resolution:
        from xlii.persona import Persona, is_valid_name

        name = address.key.strip()
        if not name:
            return Resolution(ok=True, address=address, kind="persona")  # the browseable root
        if not is_valid_name(name):
            return Resolution(
                ok=False, address=address, kind="persona", reason=f"invalid persona name: {name!r}"
            )
        p = Persona(name)
        exists = p.exists()
        return Resolution(
            ok=exists, address=address, handle=p, kind="persona",
            reason="" if exists else "persona does not exist",
        )

    def stat(self, address: Address) -> Node:
        name = address.key.strip()
        return Node(address=str(address), name=name or "persona",
                    kind="container" if not name else "leaf", extra={"type": "persona"})

    def list(self, address: Address) -> "list[Node]":
        if address.key.strip():
            return []
        from xlii.persona import list_personas

        return [
            Node(address=f"persona://{p.name}", name=p.name, kind="leaf", extra={"type": "persona"})
            for p in list_personas()
        ]

    def read(self, address: Address) -> bytes:
        from xlii.persona import Persona

        name = address.key.strip()
        if not name:
            raise IsADirectoryError("persona://: a personas root — use ls")
        p = Persona(name)
        if not p.exists():
            raise FileNotFoundError(f"persona://{name}: no such persona")
        return p.prompt_path.read_bytes()

    def exists(self, address: Address) -> bool:
        from xlii.persona import Persona

        name = address.key.strip()
        return True if not name else Persona(name).exists()

    def shell_export(self, address: Address) -> ShellExport:
        from xlii.persona import PERSONAS_DIR, Persona, is_valid_name

        name = address.key.strip()
        if not name:
            if not PERSONAS_DIR.is_dir():
                raise FileNotFoundError("persona://: no personas yet")
            return ShellExport(kind="path", path=PERSONAS_DIR)
        if not is_valid_name(name):
            raise ValueError(f"persona://{name}: invalid persona name")
        p = Persona(name)
        if not p.exists():
            raise FileNotFoundError(f"persona://{name}: no such persona")
        return ShellExport(kind="path", path=p.prompt_path)


