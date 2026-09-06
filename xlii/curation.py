"""Curation export/import — serialize user curation to a plain directory tree.

Personas (prompts + memory turns), docs, plugins, and the project registry —
everything that makes an xlii install *yours* — as files you own. Secrets are
deliberately excluded: config.json (API keys) and the vault never leave the
machine this way.

The tree layout and the export.json (format 1) manifest schema live here, in
the kernel, so any body can round-trip curation — `xlii export` / `xlii import`
are thin print wrappers over these two functions.
"""

from __future__ import annotations

import json
import shutil
import time as _time
from pathlib import Path

from xlii import __version__
from xlii.config import GLOBAL_CONFIG_DIR
from xlii.persona import CHAT_STATE_DIR, PERSONAS_DIR


def export_curation(dest: Path) -> dict[str, int]:
    """Serialize all user curation under ``dest``; return per-section file counts.

    Raises ValueError when ``dest`` exists and is not empty.
    """
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise ValueError(f"{dest} exists and is not empty — pick a fresh directory")
    dest.mkdir(parents=True, exist_ok=True)

    counts: dict[str, int] = {}

    def _copy_tree(src: Path, name: str) -> None:
        if src.is_dir():
            shutil.copytree(src, dest / name, dirs_exist_ok=True)
            counts[name] = sum(1 for p in (dest / name).rglob("*") if p.is_file())

    _copy_tree(PERSONAS_DIR, "personas")          # prompt files
    _copy_tree(CHAT_STATE_DIR, "persona-memory")  # turns + per-persona state
    _copy_tree(GLOBAL_CONFIG_DIR / "docs", "docs")
    _copy_tree(GLOBAL_CONFIG_DIR / "plugins", "plugins")
    reg = GLOBAL_CONFIG_DIR / "projects.json"
    if reg.exists():
        (dest / "registry").mkdir(exist_ok=True)
        shutil.copy2(reg, dest / "registry" / "projects.json")
        counts["registry"] = 1

    (dest / "export.json").write_text(json.dumps({
        "format": 1,
        "tool": f"xlii {__version__}",
        "created_at": _time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "excluded": ["config.json (API keys)", "vault.enc", ".vault-key"],
        "counts": counts,
    }, indent=2))
    return counts


def import_curation(src: Path, *, force: bool = False) -> tuple[int, int, Path | None]:
    """Restore an `xlii export` tree. Existing files are kept unless ``force``.

    Returns ``(restored, skipped, registry_ref_path)``. The registry maps
    names → paths/collections from the OLD machine, so it is restored as a
    reference copy (``projects.imported.json``), never over the live one; the
    third element is None when the export had no registry.

    Raises ValueError when ``src`` is not an xlii export (no export.json).
    """
    src = Path(src)
    if not (src / "export.json").exists():
        raise ValueError(f"{src} is not an xlii export (no export.json)")

    restored = skipped = 0

    def _restore_tree(name: str, target: Path) -> None:
        nonlocal restored, skipped
        srcdir = src / name
        if not srcdir.is_dir():
            return
        for f in srcdir.rglob("*"):
            if not f.is_file():
                continue
            out = target / f.relative_to(srcdir)
            if out.exists() and not force:
                skipped += 1
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, out)
            restored += 1

    _restore_tree("personas", PERSONAS_DIR)
    _restore_tree("persona-memory", CHAT_STATE_DIR)
    _restore_tree("docs", GLOBAL_CONFIG_DIR / "docs")
    _restore_tree("plugins", GLOBAL_CONFIG_DIR / "plugins")
    registry_ref: Path | None = None
    if (src / "registry" / "projects.json").exists():
        registry_ref = GLOBAL_CONFIG_DIR / "projects.imported.json"
        shutil.copy2(src / "registry" / "projects.json", registry_ref)

    return restored, skipped, registry_ref
