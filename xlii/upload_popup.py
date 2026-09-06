"""The /upload popup (U2 of proposals/upload-locker.md) — a desktop bypass for
AI file/image upload.

A tiny tkinter file picker run as a SUBPROCESS, so it never fights the REPL's
prompt_toolkit or the Textual TUI. The user picks (or drags, if `tkinterdnd2` is
installed) image/text files; on "Done" the chosen absolute paths are written to a
JSON manifest the REPL reads back and folds into the locker. Filesystem-via-manifest
is the producer contract — a mobile share-sheet / XMPP push will satisfy the same
one. Reference-by-path matches the U1 locker model (no copying into .xlii/locker/).

Run as:  python -m xlii.upload_popup <manifest_path>
Headless callers must NOT spawn this — `/upload` falls back to `/locker add`.

tkinter is imported lazily inside `pick_files()`, so this module (and its manifest
helpers) import fine on a box without `python3-tk`.

Superseded, not deleted (two-pane-file-view-spec.md §5): the in-TUI file-tab panel
tree is now the default attach path — it calls the same `state.attach_file`
producer contract with no subprocess. The popup stays as desktop sugar under the
*one consumer, many producers* rule. Drag-and-drop is the one cosmetic gap:
`tkinterdnd2` is an optional dependency, and without it the picker quietly falls
back to click-to-pick. That fallback is expected, not a bug — since the panel
supersedes the popup, it is intentionally low priority.
"""

from __future__ import annotations

import json
import sys
from typing import Sequence

WINDOW_TITLE = "xlii — upload to locker"
# Mirror the locker's accepted kinds (images ride as vision; text inlines; the
# rest are named, not embedded — see xlii.multimodal).
IMAGE_EXTS = "*.png *.jpg *.jpeg"
TEXT_EXTS = "*.txt *.md *.markdown *.json *.csv *.log"
SHAREABLE_EXTS = f"{IMAGE_EXTS} {TEXT_EXTS} *.pdf"
FILE_TYPES = [
    ("Shareable files", SHAREABLE_EXTS),
    ("Images", IMAGE_EXTS),
    ("Text", TEXT_EXTS),
    ("All files", "*"),
]


def write_manifest(manifest_path: str, paths: Sequence[str]) -> None:
    """Write the chosen absolute paths as JSON for the REPL to read back."""
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump({"files": list(paths)}, f)


def read_manifest(manifest_path: str) -> list[str]:
    """Read paths from a manifest; [] on missing/corrupt (never raises)."""
    try:
        with open(manifest_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    files = data.get("files", []) if isinstance(data, dict) else []
    return [str(p) for p in files if isinstance(p, str)]


def pick_files() -> list[str]:
    """Open the picker; return chosen absolute paths ([] if cancelled).

    A multi-select native dialog works wherever tkinter does; real drag-and-drop
    is enabled only if the optional `tkinterdnd2` package is present.
    """
    import os
    import tkinter as tk
    from tkinter import filedialog

    chosen: list[str] = []
    try:
        from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore
        root = TkinterDnD.Tk()
        dnd = True
    except (ImportError, ModuleNotFoundError):
        root = tk.Tk()
        dnd = False

    root.title(WINDOW_TITLE)
    root.geometry("480x340")

    selected: dict[str, None] = {}  # insertion-ordered set
    listbox = tk.Listbox(root, selectmode=tk.EXTENDED)

    def _add(paths) -> None:
        for p in paths:
            ap = os.path.abspath(os.path.expanduser(p))
            if os.path.isfile(ap) and ap not in selected:
                selected[ap] = None
                listbox.insert(tk.END, ap)

    def _browse() -> None:
        _add(filedialog.askopenfilenames(parent=root, title="Choose files", filetypes=FILE_TYPES))

    def _remove_selected() -> None:
        for i in reversed(listbox.curselection()):
            selected.pop(listbox.get(i), None)
            listbox.delete(i)

    def _done() -> None:
        chosen.extend(selected.keys())
        root.destroy()

    hint = "Drag files in, or click “Add files…”" if dnd else "Click “Add files…” to choose"
    tk.Label(root, text=hint, pady=6).pack(fill=tk.X)
    listbox.pack(fill=tk.BOTH, expand=True, padx=8)

    if dnd:
        def _drop(event) -> None:
            _add(root.tk.splitlist(event.data))
        listbox.drop_target_register(DND_FILES)   # type: ignore[attr-defined]
        listbox.dnd_bind("<<Drop>>", _drop)        # type: ignore[attr-defined]

    btns = tk.Frame(root)
    btns.pack(fill=tk.X, pady=8, padx=8)
    tk.Button(btns, text="Add files…", command=_browse).pack(side=tk.LEFT)
    tk.Button(btns, text="Remove", command=_remove_selected).pack(side=tk.LEFT, padx=4)
    tk.Button(btns, text="Cancel", command=root.destroy).pack(side=tk.RIGHT)
    tk.Button(btns, text="Done", command=_done).pack(side=tk.RIGHT, padx=4)

    root.mainloop()
    return chosen


def main(argv: Sequence[str]) -> int:
    if len(argv) < 2:
        print("usage: python -m xlii.upload_popup <manifest_path>", file=sys.stderr)
        return 2
    manifest_path = argv[1]
    try:
        paths = pick_files()
    except Exception as e:  # tkinter missing / display error → signal a clean fallback
        print(f"upload popup failed: {e}", file=sys.stderr)
        write_manifest(manifest_path, [])
        return 1
    write_manifest(manifest_path, paths)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
