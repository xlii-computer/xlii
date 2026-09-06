"""Tests for TUI discoverability (terminal-native-toolkit Phase 6)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from xlii.repl_cmds import register_all
from xlii.tui.discover import (
    AtSuggestion,
    collect_palette_items,
    filter_palette,
    fuzzy_score,
    at_suggestions,
    pin_at_file,
    pin_at_url,
    resolve_at_selection,
)


def test_fuzzy_score_subsequence():
    assert fuzzy_score("st", "status") >= 0
    assert fuzzy_score("xyz", "status") < 0
    assert fuzzy_score("", "anything") == 0


def test_filter_palette_orders_by_match(tmp_path):
    register_all()
    state = SimpleNamespace(command_scope="code", project=SimpleNamespace(xli_dir=tmp_path / ".xlii"))
    items = collect_palette_items(state)
    assert any(i.name == "status" for i in items)
    hits = filter_palette(items, "st")
    assert hits
    assert any(h.name == "status" for h in hits)


def test_at_suggestions_find_project_file(tmp_path):
    f = tmp_path / "frontend" / "app.tsx"
    f.parent.mkdir(parents=True)
    f.write_text("export {}", encoding="utf-8")
    state = SimpleNamespace(
        project=SimpleNamespace(project_root=tmp_path, extra_ignores=[]),
        attach_file=lambda path, once=False: {"name": Path(path).name, "kind": "text", "path": path},
    )
    sugg = at_suggestions(state, "app")
    assert any("app.tsx" in s.value for s in sugg)


def test_pin_at_file_attaches(tmp_path):
    f = tmp_path / "README.md"
    f.write_text("hi", encoding="utf-8")

    class _State:
        def __init__(self):
            self.project = SimpleNamespace(project_root=tmp_path)
            self.attached: list = []

        def attach_file(self, path, once=False):
            self.attached.append(path)
            return {"name": Path(path).name, "kind": "text"}

    st = _State()
    msg = pin_at_file(st, "README.md")
    assert "pinned README.md" in msg
    assert st.attached


def test_pin_at_url_blocks_private_hosts():
    state = SimpleNamespace(attach_doc=lambda *a, **k: None)
    assert "blocked" in pin_at_url(state, "http://127.0.0.1/secret").lower()
    assert "blocked" in pin_at_url(state, "http://169.254.169.254/latest/meta-data/").lower()
    assert "blocked" in pin_at_url(state, "http://localhost/admin").lower()


def test_validate_url_host_allows_public():
    from xlii.tui.discover import validate_url_host

    err = validate_url_host("https://example.com/")
    assert err is None or "resolve" in err.lower()


def test_resolve_at_file(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("x", encoding="utf-8")
    state = SimpleNamespace(
        project=SimpleNamespace(project_root=tmp_path),
        attach_file=lambda path, once=False: {"name": "x.txt", "kind": "text"},
    )
    msg = resolve_at_selection(state, AtSuggestion("@x.txt", "file", "x.txt"))
    assert "pinned" in msg
