---
sources: file://xlii/status.py#L758-822, file://xlii/status.py#L681-730, file://xlii/status.py#L146-156, file://xlii/tui/status.py#L36-75, file://tests/test_tui_status.py#L488-538, file://xlii/agent.py#L1031-1094
verified: false
---
# status-and-chrome

The profile bar and the answer frame are xlii's session chrome: the strip that
reads `mode · trust · cost · jrnl · id · loadout · model · meter`, and the panel a
streaming reply is drawn inside. PR #299 (the `chrome-status` vector) fixed the law
that governs both.

## The kernel/face split (the ratchet)

**The kernel emits pure segment data; the face owns all rich markup.** `xlii/status.py`
is a leaf module — no `rich`, no `prompt_toolkit`, only palette *tokens* and plain
strings. Its `profile_bar_segments(state)` returns a `list[dict]` of
`{text, style, gap}` where `style` is an abstract token (`dim`, `mode:cyan`,
`cost_over`, `journal_on`, `sess`, `plugin`, `yellow`) and `gap` is `tight|none|wide|
inline`. The face, `xlii/tui/status.py`, is the only place that turns those into Rich:
`profile_bar()` assembles a `rich.Text`, and `_rich_style()` maps `mode:X → bold X`
and the `_STYLE_MAP` tokens to real markup. One reader, two surfaces (inline
prompt_toolkit toolbar + Textual `--tui` strip), no drift.

Two tests keep markup from creeping back into the kernel:
`test_status_kernel_has_no_rich_imports` walks the AST of `xlii/status.py` and asserts
zero `rich` imports; `test_status_modules_have_no_mode_ladders` forbids `elif
state.plan_mode` / `agent.rail is not None` ladders in status.py and profile.py — mode
resolution goes through the unified `agent.active_mode` slot.

## The three axes

Mode is decomposed into orthogonal axes so one glance answers each question without
flag-soup: `exclusive_mode` (plan|rail|debug|discovery|ops|—), `trust_axis`
(freeball>yolo>safe, see [[trust-and-gates]]), and `surface_axis` (scratch|chat|code).
`frame_mode(state)` yields the `(label, color)` the input frame *and* the answer chip
share — one `_MODE_RICH` palette drives the bar tone, the frame border, and the reply
panel. Content **doorways** (skills/rules/ref/tray/artifacts/wiki) ride the frame only when
their kind is actually attached, opening `scheme://` in Pane 2 (see [[panes-and-dock]]).

## G1/G2 status decisions

The status polish locked four rules (D8–D11), all verified in `profile_bar_segments`:

- **D8 — jrnl badge always visible.** `journal()` returns `jrnl●` (recording) or `jrnl○`
  (not) unconditionally; a journal-less state (chat, pre-boot) still shows the dim off
  glyph. **No omission-as-off** — the badge is a pinned presence indicator, never dropped.
  The audit-fix commit restored this after a regression. See [[journal-and-receipts]].
- **D9 — bare trust word.** Trust is always its own segment (`safe` dim / `yolo` /
  `FREEBALL` loud), decoupled from the mode word; the rail/plan/debug affordance flag is
  suppressed when it would duplicate trust or the lead word.
- **D10 — under-budget cost is neutral.** The cost segment uses `cost_over` (bold red)
  only when `spent > budget_usd`; under budget it is `dim`. Danger ink is reserved for
  actual overage.
- **D11 — mode word accented only when non-normal.** `_mode_lead_style` keeps the neutral
  surfaces (`code`, `chat`, `scratch`) `dim`; any other lead word gets `mode:{color}`
  (bold). A normal code session's bar is quiet ink.

**PLAN → gigwork passthrough.** `_affordance_from_mode_tag` lowercases a `PLAN` status
tag, so a plan-mode controller hiring a foreign brain surfaces its label verbatim:
`plan·gigwork[haiku]` reaches the bar (test pins `plan·gigwork[kimi]`). This is the
[[plan-surface]] × [[gigwork-and-foreign-brains]] seam made visible in the chrome.

## H1 — framed streaming

In inline sessions the **first streamed tokens render inside the answer frame**, not as
bare text. `_stream_orchestrator_iteration` (`xlii/agent.py`) opens a transient
`rich.live.Live` only when `console.supports_live` is truthy — true on a real Rich
console, **false** on the Textual transcript console (Live's cursor control can't drive a
widget). Its `_live_frame` closure draws a `Panel` titled `┤ mode · role ├` and bordered
in the same color the status bar computes, read from `self.turn_record` — so the live
frame's chrome matches the bar's tone for that mode. `_streaming_tail` reserves the 2
border rows (`frame_rows`/`frame_cols`) so Live's `crop` can't hide the newest tokens.

**The Textual path streams to the transcript unchanged — deliberately.** A Textual live
pipeline was built and then reverted as out of brief: the audit-fix commit rolled
`app_turn_mixin.py`'s streaming widget and `blocks.streaming_answer_panel` back to
merge-base and rewired `drive_turn` to `_run_turn`. That same commit fixed the H1 blocker
— a function-local `rich.panel` import that shadowed the module-level `Panel` and left the
closure reading an unbound cell (NameError on the first streamed chunk in every
`supports_live` session).
