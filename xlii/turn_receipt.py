"""Turn receipts (turn-receipts-claim-gates P1 + Wave-1 exit binding) — evidence vs claims.

Built once per turn at the kernel spine's bookkeeping tail
(:func:`xlii.conversation.complete_turn_effects` — since convergence Phase 5
that is the ONE place every turn ends, so TUI, inline, and loop turns are all
covered by construction). The receipt is quiet when evidence supports the
claims and one muted line when it doesn't; it never blocks a turn (v1 policy:
warn + receipt, don't deadlock).

Division of labor with the P0 claim gate (`agent_stats._detect_unsupported_claim`):
P0 already yells about **edit** claims with no writes (its warning rides
``stats.warnings`` into the footer). The receipt records that same verdict but
only *prints* for the lanes P0 doesn't cover — **verify** and **publish**
claims without their evidence — so nothing yells twice.

Wave-1: bash tool_calls are paired to results via ``tool_call_id``; the
``--- exit N ---`` trailer (from ``t_bash``) is recorded on ``cmds``. A verify
claim plus a nonzero last verify exit is unsubstantiated (distinct warning);
a missing trailer fails open. Also: light claim↔path warn, commit-hash record.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# Claim tiers (proposal §claim detection). The edit tier reuses the P0 pattern
# from agent_stats so both gates always agree on what an "edit claim" is.
_VERIFY_CLAIM = re.compile(
    r"\b(?:tests?\s+(?:pass(?:ed|ing)?|green)|verified|all\s+green|suite\s+(?:is\s+)?green|pass(?:ed|es)\s+(?:all\s+)?tests?)\b",
    re.IGNORECASE,
)
_PUBLISH_CLAIM = re.compile(r"\b(?:committed|pushed|tagged)\b", re.IGNORECASE)

# Verify-evidence commands (bash tool arguments that count as running checks).
_VERIFY_CMD = re.compile(r"\b(?:pytest|unittest|ruff|mypy|tox|make\s+test|npm\s+test|cargo\s+test)\b")
_COMMIT_CMD = re.compile(r"\bgit\s+(?:commit|push)\b")
# Bash tool results append this trailer LAST (tool_handlers.t_bash appends it at
# the very end, and both _cap_output's spill preview and _truncate keep
# ``text[-keep:]``, so the genuine trailer always survives at the tail). Anchor
# to the tail — a quoted/echoed earlier ``--- exit 0 ---`` inside command output
# must NOT be able to spoof a pass over the real trailing exit. Parse only.
_EXIT_TRAILER = re.compile(r"--- exit (-?\d+) ---\s*\Z")
# Light path extraction near claims (backticks or bare source-ish filenames).
_CLAIMED_PATH = re.compile(
    r"`([^`\n]+)`|(?<![/\w.])([\w./-]+\.(?:py|md|toml|yml|yaml|json|txt|css|js|ts|rs|go|sh))(?![\w.])"
)
# Commit-hash extraction. Prefer git's ``[branch hash]`` commit bracket (the hash
# is the last token before ``]``); then a ``git push`` ``old..new`` range's NEW
# hash. A bare first-hex-token scan is deliberately NOT used — push output like
# ``abc1234..def5678`` would otherwise record the OLD hash.
_GIT_COMMIT_HASH = re.compile(r"\[[^\]]*?([0-9a-f]{7,40})\]")
_GIT_PUSH_RANGE = re.compile(r"[0-9a-f]{7,40}\.\.([0-9a-f]{7,40})")

_DIFF_STAT_CAP = 2000            # chars — a receipt is a note, not a diff viewer
_RECEIPTS_FILE = "receipts.jsonl"


@dataclass
class TurnReceipt:
    """The structured, always-produced finalize artifact (proposal §receipt)."""

    classes: list[str] = field(default_factory=list)
    tools: int = 0
    tool_names: list[str] = field(default_factory=list)
    dirty_paths: list[str] = field(default_factory=list)
    claims: list[str] = field(default_factory=list)
    gate: str = "ok"                       # ok | unsubstantiated
    warnings: list[str] = field(default_factory=list)
    diff_stat: str = ""
    cmds: list[dict[str, Any]] = field(default_factory=list)  # {cmd, exit|null}
    commit_hash: str = ""
    ts: float = field(default_factory=time.time)
    # plan-write-domain P1 (D1c): which source fed plan-last.md when this turn
    # followed an approval — "plan-file" (reconciled current.md) or
    # "chat-snapshot" (legacy fallback). None on ordinary turns; the field is
    # omitted from the ledger line when unset.
    plan_source: Optional[str] = None

    def compact_line(self) -> str:
        cls = "+".join(self.classes) or "explain"
        return (f"receipt: {self.gate} · class={cls} · tools={self.tools} "
                f"· files={len(self.dirty_paths)}")

    def to_dict(self) -> dict[str, Any]:
        # Stable ledger keys (never rename/remove); cmds + commit_hash are additive.
        d = {
            "ts": self.ts, "classes": self.classes, "tools": self.tools,
            "tool_names": self.tool_names, "dirty_paths": self.dirty_paths,
            "claims": self.claims, "gate": self.gate,
            "warnings": self.warnings, "diff_stat": self.diff_stat,
            "cmds": self.cmds, "commit_hash": self.commit_hash,
        }
        if self.plan_source:
            d["plan_source"] = self.plan_source
        return d


def _turn_tail(history: list) -> list:
    """This turn's history slice: back to the user message that OPENED the
    turn. Steering interjections (/btw) are user-role too but carry the
    ``[steering`` marker drain_btw_inbox stamps — walk past those."""
    start = 0
    for i in range(len(history) - 1, -1, -1):
        e = history[i]
        if e.get("role") == "user":
            start = i
            if not str(e.get("content", "")).lstrip().startswith("[steering"):
                break
    return history[start:]


def _turn_opening_text(history: list) -> str:
    """The text of the user message that opened this turn ('' when the opening
    isn't a plain string — multimodal parts can't match a planted rewrite, and
    failing closed just omits the audit field rather than mis-stamping it)."""
    for e in _turn_tail(history):
        if e.get("role") == "user":
            c = e.get("content", "")
            return c if isinstance(c, str) else ""
    return ""


def _parse_bash_args(raw: Any) -> str:
    """Extract the command string from a bash tool_call's arguments blob."""
    if isinstance(raw, dict):
        return str(raw.get("command", "") or "")
    text = str(raw or "")
    try:
        parsed = json.loads(text) if text else {}
        if isinstance(parsed, dict):
            return str(parsed.get("command", "") or "")
    except Exception:
        # Non-JSON tool args -- fall back to returning the raw text below.
        pass
    return text


def _exit_from_tool_content(content: Any) -> Optional[int]:
    """Parse the TRAILING ``--- exit N ---`` from a bash tool result.

    ``t_bash`` appends this trailer at the very end of the output, so the real
    exit is always the last trailer; anchoring to the tail (rather than the
    first match) defeats spoofing — an earlier ``--- exit 0 ---`` quoted or
    echoed inside command output can no longer masquerade as the exit and read
    a red run green. Missing trailer → None (fail open)."""
    m = _EXIT_TRAILER.search(str(content or ""))
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _harvest(history: list) -> tuple[list[str], list[dict[str, Any]], bool, bool, str]:
    """(tool names, cmds, verify_ran, commit_ran, commit_hash) from this turn.

    Bash calls are paired to their ``role=tool`` results via ``tool_call_id``.
    Exit codes come from the ``--- exit N ---`` trailer; a missing trailer fails
    open (``exit: null``) so an older/partial result still counts as evidence
    that the command ran — only an explicit nonzero exit falsifies a pass claim.
    """
    names: list[str] = []
    cmds: list[dict[str, Any]] = []
    verify_ran = False
    commit_ran = False
    commit_hash = ""
    results_by_id: dict[str, Any] = {}
    bash_calls: list[tuple[str, str]] = []  # (tool_call_id, command)

    for entry in _turn_tail(history):
        role = entry.get("role")
        if role == "tool":
            tcid = str(entry.get("tool_call_id") or "")
            if tcid:
                results_by_id[tcid] = entry.get("content", "")
            continue
        for tc in entry.get("tool_calls") or ():
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function", {}) or {}
            name = fn.get("name", "")
            if name:
                names.append(name)
            if name != "bash":
                continue
            cmd = _parse_bash_args(fn.get("arguments", ""))
            tcid = str(tc.get("id") or "")
            bash_calls.append((tcid, cmd))
            if _VERIFY_CMD.search(cmd):
                verify_ran = True
            if _COMMIT_CMD.search(cmd):
                commit_ran = True

    for tcid, cmd in bash_calls:
        content = results_by_id.get(tcid, "") if tcid else ""
        exit_code = _exit_from_tool_content(content) if tcid else None
        cmds.append({"cmd": cmd, "exit": exit_code})
        if commit_ran and not commit_hash and _COMMIT_CMD.search(cmd):
            # Prefer the ``[branch hash]`` commit bracket, then a push range's
            # NEW hash; fail open (→ rev-parse HEAD residual) if neither is
            # present. Never fall back to a bare first-hex-token: ``git push``
            # prints ``old..new`` and that would stamp the OLD hash.
            text = str(content or "")
            m = _GIT_COMMIT_HASH.search(text) or _GIT_PUSH_RANGE.search(text)
            if m:
                commit_hash = m.group(1)

    return names, cmds, verify_ran, commit_ran, commit_hash


def _last_verify_exit(cmds: list[dict[str, Any]]) -> tuple[bool, Optional[int]]:
    """Whether any verify cmd ran this turn, and the exit of the *last* one."""
    last: Optional[int] = None
    saw = False
    for c in cmds:
        if _VERIFY_CMD.search(str(c.get("cmd") or "")):
            saw = True
            last = c.get("exit")  # may be None (missing trailer)
    return saw, last


def _claimed_paths(text: str) -> list[str]:
    """Light path extraction from claim prose (backticks / source-ish names)."""
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for m in _CLAIMED_PATH.finditer(text):
        p = (m.group(1) or m.group(2) or "").strip()
        if not p or p in seen or len(p) > 200:
            continue
        seen.add(p)
        out.append(p)
    return out


def _paths_in_diff_stat(diff_stat: str) -> set[str]:
    names: set[str] = set()
    for line in (diff_stat or "").splitlines():
        # git diff --stat lines look like " path/to/file | 3 ++-"
        if "|" not in line:
            continue
        left = line.split("|", 1)[0].strip()
        if left and not left.startswith("files changed"):
            names.add(left)
    return names


def _final_text(state: Any, text: str) -> str:
    """The turn's final assistant prose (streamed turns return "" — read history)."""
    if text:
        return text
    try:
        for entry in reversed(state.agent.history):
            if entry.get("role") == "assistant" and entry.get("content"):
                return str(entry["content"])
    except Exception:
        # A malformed or missing history leaves the receipt without final text.
        pass
    return ""


def _git_diff_stat(project_root: Any) -> str:
    try:
        out = subprocess.run(
            ["git", "diff", "--stat", "HEAD"],
            cwd=str(project_root), capture_output=True, text=True, timeout=5,
        )
        stat = out.stdout.strip() if out.returncode == 0 else ""
        return stat[:_DIFF_STAT_CAP]
    except Exception:
        return ""


def _git_head_hash(project_root: Any) -> str:
    """Best-effort HEAD short hash after a commit tool ran. Empty on failure."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(project_root), capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def build_receipt(state: Any, prompt: str, text: str, dirty: set, stats: Any) -> TurnReceipt:
    """Harvest evidence + tier claims into a receipt. Pure read; never raises."""
    r = TurnReceipt()
    r.tools = int(getattr(stats, "tool_calls", 0) or 0)
    r.dirty_paths = sorted(str(p) for p in (dirty or ()))

    history = getattr(getattr(state, "agent", None), "history", None) or []

    # One-shot plan-source audit (plan-write-domain P1 D1c): approval stashed a
    # (source, expected_opening) pair, bound to the rewritten text it planted
    # as the follow-up turn's user message. Stamp ONLY when THIS turn actually
    # opens with that text — an aborted or dropped approval turn (Ctrl-C /
    # run_turn error / TUI busy-gate drop) would otherwise deliver the stash to
    # an unrelated turn's ledger line, and an audit field that lies is worse
    # than none. Consume either way (one-shot); on mismatch, discard silently —
    # the approval turn never ran.
    session = getattr(getattr(state, "agent", None), "session", None)
    stash = getattr(session, "pending_plan_source", None)
    if stash:
        try:
            session.pending_plan_source = None
        except Exception:
            # Best-effort cleanup only: receipt generation must never block a turn.
            # Some session implementations may reject assignment; ignore safely.
            pass
        try:
            plan_source, expected_opening = stash
        except (TypeError, ValueError):
            plan_source, expected_opening = None, None
        if (
            plan_source
            and expected_opening
            # `in`, not startswith: run_turn may PREFIX the rewritten text
            # (live-shell context note, a folded /plan continue refresher) —
            # the planted sentence itself is the binding.
            and expected_opening in _turn_opening_text(history)
        ):
            r.plan_source = str(plan_source)

    r.tool_names, r.cmds, verify_ran, commit_ran, commit_hash = _harvest(history)
    r.commit_hash = commit_hash

    if r.dirty_paths:
        r.classes.append("edit")
    if verify_ran:
        r.classes.append("verify")
    if commit_ran:
        r.classes.append("commit")

    final = _final_text(state, text)
    from xlii.agent_stats import _EDIT_CLAIM_PATTERN
    # Tier precedence: publish verbs ("committed", "pushed") are the publish
    # tier's claims — strip them before the edit-tier scan so "committed the
    # change" isn't double-tiered as an unsubstantiated file edit.
    edit_scan = _PUBLISH_CLAIM.sub("", final) if final else ""
    edit_claim = bool(edit_scan) and _EDIT_CLAIM_PATTERN.search(edit_scan) is not None
    verify_claim = bool(final) and _VERIFY_CLAIM.search(final) is not None
    publish_claim = bool(final) and _PUBLISH_CLAIM.search(final) is not None
    if edit_claim:
        r.claims.append("edit")
    if verify_claim:
        r.claims.append("verify")
    if publish_claim:
        r.claims.append("publish")

    if edit_claim and not r.dirty_paths:
        r.gate = "unsubstantiated"
        r.warnings.append("edit claim with no files modified this turn")
    if verify_claim and not verify_ran:
        r.gate = "unsubstantiated"
        r.warnings.append("verify claim without a test/check command this turn")
    elif verify_claim and verify_ran:
        # Exit binding: appearing is not enough — a recorded nonzero exit
        # falsifies "passed". Missing trailer fails open (legacy / partial).
        _saw, last_exit = _last_verify_exit(r.cmds)
        if last_exit is not None and last_exit != 0:
            r.gate = "unsubstantiated"
            r.warnings.append(
                f"verify claim but last check exited {last_exit}"
            )
    if publish_claim and not commit_ran:
        r.gate = "unsubstantiated"
        r.warnings.append("publish claim without a git commit/push this turn")

    if "edit" in r.classes:
        r.diff_stat = _git_diff_stat(getattr(getattr(state, "project", None),
                                             "project_root", "."))

    # P1 residual: named paths in claim prose should intersect dirty or diff.
    if edit_claim and r.dirty_paths:
        claimed = _claimed_paths(final)
        if claimed:
            known = set(r.dirty_paths) | _paths_in_diff_stat(r.diff_stat)
            # Match on basename or full relative path.
            known_bases = {Path(p).name for p in known} | known
            if not any(p in known_bases or Path(p).name in known_bases for p in claimed):
                r.warnings.append(
                    "claimed path(s) not in dirty/diff: " + ", ".join(claimed[:5])
                )
                # Soft: warn only — do not flip gate (false positives on prose paths).

    # Commit-hash residual: if we saw a commit but no hash in the tool output,
    # try a cheap rev-parse (best-effort; never raises).
    if commit_ran and not r.commit_hash:
        r.commit_hash = _git_head_hash(getattr(getattr(state, "project", None),
                                              "project_root", "."))
    return r


def claim_gates_mode(cfg: Any) -> str:
    """The claim-gates policy: ``warn`` (default) | ``strict`` | ``off``.
    Anything unrecognized degrades to ``warn`` — a typo must not silently
    disable the honesty pipe."""
    mode = str(getattr(cfg, "claim_gates", "warn") or "warn").strip().lower()
    return mode if mode in ("warn", "strict", "off") else "warn"


def record_receipt(state: Any, receipt: TurnReceipt) -> None:
    """Quiet recording: last-receipt slot on the session + JSONL under .xlii —
    the ledger is written in EVERY mode (off silences warnings, not evidence).
    Loudness follows ``claim_gates``: warn → only the lanes P0 doesn't already
    yell about (verify/publish); strict → any unsubstantiated gate, edit lane
    included; off → never."""
    try:
        state.agent.session.last_turn_receipt = receipt
    except Exception:
        # A session stub without the field still gets the on-disk receipt written below.
        pass
    try:
        xli = getattr(getattr(state, "project", None), "xli_dir", None)
        if xli is not None:
            with open(xli / _RECEIPTS_FILE, "a") as f:
                f.write(json.dumps(receipt.to_dict()) + "\n")
    except Exception:
        # The receipt file is advisory bookkeeping; a failed append must not fail the turn.
        pass
    mode = claim_gates_mode(getattr(state, "cfg", None))
    if mode == "off":
        return
    if mode == "strict":
        loud = list(receipt.warnings)
    else:
        loud = [w for w in receipt.warnings if not w.startswith("edit claim")]
    if loud:
        try:
            state.console.print(
                f"[yellow]⚠ {receipt.compact_line()} — {'; '.join(loud)}[/yellow]"
            )
        except Exception:
            # The receipt is already recorded; only the warning line is lost.
            pass


def build_and_record_receipt(state: Any, prompt: str, text: str,
                             dirty: set, stats: Any) -> Optional[TurnReceipt]:
    """The one entry the spine calls. Never raises; never blocks a turn."""
    try:
        receipt = build_receipt(state, prompt, text, dirty, stats)
        record_receipt(state, receipt)
        return receipt
    except Exception:
        return None
