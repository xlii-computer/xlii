"""Kernel catalog — xlii.xtool_catalog sanity pins."""

from __future__ import annotations

from pathlib import Path

from xlii.xtool_catalog import (
    CATALOG,
    entry_available,
    format_argv,
    lookup,
    ordered_groups,
    resolve_tool_id,
    tool_available,
)


def test_catalog_entries_which_gate_and_placeholders():
    for e in CATALOG:
        assert e.binary
        assert e.id
        assert e.argv_template
        if "<" in e.argv_template:
            assert "<path>" in e.argv_template or "<dir>" in e.argv_template


def test_fix_flag_entries_carry_destructive_argv():
    fix_entries = [e for e in CATALOG if e.fix_flag]
    assert fix_entries
    for e in fix_entries:
        if e.argv_template == f"{e.binary} <path>":
            # In-place editor (e.g. phpcbf): the plain invocation IS the
            # destructive form; fix_flag marks it, there is no extra flag.
            continue
        assert any(flag in e.argv_template for flag in ("--fix", "--write", "-w "))


def test_ordered_groups_puts_fingerprint_first():
    order = ordered_groups(["node", "python"])
    assert order[0] == "javascript"
    assert "python" in order
    assert len(order) == len(set(order))


def test_ordered_groups_never_hides_other_groups():
    order = ordered_groups(["python"])
    assert set(order) == {"python", "javascript", "rust", "go", "other"}


def test_format_argv_fills_from_dock_path(tmp_path: Path):
    f = tmp_path / "a.py"
    f.write_text("x\n")
    entry = lookup("ruff-check")
    assert entry is not None
    assert format_argv(entry, dock_path=f) == f"ruff check {f}"
    assert format_argv(entry) == "ruff check <path>"


def test_resolve_tool_id_fix_variant():
    assert resolve_tool_id("ruff-check", fix=True) == "ruff-check-fix"
    assert resolve_tool_id("biome-check", fix=True) == "biome-check-write"


def test_tool_available_uses_which(monkeypatch):
    monkeypatch.setattr("xlii.xtool_catalog.shutil.which", lambda b: "/bin/ruff" if b == "ruff" else None)
    assert tool_available("ruff") is True
    assert tool_available("biome") is False
    entry = lookup("ruff-check")
    assert entry is not None
    assert entry_available(entry) is True


def test_lookup_by_id_and_label():
    assert lookup("ruff-check") is not None
    assert lookup("Ruff check") is not None
    assert lookup("nope") is None
