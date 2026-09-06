"""Verdict types and parsing for the autonomous loop (L0+: shell; L1+: LLM)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field


@dataclass
class Finding:
    file: str = ""
    line: int = 0
    tag: str = ""
    text: str = ""


@dataclass
class Verdict:
    passed: bool
    summary: str
    signature: str
    raw: str = ""
    judge: str = "tests"
    model: str = ""
    exit_code: int = 0
    findings: list[Finding] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None


def shell_verdict(
    *,
    judge: str,
    exit_code: int,
    output: str,
    tail_lines: int = 80,
) -> Verdict:
    """Build a Verdict from a shell oracle run."""
    lines = (output or "").splitlines()
    tail = "\n".join(lines[-tail_lines:]) if lines else ""
    passed = exit_code == 0
    summary = "tests passed" if passed else f"tests failed (exit {exit_code})"
    sig_src = f"exit:{exit_code}|{hashlib.sha256(tail.encode()).hexdigest()[:16]}"
    return Verdict(
        passed=passed,
        summary=summary,
        signature=sig_src,
        raw=tail,
        judge=judge,
        exit_code=exit_code,
    )


_PASS_RE = re.compile(r"^PASS:\s*(.+)", re.IGNORECASE | re.MULTILINE)
_FAIL_ITEM_RE = re.compile(
    r"^\s*\d+\.\s*(?:(?P<file>[^:]+):(?P<line>\d+)\s*[—\-]\s*)?(?P<text>.+?)(?:\s*\[(?P<tag>[^\]]+)\])?\s*$"
)
_READ_ITEM_RE = re.compile(
    r"^\s*\d+\.\s*(?P<path>[^:]+):(?P<start>\d+)(?:-(?P<end>\d+))?\s*$",
    re.MULTILINE,
)


@dataclass
class ReadRequest:
    path: str
    start_line: int = 1
    end_line: int = 0


def parse_read_requests(text: str) -> list[ReadRequest]:
    """Parse a judge READ_REQUEST block (L3). Returns [] when absent."""
    raw = text or ""
    if "READ_REQUEST" not in raw.upper():
        return []
    idx = raw.upper().find("READ_REQUEST")
    block = raw[idx:]
    out: list[ReadRequest] = []
    for line in block.splitlines()[1:]:
        if not line.strip():
            if out:
                break
            continue
        if line.strip().upper().startswith("FAIL") or line.strip().upper().startswith("PASS"):
            break
        m = _READ_ITEM_RE.match(line)
        if m:
            out.append(
                ReadRequest(
                    path=m.group("path").strip(),
                    start_line=int(m.group("start")),
                    end_line=int(m.group("end") or 0),
                )
            )
    return out


def parse_verdict(text: str, *, judge: str = "judge", model: str = "") -> Verdict:
    """Parse LLM reviewer output (PASS/FAIL contract shared with /verify)."""
    raw = (text or "").strip()
    if not raw:
        return Verdict(
            passed=False,
            summary="empty verdict",
            signature="empty",
            raw=raw,
            judge=judge,
            model=model,
        )

    # A starved reviewer said nothing about the code — an honest "could not
    # verify", not a FAIL. Without this, the sentinel prose hashed into a
    # constant fail| signature and the no-progress rail read repeated
    # starvation as "same code failure 3×" (the 2026-07-10 loop postmortem).
    # The literal is mirrored from worker_agent.STARVED_SENTINEL (kept as a
    # literal here so this leaf module imports nothing; a test pins the pair).
    if raw == "(worker hit max_worker_iterations without finishing)":
        return Verdict(
            passed=False,
            summary=(
                "verifier could not finish — worker iteration budget exhausted "
                "(inconclusive: this says nothing about the code)"
            ),
            signature="inconclusive|worker-budget",
            raw=raw,
            judge=judge,
            model=model,
        )

    m = _PASS_RE.search(raw)
    if m and not raw.upper().startswith("FAIL"):
        summary = m.group(1).strip()
        return Verdict(
            passed=True,
            summary=summary,
            signature=f"pass|{hashlib.sha256(summary.encode()).hexdigest()[:16]}",
            raw=raw,
            judge=judge,
            model=model,
        )

    findings: list[Finding] = []
    for line in raw.splitlines():
        if line.strip().upper() == "FAIL":
            continue
        fm = _FAIL_ITEM_RE.match(line)
        if fm:
            findings.append(
                Finding(
                    file=(fm.group("file") or "").strip(),
                    line=int(fm.group("line") or 0),
                    tag=(fm.group("tag") or "").strip(),
                    text=(fm.group("text") or "").strip(),
                )
            )

    sig_parts = []
    for f in findings:
        stem = (f.text.split(".")[0] if f.text else "")[:40]
        loc = f"{f.file}:{f.line}" if f.file else stem
        sig_parts.append(f"{loc}|{stem}")
    signature = "|".join(sig_parts) if sig_parts else f"fail|{hashlib.sha256(raw.encode()).hexdigest()[:16]}"

    return Verdict(
        passed=False,
        summary=f"{len(findings)} finding(s)" if findings else "FAIL",
        signature=signature,
        raw=raw,
        judge=judge,
        model=model,
        findings=findings,
    )
