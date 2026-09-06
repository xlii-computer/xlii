"""Test isolation: point xlii's config dir AND $HOME at throwaway directories
BEFORE any xlii module is imported, so tests never read or write the
operator's real ``~/.config/xlii`` or ``~/.xlii`` (persona chat state,
scratch, worktrees, legacy drains).

Do **not** delete anything from the real home from tests — leaked-data
cleanup is operator-only (see proposals/context-memory/test-isolation-pollution.md).
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Stash the operator's real home BEFORE redirecting HOME. Idempotent across a
# second import of this module (pytest plugin load + `import tests.conftest`).
if "XLII_TEST_REAL_HOME" not in os.environ:
    os.environ["XLII_TEST_REAL_HOME"] = str(Path.home())
_REAL_HOME = os.environ["XLII_TEST_REAL_HOME"]

# Mint the throwaway HOME once; reuse on re-import so persona.CHAT_STATE_DIR
# (baked at first xlii import) stays aligned with Path.home().
if "XLII_TEST_HOME" not in os.environ:
    os.environ["XLII_TEST_HOME"] = tempfile.mkdtemp(prefix="xlii-test-home-")
_TEST_HOME = os.environ["XLII_TEST_HOME"]
os.environ["HOME"] = _TEST_HOME

# Path.home() now points at the throwaway dir; Python's user site would follow
# and lose --user installs under the real ~/.local. Keep those on sys.path.
_user_site = (
    Path(_REAL_HOME) / ".local" / "lib"
    / f"python{sys.version_info.major}.{sys.version_info.minor}"
    / "site-packages"
)
if _user_site.is_dir():
    p = str(_user_site)
    if p not in sys.path:
        sys.path.insert(0, p)
os.environ.setdefault("PYTHONUSERBASE", str(Path(_REAL_HOME) / ".local"))

if "XLII_TEST_CONFIG_DIR" not in os.environ:
    os.environ["XLII_TEST_CONFIG_DIR"] = tempfile.mkdtemp(prefix="xlii-test-config-")
_TEST_CONFIG_DIR = os.environ["XLII_TEST_CONFIG_DIR"]
os.environ["XLII_CONFIG_DIR"] = _TEST_CONFIG_DIR

# CI parity: CI runners never carry the xAI management key, but operator
# shells often do — GlobalConfig.load reads it at call time, so a leaked key
# flips tests onto management-enabled paths that CI never sees. Scrub it once
# here (session-scoped, before any xlii import) instead of relying on every
# runner remembering `env -u XAI_MANAGEMENT_API_KEY`. Tests that exercise the
# management path set it explicitly via monkeypatch.
os.environ.pop("XAI_MANAGEMENT_API_KEY", None)

# Ambient sessions (and this project's profile) often set XLII_SHELL_STYLE=styled.
# That path captures shells + prints rich.Panel for harness answers — which breaks
# hermetic tests that patch subprocess.call or assert on plain console text.
# Force raw unless a test deliberately overrides.
os.environ["XLII_SHELL_STYLE"] = "raw"


def pytest_addoption(parser):
    """`--backend` selects which storage backends the conformance suite runs
    against (tests/test_storage_conformance.py). Repeatable; defaults to the
    always-available `local` backend (plus `collections` when its class and
    credentials are present)."""
    parser.addoption(
        "--backend",
        action="append",
        default=None,
        help="Storage backend(s) for the conformance suite: local, collections, "
             "or one you register. Repeatable. Default: local (+ collections when set up).",
    )
