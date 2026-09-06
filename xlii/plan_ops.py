"""Structured plan-file operations (plan-write-domain P2+P3).

`plan check` is the implementer's ONE sanctioned WRITE into the planner's
domain: flip a checkbox by its id and record evidence — never a free text
edit. `plan amend` (P3) is the implementer's VOICE: append-only proposals
into the plan's `## Amendments` queue, which only the planner resolves.
This module is the single core shared by the agent tools (tool_handlers),
the `/plan …` slash commands (repl_cmds.mode), and the `plan://` pane.

Checkbox grammar — explicit ids survive reordering (the proposal's resolved
open question):

    - [ ] {#some-id} item text

States carry trust as signal (wiki posture):

    [x]   checked WITH a receipt (evidence annotated on the line)
    [x?]  checked without one — allowed, but visibly weaker

Amendment grammar (P3) — deliberately NOT a checkbox state, so amendments are
structurally invisible to plan_check / plan_item_ids / id-uniqueness:

    - [?] {#am-N} (YYYY-MM-DD) re {#item-id}: <text>   # contests an item
    - [?] {#am-N} (YYYY-MM-DD) new: <text>             # proposed addition
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

# A markdown checkbox line: optional indent, list marker, box. Box states we
# understand: ' ' (open), 'x' (receipted), 'x?' (unreceipted check).
_CHECKBOX_RE = re.compile(r"^(?P<indent>\s*)(?P<marker>[-*])\s+\[(?P<state>x\?|x| )\]\s")
# A checkbox box ANYWHERE in a line — used to detect a malformed line carrying
# more than one box (the grammar is one item per line).
_BOX_ANYWHERE_RE = re.compile(r"[-*]\s+\[(?:x\?|x| )\]")
# Item ids are slugs; the {#id} token is matched literally.
ITEM_ID_RE = re.compile(r"^[a-z0-9-]+$")
_ID_TOKEN_RE = re.compile(r"\{#([a-z0-9-]+)\}")

# Amendment lines (P3): '- [?] {#am-N} ...'. The '?' state is NOT in
# _CHECKBOX_RE's closed state set, so an amendment can never be checked,
# indexed, or counted as a duplicate id — the queue coexists with the plan
# by construction.
AMENDMENTS_HEADING = "## Amendments"
_AMEND_LINE_RE = re.compile(r"^\s*[-*]\s+\[\?\]\s")
_AMEND_ID_RE = re.compile(r"\{#am-(\d+)\}")


def _checkbox_item_id(line: str) -> Optional[str]:
    """The item id OF a checkbox line — the first `{#id}` token after the box —
    or None when the line is not a checkbox. This is what makes id-matching
    ignore prose mentions and receipt annotations (which live on other lines,
    or later on the same line): only the box's own id counts."""
    if not _CHECKBOX_RE.match(line):
        return None
    m = _ID_TOKEN_RE.search(line)
    return m.group(1) if m else None

# Receipts annotate, they don't paste logs: one line, bounded.
_EVIDENCE_CAP = 120
# Amendments carry an argument ("found X, spec assumes Y — propose Z"), so
# their bound is wider than a receipt's — still one line.
_AMEND_CAP = 500


class PlanOpError(ValueError):
    """A plan op refused with a teaching message (bad id, no plan, ...)."""


# --------------------------------------------------------------------------- #
#  The plan listener (plan-surface T1) — the set_job_listener shape
# --------------------------------------------------------------------------- #
#
# A module-global repaint hook a surface installs so the plan strip / pane can
# refresh the moment a plan mutates — fired by check_plan_item and amend_plan
# here, and by the plans-domain file writes in tool_handlers (the planner
# rewriting the file). The kernel never imports the TUI; the surface marshals
# to its main thread (the conversation-listener discipline).

_PLAN_LISTENER: Optional[Callable[[], None]] = None


def set_plan_listener(cb: Optional[Callable[[], None]]) -> Optional[Callable[[], None]]:
    """Install (or clear, with ``None``) the plan-change listener; returns the
    previous one so the surface restores it on teardown."""
    global _PLAN_LISTENER
    prev = _PLAN_LISTENER
    _PLAN_LISTENER = cb
    return prev


def notify_plan_changed() -> None:
    """Fire the installed listener. A raising / absent listener is ignored —
    a UI repaint hook must never fail a plan write."""
    cb = _PLAN_LISTENER
    if cb is None:
        return
    try:
        cb()
    except Exception:
        # Listener failures are intentionally ignored so plan writes are never blocked.
        pass


