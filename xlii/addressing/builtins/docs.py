"""DocsProvider provider."""
from __future__ import annotations


from xlii.addressing import (
    Address,
    Node,
    Resolution,
    ShellExport,
)


class DocsProvider:
    """``docs://`` — the reference docs (``~/.config/xlii/docs``) as a browseable list.

    ``docs://`` lists every doc; ``docs://<name>`` reads its markdown. Browseable read-only —
    a chip/F-key doorway opens ``docs://`` in a pane and selecting a doc reads it; docs are
    created/edited via ``/doc`` and ``xlii doc --new`` (not through ``cp``). Nodes carry
    ``type=doc`` so they bucket under the ``docs`` chip.
    """

    scheme = "docs"

    def resolve(self, address: Address) -> Resolution:
        name = address.target.strip()
        if not name:
            return Resolution(ok=True, address=address, kind="docs")
        from xlii.doc import Doc

        ok = Doc(name).exists()
        return Resolution(
            ok=ok, address=address, kind="docs", reason="" if ok else f"no doc named {name!r}"
        )

    def stat(self, address: Address) -> Node:
        name = address.target.strip()
        if not name:
            return Node(address="docs://", name="docs", kind="container")
        from xlii.doc import Doc

        return Node(address=str(address), name=name, kind="leaf", size=Doc(name).size_bytes(),
                    extra={"type": "doc"})

    def list(self, address: Address) -> "list[Node]":
        if address.target.strip():
            return []  # a single doc has no children
        from xlii.doc import list_docs

        return [
            Node(address=f"docs://{d.name}", name=d.name, kind="leaf", size=d.size_bytes(),
                 extra={"type": "doc"})
            for d in list_docs()
        ]

    def read(self, address: Address) -> bytes:
        name = address.target.strip()
        if not name:
            raise IsADirectoryError("docs://: a doc store root — use ls")
        from xlii.doc import Doc

        d = Doc(name)
        if not d.exists():
            raise FileNotFoundError(f"docs://{name}: no such doc")
        return d.read().encode()

    def exists(self, address: Address) -> bool:
        name = address.target.strip()
        if not name:
            return True
        from xlii.doc import Doc

        return Doc(name).exists()

    def shell_export(self, address: Address) -> ShellExport:
        # Secretly file-backed: every doc is a real ~/.config/xlii/docs/<name>.md.
        from xlii.doc import DOCS_DIR, Doc

        name = address.target.strip()
        if not name:
            if not DOCS_DIR.is_dir():
                raise FileNotFoundError("docs://: no docs yet")
            return ShellExport(kind="path", path=DOCS_DIR)
        d = Doc(name)
        if not d.exists():
            raise FileNotFoundError(f"docs://{name}: no such doc")
        return ShellExport(kind="path", path=d.path)

