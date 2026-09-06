"""`xlii.plugin.lint_plugins` — typed findings behind `xlii plugin --lint` (B8)."""

from __future__ import annotations

import xlii.plugin as plugin_mod

_GOOD = """---
id: demo
description: A demo plugin
effect: read-only
trust: subscription
actions:
  - id: ping
    method: GET
    url: https://example.com/
---

# Demo
"""

_BAD = """---
id: other
effect: banana
---

# Broken
"""


def test_lint_plugins_returns_typed_findings(monkeypatch):
    monkeypatch.setattr(plugin_mod, "list_stock_plugins",
                        lambda: [("demo", _GOOD), ("broken", _BAD)])
    monkeypatch.setattr(plugin_mod, "list_plugins", lambda: [])

    findings = plugin_mod.lint_plugins()
    assert [f.plugin_id for f in findings] == ["demo", "broken"]
    assert all(f.origin == "stock" for f in findings)
    assert findings[0].problems == []
    assert findings[1].problems == [
        "id 'other' != filename 'broken'",
        "missing description",
        "invalid effect: 'banana'",
        "invalid trust: None",
        "missing actions (not a plugin)",
    ]


def test_lint_plugins_missing_frontmatter(monkeypatch):
    monkeypatch.setattr(plugin_mod, "list_stock_plugins",
                        lambda: [("bare", "# no frontmatter\n")])
    monkeypatch.setattr(plugin_mod, "list_plugins", lambda: [])
    (finding,) = plugin_mod.lint_plugins()
    assert finding.problems == ["missing frontmatter (--- block)"]


def test_lint_plugins_stock_pack_passes():
    """The CI gate's invariant: the bundled stock pack lints clean."""
    findings = plugin_mod.lint_plugins()
    assert findings, "expected the bundled stock pack to be linted"
    bad = [f for f in findings if f.problems]
    assert bad == []


def test_argus_cohort_stock_plugins_parse():
    from pathlib import Path

    import xlii
    from xlii.plugin_manifest import parse_manifest

    root = Path(xlii.__file__).parent / "stock_plugins"
    for name in ("yahoo-finance", "sec-edgar", "nyt", "mapbox", "appwrite"):
        raw = (root / f"{name}.md").read_text(encoding="utf-8")
        m = parse_manifest(raw)
        assert m is not None, name
        assert m.plugin_id == name
        assert m.actions, name


def test_stock_plugins_say_xlii_not_xli():
    """Paste-ready auth lines must name the live binary (rename leftover)."""
    from pathlib import Path

    import xlii

    root = Path(xlii.__file__).parent / "stock_plugins"
    hits = []
    for path in sorted(root.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if "xli auth" in text or "xli chat" in text or "xli daemon" in text:
            hits.append(path.name)
    assert hits == []