@dataclass
class CheckResult:
    """What a check did — enough for any caller (tool / slash / pane) to render."""

    file: Path
    item_id: str
    old_state: str          # " " | "x" | "x?"
    new_state: str
    line: str               # the checkbox line after the op (no line ending)
    changed: bool
    note: str = ""          # human-facing detail for no-ops / upgrades


@dataclass
class AmendResult:
    """What an amend queued — mirrors CheckResult so every caller renders it."""

    file: Path
    amend_id: str           # "am-N"
    line: str               # the appended queue entry (no line ending)
    section_created: bool   # True when this append created '## Amendments'


def _sanitize_evidence(evidence: Optional[str]) -> str:
    """Collapse evidence to ONE bounded line — it annotates a checkbox, it is
    not a place to paste output."""
    if not evidence:
        return ""
    ev = " ".join(str(evidence).split())
    if len(ev) > _EVIDENCE_CAP:
        ev = ev[: _EVIDENCE_CAP - 1] + "…"
    return ev


def _unfenced_lines(lines):
    """Yield ``(index, line)`` for lines OUTSIDE ``` code fences (the fence
    delimiter lines themselves excluded). Every structural scan over a plan —
    checkbox ids, headings, amendment entries, {#am-N} numbering — must look
    through this filter, or fenced EXAMPLES read as real items (a fenced
    '- [ ] {#id}' would collide with the live one; a fenced '## Amendments'
    would suppress real section creation)."""
    fenced = False
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            yield i, ln


def plan_item_ids(text: str) -> list[str]:
    """All checkbox `{#id}`s in a plan text, in order (the teaching aid for
    id-not-found errors, and P3's pane index). Fence-aware: fenced examples
    don't count."""
    ids: list[str] = []
    for _i, line in _unfenced_lines(text.splitlines()):
        cid = _checkbox_item_id(line)
        if cid is not None:
            ids.append(cid)
    return ids


