"""Project browser GUI producer (project-browser.md P3) — a tkinter subprocess.

Mirrors xlii/upload_popup.py: a tiny tkinter picker run as a SUBPROCESS so it
never fights the REPL's prompt_toolkit or the Textual TUI. It lists the project's
ignore-filtered files; the user selects some, picks an ACTION (attach / reference
/ edit), and on Done the choice is written to a JSON manifest the REPL reads back.

Manifest contract (backward-compatible with upload's ``{"files": [...]}``):

    {"action": "attach" | "reference" | "edit" | "cancel", "files": [abs, ...]}

``action`` omitted → ``attach``, so an upload-style manifest still ingests. The
producer/consumer split matches upload-locker.md: the popup only writes paths +
intent; the REPL owns what each action does (locker attach, input insert, $EDITOR).

Run as:  python -m xlii.browse_popup <manifest_path> --root <project_root>
Headless callers must NOT spawn this — /browse falls back to its headless views.

tkinter is imported lazily inside pick(), so this module (and its manifest helpers)
import fine on a box without python3-tk.
"""

from __future__ import annotations

import json
import sys
from typing import Sequence

WINDOW_TITLE = "xlii — browse project"
ACTIONS = ("attach", "reference", "edit")
_MAX_LISTED = 5000  # backstop on the file list shown in the picker


def write_manifest(manifest_path: str, files: Sequence[str], action: str = "attach") -> None:
    """Write the chosen absolute paths + action as JSON for the REPL to read back."""
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump({"action": action, "files": list(files)}, f)


def read_manifest(manifest_path: str) -> tuple[str, list[str]]:
    """Return ``(action, files)``; ``("attach", [])`` on missing/corrupt (never raises).

    Backward compatible: a manifest with no ``action`` key (upload's shape) reads
    as ``attach``. Unknown action strings fall back to ``attach``.
    """
    try:
        with open(manifest_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return ("attach", [])
    if not isinstance(data, dict):
        return ("attach", [])
    action = data.get("action", "attach")
    if action not in (*ACTIONS, "cancel"):
        action = "attach"
    files = [str(p) for p in data.get("files", []) if isinstance(p, str)]
    return (action, files)


def _list_project_files(root: str) -> list[str]:
    """Ignore-filtered project-relative file paths for the picker (bounded)."""
    from pathlib import Path

    from xlii.ignore import load_ignore_spec, walk_pruned

    base = Path(root)
    spec = load_ignore_spec(base)
    rels = [p.relative_to(base).as_posix() for p in walk_pruned(base, spec, max_files=_MAX_LISTED)]
    return sorted(rels)


def pick(root: str) -> tuple[str, list[str]]:
    """Open the picker; return ``(action, abs_paths)``. ``("cancel", [])`` on cancel."""
    import tkinter as tk
    from pathlib import Path

    base = Path(root)
    rels = _list_project_files(root)

    result: dict[str, object] = {"action": "cancel", "files": []}
    win = tk.Tk()
    win.title(WINDOW_TITLE)
    win.geometry("560x600")

    tk.Label(win, text=f"{base.name} — select files, choose an action, Done", pady=6).pack(fill=tk.X)

    filter_var = tk.StringVar()
    tk.Entry(win, textvariable=filter_var).pack(fill=tk.X, padx=8)

    listbox = tk.Listbox(win, selectmode=tk.EXTENDED)
    listbox.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

    def _refill(*_a) -> None:
        needle = filter_var.get().lower()
        listbox.delete(0, tk.END)
        for r in rels:
            if needle in r.lower():
                listbox.insert(tk.END, r)

    filter_var.trace_add("write", _refill)
    _refill()

    action_var = tk.StringVar(value="attach")
    radios = tk.Frame(win)
    radios.pack(fill=tk.X, padx=8)
    for a in ACTIONS:
        tk.Radiobutton(radios, text=a, value=a, variable=action_var).pack(side=tk.LEFT)

    def _done() -> None:
        sel = [(base / listbox.get(i)).resolve() for i in listbox.curselection()]
        result["files"] = [str(p) for p in sel]
        result["action"] = action_var.get()
        win.destroy()

    btns = tk.Frame(win)
    btns.pack(fill=tk.X, pady=8, padx=8)
    tk.Button(btns, text="Cancel", command=win.destroy).pack(side=tk.RIGHT)
    tk.Button(btns, text="Done", command=_done).pack(side=tk.RIGHT, padx=4)

    win.mainloop()
    return (str(result["action"]), list(result["files"]))  # type: ignore[arg-type]


def main(argv: Sequence[str]) -> int:
    if len(argv) < 2:
        print("usage: python -m xlii.browse_popup <manifest_path> --root <project_root>", file=sys.stderr)
        return 2
    manifest_path = argv[1]
    root = "."
    if "--root" in argv:
        i = list(argv).index("--root")
        if i + 1 < len(argv):
            root = argv[i + 1]
    try:
        action, files = pick(root)
    except Exception as e:  # tkinter missing / display error → clean fallback signal
        print(f"browse popup failed: {e}", file=sys.stderr)
        write_manifest(manifest_path, [], "cancel")
        return 1
    write_manifest(manifest_path, files, action)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
