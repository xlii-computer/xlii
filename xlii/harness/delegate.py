"""Unified harness delegation — ACP sessions and headless one-shots."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from xlii.harness.acp_session import AcpHarnessSession, acp_harness_names
from xlii.harness.brief import HarnessBrief
from xlii.harness.detect import harness_meta, list_harness_names
from xlii.harness.runner import run_ask

EventCallback = Callable[[str, dict[str, Any]], None]

HEADLESS_HARNESSES = frozenset({"codex"})


@dataclass
class DelegateResult:
    text: str = ""
    files_touched: list[str] = field(default_factory=list)
    stop_reason: str = ""
    notes: list[str] = field(default_factory=list)
    error: str | None = None
    harness: str = ""
    mode: str = ""
    model: str = ""
    tier: str = ""


def _default_model(name: str, model: str | None) -> str:
    if model:
        return model
    return str(harness_meta(name).get("default_model", name))


def run_delegate(
    name: str,
    task: str,
    *,
    mode: str = "agent",
    model: str | None = None,
    permission: str = "allow",
    with_context: bool = False,
    project_root: Path,
    xli_dir: Path | None = None,
    on_event: EventCallback | None = None,
    timeout_s: float = 600.0,
) -> DelegateResult:
    """Drive an external harness for one task in the current project."""
    name = name.lower().strip()

    from xlii.harness.detect import load_local_harnesses

    load_local_harnesses(xli_dir if xli_dir is not None else project_root / ".xlii")

    if name not in list_harness_names():
        return DelegateResult(
            error=f"unknown harness {name!r} (choose: {', '.join(list_harness_names())})",
            harness=name,
            mode=mode,
        )

    model_label = _default_model(name, model)
    meta = harness_meta(name)

    if name in acp_harness_names():
        return _run_acp_delegate(
            name,
            task,
            mode=mode,
            model=model,
            permission=permission,
            with_context=with_context,
            project_root=project_root,
            on_event=on_event,
            timeout_s=timeout_s,
            model_label=model_label,
            tier=str(meta["tier"]),
        )

    return _run_headless_delegate(
        name,
        task,
        mode=mode,
        model=model,
        permission=permission,
        project_root=project_root,
        timeout_s=int(timeout_s),
        model_label=model_label,
        tier=str(meta["tier"]),
    )


def _run_acp_delegate(
    name: str,
    task: str,
    *,
    mode: str,
    model: str | None,
    permission: str,
    with_context: bool,
    project_root: Path,
    on_event: EventCallback | None,
    timeout_s: float,
    model_label: str,
    tier: str,
) -> DelegateResult:
    session = AcpHarnessSession(
        name,
        project_root=project_root,
        mode=mode,
        model=model,
        permission=permission,
        with_context=with_context,
        on_event=on_event,
        timeout_s=timeout_s,
    )
    result = session.run(task)
    if result.error:
        return DelegateResult(
            error=result.error,
            harness=name,
            mode=mode,
            model=model_label,
            tier=tier,
            notes=result.notes,
        )
    return DelegateResult(
        text=result.text,
        files_touched=list(result.files_touched),
        stop_reason=result.stop_reason or "",
        harness=name,
        mode=mode,
        model=model_label,
        tier=tier,
        notes=result.notes,
    )


def _run_headless_delegate(
    name: str,
    task: str,
    *,
    mode: str,
    model: str | None,
    permission: str,
    project_root: Path,
    timeout_s: int,
    model_label: str,
    tier: str,
) -> DelegateResult:
    if mode not in ("agent", "plan", "ask"):
        return DelegateResult(
            error=f"unknown mode {mode!r} (use agent|plan|ask)",
            harness=name,
            mode=mode,
        )

    ask_result = run_ask(
        name,
        HarnessBrief(
            kind="delegate",
            tier=tier,
            question=task,
            project_root=project_root,
        ),
        model=model,
        timeout_s=timeout_s,
        mode=mode,
        permission=permission,
    )
    if ask_result.error:
        return DelegateResult(
            error=ask_result.error,
            harness=name,
            mode=mode,
            model=ask_result.model or model_label,
            tier=tier,
        )
    return DelegateResult(
        text=ask_result.text,
        harness=name,
        mode=mode,
        model=ask_result.model or model_label,
        tier=tier,
    )
