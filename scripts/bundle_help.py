#!/usr/bin/env python
"""Copy core-tier help topics from docs/help/ into xlii/help/ for offline /howto.

Only the **core** tier is bundled (offline-complete). Extended-tier topics and
per-command docs (commands/*.md) stay GitHub-sourced/etag-cached so they are
always current — see xlii/help_corpus.py.

Run after editing docs/help/:
    python scripts/bundle_help.py          # write bundled copy
    python scripts/bundle_help.py --check  # exit 1 if bundle is stale (CI)
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "help"
DST = ROOT / "xlii" / "help"


def _core_files(manifest_path: Path) -> list[Path]:
    data = yaml.safe_load(manifest_path.read_text())
    topics = data.get("topics") or {}
    paths = [manifest_path]
    for spec in topics.values():
        if not isinstance(spec, dict):
            continue
        if spec.get("tier") != "core":
            continue
        rel = spec.get("path")
        if rel:
            paths.append(SRC / rel)
    return paths


def bundle() -> list[str]:
    """Copy core-tier help files into xlii/help/. Returns list of changed paths."""
    manifest = SRC / "manifest.yaml"
    if not manifest.is_file():
        raise SystemExit(f"bundle_help: missing {manifest}")

    changed: list[str] = []
    for src in _core_files(manifest):
        if not src.is_file():
            raise SystemExit(f"bundle_help: missing source file {src.relative_to(ROOT)}")
        rel = src.relative_to(SRC)
        dst = DST / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.is_file() or src.read_bytes() != dst.read_bytes():
            shutil.copy2(src, dst)
            changed.append(str(dst.relative_to(ROOT)))
    return changed


def check() -> list[str]:
    """Return relative paths that differ or are missing in the bundle."""
    manifest = SRC / "manifest.yaml"
    if not manifest.is_file():
        return ["docs/help/manifest.yaml (missing)"]
    stale: list[str] = []
    for src in _core_files(manifest):
        rel = src.relative_to(SRC)
        dst = DST / rel
        if not dst.is_file():
            stale.append(str(rel))
        elif src.read_bytes() != dst.read_bytes():
            stale.append(str(rel))
    return stale


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="Exit 1 if xlii/help/ is stale (does not write).")
    args = ap.parse_args(argv)

    if args.check:
        stale = check()
        if stale:
            for p in stale:
                print(f"✗ stale help bundle: {p}")
            print("  run `python scripts/bundle_help.py` to refresh.")
            return 1
        print("✓ help bundle is up to date")
        return 0

    changed = bundle()
    if changed:
        print("bundle_help: refreshed " + ", ".join(changed))
    else:
        print("bundle_help: nothing to do (already up to date).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
