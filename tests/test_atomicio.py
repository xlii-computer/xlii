"""Atomic-write contract for xlii/atomicio.py (fabric F3 — durable OMEMO state).

The daemon's OMEMO crypto ledger is written through write_text_atomic; a crash
mid-write must never corrupt it. These tests pin: round-trip, parent creation,
0600 perms, no temp left behind, and — the load-bearing one — that a *failed*
write leaves the previous file intact.

Dependency-free (no [daemon] extra needed). Run directly:
    ./venv/bin/python -m pytest tests/test_atomicio.py
"""

import os
import stat

import pytest

from xlii.atomicio import write_text_atomic


def test_roundtrip(tmp_path):
    p = tmp_path / "state.json"
    write_text_atomic(p, '{"k": 1}')
    assert p.read_text() == '{"k": 1}'


def test_creates_parent_dirs(tmp_path):
    p = tmp_path / "a" / "b" / "c.json"
    write_text_atomic(p, "hi")
    assert p.read_text() == "hi"


def test_overwrites_existing(tmp_path):
    p = tmp_path / "s.json"
    write_text_atomic(p, "v1")
    write_text_atomic(p, "v2")
    assert p.read_text() == "v2"


def test_default_mode_is_0600(tmp_path):
    p = tmp_path / "secret.json"
    write_text_atomic(p, "x")
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_no_temp_file_left_behind(tmp_path):
    p = tmp_path / "s.json"
    write_text_atomic(p, "done")
    assert list(tmp_path.iterdir()) == [p]  # only the real file, no .tmp


def test_failed_replace_leaves_original_intact(tmp_path, monkeypatch):
    """The atomicity contract: if the rename fails, the previous content stays
    valid and no half-written temp survives."""
    p = tmp_path / "ledger.json"
    write_text_atomic(p, "GOOD_V1")

    def boom(src, dst):
        raise OSError("simulated crash during rename")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        write_text_atomic(p, "PARTIAL_V2")

    assert p.read_text() == "GOOD_V1"          # original untouched
    assert list(tmp_path.iterdir()) == [p]     # temp cleaned up