def resolve_plan_file(plans_dir: Path, plan: Optional[str] = None) -> Path:
    """The plan file an op targets: the explicit name when given, else
    `current.md` when present, else the newest `*.md` in the dir. (The
    'current' exclusion elsewhere is only for the saved-plans OFFER list —
    ops happily target it.)

    ``plan`` is a bare plan NAME, never a path. plan_check exists precisely so
    the implementer can ONLY touch plans/, so a value carrying a path
    separator, `..`, or an anchor is rejected, and the built target is
    re-checked to sit strictly inside the resolved ``plans_dir`` (a symlink
    pointing out is caught too). Without this the `plan` arg would be the
    widest write in the whole tool set."""
    plans_dir = Path(plans_dir)
    if plan:
        # A bare name only — the model is handed an id and a name, never a
        # filesystem path. Reject separators / .. / anchors up front so the
        # teaching message is precise.
        parts = plan.replace("\\", "/").split("/")
        if (
            "/" in plan
            or "\\" in plan
            or ".." in parts
            or Path(plan).is_absolute()
            or Path(plan).anchor
        ):
            raise PlanOpError(
                f"plan '{plan}' must be a bare plan name like 'current', not a "
                "path — plan_check only ever touches .xlii/plans/"
            )
        # Names that already end in .md are tried AS GIVEN first — the plan://
        # provider lists a legacy 'spec.md.md' file under the key 'spec.md',
        # and every key the provider emits must resolve back — then with .md
        # appended (so 'spec.md' also finds a plain spec.md.md on disk).
        tries = [plan, f"{plan}.md"] if plan.endswith(".md") else [f"{plan}.md"]
        f = next((plans_dir / t for t in tries if (plans_dir / t).is_file()), None)
        if f is None:
            raise PlanOpError(
                f"no plan file '{tries[-1]}' in plans/ — /plan list shows saved plans"
            )
        # Belt-and-suspenders (the P0 containment idiom): resolved target must
        # stay inside plans/ — catches a symlink under plans/ pointing out and
        # any join surprise the name-check missed.
        base = plans_dir.resolve()
        if f.resolve() != base and not f.resolve().is_relative_to(base):
            raise PlanOpError(
                f"plan '{plan}' resolves outside .xlii/plans/ — refused"
            )
        return f
    cur = plans_dir / "current.md"
    if cur.is_file():
        return cur
    candidates = (
        sorted(
            (p for p in plans_dir.glob("*.md") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if plans_dir.is_dir()
        else []
    )
    if not candidates:
        raise PlanOpError(
            "no plan files found — plan mode writes plans/current.md (/plan to start one)"
        )
    return candidates[0]


def _read_plan_text(f: Path) -> str:
    """Read a plan file verbatim (``newline=""`` — CRLF preserved). I/O and
    encoding failures become teaching PlanOpErrors: a non-UTF-8 file must
    refuse cleanly, never traceback through a tool or slash command."""
    try:
        with f.open("r", encoding="utf-8", newline="") as fh:
            return fh.read()
    except UnicodeDecodeError as e:
        raise PlanOpError(
            f"{f.name} is not UTF-8 text — plan files must be UTF-8 "
            f"(byte {e.start}: {e.reason})"
        ) from e
    except OSError as e:
        raise PlanOpError(f"could not read {f.name}: {e}") from e


def check_plan_item(
    plans_dir: Path,
    item_id: str,
    *,
    evidence: Optional[str] = None,
    plan: Optional[str] = None,
) -> CheckResult:
    """Flip the `- [ ] {#item_id}` checkbox: `[x]` with evidence, `[x?]`
    without. Idempotent on `[x]`; upgrades `[x?]` → `[x]` when evidence
    arrives. Never unchecks (the planner unchecks by editing — opposite
    powers) and never touches any other byte of the file."""
    item_id = (item_id or "").strip().lstrip("#")
    if not ITEM_ID_RE.match(item_id):
        raise PlanOpError(
            "item_id must be a slug ([a-z0-9-]+) from a '- [ ] {#id} ...' line"
        )
    f = resolve_plan_file(plans_dir, plan)
    text = _read_plan_text(f)

    lines = text.splitlines(keepends=True)
    # Match on the CHECKBOX's own id, never a raw substring — a prose mention or
    # a receipt annotation carrying `{#id}` must not count as a hit (that scan
    # self-locks-out the model, which is told to put refs in evidence). Fence-
    # aware: a fenced example carrying the same id must not trip uniqueness.
    hits = [i for i, ln in _unfenced_lines(lines) if _checkbox_item_id(ln) == item_id]
    if not hits:
        ids = plan_item_ids(text)
        hint = (
            "known ids: " + ", ".join(ids)
            if ids
            else "this plan has no '{#id}' checkboxes — the planner (/plan) marks "
                 "actionable items as '- [ ] {#id} ...'"
        )
        raise PlanOpError(f"no '{{#{item_id}}}' checkbox in {f.name} — {hint}")
    if len(hits) > 1:
        raise PlanOpError(
            f"'{{#{item_id}}}' is the id of {len(hits)} checkboxes in {f.name} — "
            "ids must be unique; the planner (/plan) should dedupe before it can "
            "be checked"
        )

    i = hits[0]
    body = lines[i].rstrip("\r\n")
    ending = lines[i][len(body):]
    m = _CHECKBOX_RE.match(body)
    if m is None:
        raise PlanOpError(
            f"the '{{#{item_id}}}' line in {f.name} is not a '- [ ]' checkbox — "
            "plan_check flips checkboxes, it never edits plan text"
        )
    # One box per line (the taught grammar). A second box on the line would make
    # the anchored rewrite flip the FIRST box while reporting the requested id —
    # refuse rather than silently touch the wrong item. Count boxes only in the
    # item text, not in a receipt annotation we appended (evidence may itself
    # contain a "- [ ]" substring, which is prose, not a checkbox).
    item_text = body.split(" — receipt: ", 1)[0]
    if len(_BOX_ANYWHERE_RE.findall(item_text)) > 1:
        raise PlanOpError(
            f"the '{{#{item_id}}}' line in {f.name} has multiple checkboxes — one "
            "item per line; the planner (/plan) should split them"
        )

    old_state = m.group("state")
    ev = _sanitize_evidence(evidence)

    if old_state == "x":
        return CheckResult(
            file=f, item_id=item_id, old_state="x", new_state="x", line=body,
            changed=False,
            note="already checked [x] — no-op (unchecking is the planner's edit)",
        )
    if old_state == "x?" and not ev:
        return CheckResult(
            file=f, item_id=item_id, old_state="x?", new_state="x?", line=body,
            changed=False,
            note="already checked [x?] — pass evidence to upgrade it to a receipted [x]",
        )

    new_state = "x" if ev else "x?"
    start = m.start("state")
    new_body = body[:start] + new_state + body[start + len(old_state):]
    if ev:
        new_body = f"{new_body} — receipt: {ev}"
    lines[i] = new_body + ending

    from xlii.atomicio import write_bytes_atomic

    # Write bytes: write_text_atomic opens in text mode (universal-newline
    # translation), which would rewrite the CRLF our newline="" read preserved
    # to CR CRLF on non-POSIX. Bytes keep the file byte-identical off the
    # target line on every platform.
    write_bytes_atomic(f, "".join(lines).encode("utf-8"))
    notify_plan_changed()
    if old_state == "x?":
        note = "upgraded [x?] → [x]"
    elif ev:
        note = "checked [x]"
    else:
        note = "checked [x?] — no receipt; pass evidence for a receipted [x]"
    return CheckResult(
        file=f, item_id=item_id, old_state=old_state, new_state=new_state,
        line=new_body, changed=True, note=note,
    )


def _sanitize_amend_text(text: Optional[str]) -> str:
    """One bounded line — an amendment argues a point, it doesn't paste logs."""
    if not text:
        return ""
    t = " ".join(str(text).split())
    if len(t) > _AMEND_CAP:
        t = t[: _AMEND_CAP - 1] + "…"
    return t


def plan_progress(text: str) -> tuple[int, int]:
    """``(checked, total)`` over a plan's checkboxes — `[x]` and `[x?]` both
    count as checked (marked done; the receipt only grades trust). Fence-aware
    like every structural scan. The plan:// pane's progress column."""
    checked = total = 0
    for _i, line in _unfenced_lines(text.splitlines()):
        m = _CHECKBOX_RE.match(line)
        if m is None:
            continue
        total += 1
        if m.group("state") in ("x", "x?"):
            checked += 1
    return checked, total


@dataclass(frozen=True)
class PlanItem:
    """One checkbox item, parsed for display (the strip + the items pane).

    ``item_id`` is "" when the line carries no ``{#id}`` token (legal, but
    unaddressable — check/amend need an id). ``receipt`` is the evidence
    annotation, "" when none.
    """

    item_id: str
    state: str      # " " | "x" | "x?"
    text: str       # item text, id token and receipt annotation stripped
    receipt: str


def plan_items(text: str) -> list[PlanItem]:
    """Every checkbox item in a plan, in file order, parsed for display.
    Fence-aware like every structural scan; amendments (``[?]``) are not
    items and live in :func:`pending_amendments`."""
    out: list[PlanItem] = []
    for _i, line in _unfenced_lines(text.splitlines()):
        m = _CHECKBOX_RE.match(line)
        if m is None:
            continue
        body = line[m.end():]
        body, _sep, receipt = body.partition(" — receipt: ")
        id_m = _ID_TOKEN_RE.search(body)
        item_id = id_m.group(1) if id_m else ""
        if id_m:
            body = (body[: id_m.start()] + body[id_m.end():])
        out.append(PlanItem(
            item_id=item_id, state=m.group("state"),
            text=" ".join(body.split()), receipt=receipt.strip(),
        ))
    return out


def pending_amendments(text: str) -> list[tuple[str, str]]:
    """The plan's queued `- [?]` amendment entries as ``(amend_id, line)``
    pairs, in file order. Fence-aware (a fenced example is not a pending
    proposal). Shared by the /plan entry count, /plan show, and the plan://
    pane."""
    out: list[tuple[str, str]] = []
    for _i, line in _unfenced_lines(text.splitlines()):
        if _AMEND_LINE_RE.match(line):
            m = _AMEND_ID_RE.search(line)
            if m:
                out.append((f"am-{m.group(1)}", line))
    return out


def amend_plan(
    plans_dir: Path,
    text: str,
    *,
    item_id: Optional[str] = None,
    plan: Optional[str] = None,
) -> AmendResult:
    """Queue an amendment (plan-write-domain P3): insert ONE `- [?] {#am-N}`
    entry INTO the plan's `## Amendments` section — after its last contiguous
    entry, BEFORE whatever section follows (the preamble tells the planner to
    keep other sections, so the queue is rarely last). Heading absent → the
    section is created at EOF. Insertion-only — every existing byte is
    preserved; the planner resolves the entry by editing the plan (accept →
    fold in with a `(from {#am-N})` provenance note + remove; reject → replace
    with a rejection note). ``item_id`` contests an existing checkbox item;
    omitted means a proposed addition (adds are ALLOWED, visibly
    implementer-origin until the planner promotes them)."""
    body = _sanitize_amend_text(text)
    if not body:
        raise PlanOpError(
            "amendment text required — say what you found and what you propose, "
            "e.g. plan_amend(text='found X, spec assumes Y — propose Z')"
        )
    f = resolve_plan_file(plans_dir, plan)
    existing = _read_plan_text(f)

    if item_id is not None:
        item_id = str(item_id).strip().lstrip("#")
        if not ITEM_ID_RE.match(item_id):
            raise PlanOpError(
                "item_id must be a slug ([a-z0-9-]+) from a '- [ ] {#id} ...' line"
            )
        ids = plan_item_ids(existing)
        if item_id not in ids:
            hint = (
                "known ids: " + ", ".join(ids)
                if ids
                else "this plan has no '{#id}' checkboxes yet — omit item_id to "
                     "propose an addition"
            )
            raise PlanOpError(
                f"no '{{#{item_id}}}' checkbox in {f.name} to contest — {hint}"
            )

    # Numbering: 1 + the file-wide UNFENCED max {#am-N} — file-wide (not
    # section-scoped) so a `(from {#am-N})` provenance note on an accepted,
    # removed entry still holds the high-water mark and ids are never reused.
    n_max = 0
    for _i, ln in _unfenced_lines(existing.splitlines()):
        for tok in _AMEND_ID_RE.findall(ln):
            n_max = max(n_max, int(tok))
    n = n_max + 1
    stamp = time.strftime("%Y-%m-%d")
    subject = f"re {{#{item_id}}}:" if item_id else "new:"
    entry = f"- [?] {{#am-{n}}} ({stamp}) {subject} {body}"

    lines = existing.splitlines(keepends=True)
    heading_idx = None
    for i, ln in _unfenced_lines(lines):  # fence-aware: a fenced example isn't the section
        if ln.rstrip("\r\n").strip() == AMENDMENTS_HEADING:
            heading_idx = i
            break

    from xlii.atomicio import write_bytes_atomic

    if heading_idx is None:
        # Create the section at EOF (a blank separator; heal a missing EOL).
        suffix = ""
        if existing and not existing.endswith(("\n", "\r")):
            suffix += "\n"
        if existing.strip():
            suffix += "\n"
        suffix += AMENDMENTS_HEADING + "\n" + entry + "\n"
        new_text = existing + suffix
    else:
        # Insert INTO the section: after its last contiguous `- [?]` entry
        # (directly after the heading when empty), BEFORE the next section —
        # an entry must never file under whatever section happens to be last.
        insert_at = heading_idx + 1
        while insert_at < len(lines) and _AMEND_LINE_RE.match(lines[insert_at]):
            insert_at += 1
        # Match the section's own line ending so a CRLF file stays uniform.
        ending = "\r\n" if lines[heading_idx].endswith("\r\n") else "\n"
        if insert_at > 0 and not lines[insert_at - 1].endswith(("\n", "\r")):
            lines[insert_at - 1] += ending  # heal a missing EOL at EOF
        lines.insert(insert_at, entry + ending)
        new_text = "".join(lines)

    # Bytes, not text mode: the same CRLF discipline as check_plan_item —
    # existing content must stay byte-identical on every platform.
    write_bytes_atomic(f, new_text.encode("utf-8"))
    notify_plan_changed()
    return AmendResult(
        file=f, amend_id=f"am-{n}", line=entry, section_created=heading_idx is None,
    )


def render_plan_lines(text: str) -> list[str]:
    """Rich-markup lines for a plan: checkbox states styled — `[x]` green,
    `[x?]` yellow, `[ ]` dim — and `- [?]` amendment entries magenta; prose
    passed through (escaped). Console-level, so both the inline REPL and the
    TUI render it identically; the plan:// pane gets its own view."""
    from rich.markup import escape

    styles = {"x": "green", "x?": "yellow", " ": "dim"}
    out: list[str] = []
    for line in text.splitlines():
        m = _CHECKBOX_RE.match(line)
        if m is not None:
            style = styles[m.group("state")]
            out.append(f"[{style}]{escape(line)}[/{style}]")
        elif _AMEND_LINE_RE.match(line):
            out.append(f"[magenta]{escape(line)}[/magenta]")
        else:
            out.append(escape(line))
    return out
