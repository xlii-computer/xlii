"""``WikiPane`` — semantic-memory pages, over ``wiki://`` (project) or ``xwiki://`` (vendor).

One row per page. **Project scope** (``wiki://``): a trust marker (``✓`` verified · ``?``
unverified — write → refute → promote, worn in the UI); a page currently **riding the turn**
(attached via the ``wiki:`` /doc channel) gets the green ● accent dot. **Vendor scope**
(``xwiki://`` — xlii's shipped self-docs): every row wears ``⌂`` instead — shipped/read-only,
version-accurate by construction, so the trust ladder doesn't apply. Tiers never merge: a
mounted pane serves exactly one scope, named in its title. Up/Down move the selection; the
primary action views the page, attach/detach ride/unride it.
"""

from __future__ import annotations

from typing import Optional

from xlii.addressing import Address, Node
from xlii.panes import ATTACH, DETACH, RETARGET_SLOT, Action, Outcome, Rendered, RenderedRow, Selection
from xlii.panes.select import select_target

_VERIFIED_MARK = "✓"
_UNVERIFIED_MARK = "?"
_VENDOR_MARK = "⌂"


def _store_for(scheme: str):
    """The xli_dir-shaped root a scheme reads: the ambient project (``wiki``) or the
    shipped bundle (``xwiki``). ``None`` → the pane lists empty."""
    if scheme == "xwiki":
        from xlii.selfwiki import selfwiki_root

        return selfwiki_root()
    from xlii.active_session import active_xli_dir

    return active_xli_dir()


class WikiPane:
    """A read-only list of wiki pages: trust marker + name, green-dotting the riding ones."""

    def __init__(self, address: "str | Address | None" = None) -> None:
        self._address: Address = Address(scheme="wiki")
        self._pages: list = []           # list[WikiPage] — name-sorted, or rank-ordered in results mode
        self._query: str = ""            # non-empty in results mode (wiki://?q=…): the search terms
        self._sel: int = 0
        if address is not None:
            self.mount(address)

    @property
    def address(self) -> Address:
        return self._address

    def mount(self, address: "str | Address", *, select: Optional[str] = None) -> None:
        from xlii.wiki import list_pages, search_pages

        self._address = address if isinstance(address, Address) else Address.parse(address)
        d = _store_for(self._address.scheme)
        self._query = (self._address.query or {}).get("q", "").strip()
        if self._query and d is not None:
            # Results mode: the ranked hits for the query, in rank order (the
            # "results page" of the wiki browser — /askjo wiki and /howto wiki
            # open this, each against its own scope).
            order = [name for name, *_ in search_pages(d, self._query, limit=20)]
            by_name = {p.name: p for p in list_pages(d)}
            self._pages = [by_name[n] for n in order if n in by_name]
        else:
            self._pages = list_pages(d) if d is not None else []
        self._sel = 0
        target = select_target(select, self._address)
        if target:
            for i, p in enumerate(self._pages):
                if p.name == target:
                    self._sel = i
                    break

    def _scheme(self) -> str:
        return self._address.scheme or "wiki"

    def _mark(self, p) -> str:
        if self._scheme() == "xwiki":
            return _VENDOR_MARK  # shipped/read-only — the trust ladder doesn't apply
        return _VERIFIED_MARK if p.verified else _UNVERIFIED_MARK

    def render(self) -> Rendered:
        scheme = self._scheme()
        riding = self._riding_names() if scheme == "wiki" else set()
        rows = [
            RenderedRow(
                text=f"{self._mark(p)} {p.name}",
                address=f"{scheme}://{p.name}", kind="leaf",
                selected=(i == self._sel), accent=(p.name in riding),
            )
            for i, p in enumerate(self._pages)
        ]
        title = f'{scheme}:// · "{self._query}"' if self._query else f"{scheme}://"
        return Rendered(title=title, rows=tuple(rows), empty=not rows)

    def selection(self) -> Selection:
        if not self._pages:
            return Selection(node=None)
        scheme = self._scheme()
        p = self._pages[self._sel]
        return Selection(node=Node(address=f"{scheme}://{p.name}", name=p.name, kind="leaf",
                                   extra={"type": scheme, "verified": p.verified}))

    def actions(self) -> "list[Action]":
        """view / attach / detach over the selected page. ``view`` (first = the Enter default)
        morphs the pane to the page's markdown; attach/detach ride/unride it on the turn. On
        the project scope the green dot follows and unverified pages attach wearing their
        warning banner; vendor pages attach with the shipped-self-doc header instead."""
        if not self._pages:
            return []
        addr = f"{self._scheme()}://{self._pages[self._sel].name}"
        return [
            Action("view", "View", Outcome(RETARGET_SLOT, addr)),
            Action("attach", "Attach", Outcome(ATTACH, addr)),
            Action("detach", "Detach", Outcome(DETACH, addr)),
        ]

    def handle(self, key: str) -> bool:
        if not self._pages:
            return False
        if key == "down":
            self._sel = min(self._sel + 1, len(self._pages) - 1)
            return True
        if key == "up":
            self._sel = max(self._sel - 1, 0)
            return True
        if key == "home":
            self._sel = 0
            return True
        if key == "end":
            self._sel = len(self._pages) - 1
            return True
        return False  # enter/back fall through to the surface (actions / close)

    def select_index(self, i: int) -> bool:
        """Select the page at index ``i`` (a mouse click's target row)."""
        if 0 <= i < len(self._pages):
            self._sel = i
            return True
        return False

    @staticmethod
    def _riding_names() -> "set[str]":
        """Names of wiki pages attached to the live session (the ``wiki:`` /doc channel)."""
        from xlii.active_session import active_session
        from xlii.attach import WIKI_ATTACH_PREFIX

        docs = getattr(active_session(), "attached_docs", None) or []
        return {n[len(WIKI_ATTACH_PREFIX):] for n, _ in docs
                if isinstance(n, str) and n.startswith(WIKI_ATTACH_PREFIX)}
