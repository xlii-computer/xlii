"""GitProvider provider."""
from __future__ import annotations

from pathlib import Path

from xlii.addressing import (
    Address,
    Node,
    Resolution,
    ShellExport,
)


class GitProvider:
    """``git://`` — the working tree's source-control state as a read-only VFS (the "Git" doorway).

    The address space the :class:`~xlii.panes.git.GitPane` and ``xlii cat``/``ls`` browse:

    * ``git://`` — the **changed set** root (container): every staged / modified / untracked path
      as a leaf. The Panel opens the sectioned :class:`~xlii.panes.git.GitPane` here (its own
      Staged / Changes / Untracked view + stage/commit actions); the raw ``ls`` is a flat list.
    * ``git://diff/<path>`` — the unstaged working-tree diff of one file (``git diff -- <path>``);
      an untracked file shows as all-additions. ``git://diff`` (no path) lists every unstaged file.
    * ``git://staged/<path>`` — the staged diff of one file (``git diff --cached -- <path>``);
      ``git://staged`` lists every staged file.
    * ``git://log`` — recent commits (container); ``git://log/<sha>`` is that commit (``git show``).

    Read-only through the VFS — mutations (stage/unstage/commit/discard) are the ``/git`` command's
    job, review-before-run. Forge-agnostic: every fact comes from the local ``git`` binary via
    :func:`xlii.loop_bundle.git_cmd` and :mod:`xlii.git_status`. Resolves the repo from the ambient
    session's cwd (:func:`xlii.active_session.active_cwd`); outside a git repo every listing is empty
    rather than raising (the ``git_status`` graceful-empty contract).
    """

    scheme = "git"

    _KEYS = ("", "diff", "staged", "log")

    @staticmethod
    def _root() -> "Path | None":
        from xlii.active_session import active_cwd
        from xlii.git_status import find_repo_root

        return find_repo_root(active_cwd() or Path.cwd())

    def resolve(self, address: Address) -> Resolution:
        key = address.key.strip()
        if key not in self._KEYS:
            return Resolution(ok=False, address=address, kind="git", reason=f"unknown git path {key!r}")
        root = self._root()
        return Resolution(ok=root is not None, address=address, kind="git",
                          reason="" if root is not None else "not a git repo")

    def stat(self, address: Address) -> Node:
        key = address.key.strip()
        if not key:
            return Node(address="git://", name="git", kind="container", extra={"type": "git"})
        if not address.subpath:
            return Node(address=str(address), name=key, kind="container", extra={"type": "git"})
        name = address.subpath.rsplit("/", 1)[-1]
        kind = "commit" if key == "log" else "diff"
        return Node(address=str(address), name=name, kind="leaf", extra={"type": kind})

    def list(self, address: Address) -> "list[Node]":
        root = self._root()
        if root is None:
            return []
        key = address.key.strip()
        if key not in self._KEYS or address.subpath:
            return []
        if key == "log":
            from xlii.loop_bundle import git_cmd

            out, err = git_cmd(root, ["log", "--oneline", "-n", "50"])
            if err:
                return []
            nodes: "list[Node]" = []
            for line in out.splitlines():
                line = line.strip()
                if not line:
                    continue
                sha = line.split(" ", 1)[0]
                nodes.append(Node(address=f"git://log/{sha}", name=line, kind="leaf", extra={"type": "commit"}))
            return nodes
        from xlii.git_status import porcelain_entries

        nodes = []
        for x, y, path in porcelain_entries(root):
            if key in ("", "staged") and x not in (" ", "?"):
                nodes.append(Node(address=f"git://staged/{path}", name=path, kind="leaf",
                                  extra={"type": "diff", "status": x.upper(), "staged": True}))
            if key in ("", "diff") and y not in (" ",):
                st = "?" if y == "?" else y.upper()
                nodes.append(Node(address=f"git://diff/{path}", name=path, kind="leaf",
                                  extra={"type": "diff", "status": st, "staged": False}))
        return nodes

    def read(self, address: Address) -> bytes:
        root = self._root()
        if root is None:
            raise FileNotFoundError("git://: not a git repository")
        key = address.key.strip()
        sub = address.subpath
        if key not in self._KEYS:
            raise FileNotFoundError(f"git://{key}: unknown git path")
        if not sub:
            raise IsADirectoryError(f"{address}: a git listing — use ls")
        from xlii.loop_bundle import git_cmd

        if key == "log":
            out, err = git_cmd(root, ["show", sub])
            if err:
                raise FileNotFoundError(f"git://log/{sub}: {err}")
            return out.encode()
        if key == "staged":
            out, err = git_cmd(root, ["diff", "--cached", "--", sub])
            if err:
                raise FileNotFoundError(f"git://staged/{sub}: {err}")
            return (out or f"(no staged changes in {sub})\n").encode()
        # key == "diff": unstaged working-tree diff; untracked → show as all-additions.
        out, err = git_cmd(root, ["diff", "--", sub])
        if err:
            raise FileNotFoundError(f"git://diff/{sub}: {err}")
        if not out.strip():
            import os

            out2, err2 = git_cmd(root, ["diff", "--no-index", "--", os.devnull, sub])
            if err2 is None and out2.strip():
                out = out2
        return (out or f"(no unstaged changes in {sub})\n").encode()

    def exists(self, address: Address) -> bool:
        return self._root() is not None and address.key.strip() in self._KEYS

    def shell_export(self, address: Address) -> ShellExport:
        key = address.key.strip()
        if key not in self._KEYS:
            raise FileNotFoundError(f"git://{key}: unknown git path")
        if not address.subpath:
            return ShellExport(kind="address")  # listings — no artifact behind them
        # The address denotes the DIFF/commit, not the working-tree file (that is
        # file://) — the content exists only as git output, so snapshot it.
        suffix = ".txt" if key == "log" else ".diff"
        return ShellExport(kind="content", content=self.read(address), suffix=suffix)


