"""``ProjectsPane`` — the project registry as a launch list over ``projects://``.

Typed-workbenches B5 (the home launch state, node-local half): one row per
registered project, badged with its workbench type; the current project is
accented. The one action **seeds ``/project switch <name>`` into the command
line** — review-before-run, the same contract as the tasks pane: the surface
never teleports on its own. Reads the registry directly (not the ambient
session — the list is node-global, the badge per-project), so it renders the
same on the TUI and the face.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from xlii.addressing import Address, Node
from xlii.addressing.builtins.projects import (
    project_address,
    project_name_from_target,
    project_path_from_address,
)
from xlii.panes import PREFILL, Action, Outcome, Rendered, RenderedRow, Selection


def _is_chat_island(entry) -> bool:
    return str(getattr(entry, "name", "") or "").startswith("chat/")


def _split_entries(entries) -> tuple[list, list]:
    folders = sorted(
        (e for e in entries if not _is_chat_island(e)),
        key=lambda e: e.name.lower(),
    )
    chats = sorted(
        (e for e in entries if _is_chat_island(e)),
        key=lambda e: e.name.lower(),
    )
    return folders, chats


def _node_key(entry) -> str:
    return (getattr(entry, "node", None) or "").strip()


def _groups(folders: list) -> list[tuple[str, list]]:
    """This box first (``here``), then one caption per node name."""
    buckets: dict[str, list] = {}
    order: list[str] = []
    for e in folders:
        key = _node_key(e)
        if key not in buckets:
            order.append(key)
            buckets[key] = []
        buckets[key].append(e)
    groups: list[tuple[str, list]] = []
    if "" in buckets:
        groups.append(("here", buckets[""]))
    for key in sorted(k for k in order if k):
        groups.append((key, buckets[key]))
    return groups


class ProjectsPane:
    """A read-only list of registered projects; the action seeds the switch."""

    def __init__(self, address: "str | Address | None" = None) -> None:
        self._address: Address = Address(scheme="projects")
        self._entries: list = []
        self._sel: int = 0
        if address is not None:
            self.mount(address)

    @property
    def address(self) -> Address:
        return self._address

    def mount(self, address: "str | Address", *, select: Optional[str] = None) -> None:
        self._address = address if isinstance(address, Address) else Address.parse(address)
        self._refresh_entries(select=select)

    def _refresh_entries(self, *, select: Optional[str] = None) -> None:
        from xlii.registry import Registry

        raw_target = select or self._address.target.strip()
        selected_path = ""
        if self._entries and 0 <= self._sel < len(self._entries):
            selected_path = self._entries[self._sel].path
        from xlii.fabric_projects import federated_visible

        folders, chats = _split_entries(federated_visible(Registry.load().entries))
        grouped: list = []
        for _title, items in _groups(folders):
            grouped.extend(items)
        self._entries = grouped + chats
        self._sel = 0
        wanted_path = ""
        if select:
            wanted_path = project_path_from_address(select)
        if not wanted_path:
            wanted_path = selected_path
        wanted_name = ""
        if select and "://" in str(select):
            wanted_name = project_name_from_target(Address.parse(str(select)).target)
        elif raw_target:
            wanted_name = project_name_from_target(raw_target)
        if wanted_path:
            for i, e in enumerate(self._entries):
                if e.path == wanted_path:
                    self._sel = i
                    return
        if wanted_name:
            for i, e in enumerate(self._entries):
                if e.name == wanted_name:
                    self._sel = i
                    break

    @staticmethod
    def _badge(entry) -> str:
        """ghost · collection · pack. Node lives in the section caption, not the row."""
        try:
            from xlii.project_resolver import project_is_alive

            if not project_is_alive(entry):
                return "ghost"
        except Exception:
            return "ghost"
        try:
            from xlii.config import PROJECT_KIND_COLLECTION, ProjectConfig, project_kind

            cfg = ProjectConfig.load(Path(entry.path))
            if cfg is not None and project_kind(cfg) == PROJECT_KIND_COLLECTION:
                return "collection"
        except Exception:
            return ""
        try:
            from xlii.workbench import load_active_type

            wb = load_active_type(Path(entry.path) / ".xlii")
            if wb and wb not in ("chat", "home"):
                return wb
        except Exception:
            return ""
        return ""

    @staticmethod
    def _can_cloud_sync(entry) -> bool:
        """Throne opt-in: node rows, or a desk that already has / points at files."""
        if _node_key(entry):
            return True
        try:
            from xlii.config import ProjectConfig
            from xlii.desk_files import files_root_of

            cfg = ProjectConfig.load(Path(entry.path))
            if cfg is None:
                return bool(getattr(entry, "collection_id", None))
            return bool(files_root_of(cfg) or cfg.collection_id)
        except Exception:
            return bool(getattr(entry, "collection_id", None))

    @staticmethod
    def _is_current(entry) -> bool:
        try:
            from xlii.active_session import active_session

            state = active_session()
            root = getattr(getattr(state, "project", None), "project_root", None)
            return bool(root) and Path(entry.path).resolve() == Path(str(root)).resolve()
        except Exception:
            return False

    def render(self) -> Rendered:
        self._refresh_entries()
        folders, chats = _split_entries(self._entries)
        rows = []
        off = 0
        for title, items in _groups(folders):
            rows.append(RenderedRow(text=f"── {title} ──", address="", kind="caption"))
            for i, e in enumerate(items):
                badge = self._badge(e)
                label = e.name + (f"  · {badge}" if badge else "")
                rows.append(RenderedRow(
                    text=label, address=project_address(e.name, e.path), kind="leaf",
                    selected=(off + i == self._sel), accent=self._is_current(e),
                ))
            off += len(items)
        if chats:
            rows.append(RenderedRow(text="── chats ──", address="", kind="caption"))
            for i, e in enumerate(chats):
                badge = self._badge(e)
                label = e.name.split("/", 1)[-1]
                if badge:
                    label = f"{label}  · {badge}"
                rows.append(RenderedRow(
                    text=label, address=project_address(e.name, e.path), kind="leaf",
                    selected=(off + i == self._sel), accent=self._is_current(e),
                ))
        return Rendered(title="projects://", rows=tuple(rows), empty=not rows)

    def selection(self) -> Selection:
        if not self._entries:
            return Selection(node=None)
        e = self._entries[self._sel]
        return Selection(node=Node(
            address=project_address(e.name, e.path), name=e.name,
            kind="leaf", extra={"type": "project", "path": e.path},
        ))

    def actions(self) -> "list[Action]":
        if not self._entries:
            return [
                Action("prune", "Prune ghosts",
                       Outcome(PREFILL, "projects://", text="/project prune")),
            ]
        e = self._entries[self._sel]
        addr = project_address(e.name, e.path)
        ghost = self._badge(e).startswith("ghost")
        acts = []
        if not ghost:
            acts.append(Action(
                "open", "Open",
                Outcome(PREFILL, addr, text=f"/project switch @{e.path}"),
            ))
            if self._can_cloud_sync(e):
                acts.append(Action(
                    "sync", "Sync to cloud",
                    Outcome(PREFILL, addr, text="/sync"),
                ))
        acts.append(Action(
            "rm", "Delete",
            Outcome(PREFILL, addr, text=f"/project forget {e.path}"),
        ))
        acts.append(Action(
            "prune", "Prune ghosts",
            Outcome(PREFILL, "projects://", text="/project prune"),
        ))
        return acts

    def handle(self, key: str) -> bool:
        if not self._entries:
            return False
        if key == "down":
            self._sel = min(self._sel + 1, len(self._entries) - 1)
            return True
        if key == "up":
            self._sel = max(self._sel - 1, 0)
            return True
        if key == "home":
            self._sel = 0
            return True
        if key == "end":
            self._sel = len(self._entries) - 1
            return True
        return False  # enter/back fall through to the surface (actions / close)

    def select_index(self, i: int) -> bool:
        if 0 <= i < len(self._entries):
            self._sel = i
            return True
        return False
