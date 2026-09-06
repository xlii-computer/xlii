"""``GitPane`` — the Gitpanel source-control view over ``git://``.

VS Code's source-control panel, xlii-native. Sections: **Staged** · **Changes** · **Untracked**
· **Stashes** — each file row a status letter + path; stash rows carry ``stash@{n}`` + message.
Selecting a file **views its diff** in the working slot (a ``git://diff/<path>`` leaf); stash rows
carry NO ``git://`` address (the VFS resolves no ``git://stash/…`` path) — their patch view is a
PREFILLed ``!git stash show -p``, review-before-run like everything else. Stage/unstage/discard/
commit actions **seed ``/gitpain`` into the command line** (review-before-run). Stash uses
:data:`~xlii.panes.CLAIM_INPUT` for the required message (git's portable ``-m`` store — decision D2).

Pure projection of ``(address, selection)`` like every pane: the lists are read from the local
``git`` binary (:mod:`xlii.git_status`) in :meth:`mount`, selection is re-resolved to its index,
and successful ``/gitpain`` runs in the TUI re-mount to reflect a stage/commit/stash. Outside a
git repo the view is empty rather than an error (the ``git_status`` graceful-empty rule).
"""

from __future__ import annotations

import shlex
from typing import Callable, Optional

from xlii.addressing import Address, Node
from xlii.panes import (
    CLAIM_INPUT,
    ENQUEUE_TURN,
    PREFILL,
    RETARGET_SLOT,
    Action,
    InputClaim,
    Outcome,
    Rendered,
    RenderedRow,
    Selection,
)

# git status letter → the RenderedRow.tone the surface colours (added·green, modified·yellow,
# removed·red, renamed·cyan, untracked·green — the git status palette).
_STATUS_TONE = {"A": "added", "M": "modified", "D": "removed", "R": "renamed", "C": "renamed", "?": "untracked"}

_CMD = "/gitpain"


class GitpainPrefillBridge:
    """CLAIM_INPUT → PREFILL bridge for Gitpanel stash asks (TUI binds via CONDUCTOR)."""

    _prefill: Optional[Callable[[str], None]] = None

    @classmethod
    def bind_prefill(cls, prefill: Callable[[str], None]) -> None:
        cls._prefill = prefill

    @classmethod
    def prefill(cls, text: str) -> None:
        if cls._prefill is not None:
            cls._prefill(text)


def _tools_action(path: str, address: "str | Address") -> "Action | None":
    """D19 conductor integration: one Tools button on a file row — seed the
    fingerprint-first available check tool from the xtool catalog over this
    file as a ``!`` line (review-before-run). Destructive fix variants stay in
    ``/xtool`` where their flag is shown; ``None`` when no tool resolves."""
    try:
        from pathlib import Path

        from xlii import xtool_catalog as XT
        from xlii.active_session import active_cwd
        from xlii.git_status import find_repo_root

        root = find_repo_root(active_cwd() or Path.cwd())
        for group in XT.ordered_groups(XT.project_fingerprints(root)):
            for e in XT.entries_in_group(group, legacy=False):
                if e.fix_flag or not XT.entry_available(e):
                    continue
                target = (root / path) if root is not None else Path(path)
                argv = XT.format_argv(e, dock_path=target)
                return Action("tools", e.label, Outcome(PREFILL, address, text=f"!{argv}"))
    except Exception:
        # Documented contract: any resolution failure yields no Tools button.
        pass
    return None


def _stash_prefill(message: str, *, include_untracked: bool) -> None:
    msg = (message or "").strip()
    if not msg:
        return
    cmd = f"{_CMD} stash -m {shlex.quote(msg)}"
    if include_untracked:
        cmd += " -u"
    GitpainPrefillBridge.prefill(cmd)


