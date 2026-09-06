"""``ExplorerPane`` — client #1 of the pane contract, a file-manager pane over any address.

Mirrors how ``xlii/cmds/vfs.py`` was client #1 of addressing: prove the pane-type contract end
to end against the real VFS, headless, before any TUI wiring. An explorer:

* **mounts** an :class:`~xlii.addressing.Address`,
* **renders** by calling :func:`~xlii.addressing.vfs_list` → :class:`~xlii.addressing.Node`\\ s
  (containers first, already sorted by the provider — no re-sort here),
* **refreshes** by re-listing (re-projection *is* the refresh — there is no cached tree),
* **navigates** by adopting a child ``Node.address`` (every node carries its full
  ``scheme://…`` address, so "go in" is just "make that the pane's address"), or by walking to
  the parent address — parent walks that would leave the session Files root
  (``files_browse_fence``) return ``None``, so Back bottoms out at the project
  tree instead of the host home. This is a view fence, not a shell ``cd``.
* exposes the focused node as its **selection** and offers **actions** (open / open-other) over
  it with bounded outcomes the kernel executes.
* on ``file://`` listings, folders that are xlii desks (registry path or
  ``.xlii/project.json``) render with the green accent dot — scratch Files is
  ``~``, so the desks you can switch into scan as a glance.

The pane's only authoritative state is ``(_address, _selected)``; ``_nodes`` is a recomputed
cache. Rebuild from those two values via :meth:`mount`'s ``select=`` and the projection is
byte-identical — the state-ownership rule, proven by :func:`reconstruct`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from xlii.addressing import Address, Node, supports_vfs, vfs_list, vfs_read
from xlii.panes import (
    ATTACH,
    DETACH,
    NAVIGATE,
    PREFILL,
    RETARGET_SLOT,
    Action,
    Outcome,
    Rendered,
    RenderedRow,
    Selection,
)

# schemes whose target is a path *inside* a keyed entity (key + subpath), so the parent is the
# subpath with its last segment trimmed. ``file`` is handled separately (its whole target is the
# path). Schemes absent here (``xlii``, ``persona``) have no hierarchy → no parent.
_SUBPATH_SCHEMES = frozenset({
    "project", "conv", "config", "git", "map",
    "ftp", "sftp", "dav", "smb",
})
# Filesystem-like listings hide dotfiles by default. Docs/skills/marks do not.
_DOT_SCHEMES = frozenset({"file", "project", "ftp", "sftp", "dav", "smb", "via"})


def _is_dot(name: str) -> bool:
    return bool(name) and name.startswith(".") and name not in (".", "..")


def _hidden_pref() -> bool:
    try:
        from xlii.active_session import active_session

        sess = active_session()
        return bool(getattr(sess, "explorer_show_hidden", False)) if sess is not None else False
    except Exception:
        return False


def _set_hidden_pref(on: bool) -> None:
    try:
        from xlii.active_session import active_session

        sess = active_session()
        if sess is not None:
            sess.explorer_show_hidden = bool(on)
    except Exception:
        # No active session to remember the preference in -- it applies to this view only.
        pass


def _registered_project_roots() -> "set[Path]":
    """Resolved local paths from the project registry. Empty outside a usable registry."""
    try:
        from xlii.registry import Registry

        roots: set[Path] = set()
        for entry in Registry.load().entries:
            raw = (getattr(entry, "path", None) or "").strip()
            if not raw:
                continue
            try:
                p = Path(raw).expanduser().resolve()
            except OSError:
                continue
            if p.is_dir():
                roots.add(p)
        return roots
    except Exception:
        return set()


def _file_path_of(address: str) -> Optional[Path]:
    try:
        addr = Address.parse(address)
    except Exception:
        return None
    if addr.scheme != "file" or not addr.target:
        return None
    try:
        return Path(addr.target).expanduser().resolve()
    except OSError:
        return None


def _is_xlii_project_dir(path: Path) -> bool:
    """True when *path* is an initialized project tree (``.xlii/project.json``)."""
    try:
        from xlii.config import PROJECT_CONFIG_FILE, PROJECT_DIR_NAME

        return (path / PROJECT_DIR_NAME / PROJECT_CONFIG_FILE).is_file()
    except OSError:
        return False


def _known_project_nodes(nodes: tuple[Node, ...], *, scheme: str) -> "set[str]":
    """Addresses of listing rows that are xlii project folders.

    Scratch Files is ``~``: this is the quick scan for desks you can switch into.
    A folder matches when it is in the registry *or* it has ``.xlii/project.json``.
    Remote listings are left alone — the registry is local paths.
    """
    if scheme != "file":
        return set()
    known = _registered_project_roots()
    hits: set[str] = set()
    for n in nodes:
        if n.kind != "container":
            continue
        path = _file_path_of(n.address)
        if path is None:
            continue
        if path in known or _is_xlii_project_dir(path):
            hits.add(n.address)
    return hits


class ExplorerPane:
    """A browseable pane: ``(address, selection) → rendered Nodes`` over the VFS."""

    def __init__(self, address: "str | Address | None" = None) -> None:
        self._address: Address = Address(scheme="")
        self._all: tuple[Node, ...] = ()
        self._nodes: tuple[Node, ...] = ()
        self._selected: str = ""  # the selected node's address, "" when the listing is empty
        self._show_hidden: bool = _hidden_pref()
        if address is not None:
            self.mount(address)

    # --- contract: address / mount -------------------------------------------

    @property
    def address(self) -> Address:
        return self._address

    def mount(self, address: "str | Address", *, select: Optional[str] = None) -> None:
        """Bind to ``address`` and project it. ``select`` (a node address) restores a prior
        selection — the hook that makes a pane reconstructible from ``(address, selection)``."""
        addr = address if isinstance(address, Address) else Address.parse(address)
        if not supports_vfs(addr.scheme):
            raise NotImplementedError(f"{addr.scheme or 'no'}:// is not browseable")
        self._address = addr
        self._all = tuple(vfs_list(addr))
        self._apply_filter(select=select)

    def refresh(self) -> None:
        """Re-list the current address (the refresh), keeping the selection if it survives."""
        self.mount(self._address, select=self._selected)

    # --- contract: render / selection / actions ------------------------------

    def render(self) -> Rendered:
        riding = self._riding()  # {address} of attached leaves, for the green dot (attachable schemes)
        desks = _known_project_nodes(self._nodes, scheme=self._address.scheme)
        rows: list[RenderedRow] = []
        if self._dot_toggle():
            n_hidden = sum(1 for n in self._all if _is_dot(n.name))
            if self._show_hidden:
                label = "hidden · on"
            elif n_hidden:
                label = f"hidden · {n_hidden} hidden"
            else:
                label = "hidden · off"
            rows.append(RenderedRow(
                text=label, address="key:hidden", kind="caption", selected=False,
            ))
        rows.extend(
            RenderedRow(
                text=n.name + ("/" if n.kind == "container" else ""),
                address=n.address,
                kind=n.kind,
                selected=(n.address == self._selected),
                accent=(n.address in riding or n.address in desks),
            )
            for n in self._nodes
        )
        title = str(self._address)
        if self._address.scheme == "via" and self._address.subpath:
            title = self._address.subpath
        return Rendered(title=title, rows=tuple(rows), empty=not self._nodes)

    def _riding(self) -> "set[str]":
        """Addresses of the currently-attached leaves in this listing — the green-dot set. Empty for
        a non-attachable scheme (skills/docs/marks ride; file/config/… don't), so a plain file
        browser pays nothing."""
        from xlii.attach import attachable, is_attached

        if not attachable(self._address):
            return set()
        from xlii.active_session import active_session

        session = active_session()
        return {n.address for n in self._nodes if n.kind == "leaf" and is_attached(session, n.address)}

    def selection(self) -> Selection:
        return Selection(node=self._focused())

    def actions(self) -> "list[Action]":
        acts: list[Action] = []
        from xlii.desk_files import REMOTE_FILE_SCHEMES

        if self._address.scheme in REMOTE_FILE_SCHEMES:
            acts.append(Action(
                "use-files",
                "Use as project files",
                Outcome(PREFILL, text=f"/project files {self._address}"),
            ))
        node = self._focused()
        if node is None:
            return acts
        if node.kind == "container":
            acts.extend([
                Action("open", "Open", Outcome(NAVIGATE, node.address)),
                Action("open-other", "Open in other pane", Outcome(RETARGET_SLOT, node.address)),
            ])
            return acts
        # a leaf: view it (a view renderer reads it via vfs_read), plus attach/detach when the leaf's
        # scheme rides the turn (docs / marks) — the same three buttons + a/v/d the skills pane offers.
        acts.append(Action("view", "View", Outcome(RETARGET_SLOT, node.address)))
        from xlii.attach import attachable

        if attachable(node.address):
            acts.append(Action("attach", "Attach", Outcome(ATTACH, node.address)))
            acts.append(Action("detach", "Detach", Outcome(DETACH, node.address)))
        return acts

    # --- contract: handle (local navigation only) ----------------------------

    def handle(self, key: str) -> bool:
        if key in ("down", "up", "home", "end"):
            return self._move(key)
        if key == "hidden":
            return self._toggle_hidden()
        if key == "enter":
            node = self._focused()
            if node is not None and node.kind == "container":
                self.mount(node.address)  # adopt the child's address — "go in"
                return True
            return False  # a leaf: not local nav — the surface consults actions()
        if key == "back":
            parent = self._parent(self._address)
            if parent is not None:
                self.mount(parent)
                return True
            return False
        return False

    def select_index(self, i: int) -> bool:
        """Select the row at index ``i`` (a mouse click's target row). Returns True if in range —
        the surface uses it so a click selects like the arrow keys do."""
        if 0 <= i < len(self._nodes):
            self._selected = self._nodes[i].address
            return True
        return False

    # --- reading a leaf (for a paired view pane) -----------------------------

    def read_selected(self) -> bytes:
        """Bytes of the focused leaf, via the VFS. Raises if nothing / a container is selected."""
        node = self._focused()
        if node is None or node.kind != "leaf":
            raise IsADirectoryError("no leaf selected")
        return vfs_read(node.address)

    # --- internals -----------------------------------------------------------

    def _focused(self) -> Optional[Node]:
        i = self._index_of(self._selected)
        return self._nodes[i] if i is not None else None

    def _index_of(self, node_address: str) -> Optional[int]:
        for i, n in enumerate(self._nodes):
            if n.address == node_address:
                return i
        return None

    def _default_selection(self) -> str:
        return self._nodes[0].address if self._nodes else ""

    def _dot_toggle(self) -> bool:
        return self._address.scheme in _DOT_SCHEMES

    def _apply_filter(self, *, select: Optional[str] = None) -> None:
        if self._show_hidden or not self._dot_toggle():
            self._nodes = self._all
        else:
            self._nodes = tuple(n for n in self._all if not _is_dot(n.name))
        pick = select if select is not None else self._selected
        self._selected = pick if (pick and self._index_of(pick) is not None) else self._default_selection()

    def _toggle_hidden(self) -> bool:
        if not self._dot_toggle():
            return False
        self._show_hidden = not self._show_hidden
        _set_hidden_pref(self._show_hidden)
        self._apply_filter()
        return True

    def _move(self, key: str) -> bool:
        if not self._nodes:
            return False
        i = self._index_of(self._selected)
        i = 0 if i is None else i
        if key == "down":
            i = min(i + 1, len(self._nodes) - 1)
        elif key == "up":
            i = max(i - 1, 0)
        elif key == "home":
            i = 0
        elif key == "end":
            i = len(self._nodes) - 1
        self._selected = self._nodes[i].address
        return True

    @staticmethod
    def _parent(addr: Address) -> Optional[Address]:
        """The parent address, or ``None`` at a root / on a non-hierarchical scheme /
        at the session Files fence. Built as a ``scheme://target`` string and parsed,
        so it round-trips like an adopted child address.

        A listing that is *not* under this desk's Files root (Remotes host browse)
        stays unfenced. Walking Files up from a project tree stops at that tree.
        """
        parent: Optional[Address] = None
        if addr.scheme == "file":
            p = Path(addr.target)
            if p != p.parent:
                parent = Address.parse(f"file://{p.parent}")
        elif addr.scheme == "via" and addr.subpath:
            inner = Address.parse(addr.subpath)
            inner_parent = ExplorerPane._parent(inner)
            if inner_parent is not None:
                parent = Address.parse(f"via://{addr.key}/{inner_parent}")
        elif addr.scheme in _SUBPATH_SCHEMES and addr.subpath:
            parent_sub = addr.subpath.rsplit("/", 1)[0] if "/" in addr.subpath else ""
            target = f"{addr.key}/{parent_sub}".rstrip("/")
            parent = Address.parse(f"{addr.scheme}://{target}")
        elif addr.target and "/" not in addr.target:
            # a keyed-root scheme (docs://name, mark://x, config://global): back out to scheme://
            parent = Address.parse(f"{addr.scheme}://")
        from xlii.desk_files import parent_inside_files_fence

        return parent_inside_files_fence(addr, parent)


def reconstruct(pane: ExplorerPane) -> ExplorerPane:
    """Rebuild a pane purely from its ``(address, selection)`` — the state-ownership proof.

    The result renders byte-identically to ``pane``. Used by tests, and the shape the kernel
    uses to destroy/restore a slot."""
    rebuilt = ExplorerPane()
    rebuilt.mount(pane.address, select=pane.selection().address)
    return rebuilt
