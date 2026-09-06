"""Collusion / test-weakening detection for the autonomous loop (L2)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from xlii.atomicio import write_text_atomic


def _assert_count(text: str) -> int:
    return len(re.findall(r"\bassert\b", text))


def _raises_count(text: str) -> int:
    return len(re.findall(r"pytest\.raises|@pytest\.mark\.(xfail|skip)", text))


def fingerprint_test_file(content: str) -> dict[str, Any]:
    return {
        "sha256": hashlib.sha256(content.encode()).hexdigest(),
        "asserts": _assert_count(content),
        "raises": _raises_count(content),
    }


def is_test_path(rel: str) -> bool:
    return rel.startswith("tests/") or rel.endswith("_test.py") or "/test_" in rel


def snapshot_test_files(test_files: dict[str, str]) -> dict[str, dict[str, Any]]:
    return {path: fingerprint_test_file(content) for path, content in test_files.items()}


def detect_weakening(
    prior_by_cycle: dict[str, dict[str, dict[str, Any]]],
    current: dict[str, str],
) -> list[str]:
    """Compare current test file contents to the strongest prior snapshot per path."""
    if not current:
        return []

    strongest: dict[str, dict[str, Any]] = {}
    for per_cycle in prior_by_cycle.values():
        for path, fp in per_cycle.items():
            prev = strongest.get(path)
            if prev is None or fp.get("asserts", 0) > prev.get("asserts", 0):
                strongest[path] = fp

    alarms: list[str] = []
    for path, content in current.items():
        cur = fingerprint_test_file(content)
        old = strongest.get(path)
        if old is None:
            continue
        if cur["asserts"] < old.get("asserts", 0):
            alarms.append(
                f"{path}: assert count weakened ({old.get('asserts')} → {cur['asserts']})"
            )
        if cur["raises"] < old.get("raises", 0):
            alarms.append(
                f"{path}: pytest.raises/xfail/skip markers reduced ({old.get('raises')} → {cur['raises']})"
            )
    return alarms


def write_collusion_alarm(xli_dir: Path, *, cycle: int, alarms: list[str]) -> None:
    body = (
        f"# Loop collusion alarm — cycle {cycle}\n\n"
        "Test files appear weaker than in a prior cycle while judges returned PASS.\n\n"
        + "\n".join(f"- {a}" for a in alarms)
        + "\n"
    )
    write_text_atomic(xli_dir / "loop-collusion-alarm.md", body)
