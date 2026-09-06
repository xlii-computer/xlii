"""Plan-surface T1: the plan_items parser, the plan listener seam, and the
strip's pure state — the todo surface over the existing plan substrate. The
pane half lives in test_panes_plan.py; nothing here needs a terminal."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xlii import plan_ops as P

PLAN = """# goal
- [ ] {#a} first thing
- [x?] {#b} second thing
- [x] {#c} third thing — receipt: pytest green

```
- [ ] {#fenced} example, not real
```

## Amendments
- [?] {#am-1} (2026-07-19) new: consider X
"""


@pytest.fixture(autouse=True)
def _clear_listener():
    P.set_plan_listener(None)
    yield
    P.set_plan_listener(None)


def _plans(tmp_path, text=PLAN):
    d = tmp_path / "plans"
    d.mkdir(parents=True, exist_ok=True)
    (d / "current.md").write_text(text)
    return d


# --- plan_items -----------------------------------------------------------------

def test_plan_items_parses_states_ids_receipts():
    items = P.plan_items(PLAN)
    assert [(i.item_id, i.state) for i in items] == [("a", " "), ("b", "x?"), ("c", "x")]
    assert items[0].text == "first thing"
    assert items[2].text == "third thing"           # id token + receipt stripped
    assert items[2].receipt == "pytest green"
    assert all(i.item_id != "fenced" for i in items)  # fenced examples aren't items


def test_plan_items_tolerates_idless_boxes():
    items = P.plan_items("- [ ] no id here\n")
    assert items[0].item_id == "" and items[0].text == "no id here"


# --- the listener seam ------------------------------------------------------------

def test_listener_set_fire_clear_and_raising():
    fired = []
    result = P.set_plan_listener(lambda: fired.append(1))
    assert result is None
    P.notify_plan_changed()
    P.set_plan_listener(None)
    P.notify_plan_changed()
    assert fired == [1]
    P.set_plan_listener(lambda: 1 / 0)              # a raising listener is swallowed
    P.notify_plan_changed()


def test_check_and_amend_fire_the_listener(tmp_path):
    d = _plans(tmp_path)
    fired = []
    P.set_plan_listener(lambda: fired.append(1))
    P.check_plan_item(d, "a", evidence="done")
    assert fired == [1]
    P.amend_plan(d, "found Y — propose Z")
    assert fired == [1, 1]
    P.check_plan_item(d, "c", evidence="again")     # [x] no-op writes nothing
    assert fired == [1, 1]


def test_plans_domain_write_hook_fires(tmp_path):
    """The planner rewriting a plans/ file repaints too — the tool_handlers
    hook fires only for paths directly inside the plans domain."""
    from xlii.tool_handlers import _notify_if_plan

    d = _plans(tmp_path)
    ctx = SimpleNamespace(project=SimpleNamespace(xli_dir=tmp_path))
    fired = []
    P.set_plan_listener(lambda: fired.append(1))
    _notify_if_plan(ctx, d / "current.md")
    assert fired == [1]
    _notify_if_plan(ctx, tmp_path / "elsewhere.md")
    assert fired == [1]


# --- the strip's pure state ---------------------------------------------------------

def _state(tmp_path, *, plan_mode=False, project=True):
    proj = SimpleNamespace(xli_dir=tmp_path) if project else None
    return SimpleNamespace(project=proj, agent=SimpleNamespace(plan_mode=plan_mode))


def test_strip_hides_without_project_plan_or_items(tmp_path):
    from xlii.tui.plan_strip import plan_strip_state

    assert plan_strip_state(_state(tmp_path, project=False)) is None
    assert plan_strip_state(_state(tmp_path)) is None            # no plans dir
    _plans(tmp_path, "just prose, no boxes\n")
    assert plan_strip_state(_state(tmp_path)) is None            # zero items


def test_strip_shows_progress_next_item_and_amendments(tmp_path):
    from xlii.tui.plan_strip import plan_strip_state

    _plans(tmp_path)
    name, line = plan_strip_state(_state(tmp_path))
    assert name == "current"
    flat = line.plain
    assert "2/3" in flat                     # [x] + [x?] both count as checked
    assert "▸ first thing" in flat           # the next OPEN item
    assert "✎1" in flat                      # the pending amendment rides along


def test_strip_done_plan_shows_only_in_plan_mode(tmp_path):
    from xlii.tui.plan_strip import plan_strip_state

    _plans(tmp_path, "- [x] {#a} one — receipt: ok\n")
    assert plan_strip_state(_state(tmp_path)) is None            # done work leaves the chrome
    name, line = plan_strip_state(_state(tmp_path, plan_mode=True))
    assert "1/1" in line.plain and "✓" in line.plain


def test_strip_named_plan_and_long_item_cap(tmp_path):
    from xlii.tui.plan_strip import plan_strip_state

    d = tmp_path / "plans"
    d.mkdir(parents=True)
    (d / "spec.md").write_text(f"- [ ] {{#a}} {'x' * 200}\n")   # no current.md → newest wins
    name, line = plan_strip_state(_state(tmp_path))
    assert name == "spec"
    assert "plan[spec]" in line.plain
    assert "x" * 200 not in line.plain                           # capped, ellipsized
