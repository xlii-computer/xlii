"""plan-write-domain P3 — `plan amend`: the implementer's proposal channel.

Layers, mirroring test_plan_check.py: the core op (xlii.plan_ops.amend_plan +
pending_amendments), the structural invariant ([?] entries are invisible to
every checkbox mechanism), the `plan_amend` agent tool (palette membership +
handler under the execute/preview profiles), and the human mirrors
(/plan amend, the /plan entry note, /plan show styling).
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from types import SimpleNamespace

from xlii import tools
from xlii.commands import dispatch_repl_command, find_repl_command
from xlii.mode_controller import PlanController
from xlii.plan_ops import (
    AMENDMENTS_HEADING,
    PlanOpError,
    amend_plan,
    check_plan_item,
    pending_amendments,
    plan_item_ids,
    render_plan_lines,
)
from xlii.repl_cmds import register_all
from xlii.tools import PLAN_MODE_TOOLS, WORKER_REGISTRY, plan_write_schemas, tool_schemas

from tests.helpers import FakeConsole, make_agent, make_tool_ctx, script_iterations

register_all()

PLAN = """# goal

- [ ] {#a} first thing
- [ ] {#b} second thing
prose mentioning nothing
"""

_ENTRY_RE = re.compile(
    r"^- \[\?\] \{#am-(\d+)\} \(\d{4}-\d{2}-\d{2}\) (re \{#[a-z0-9-]+\}:|new:) .+$"
)


def _plans(tmp_path, text=PLAN, name="current.md") -> Path:
    d = tmp_path / "plans"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text)
    return d


# --------------------------------------------------------------------------- #
#  core op
# --------------------------------------------------------------------------- #

def test_amend_re_item_happy_path(tmp_path):
    d = _plans(tmp_path)
    res = amend_plan(d, "found X, spec assumes Y — propose Z", item_id="a")
    assert res.amend_id == "am-1" and res.section_created
    assert _ENTRY_RE.match(res.line), res.line
    text = (d / "current.md").read_text()
    assert AMENDMENTS_HEADING in text
    assert "re {#a}: found X, spec assumes Y — propose Z" in text
    assert text.startswith(PLAN)  # append-only: the plan itself is untouched


def test_amend_new_item_form(tmp_path):
    d = _plans(tmp_path)
    res = amend_plan(d, "we also need a rollback step")
    assert res.amend_id == "am-1"
    assert ") new: we also need a rollback step" in res.line
    assert "re {#" not in res.line


def test_auto_numbering_across_both_forms(tmp_path):
    d = _plans(tmp_path)
    assert amend_plan(d, "one", item_id="a").amend_id == "am-1"
    assert amend_plan(d, "two").amend_id == "am-2"
    assert amend_plan(d, "three", item_id="b").amend_id == "am-3"
    # numbering follows the MAX anywhere in the file, so a gap never collides
    text = (d / "current.md").read_text()
    (d / "current.md").write_text(text.replace("{#am-2}", "{#am-7}"))
    assert amend_plan(d, "four").amend_id == "am-8"


def test_section_created_once_then_reused(tmp_path):
    d = _plans(tmp_path)
    assert amend_plan(d, "one").section_created is True
    assert amend_plan(d, "two").section_created is False
    text = (d / "current.md").read_text()
    assert text.count(AMENDMENTS_HEADING) == 1
    assert [i for i, _ in pending_amendments(text)] == ["am-1", "am-2"]


def test_append_preserves_existing_bytes(tmp_path):
    text = "# goal\r\n- [ ] {#a} first\n- [ ] {#b} last no newline"
    d = _plans(tmp_path, text)
    amend_plan(d, "note", item_id="a")
    raw = (d / "current.md").read_bytes().decode("utf-8")
    # every existing byte survives verbatim (the missing EOL is healed AFTER it)
    assert raw.startswith(text + "\n")
    assert b"\r\r" not in (d / "current.md").read_bytes()
    assert AMENDMENTS_HEADING in raw


def test_amend_text_collapsed_and_capped(tmp_path):
    d = _plans(tmp_path)
    res = amend_plan(d, "line1\nline2\t spaced " + "y" * 600)
    body = res.line.split(" new: ", 1)[1]
    assert "\n" not in body and "\t" not in body
    assert body.startswith("line1 line2 spaced")
    assert len(body) == 500 and body.endswith("…")


def test_empty_text_is_a_teaching_error(tmp_path):
    d = _plans(tmp_path)
    for bad in ("", "   \n\t"):
        try:
            amend_plan(d, bad)
            raise AssertionError("expected PlanOpError")
        except PlanOpError as e:
            assert "amendment text required" in str(e)


def test_unknown_item_id_lists_known_ids(tmp_path):
    d = _plans(tmp_path)
    try:
        amend_plan(d, "text", item_id="nope")
        raise AssertionError("expected PlanOpError")
    except PlanOpError as e:
        assert "{#nope}" in str(e) and "known ids: a, b" in str(e)
    assert AMENDMENTS_HEADING not in (d / "current.md").read_text()  # nothing written


def test_bad_slug_item_id_is_an_error(tmp_path):
    d = _plans(tmp_path)
    try:
        amend_plan(d, "text", item_id="UP PER")
        raise AssertionError("expected PlanOpError")
    except PlanOpError as e:
        assert "slug" in str(e)


# --------------------------------------------------------------------------- #
#  review round — section-anchored insertion, fences, decode, numbering
# --------------------------------------------------------------------------- #

def test_amend_inserts_into_section_not_at_eof(tmp_path):
    """Fix 1 (three lenses): with sections AFTER ## Amendments (normal — the
    preamble tells the planner to keep notes/questions), the entry must file
    under the queue heading — never under whatever section is last."""
    text = ("# goal\n- [ ] {#a} one\n\n" + AMENDMENTS_HEADING + "\n"
            "- [?] {#am-1} (2026-07-12) new: first\n\n## Notes\nkeep me last\n")
    d = _plans(tmp_path, text)
    amend_plan(d, "second", item_id="a")
    lines = (d / "current.md").read_text().splitlines()
    i_head = lines.index(AMENDMENTS_HEADING)
    i_notes = lines.index("## Notes")
    i_new = next(i for i, ln in enumerate(lines) if "{#am-2}" in ln)
    assert i_head < i_new < i_notes                       # inside the section
    assert lines[i_new - 1].startswith("- [?] {#am-1}")   # after the last entry
    assert lines[-1] == "keep me last"                    # trailing section intact


def test_amend_insertion_preserves_all_other_bytes(tmp_path):
    text = ("# goal\r\n- [ ] {#a} one\r\n" + AMENDMENTS_HEADING + "\r\n"
            "- [?] {#am-1} (2026-07-12) new: first\r\n## Notes\r\nlast no newline")
    d = _plans(tmp_path, text)
    amend_plan(d, "second")
    raw = (d / "current.md").read_bytes().decode("utf-8")
    inserted = next(ln for ln in raw.splitlines(keepends=True) if "{#am-2}" in ln)
    assert inserted.endswith("\r\n")                      # matches the section's ending
    assert raw.replace(inserted, "", 1) == text           # every other byte identical
    assert b"\r\r" not in (d / "current.md").read_bytes()


def test_amend_into_empty_section_directly_after_heading(tmp_path):
    d = _plans(tmp_path, "# goal\n" + AMENDMENTS_HEADING + "\n\n## Notes\nnote\n")
    amend_plan(d, "first")
    lines = (d / "current.md").read_text().splitlines()
    assert lines[lines.index(AMENDMENTS_HEADING) + 1].startswith("- [?] {#am-1}")


def test_amend_heading_as_last_line_without_eol(tmp_path):
    d = _plans(tmp_path, "# goal\n" + AMENDMENTS_HEADING)  # no trailing newline
    amend_plan(d, "first")
    assert AMENDMENTS_HEADING + "\n- [?] {#am-1}" in (d / "current.md").read_text()


FENCED = """# goal

- [ ] {#a} real item

```
## Amendments
- [?] {#am-9} (2026-01-01) new: fenced example
- [ ] {#a} fenced duplicate checkbox
- [ ] {#fake} fenced example item
```
"""


def test_fenced_examples_are_invisible_to_every_scan(tmp_path):
    """Fix 2: fenced ## Amendments / [?] / checkbox lines are EXAMPLES, not
    structure — section creation, numbering, pending counts, ids, uniqueness
    all look through the fence filter."""
    d = _plans(tmp_path, FENCED)
    assert plan_item_ids(FENCED) == ["a"]                 # {#fake} not indexed
    assert pending_amendments(FENCED) == []               # no phantom ✎ count
    res = amend_plan(d, "real proposal")
    assert res.section_created                            # fenced heading ≠ section
    assert res.amend_id == "am-1"                         # fenced am-9 ignored
    assert (d / "current.md").read_text().startswith(FENCED)
    # the fenced duplicate-looking checkbox doesn't lock out the real {#a}
    assert check_plan_item(d, "a", evidence="ref").changed


def test_non_utf8_plan_is_a_teaching_error(tmp_path):
    d = tmp_path / "plans"
    d.mkdir()
    (d / "current.md").write_bytes(b"- [ ] {#a} caf\xe9 latin-1\n")
    for op in (lambda: amend_plan(d, "text"),
               lambda: check_plan_item(d, "a")):
        try:
            op()
            raise AssertionError("expected PlanOpError")
        except PlanOpError as e:
            assert "UTF-8" in str(e) and "current.md" in str(e)


def test_provenance_token_outside_section_bumps_numbering(tmp_path):
    """Fix 5/8b: the accept flow keeps `(from {#am-N})` on the folded item —
    the FILE-WIDE max scan honors it, so ids are never reused."""
    d = _plans(tmp_path, "- [x] {#a} done (from {#am-2})\n")
    assert amend_plan(d, "next proposal").amend_id == "am-3"


def test_t_plan_amend_null_or_blank_text_is_refused(tmp_path):
    _plans(tmp_path / ".xlii")
    ctx = make_tool_ctx(tmp_path)
    for bad_args in ({"text": None}, {}, {"text": "   "}):
        r = tools.t_plan_amend(ctx, bad_args)
        assert r.is_error and "needs `text`" in r.content
    text = (tmp_path / ".xlii/plans/current.md").read_text()
    assert "None" not in text and AMENDMENTS_HEADING not in text


def test_t_plan_check_null_item_id_never_stringifies(tmp_path):
    _plans(tmp_path / ".xlii")
    ctx = make_tool_ctx(tmp_path)
    r = tools.t_plan_check(ctx, {"item_id": None})
    assert r.is_error and "slug" in r.content
    assert "None" not in (tmp_path / ".xlii/plans/current.md").read_text()


def test_entry_note_targets_amends_default_file(tmp_path):
    """Finding 10: with current.md ABSENT, amend defaults to the newest named
    plan — the /plan entry note must count pending on that same file and name
    it."""
    d = _plans(tmp_path / ".xlii", name="feature.md")
    amend_plan(d, "queued on the named plan")
    agent_ctx, con = _slash_ctx(tmp_path)
    dispatch_repl_command("/plan", agent_ctx)
    assert "1 pending amendment(s) in plan 'feature'" in con.text


# --------------------------------------------------------------------------- #
#  containment — same resolver, same rules as plan_check
# --------------------------------------------------------------------------- #

def test_plan_param_traversal_refused(tmp_path):
    d = _plans(tmp_path)
    outside = tmp_path / "SECRETS.md"
    outside.write_text("- [ ] {#s} secret\n")
    for bad in ("../SECRETS", "a/b", "/etc/passwd"):
        try:
            amend_plan(d, "pwn", plan=bad)
            raise AssertionError(f"expected PlanOpError for plan={bad!r}")
        except PlanOpError as e:
            assert "not a" in str(e) and "path" in str(e)
    assert outside.read_text() == "- [ ] {#s} secret\n"


# --------------------------------------------------------------------------- #
#  the structural invariant — [?] entries are inert to every checkbox mechanism
# --------------------------------------------------------------------------- #

def test_amendments_invisible_to_checkbox_machinery(tmp_path):
    d = _plans(tmp_path)
    amend_plan(d, "contest the first thing", item_id="a")
    amend_plan(d, "add a rollback step")
    text = (d / "current.md").read_text()
    # not indexed: the pane index / known-ids list is unchanged
    assert plan_item_ids(text) == ["a", "b"]
    # the contested item is still checkable — no duplicate-id lockout
    res = check_plan_item(d, "a", evidence="ref")
    assert res.changed and res.new_state == "x"
    # the amendment itself is untargetable by plan_check
    try:
        check_plan_item(d, "am-1")
        raise AssertionError("expected PlanOpError")
    except PlanOpError as e:
        assert "no '{#am-1}' checkbox" in str(e)


def test_render_styles_amendment_lines(tmp_path):
    lines = render_plan_lines(
        "- [x] {#a} done\n- [?] {#am-1} (2026-07-12) new: idea\nprose"
    )
    assert lines[0].startswith("[green]")
    assert lines[1].startswith("[magenta]") and lines[1].endswith("[/magenta]")
    assert lines[2] == "prose"


# --------------------------------------------------------------------------- #
#  agent tool — palette membership + handler under real profiles
# --------------------------------------------------------------------------- #

def test_plan_amend_in_full_palette_not_in_plan_palette():
    full = {s["function"]["name"] for s in tool_schemas()}
    plan = {s["function"]["name"] for s in plan_write_schemas()}
    assert "plan_amend" in full
    assert "plan_amend" not in plan          # the planner RESOLVES, never files
    assert "plan_amend" not in PLAN_MODE_TOOLS
    assert "plan_amend" not in WORKER_REGISTRY
    assert "plan_amend" not in tools.PARALLEL_SAFE


def test_t_plan_amend_works_under_execute_deny(tmp_path):
    _plans(tmp_path / ".xlii")
    ctx = make_tool_ctx(tmp_path)
    ctx.write_deny = (str((tmp_path / ".xlii" / "plans").resolve()),)
    r = tools.t_plan_amend(ctx, {"text": "found X — propose Z", "item_id": "a"})
    assert not r.is_error and "queued amendment {#am-1}" in r.content
    assert "re {#a}:" in (tmp_path / ".xlii/plans/current.md").read_text()
    assert ".xlii/plans/current.md" in ctx.dirty_paths


def test_t_plan_amend_preview_outside_project_root(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    state_dir = tmp_path / "preview-state"
    _plans(state_dir)
    ctx = make_tool_ctx(root)
    ctx.project.xli_dir = state_dir
    r = tools.t_plan_amend(ctx, {"text": "works out of tree"})
    assert not r.is_error
    assert "new: works out of tree" in (state_dir / "plans" / "current.md").read_text()
    assert ctx.dirty_paths == set()  # out-of-root: nothing to sync


def test_t_plan_amend_errors_are_tool_results(tmp_path):
    ctx = make_tool_ctx(tmp_path)
    r = tools.t_plan_amend(ctx, {"text": "no plans exist yet"})
    assert r.is_error and "no plan files" in r.content


def test_plan_mode_turn_cannot_call_plan_amend(tmp_path):
    """Deliberately absent from plan mode's palette — the planner resolves
    amendments by editing directly; a scripted call is refused at dispatch."""
    d = _plans(tmp_path / ".xlii")
    agent = make_agent(tmp_path)
    agent.set_mode(PlanController())
    script_iterations(
        agent,
        ("filing", [("plan_amend", {"text": "sneaky"})]),
        ("done", None),
    )
    agent.run_turn("go")
    results = [h["content"] for h in agent.history if h.get("role") == "tool"]
    assert any("advertised palette" in t for t in results)
    assert AMENDMENTS_HEADING not in (d / "current.md").read_text()


def test_execute_turn_amends_end_to_end(tmp_path):
    _plans(tmp_path / ".xlii")
    agent = make_agent(tmp_path)  # active_mode None = execute profile
    script_iterations(
        agent,
        ("proposing", [("plan_amend", {"text": "spec assumes Y — propose Z",
                                       "item_id": "b"})]),
        ("done", None),
    )
    agent.run_turn("flag the problem")
    text = (tmp_path / ".xlii/plans/current.md").read_text()
    assert "re {#b}: spec assumes Y — propose Z" in text


# --------------------------------------------------------------------------- #
#  slash mirrors — /plan amend, the entry note, /plan show
# --------------------------------------------------------------------------- #

def _slash_ctx(tmp_path):
    agent = make_agent(tmp_path)
    proj = agent.project
    Path(proj.xli_dir).mkdir(parents=True, exist_ok=True)
    con = FakeConsole()
    return {"agent": agent, "console": con, "project": proj,
            "state": SimpleNamespace(loop=None)}, con


def test_slash_plan_amend_re_item(tmp_path):
    ctx, con = _slash_ctx(tmp_path)
    _plans(tmp_path / ".xlii")
    dispatch_repl_command("/plan amend --re a found X, propose Z", ctx)
    text = (tmp_path / ".xlii/plans/current.md").read_text()
    assert "re {#a}: found X, propose Z" in text
    assert "queued amendment {#am-1}" in con.text


def test_slash_plan_amend_new_and_named_plan(tmp_path):
    ctx, con = _slash_ctx(tmp_path)
    d = _plans(tmp_path / ".xlii")
    (d / "other.md").write_text("- [ ] {#z} other item\n")
    dispatch_repl_command("/plan amend --plan other add a rollback", ctx)
    assert "new: add a rollback" in (d / "other.md").read_text()
    assert AMENDMENTS_HEADING not in (d / "current.md").read_text()


def test_slash_plan_amend_usage_when_no_text(tmp_path):
    ctx, con = _slash_ctx(tmp_path)
    dispatch_repl_command("/plan amend", ctx)
    assert "usage: /plan amend" in con.text


def test_slash_plan_amend_teaches_on_bad_item(tmp_path):
    ctx, con = _slash_ctx(tmp_path)
    _plans(tmp_path / ".xlii")
    dispatch_repl_command("/plan amend --re nope some text", ctx)
    assert "known ids: a, b" in con.text


def test_plan_entry_notes_pending_amendments(tmp_path):
    ctx, con = _slash_ctx(tmp_path)
    d = _plans(tmp_path / ".xlii")
    amend_plan(d, "first proposal", item_id="a")
    amend_plan(d, "second proposal")
    dispatch_repl_command("/plan", ctx)
    assert "2 pending amendment(s) in the working plan" in con.text
    assert "/plan continue" in con.text


def test_plan_entry_quiet_without_amendments(tmp_path):
    ctx, con = _slash_ctx(tmp_path)
    _plans(tmp_path / ".xlii")
    dispatch_repl_command("/plan", ctx)
    assert "pending amendment" not in con.text


def test_slash_plan_show_styles_amendments(tmp_path):
    ctx, con = _slash_ctx(tmp_path)
    d = _plans(tmp_path / ".xlii")
    amend_plan(d, "an idea")
    dispatch_repl_command("/plan show", ctx)
    assert "[magenta]" in con.text


def test_plan_usage_mentions_amend():
    cmd = find_repl_command("/plan")
    assert cmd is not None and "amend" in cmd.usage


def test_slash_quoted_plan_name_with_whitespace(tmp_path):
    """Fix 7: the /plan args shlex-tokenize, so a quoted 'my plan' (writable
    by the planner's write_file — no name grammar there) works for --plan."""
    ctx, con = _slash_ctx(tmp_path)
    d = tmp_path / ".xlii" / "plans"
    d.mkdir(parents=True)
    (d / "my plan.md").write_text("- [ ] {#z} spaced-name item\n")
    dispatch_repl_command("/plan check --plan 'my plan' z --receipt ref", ctx)
    assert "- [x] {#z}" in (d / "my plan.md").read_text()
    dispatch_repl_command("/plan amend --plan 'my plan' works too", ctx)
    assert "new: works too" in (d / "my plan.md").read_text()


def test_slash_amend_apostrophe_text_does_not_crash(tmp_path):
    # unbalanced shlex quote (natural prose) falls back to plain split
    ctx, con = _slash_ctx(tmp_path)
    _plans(tmp_path / ".xlii")
    dispatch_repl_command("/plan amend don't ship the flag", ctx)
    assert "new: don't ship the flag" in (
        tmp_path / ".xlii/plans/current.md"
    ).read_text()


def test_entry_stamp_is_today(tmp_path):
    d = _plans(tmp_path)
    res = amend_plan(d, "dated")
    assert f"({time.strftime('%Y-%m-%d')})" in res.line
