"""plan-write-domain P2 — `plan check`: the receipted checkbox op.

Layers: the core op (xlii.plan_ops), the `plan_check` agent tool (palette
membership + handler under the execute/preview profiles), and the human
mirrors (/plan check, /plan show).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from xlii import tools
from xlii.commands import dispatch_repl_command, find_repl_command
from xlii.mode_controller import PlanController
from xlii.plan_ops import (
    PlanOpError,
    check_plan_item,
    plan_item_ids,
    render_plan_lines,
    resolve_plan_file,
)
from xlii.repl_cmds import register_all
from xlii.tools import PLAN_MODE_TOOLS, WORKER_REGISTRY, plan_write_schemas, tool_schemas

from tests.helpers import FakeConsole, make_agent, make_tool_ctx, script_iterations

register_all()

PLAN = """# goal

- [ ] {#a} first thing
- [ ] {#b} second thing
  - [ ] {#c} nested thing
prose mentioning nothing
"""


def _plans(tmp_path, text=PLAN, name="current.md") -> Path:
    d = tmp_path / "plans"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text)
    return d


# --------------------------------------------------------------------------- #
#  core op
# --------------------------------------------------------------------------- #

def test_check_receipted_happy_path(tmp_path):
    d = _plans(tmp_path)
    res = check_plan_item(d, "a", evidence="commit abc123")
    assert res.changed and res.old_state == " " and res.new_state == "x"
    text = (d / "current.md").read_text()
    assert "- [x] {#a} first thing — receipt: commit abc123" in text
    assert "- [ ] {#b} second thing" in text  # neighbors untouched


def test_check_unreceipted_is_marked_weaker(tmp_path):
    d = _plans(tmp_path)
    res = check_plan_item(d, "a")
    assert res.changed and res.new_state == "x?"
    text = (d / "current.md").read_text()
    assert "- [x?] {#a} first thing" in text
    assert "receipt:" not in text


def test_indented_checkbox_checks_too(tmp_path):
    d = _plans(tmp_path)
    res = check_plan_item(d, "c", evidence="pytest run")
    assert res.changed
    assert "  - [x] {#c} nested thing — receipt: pytest run" in (d / "current.md").read_text()


def test_evidence_upgrades_unreceipted_check(tmp_path):
    d = _plans(tmp_path, "- [x?] {#a} thing\n")
    res = check_plan_item(d, "a", evidence="test-run 42")
    assert res.changed and res.old_state == "x?" and res.new_state == "x"
    assert "upgraded" in res.note
    assert (d / "current.md").read_text() == "- [x] {#a} thing — receipt: test-run 42\n"


def test_already_receipted_is_idempotent(tmp_path):
    body = "- [x] {#a} thing — receipt: old\n"
    d = _plans(tmp_path, body)
    for ev in ("new evidence", None):
        res = check_plan_item(d, "a", evidence=ev)
        assert not res.changed and res.new_state == "x"
        assert (d / "current.md").read_text() == body  # no duplicate annotation


def test_unreceipted_recheck_without_evidence_is_noop(tmp_path):
    body = "- [x?] {#a} thing\n"
    d = _plans(tmp_path, body)
    res = check_plan_item(d, "a")
    assert not res.changed and res.new_state == "x?"
    assert "evidence" in res.note  # teaches the upgrade path
    assert (d / "current.md").read_text() == body


def test_id_not_found_lists_known_ids(tmp_path):
    d = _plans(tmp_path)
    try:
        check_plan_item(d, "nope")
        raise AssertionError("expected PlanOpError")
    except PlanOpError as e:
        msg = str(e)
        assert "{#nope}" in msg and "a, b, c" in msg


def test_duplicate_id_is_an_error(tmp_path):
    d = _plans(tmp_path, "- [ ] {#a} one\n- [ ] {#a} two\n")
    try:
        check_plan_item(d, "a")
        raise AssertionError("expected PlanOpError")
    except PlanOpError as e:
        assert "unique" in str(e)
    assert "[x" not in (d / "current.md").read_text()


def test_prose_only_id_is_not_a_checkbox_hit(tmp_path):
    # A {#id} that appears ONLY on a non-checkbox line isn't a checkbox — the
    # checkbox-only scan (Fix 2) treats it as not-found, never a text edit.
    d = _plans(tmp_path, "See {#a} above for details.\n")
    try:
        check_plan_item(d, "a")
        raise AssertionError("expected PlanOpError")
    except PlanOpError as e:
        assert "no '{#a}' checkbox" in str(e)


def test_bad_slug_id_is_an_error(tmp_path):
    d = _plans(tmp_path)
    for bad in ("", "UPPER", "sp ace", "a_b"):
        try:
            check_plan_item(d, bad)
            raise AssertionError("expected PlanOpError")
        except PlanOpError as e:
            assert "slug" in str(e)


def test_explicit_plan_name_targets_that_file(tmp_path):
    d = _plans(tmp_path)  # current.md exists too
    (d / "other.md").write_text("- [ ] {#z} other item\n")
    res = check_plan_item(d, "z", evidence="ref", plan="other")
    assert res.file.name == "other.md" and res.changed
    assert "- [x] {#z} other item — receipt: ref" in (d / "other.md").read_text()
    assert "[x]" not in (d / "current.md").read_text()


def test_missing_explicit_plan_errors(tmp_path):
    d = _plans(tmp_path)
    try:
        check_plan_item(d, "a", plan="ghost")
        raise AssertionError("expected PlanOpError")
    except PlanOpError as e:
        assert "ghost.md" in str(e)


def test_current_md_is_the_default_target(tmp_path):
    d = _plans(tmp_path)
    (d / "older.md").write_text("- [ ] {#a} decoy\n")
    res = check_plan_item(d, "a")
    assert res.file.name == "current.md"


def test_newest_plan_is_the_fallback_without_current(tmp_path):
    d = tmp_path / "plans"
    d.mkdir()
    (d / "old.md").write_text("- [ ] {#a} old item\n")
    (d / "new.md").write_text("- [ ] {#a} new item\n")
    os.utime(d / "old.md", (1_000, 1_000))
    assert resolve_plan_file(d).name == "new.md"
    res = check_plan_item(d, "a", evidence="e")
    assert res.file.name == "new.md"


def test_no_plan_files_is_a_teaching_error(tmp_path):
    d = tmp_path / "plans"  # doesn't even exist
    try:
        check_plan_item(d, "a")
        raise AssertionError("expected PlanOpError")
    except PlanOpError as e:
        assert "/plan" in str(e)


def test_only_the_target_line_changes(tmp_path):
    text = "# goal\r\n\n- [ ] {#a} first\n\t- [ ] {#b} tabbed  \n- [ ] {#c} last no newline"
    d = _plans(tmp_path, text)
    check_plan_item(d, "b", evidence="ref")
    before = text.splitlines(keepends=True)
    # raw bytes: read_text would itself normalize the \r\n we're checking for
    after = (d / "current.md").read_bytes().decode("utf-8").splitlines(keepends=True)
    assert len(before) == len(after)
    for i, (b, a) in enumerate(zip(before, after)):
        if "{#b}" in b:
            assert a == "\t- [x] {#b} tabbed   — receipt: ref\n"
        else:
            assert a == b, i  # every other byte preserved (incl. \r\n, no-EOL tail)


def test_evidence_collapsed_to_one_bounded_line(tmp_path):
    d = _plans(tmp_path)
    res = check_plan_item(d, "a", evidence="line1\nline2\t  spaced\n" + "y" * 300)
    annotation = res.line.split(" — receipt: ", 1)[1]
    assert "\n" not in annotation and "\t" not in annotation
    assert annotation.startswith("line1 line2 spaced")
    assert len(annotation) == 120 and annotation.endswith("…")


def test_plan_item_ids_reads_only_checkbox_ids():
    assert plan_item_ids(PLAN + "notes {#not-a-box}\n") == ["a", "b", "c"]


# --------------------------------------------------------------------------- #
#  review round — containment + checkbox-only scan + CRLF + one-box-per-line
# --------------------------------------------------------------------------- #

def test_plan_param_rejects_path_traversal(tmp_path):
    """Fix 1 (HIGH): `plan` is a bare NAME, never a path — a separator, `..`,
    or an anchor is refused, so plan_check can never escape plans/."""
    d = _plans(tmp_path)
    outside = tmp_path / "SECRETS.md"
    outside.write_text("- [ ] {#pwn} secret\n")
    for bad in ("../../SECRETS", "../SECRETS", "a/b", "/etc/passwd", "sub/plan"):
        try:
            check_plan_item(d, "pwn", evidence="INJECTED", plan=bad)
            raise AssertionError(f"expected PlanOpError for plan={bad!r}")
        except PlanOpError as e:
            assert "not a" in str(e) and "path" in str(e)
    # nothing outside plans/ was touched
    assert outside.read_text() == "- [ ] {#pwn} secret\n"


def test_plan_param_absolute_path_refused(tmp_path):
    d = _plans(tmp_path)
    target = tmp_path / "evil.md"
    target.write_text("- [ ] {#z} item\n")
    try:
        check_plan_item(d, "z", evidence="x", plan=str(tmp_path / "evil"))
        raise AssertionError("expected PlanOpError")
    except PlanOpError as e:
        assert "path" in str(e)
    assert target.read_text() == "- [ ] {#z} item\n"


def test_symlinked_plan_file_escaping_plans_dir_refused(tmp_path):
    """A plan 'file' that is a symlink pointing OUT of plans/ is caught by the
    resolved-containment guard (Fix 1 belt-and-suspenders)."""
    d = _plans(tmp_path)
    secret = tmp_path / "outside.md"
    secret.write_text("- [ ] {#s} item\n")
    os.symlink(secret, d / "leak.md")
    try:
        check_plan_item(d, "s", evidence="x", plan="leak")
        raise AssertionError("expected PlanOpError")
    except PlanOpError as e:
        assert "outside" in str(e)
    assert secret.read_text() == "- [ ] {#s} item\n"


def test_prose_mention_does_not_block_checking(tmp_path):
    """Fix 2: a prose line mentioning {#a} must not make {#a} look duplicated."""
    d = _plans(tmp_path, "- [ ] {#a} first thing\nNote: {#a} is blocked on infra\n")
    res = check_plan_item(d, "a", evidence="ref")
    assert res.changed
    text = (d / "current.md").read_text()
    assert "- [x] {#a} first thing — receipt: ref" in text
    assert "Note: {#a} is blocked on infra" in text  # prose untouched


def test_receipt_ref_does_not_lock_out_the_referenced_id(tmp_path):
    """Fix 2 self-lockout: checking {#a} with a receipt naming {#b} must not
    make {#b} un-checkable (the model is told to put refs in evidence)."""
    d = _plans(tmp_path)
    check_plan_item(d, "a", evidence="see {#b} for follow-up")
    res = check_plan_item(d, "b", evidence="ref")   # must not raise "appears 2 times"
    assert res.changed and res.new_state == "x"
    assert "- [x] {#b} second thing — receipt: ref" in (d / "current.md").read_text()


def test_genuine_duplicate_checkbox_id_still_errors(tmp_path):
    d = _plans(tmp_path, "- [ ] {#a} one\n- [ ] {#a} two\n")
    try:
        check_plan_item(d, "a")
        raise AssertionError("expected PlanOpError")
    except PlanOpError as e:
        assert "unique" in str(e)


def test_crlf_file_round_trips_without_cr_accretion(tmp_path):
    """Fix 3: the write must not translate the CRLF the read preserved (no
    \\r\\r accretion), on any platform."""
    d = _plans(tmp_path, "# goal\r\n- [ ] {#a} first\r\n- [ ] {#b} second\r\n")
    check_plan_item(d, "a", evidence="ref")
    raw = (d / "current.md").read_bytes()
    assert b"\r\r" not in raw
    assert b"- [x] {#a} first \xe2\x80\x94 receipt: ref\r\n" in raw
    assert b"- [ ] {#b} second\r\n" in raw  # neighbor CRLF intact


def test_multiple_checkboxes_on_one_line_refused(tmp_path):
    """Fix 4: a line with two boxes would let the anchored rewrite flip the
    first while reporting the requested id — refuse instead."""
    d = _plans(tmp_path, "- [ ] {#a} first - [ ] {#b} second\n")
    try:
        check_plan_item(d, "a", evidence="ref")
        raise AssertionError("expected PlanOpError")
    except PlanOpError as e:
        assert "multiple checkboxes" in str(e)
    assert (d / "current.md").read_text() == "- [ ] {#a} first - [ ] {#b} second\n"


def test_receipt_containing_box_substring_does_not_trip_multibox_guard(tmp_path):
    """The multi-box guard counts boxes in the item text only, not in a receipt
    annotation whose evidence happens to contain a '- [ ]' substring."""
    d = _plans(tmp_path, "- [x] {#a} done — receipt: reverted the - [ ] hack\n")
    # a re-check is the idempotent no-op, NOT a false 'multiple checkboxes' error
    res = check_plan_item(d, "a", evidence="anything")
    assert not res.changed and res.new_state == "x"


def test_t_plan_check_plan_param_traversal_is_tool_error(tmp_path):
    """The escape is closed at the tool boundary too (the handler passes `plan`
    straight through to the guarded core)."""
    _plans(tmp_path / ".xlii")
    (tmp_path / "README.md").write_text("- [ ] {#x} item\n")
    ctx = make_tool_ctx(tmp_path)
    r = tools.t_plan_check(ctx, {"item_id": "x", "evidence": "e", "plan": "../../README"})
    assert r.is_error and "path" in r.content
    assert (tmp_path / "README.md").read_text() == "- [ ] {#x} item\n"
    assert ctx.dirty_paths == set()


# --------------------------------------------------------------------------- #
#  agent tool — palette membership + handler under real profiles
# --------------------------------------------------------------------------- #

def test_plan_check_in_full_palette_not_in_plan_palette():
    full = {s["function"]["name"] for s in tool_schemas()}
    plan = {s["function"]["name"] for s in plan_write_schemas()}
    assert "plan_check" in full
    assert "plan_check" not in plan          # the planner edits the file directly
    assert "plan_check" not in PLAN_MODE_TOOLS
    assert "plan_check" not in WORKER_REGISTRY
    assert "plan_check" not in tools.PARALLEL_SAFE


def test_t_plan_check_is_the_sanctioned_path_under_execute_deny(tmp_path):
    """The execute profile denies plans/ for write_file/edit_file — plan_check
    still works: the structured op IS the sanctioned write."""
    _plans(tmp_path / ".xlii", name="current.md")
    ctx = make_tool_ctx(tmp_path)
    plans = str((tmp_path / ".xlii" / "plans").resolve())
    ctx.write_deny = (plans,)
    # the free-text path is refused...
    r = tools.t_write_file(ctx, {"path": ".xlii/plans/current.md", "content": "x"})
    assert r.is_error and "plan_check" in r.content  # refusal points at the op
    # ...the structured op succeeds
    r = tools.t_plan_check(ctx, {"item_id": "a", "evidence": "commit abc"})
    assert not r.is_error and "checked [x]" in r.content
    assert "- [x] {#a}" in (tmp_path / ".xlii/plans/current.md").read_text()
    assert ".xlii/plans/current.md" in ctx.dirty_paths  # in-root → queued for sync


def test_t_plan_check_works_in_preview_outside_project_root(tmp_path):
    """state_dir_override: plans_dir outside project_root — the handler must not
    route through _resolve_in_project; dirty-paths skip pinned."""
    root = tmp_path / "repo"
    root.mkdir()
    state_dir = tmp_path / "preview-state"
    _plans(state_dir, name="current.md")
    ctx = make_tool_ctx(root)
    ctx.project.xli_dir = state_dir
    r = tools.t_plan_check(ctx, {"item_id": "b"})
    assert not r.is_error and "[x?]" in r.content
    assert "- [x?] {#b}" in (state_dir / "plans" / "current.md").read_text()
    assert ctx.dirty_paths == set()  # out-of-root: nothing to sync


def test_t_plan_check_errors_are_tool_results_not_raises(tmp_path):
    ctx = make_tool_ctx(tmp_path)
    r = tools.t_plan_check(ctx, {"item_id": "a"})
    assert r.is_error and "no plan files" in r.content


def test_plan_mode_turn_cannot_call_plan_check(tmp_path):
    """Deliberately absent from plan mode's palette: a scripted call is refused
    at dispatch as unadvertised."""
    plans_body = "- [ ] {#a} thing\n"
    d = _plans(tmp_path / ".xlii")
    (d / "current.md").write_text(plans_body)
    agent = make_agent(tmp_path)
    agent.set_mode(PlanController())
    script_iterations(
        agent,
        ("checking", [("plan_check", {"item_id": "a", "evidence": "e"})]),
        ("done", None),
    )
    agent.run_turn("go")
    results = [h["content"] for h in agent.history if h.get("role") == "tool"]
    assert any("advertised palette" in t for t in results)
    assert (d / "current.md").read_text() == plans_body  # untouched


def test_execute_turn_checks_item_end_to_end(tmp_path):
    _plans(tmp_path / ".xlii")
    agent = make_agent(tmp_path)  # active_mode None = execute profile
    script_iterations(
        agent,
        ("checking", [("plan_check", {"item_id": "a", "evidence": "commit f00"})]),
        ("done", None),
    )
    agent.run_turn("mark a done")
    text = (tmp_path / ".xlii/plans/current.md").read_text()
    assert "- [x] {#a} first thing — receipt: commit f00" in text


# --------------------------------------------------------------------------- #
#  slash mirrors — /plan check and /plan show
# --------------------------------------------------------------------------- #

def _slash_ctx(tmp_path):
    agent = make_agent(tmp_path)
    proj = agent.project
    Path(proj.xli_dir).mkdir(parents=True, exist_ok=True)
    con = FakeConsole()
    from types import SimpleNamespace

    return {"agent": agent, "console": con, "project": proj,
            "state": SimpleNamespace(loop=None)}, con


def test_slash_plan_check_receipted(tmp_path):
    ctx, con = _slash_ctx(tmp_path)
    _plans(tmp_path / ".xlii")
    dispatch_repl_command("/plan check a --receipt abc123", ctx)
    assert "- [x] {#a} first thing — receipt: abc123" in (
        tmp_path / ".xlii/plans/current.md"
    ).read_text()
    assert "checked [x]" in con.text


def test_slash_plan_check_teaches_on_bad_id(tmp_path):
    ctx, con = _slash_ctx(tmp_path)
    _plans(tmp_path / ".xlii")
    dispatch_repl_command("/plan check nope", ctx)
    assert "known ids: a, b, c" in con.text


def test_slash_plan_check_usage_when_no_id(tmp_path):
    ctx, con = _slash_ctx(tmp_path)
    dispatch_repl_command("/plan check", ctx)
    assert "usage: /plan check" in con.text


def test_slash_plan_show_styles_states(tmp_path):
    ctx, con = _slash_ctx(tmp_path)
    d = _plans(tmp_path / ".xlii",
               "- [x] {#a} done — receipt: r\n- [x?] {#b} weak\n- [ ] {#c} open\nprose\n")
    assert d.name == "plans"
    dispatch_repl_command("/plan show", ctx)
    assert "[green]" in con.text and "[yellow]" in con.text and "[dim]" in con.text
    assert "prose" in con.text


def test_render_plan_lines_styles():
    lines = render_plan_lines("- [x] {#a} done\n- [x?] {#b} weak\n- [ ] {#c} open\nprose")
    assert lines[0].startswith("[green]") and lines[1].startswith("[yellow]")
    assert lines[2].startswith("[dim]") and lines[3] == "prose"


def test_plan_usage_mentions_check_and_show():
    cmd = find_repl_command("/plan")
    assert cmd is not None
    assert "check" in cmd.usage and "show" in cmd.usage


def test_stale_current_still_targetable_by_ops(tmp_path):
    """'current' is excluded from the saved-plans OFFER list only — the check
    op happily targets it (regression guard against over-extending Fix 5)."""
    d = _plans(tmp_path / "x")
    os.utime(d / "current.md", (1_000, 1_000))
    assert time.time() > 1_000
    assert resolve_plan_file(d).name == "current.md"
