#!/usr/bin/env python
"""Stamp the version — MAJOR.YYDDD.TESTS: date + green-test count as the receipt.

Version format: ``MAJOR.<YY><day-of-year>.<tests>``, e.g. ``0.26201.4241`` =
major 0 (alpha), day 201 of 2026, 4241 tests green in the run that authorized
the stamp. MAJOR 0 = not public; ``--set 1`` is the public/community gate,
not a vanity bump. Pure PEP 440 release segments, year-first so versions
sort numerically across year boundaries. The version stores no zodiac —
``xlii.version_sign`` derives the season from the date at display time
(``xlii --version`` → ``xlii 0.26201.4241 cancer``), keeping the string short.

**A red suite refuses to stamp** — the full pytest run is the mint authority:
a version string is a receipt, not a claim.

    python scripts/stamp_version.py                # restamp (same major, today + count)
    python scripts/stamp_version.py --major        # bump the major
    python scripts/stamp_version.py --set 2        # explicit major
    python scripts/stamp_version.py --dry-run      # print what would be stamped

Writes the single source of truth: ``__version__`` in ``xlii/__init__.py``
(pyproject reads it via ``[tool.setuptools.dynamic]``).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
INIT = ROOT / "xlii" / "__init__.py"

_MAJOR_RE = re.compile(r"^(\d+)\.")
_ASSIGN_RE = re.compile(r'^__version__ = "(?P<v>[^"]+)"$', re.MULTILINE)
_PASSED_RE = re.compile(r"(\d+) passed")
_HELP_REF_RE = re.compile(r"^ref:[ \t]*\S+[ \t]*$", re.MULTILINE)


def current_version(text: str) -> str:
    """The ``__version__`` string in xlii/__init__.py's source text."""
    m = _ASSIGN_RE.search(text)
    if not m:
        raise SystemExit("stamp_version: no __version__ assignment in xlii/__init__.py")
    return m.group("v")


def current_major(version: str) -> int:
    """The MAJOR of any prior stamp (works on every historical form)."""
    m = _MAJOR_RE.match(version)
    if not m:
        raise SystemExit(f"stamp_version: unparseable version {version!r}")
    return int(m.group(1))


def date_serial(date: _dt.date) -> int:
    """YYDDD — year-first so versions sort across year boundaries
    (day 5 of 2027 → 27005 > 26201). Day-of-year zero-padded to 3."""
    return int(f"{date:%y}{date.timetuple().tm_yday:03d}")


def build_version(major: int, date: _dt.date, passed: int) -> str:
    return f"{major}.{date_serial(date)}.{passed}"


def parse_passed(pytest_output: str) -> int:
    """The green count from pytest's summary line. 0 when none found —
    callers treat that as unstampable."""
    matches = _PASSED_RE.findall(pytest_output)
    return int(matches[-1]) if matches else 0


def run_suite() -> tuple[int, int]:
    """Run the full suite; return (exit_code, passed_count). The REAL exit code
    is captured from the process — never inferred from output."""
    # timeout-method=thread: signal/alarm can take down the whole suite when a
    # test spawns a long-lived subprocess (e.g. a real xlii-desktop). Thread
    # timeouts still mark the test failed without SIGALRM-killing pytest.
    proc = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-q",
            "--timeout=60", "--timeout-method=thread",
        ],
        cwd=ROOT, capture_output=True, text=True,
        env={k: v for k, v in os.environ.items() if k != "XAI_MANAGEMENT_API_KEY"},
    )
    return proc.returncode, parse_passed(proc.stdout + proc.stderr)


def help_manifest_path() -> Path:
    """The BUNDLED help manifest, located relative to ``INIT``.

    Derived, not a module constant, so monkeypatching ``INIT`` (how the stamp
    tests keep :func:`main` off the real tree) redirects the pin too — a test
    stamping a fake version must not rewrite the shipped manifest. Only the
    bundle is pinned: ``docs/help/manifest.yaml`` (the source of truth a dev
    checkout reads) keeps ``ref: main``, and ``bundle_help.py`` copies it back
    over this file, so the pin lives exactly where it belongs — in the artifact
    that ships."""
    return INIT.parent / "help" / "manifest.yaml"


def pin_help_ref(version: str, *, path: Optional[Path] = None) -> Optional[str]:
    """Point the bundled help manifest's ``ref:`` at ``v<version>``.

    A PyPI install used to fetch extended-tier help shards from ``main``, i.e.
    docs for whatever HEAD happens to be — not for the build the operator is
    running. Stamping is the one moment the version is known, so it is where the
    pin belongs. Returns the new ref, or None when there is nothing to rewrite
    (no bundle, or no ``ref:`` key) — a missing pin must never fail a stamp, and
    ``help_ref()`` keeps honoring the manifest value and ``XLII_HELP_REF``
    either way, so a dev tree reading docs/help/manifest.yaml is unaffected.
    """
    path = path if path is not None else help_manifest_path()
    try:
        text = path.read_text()
    except OSError:
        return None
    ref = f"v{version}"
    new_text, count = _HELP_REF_RE.subn(f"ref: {ref}", text, count=1)
    if not count or new_text == text:
        return None
    path.write_text(new_text)
    return ref


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--major", action="store_true", help="bump the major")
    g.add_argument("--set", dest="explicit", type=int, metavar="N",
                   help="explicit major instead of keeping the current one")
    ap.add_argument("--dry-run", action="store_true",
                    help="run the suite and print the stamp without writing")
    args = ap.parse_args()

    text = INIT.read_text()
    old = current_version(text)
    if args.explicit is not None:
        major = args.explicit
    else:
        major = current_major(old) + (1 if args.major else 0)

    print("stamp_version: running the full suite (the mint authority)…")
    rc, passed = run_suite()
    if rc != 0 or passed == 0:
        print(f"stamp_version: REFUSED — suite exit {rc}, {passed} passed. "
              "A version stamp is a receipt; fix the suite first.", file=sys.stderr)
        return 1

    new = build_version(major, _dt.date.today(), passed)
    from xlii import version_season_line

    season = version_season_line(new) or ""
    tail = f" {season}" if season else ""
    if args.dry_run:
        print(f"stamp_version: would stamp {old} -> {new}{tail} ({passed} passed)")
        return 0

    INIT.write_text(_ASSIGN_RE.sub(f'__version__ = "{new}"', text, count=1))
    print(f"stamp_version: {old} -> {new}{tail} ({passed} passed)")
    pinned = pin_help_ref(new)
    if pinned:
        print(f"stamp_version: bundled help ref -> {pinned} "
              "(this build's docs, not main's)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
