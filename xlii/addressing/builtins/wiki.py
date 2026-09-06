"""WikiProvider provider."""
from __future__ import annotations

from pathlib import Path

from xlii.addressing import (
    Address,
    Node,
    Resolution,
    ShellExport,
)


class WikiProvider:
    """``wiki://`` — the semantic-memory pages of a scope's ``.xlii/wiki/``, browseable + writable.

    ``wiki://`` lists every page (nodes carry ``type=wiki``, the page ``title`` as a brief, and
    its ``verified`` trust flag); ``wiki://<name>`` reads the page body, and
    ``wiki://<name>#<section>`` reads **just that heading's span** — the anchor-honoring read
    that makes a wiki section a precise provenance/context unit.

    **Scope is mountable.** The default instance reads the *active project* through the ambient
    session (:mod:`xlii.active_session`); pass ``root=`` a fixed ``.xlii`` dir (or a callable
    returning one) to mount a second scope — e.g. a global ``~/.config/xlii`` wiki-of-xlii — with
    no other change. Architected-for-two, populated-for-one: only the project scope registers
    today. Outside any project the default instance lists empty rather than raising.

    **Write side** (:class:`~xlii.addressing.WritableVfs`): ``write`` sets a page *body*
    (delegating to :func:`xlii.wiki.write_page`) and ``delete`` removes a page. A body edit
    **keeps the page's provenance (``sources:``) but resets ``verified`` to false** — changed
    claims need a fresh skeptical pass (``/wiki verify``). Provenance and the verify→promote step
    are the ``/wiki`` command's job, never smuggled through raw bytes; ``mkdir`` is refused
    because pages are a flat namespace.
    """

    scheme = "wiki"

    def __init__(self, root=None, *, scheme: "str | None" = None, readonly: bool = False):
        # root: None → the active project's .xlii (resolved per call, the default project scope);
        # a Path/str → a fixed .xlii dir; a callable → resolved per call. The scope seam that lets
        # a second wiki-of-xlii mount with no other change — and now does: the ``xwiki://``
        # vendor scope is this class over the shipped bundle (scheme="xwiki", readonly=True).
        # readonly refuses write/delete — vendor pages are regenerated, never edited in place.
        self._root = root
        self._readonly = readonly
        if scheme is not None:
            self.scheme = scheme  # instance override; addresses mint with this scheme

    def resolve(self, address: Address) -> Resolution:
        name = address.key.strip()
        if not name:
            return Resolution(ok=True, address=address, kind="wiki")  # the browseable root
        ok = self._exists(name)
        return Resolution(ok=ok, address=address, kind="wiki",
                          reason="" if ok else f"no wiki page named {name!r}")

    def stat(self, address: Address) -> Node:
        name = address.key.strip()
        if not name:
            # Preserve a ``?q=…`` search query on the round-tripped node address so
            # the Dock mounts WikiPane in results mode (a query is still the root
            # container — an empty ``wiki://`` stats identically to before).
            return Node(address=str(address), name=self.scheme, kind="container")
        extra: dict = {"type": "wiki"}
        d = self._store()
        if d is not None:
            from xlii import wiki as W

            if W.page_exists(d, name):
                page = W.read_page(d, name)
                extra.update({"verified": page.verified, "brief": page.title,
                              "sources": list(page.sources)})
        return Node(address=str(address), name=name, kind="leaf", extra=extra)

    def list(self, address: Address) -> "list[Node]":
        if address.key.strip():
            return []
        d = self._store()
        if d is None:
            return []
        from xlii import wiki as W

        return [
            Node(address=f"{self.scheme}://{p.name}", name=p.name, kind="leaf",
                 extra={"type": "wiki", "verified": p.verified, "brief": p.title,
                        "sources": list(p.sources)})
            for p in W.list_pages(d)
        ]

    def _no_store_reason(self) -> str:
        """Why there's no store to serve: the vendor bundle is absent, or (project
        scope) there's no active project. Wrong words would misdiagnose the miss."""
        if self._readonly:
            return "no shipped self-wiki in this build"
        return "no active project (the wiki lives in .xlii)"

    def read(self, address: Address) -> bytes:
        s = self.scheme
        name = address.key.strip()
        if not name:
            raise IsADirectoryError(f"{s}://: a wiki root — use ls")
        d = self._store()
        if d is None:
            raise FileNotFoundError(f"{s}://{name}: {self._no_store_reason()}")
        from xlii import wiki as W

        if not W.page_exists(d, name):
            raise FileNotFoundError(f"{s}://{name}: no such page")
        page = W.read_page(d, name)
        if address.anchor:
            section = W.extract_section(page.body, address.anchor)
            if section is None:
                raise FileNotFoundError(f"{s}://{name}#{address.anchor}: no such section")
            return section.encode()
        return page.body.encode()

    def exists(self, address: Address) -> bool:
        name = address.key.strip()
        return True if not name else self._exists(name)

    def write(self, address: Address, data: bytes) -> None:
        if self._readonly:
            raise PermissionError(
                f"{self.scheme}:// is the shipped self-wiki — read-only "
                "(regenerated per release, never edited in place)"
            )
        name = self._page_name(address, "write")
        d = self._store()
        if d is None:
            raise FileNotFoundError(f"{self.scheme}://{name}: {self._no_store_reason()}")
        from xlii import wiki as W

        incoming = W.parse_page(name, data.decode())          # tolerant of body-only or full-page
        sources = incoming.sources
        if not sources and W.page_exists(d, name):
            sources = W.read_page(d, name).sources            # a body edit keeps prior provenance
        W.write_page(d, name, incoming.body, sources=sources)  # verified resets — re-run /wiki verify

    def delete(self, address: Address, recursive: bool = False) -> None:
        if self._readonly:
            raise PermissionError(
                f"{self.scheme}:// is the shipped self-wiki — read-only "
                "(regenerated per release, never edited in place)"
            )
        name = self._page_name(address, "delete")
        d = self._store()
        if d is None:
            raise FileNotFoundError(f"{self.scheme}://{name}: {self._no_store_reason()}")
        from xlii import wiki as W

        if not W.delete_page(d, name):
            raise FileNotFoundError(f"{self.scheme}://{name}: no such page")

    def mkdir(self, address: Address) -> None:
        raise NotImplementedError(
            f"{self.scheme}:// pages are a flat namespace — there are no directories"
        )

    def shell_export(self, address: Address) -> ShellExport:
        s = self.scheme
        d = self._store()
        if d is None:
            raise FileNotFoundError(f"{s}://: {self._no_store_reason()}")
        from xlii import wiki as W

        name = address.key.strip()
        if not name:
            if self._readonly:
                # Never hand the shell the installed bundle directory — a writable
                # handle into the read-only store the write/delete guards protect.
                raise NotImplementedError(
                    f"{s}://: the shipped self-wiki exports pages, not its bundle dir"
                )
            wd = W.wiki_dir(d)
            if not wd.is_dir():
                raise FileNotFoundError(f"{s}://: no wiki pages yet")
            return ShellExport(kind="path", path=wd)
        if not W.page_exists(d, name):
            raise FileNotFoundError(f"{s}://{name}: no such page")
        if address.anchor or self._readonly:
            # A section span exists only as a read — materialize the anchor-honoring
            # extract. The read-only scope exports EVERY page as a content snapshot:
            # a real path would let `!$EDITOR <path>` mutate the shipped bundle.
            return ShellExport(kind="content", content=self.read(address), suffix=".md")
        # Caveat: the on-disk page carries frontmatter (sources/verified) that
        # read() strips — a plain path exports slightly more than the VFS view.
        return ShellExport(kind="path", path=W.page_path(d, name))

    def _page_name(self, address: Address, verb: str) -> str:
        from xlii import wiki as W

        name = address.key.strip()
        if not name:
            raise IsADirectoryError(f"{self.scheme}://: a wiki root — {verb} a page, not the root")
        if not W.is_valid_name(name):
            raise ValueError(f"{self.scheme}://{name}: invalid page name (letters/digits/._- only)")
        return name

    def _store(self):
        r = self._root
        if r is None:
            from xlii.active_session import active_xli_dir

            return active_xli_dir()
        return r() if callable(r) else Path(r)

    def _exists(self, name: str) -> bool:
        d = self._store()
        if d is None:
            return False
        from xlii import wiki as W

        return W.page_exists(d, name)


