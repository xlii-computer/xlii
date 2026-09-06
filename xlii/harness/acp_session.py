"""Unified ACP harness sessions — Cursor, Grok Build, Claude Code."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from xlii.acp_client import AcpClient, AcpError
from xlii.harness.detect import HARNESS_SPECS, resolve_binary
from xlii.harness.mcp_context import ensure_deep_context

EventCallback = Callable[[str, dict[str, Any]], None]

ACP_HARNESSES = frozenset({"cursor", "grok", "claude"})

# Community adapters may register extra ACP harnesses at runtime.
_EXTRA_ACP_SUFFIX: dict[str, tuple[str, ...]] = {}


@dataclass(frozen=True)
class AcpHarnessProfile:
    """Wire format for one harness's ACP subprocess."""

    name: str
    argv_suffix: tuple[str, ...]
    login_hint: str
    mcp_harness: str | None = None  # harness name for ensure_deep_context


ACP_PROFILES: dict[str, AcpHarnessProfile] = {
    "cursor": AcpHarnessProfile(
        name="cursor",
        argv_suffix=("acp",),
        login_hint="cursor-agent login",
        mcp_harness="cursor",
    ),
    "grok": AcpHarnessProfile(
        name="grok",
        argv_suffix=("agent", "stdio"),
        login_hint="grok login or XAI_API_KEY",
        mcp_harness="grok",
    ),
    "claude": AcpHarnessProfile(
        name="claude",
        argv_suffix=("acp",),
        login_hint="ANTHROPIC_API_KEY or claude login",
        mcp_harness=None,
    ),
}


def acp_harness_names() -> frozenset[str]:
    return frozenset({*ACP_HARNESSES, *_EXTRA_ACP_SUFFIX})


def resolve_acp_argv(name: str) -> list[str]:
    """Build ``[binary, *suffix]`` for an ACP harness subprocess."""
    name = name.lower()
    spec = HARNESS_SPECS.get(name)
    if spec is None:
        raise AcpError(f"unknown ACP harness {name!r}")
    cli = resolve_binary(spec)
    if not cli:
        raise AcpError(f"{name} CLI not found ({spec.auth_hint})")
    profile = ACP_PROFILES.get(name)
    suffix = _EXTRA_ACP_SUFFIX.get(name)
    if suffix is None and profile is not None:
        suffix = profile.argv_suffix
    if suffix is None:
        suffix = ("acp",)
    return [cli, *suffix]


def acp_login_hint(name: str) -> str:
    profile = ACP_PROFILES.get(name.lower())
    if profile:
        return profile.login_hint
    spec = HARNESS_SPECS.get(name.lower())
    return spec.auth_hint if spec else "check harness auth docs"


@dataclass
class AcpSessionResult:
    text: str = ""
    files_touched: list[str] = field(default_factory=list)
    stop_reason: str = ""
    notes: list[str] = field(default_factory=list)
    error: str | None = None


class AcpHarnessSession:
    """One ACP turn for any registered harness."""

    def __init__(
        self,
        harness: str,
        *,
        project_root: Path,
        mode: str = "agent",
        model: str | None = None,
        permission: str = "allow",
        with_context: bool = False,
        on_event: EventCallback | None = None,
        timeout_s: float = 600.0,
    ) -> None:
        self.harness = harness.lower()
        self.project_root = project_root
        self.mode = mode
        self.model = model
        self.permission = permission
        self.with_context = with_context
        self.on_event = on_event
        self.timeout_s = timeout_s

    def run(self, task: str) -> AcpSessionResult:
        notes: list[str] = []
        profile = ACP_PROFILES.get(self.harness)

        if self.with_context and profile and profile.mcp_harness:
            action, approved = ensure_deep_context(self.project_root, profile.mcp_harness)
            if action == "added":
                notes.append(f"registered xlii-deep-contexts in .{profile.mcp_harness}/mcp.json")
            if profile.mcp_harness == "cursor" and not approved:
                notes.append("run `cursor-agent mcp enable xlii-deep-contexts`")

        try:
            argv = resolve_acp_argv(self.harness)
        except AcpError as e:
            return AcpSessionResult(error=str(e), notes=notes)

        client: AcpClient | None = None
        try:
            with AcpClient(
                cwd=self.project_root,
                argv=argv,
                mode=self.mode,
                model=self.model,
                permission=self.permission,
                on_event=self.on_event,
                request_timeout=self.timeout_s,
                login_hint=acp_login_hint(self.harness),
            ) as client:
                client.new_session()
                result = client.prompt(task, timeout=self.timeout_s)
        except AcpError as e:
            if client is not None:
                notes.extend(client.notes)
            return AcpSessionResult(error=str(e), notes=notes)
        except Exception as e:
            if client is not None:
                notes.extend(client.notes)
            return AcpSessionResult(error=str(e), notes=notes)

        if client is not None:
            notes.extend(client.notes)
        return AcpSessionResult(
            text=result.text,
            files_touched=list(result.files_touched),
            stop_reason=result.stop_reason or "",
            notes=notes,
        )


class AcpAskAdapter:
    """Headless ask adapter for community ACP harnesses."""

    def __init__(self, harness: str) -> None:
        self.harness = harness
        self.name = harness
        spec = HARNESS_SPECS.get(harness)
        self.tier = spec.tier if spec else "cross_agent"
        self.default_model = spec.default_model if spec else harness

    def run_ask(
        self,
        brief,
        *,
        model: str | None = None,
        timeout_s: int = 600,
        mode: str = "ask",
        permission: str = "allow",
    ):
        from xlii.harness.base import HarnessResult

        # Community ACP ask runs are read-only; permission accepted for a
        # uniform adapter signature (write-gating lives in the delegate session).
        del permission
        model = model or self.default_model
        root = brief.project_root or Path.cwd()
        session = AcpHarnessSession(
            self.harness,
            project_root=root,
            mode=mode,
            model=model,
            timeout_s=float(timeout_s),
        )
        result = session.run(brief.render_prompt())
        if result.error:
            return HarnessResult(
                text="",
                model=model,
                harness=self.name,
                tier=brief.tier or self.tier,
                error=result.error,
            )
        return HarnessResult(
            text=result.text,
            model=model,
            harness=self.name,
            tier=brief.tier or self.tier,
        )
