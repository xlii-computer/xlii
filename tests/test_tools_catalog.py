"""Track J — Tools → Shell tools… catalog."""

from __future__ import annotations

from types import SimpleNamespace

from xlii.tui.app import XliiApp
from xlii.tui.tools_catalog import group_menu_items, prefill_line, tool_menu_items
from xlii.xtool_catalog import CATALOG


def test_catalog_has_primary_and_legacy_tools():
    assert "python" in {e.group for e in CATALOG}
    assert any(e.legacy for e in CATALOG)
    assert any(e.fix_flag for e in CATALOG)


def test_group_menu_items_enablement(monkeypatch):
    monkeypatch.setattr("xlii.xtool_catalog.shutil.which", lambda b: "/bin/ruff" if b == "ruff" else None)
    by_id = {item_id: en for item_id, _label, en in group_menu_items(["python"])}
    assert by_id["xtgrp:python"] is True
    assert by_id["xtgrp:javascript"] is False


def test_tool_menu_items_primary_vs_more():
    primary = tool_menu_items("python", legacy=False)
    more = tool_menu_items("python", legacy=True)
    assert any("ruff" in i[0] for i in primary)
    assert any("flake8" in i[0] for i in more)
    assert not any("flake8" in i[0] for i in primary)


def test_prefill_line_includes_destructive_flags(monkeypatch):
    monkeypatch.setattr("xlii.xtool_catalog.shutil.which", lambda b: "/bin/ruff")
    assert prefill_line("ruff-check-fix") == "!ruff check --fix <path>"


def test_tools_menu_entry_stays_on_tools_bar():
    class _Host:
        _CONSOLE_CATEGORIES = XliiApp._CONSOLE_CATEGORIES
        _state = SimpleNamespace()

        def _bind_menu_rows(self, menu: str):
            return []

        def _menu_items(self, title: str):
            from xlii.tui.app_menu_mixin import AppMenuMixin
            return AppMenuMixin._menu_items(self, title)

    assert "tools:shelltools" in [item_id for item_id, _label, _en in _Host()._menu_items("Tools")]
    assert "cmd:tools" not in [item_id for item_id, _label, _en in _Host()._menu_items("Commands")]
