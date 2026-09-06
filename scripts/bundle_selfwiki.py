#!/usr/bin/env python
"""Copy the xwiki vendor pages from docs/selfwiki/ into xlii/selfwiki/wiki/.

docs/selfwiki/ is SOURCE (the curated, tracked self-wiki — xlii documenting
xlii); xlii/selfwiki/wiki/ is the BUNDLE that ships in the wheel and serves the
read-only ``xwiki://`` scope in every project. Never edit the bundle directly.

The extra ``wiki/`` nesting makes the bundle an xli_dir-shaped root, so every
store/provider/retrieval helper (``xlii.wiki``, ``WikiProvider(root=…)``,
``xlii.wiki_retrieval``) works on it unchanged.

Run after editing docs/selfwiki/:
    python scripts/bundle_selfwiki.py          # write bundled copy
    python scripts/bundle_selfwiki.py --check  # exit 1 if bundle is stale (CI)
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "selfwiki"
DST = ROOT / "xlii" / "selfwiki" / "wiki"

# Wikilink targets that aren't pages: TOML array-of-tables syntax quoted in prose.
_NON_PAGE_LINKS = {"step", "edge"}


def _dangling_links(pages: "list[Path]") -> "list[tuple[str, str]]":
    names = {p.stem for p in pages}
    link = re.compile(r"\[\[([A-Za-z0-9._-]+)\]\]")
    return [
        (p.stem, m.group(1))
        for p in pages
        for m in link.finditer(p.read_text())
        if m.group(1) not in names and m.group(1) not in _NON_PAGE_LINKS
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify only; exit 1 on drift")
    args = ap.parse_args()

    pages = sorted(SRC.glob("*.md"))
    if not pages:
        print(f"bundle_selfwiki: no pages under {SRC}", file=sys.stderr)
        return 1
    dangling = _dangling_links(pages)
    if dangling:
        for page, target in dangling:
            print(f"bundle_selfwiki: dangling [[{target}]] in {page}.md", file=sys.stderr)
        return 1

    stale: list[str] = []
    for src in pages:
        dst = DST / src.name
        if not dst.exists() or dst.read_text() != src.read_text():
            stale.append(src.name)
    extra = [p.name for p in DST.glob("*.md")] if DST.is_dir() else []
    extra = [n for n in extra if not (SRC / n).exists()]

    if args.check:
        if stale or extra:
            print(f"bundle_selfwiki: stale bundle — run `python scripts/bundle_selfwiki.py` "
                  f"(stale: {stale or 'none'}, orphaned: {extra or 'none'})", file=sys.stderr)
            return 1
        print("bundle_selfwiki: bundle is fresh.")
        return 0

    if not stale and not extra:
        print("bundle_selfwiki: nothing to do (already up to date).")
        return 0
    DST.mkdir(parents=True, exist_ok=True)
    for name in extra:
        (DST / name).unlink()
    for src in pages:
        shutil.copy2(src, DST / src.name)
    print(f"bundle_selfwiki: refreshed {len(pages)} pages"
          + (f", removed {len(extra)} orphaned" if extra else "") + ".")
    return 0


if __name__ == "__main__":
    sys.exit(main())