class GitPane:
    """Gitpanel — the changed-files + stashes view over ``git://``."""

    def __init__(self, address: "str | Address | None" = None) -> None:
        self._address: Address = Address(scheme="git")
        self._sections: "list[tuple[str, list[dict]]]" = []
        self._rows: "list[dict]" = []  # the selectable rows, in display order
        self._sel: int = 0
        self._title: str = "Gitpanel"
        self._in_repo: bool = False
        self._has_upstream: bool = False  # an ahead/behind reading → sync is meaningful
        self._stash_count: int = 0
        if address is not None:
            self.mount(address)

    @property
    def address(self) -> Address:
        return self._address

    def mount(self, address: "str | Address", *, select: Optional[str] = None) -> None:
        self._address = address if isinstance(address, Address) else Address.parse(address)
        self._load()
        self._sel = 0
        target = select or self._address.subpath.strip()
        if target:
            for i, row in enumerate(self._rows):
                if row.get("path") == target or row.get("ref") == target or select == row.get("address"):
                    self._sel = i
                    break

    def _load(self) -> None:
        from pathlib import Path

        from xlii.active_session import active_cwd
        from xlii.git_status import ahead_behind, branch_name, find_repo_root, porcelain_entries, stash_entries

        root = find_repo_root(active_cwd() or Path.cwd())
        self._in_repo = root is not None
        if root is None:
            self._sections, self._rows, self._title = [], [], "Gitpanel (not a repo)"
            self._has_upstream, self._stash_count = False, 0
            return
        staged, changed, untracked = [], [], []
        for x, y, path in porcelain_entries(root):
            if x not in (" ", "?"):
                staged.append({"kind": "file", "path": path, "status": x.upper(), "staged": True,
                               "address": f"git://staged/{path}"})
            if x == "?" and y == "?":
                untracked.append({"kind": "file", "path": path, "status": "?", "staged": False,
                                  "address": f"git://diff/{path}"})
            elif y not in (" ",):
                changed.append({"kind": "file", "path": path, "status": y.upper(), "staged": False,
                                "address": f"git://diff/{path}"})
        # Stash rows carry NO address: the git:// VFS resolves no stash path, and a
        # minted-but-unresolvable address breaks selection flows (copy/export) with
        # "unknown git path". The patch view is a PREFILL instead (see actions()).
        stashes = [
            {"kind": "stash", "ref": s.ref, "index": s.index, "message": s.message,
             "path": s.ref, "address": "", "status": "S", "staged": False}
            for s in stash_entries(root)
        ]
        self._sections = [
            ("Staged", staged),
            ("Changes", changed),
            ("Untracked", untracked),
            ("Stashes", stashes),
        ]
        self._rows = [row for _name, items in self._sections for row in items]
        branch = branch_name(root) or "?"
        tag = ""
        ab = ahead_behind(root)
        self._has_upstream = ab is not None
        if ab:
            ahead, behind = ab
            tag = (f" ↑{ahead}" if ahead else "") + (f" ↓{behind}" if behind else "")
        self._stash_count = len(stashes)
        if self._stash_count:
            tag += f"  ⚑{self._stash_count}"
        self._title = f"Gitpanel  ·  {branch}{tag}"

    def render(self) -> Rendered:
        # The action verbs are the footer buttons (data-driven from actions()), so the body stays a
        # clean row list — no hotkey legend.
        if not self._in_repo:
            return Rendered(title=self._title, rows=(), empty=True)
        if not self._rows:
            return Rendered(title=self._title, empty=False, rows=(
                RenderedRow(text="✓ working tree clean", address="", kind="caption"),
            ))
        rows: "list[RenderedRow]" = []
        ri = 0
        for name, items in self._sections:
            if not items:
                continue
            rows.append(RenderedRow(text=f"─ {name} ({len(items)}) ", address="", kind="caption"))
            for item in items:
                if item["kind"] == "stash":
                    text = f"{item['ref']}  {item['message']}"
                else:
                    text = f"{item['status']}  {item['path']}"
                rows.append(RenderedRow(
                    text=text,
                    address=item["address"], kind="leaf",
                    selected=(ri == self._sel), accent=item.get("staged", False),
                    tone=_STATUS_TONE.get(item.get("status", ""), "")))
                ri += 1
        return Rendered(title=self._title, rows=tuple(rows), empty=False)

    def selection(self) -> Selection:
        if not self._rows:
            return Selection(node=None)
        row = self._rows[self._sel]
        name = row.get("path") or row.get("ref") or ""
        extra = {"type": "stash" if row["kind"] == "stash" else "diff",
                 "status": row.get("status", ""), "staged": row.get("staged", False)}
        if row["kind"] == "stash":
            extra["stash_index"] = row["index"]
        return Selection(node=Node(address=row["address"], name=name, kind="leaf", extra=extra))

    def _stash_claim(self, *, include_untracked: bool) -> InputClaim:
        def _submit(text: str) -> None:
            _stash_prefill(text, include_untracked=include_untracked)

        return InputClaim(prompt="stash message:", on_submit=_submit)

    def actions(self) -> "list[Action]":
        """The pane's actions. Selection-scoped first — for a file row, ``view`` (the Enter
        default) morphs the working slot to the file's diff and the mutators seed ``/gitpain …``
        into the command line (review-before-run); for a stash row, ``view`` PREFILLs the
        ``!git stash show -p`` patch and pop/apply/drop seed their confirm-tier commands. Then
        repo-scoped ``sync`` / ``branch`` / ``stash`` / ``sweep`` (independent of the selection,
        so they work on a clean tree too). Each action becomes a footer button. Empty outside a
        git repo."""
        if not self._in_repo:
            return []
        acts: "list[Action]" = []
        if self._rows:
            row = self._rows[self._sel]
            if row["kind"] == "stash":
                n = row["index"]
                acts.append(Action("view", "View patch",
                                   Outcome(PREFILL, "git://",
                                           text=f"!git stash show -p stash@{{{n}}}")))
                acts.append(Action("pop", "Pop stash",
                                   Outcome(PREFILL, "git://", text=f"{_CMD} stash pop {n}")))
                acts.append(Action("apply", "Apply stash",
                                   Outcome(PREFILL, "git://", text=f"{_CMD} stash apply {n}")))
                acts.append(Action("drop", "Drop stash",
                                   Outcome(PREFILL, "git://", text=f"{_CMD} stash drop {n}")))
            else:
                path = row["path"]
                acts.append(Action("view", "View diff", Outcome(RETARGET_SLOT, row["address"])))
                if row["staged"]:
                    acts.append(Action("unstage", "Unstage",
                                       Outcome(PREFILL, row["address"], text=f"{_CMD} unstage {path}")))
                else:
                    acts.append(Action("stage", "Stage",
                                       Outcome(PREFILL, row["address"], text=f"{_CMD} stage {path}")))
                acts.append(Action("commit", "Commit staged…", Outcome(PREFILL, "git://", text=f"{_CMD} commit ")))
                acts.append(Action("generate", "Generate message (AI)",
                                   Outcome(PREFILL, "git://", text=f"{_CMD} commit summary")))
                acts.append(Action("journal", "Commit from journal (AI)",
                                   Outcome(PREFILL, "git://", text=f"{_CMD} commit journal")))
                acts.append(Action("discard", "Discard changes",
                                   Outcome(PREFILL, row["address"], text=f"{_CMD} discard {path}")))
                acts.append(Action("review", "Review with AI",
                                   Outcome(ENQUEUE_TURN, row["address"],
                                           text="Review this diff for bugs and issues.")))
                tools = _tools_action(path, row["address"])
                if tools is not None:
                    acts.append(tools)
        if self._has_upstream:
            acts.append(Action("sync", "Sync (pull + push)", Outcome(PREFILL, "git://", text=f"{_CMD} sync")))
        acts.append(Action("branch", "Switch branch…", Outcome(PREFILL, "git://", text=f"{_CMD} branch ")))
        acts.append(Action("stash", "Stash…",
                           Outcome(CLAIM_INPUT, claim=self._stash_claim(include_untracked=False))))
        acts.append(Action("stash-u", "Stash incl. untracked…",
                           Outcome(CLAIM_INPUT, claim=self._stash_claim(include_untracked=True))))
        acts.append(Action("stash-journal", "Stash from journal (AI)",
                           Outcome(PREFILL, "git://", text=f"{_CMD} stash journal")))
        acts.append(Action("sweep", "Sweep merged…", Outcome(PREFILL, "git://", text=f"{_CMD} sweep")))
        return acts

    def handle(self, key: str) -> bool:
        if not self._rows:
            return False
        if key == "down":
            self._sel = min(self._sel + 1, len(self._rows) - 1)
            return True
        if key == "up":
            self._sel = max(self._sel - 1, 0)
            return True
        if key == "home":
            self._sel = 0
            return True
        if key == "end":
            self._sel = len(self._rows) - 1
            return True
        return False  # enter/back fall through to the surface (actions / close)

    def select_index(self, i: int) -> bool:
        """Select the row at index ``i`` (a mouse click's row). Returns True if in range."""
        if 0 <= i < len(self._rows):
            self._sel = i
            return True
        return False
