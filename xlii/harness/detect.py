"""Detect external harness CLIs on PATH for /status and /consult --via."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_LOCAL_LOADED: dict[str, float] = {}
_BUILTIN_HARNESSES = frozenset({"cursor", "claude", "codex", "grok"})
_LOCAL_BY_XLI: dict[str, set[str]] = {}
_ACTIVE_XLI: str | None = None

@dataclass(frozen=True)
class HarnessSpec:
    name: str
    binaries: tuple[str, ...]
    binary_env: str
    tier: str
    auth_hint: str
    default_model: str
    # Vector C / seam #5: the human-facing label the harness wears when it is the
    # foreground *mode* (`/cursor on` → "cursor", claude → "claude code"). Empty
    # falls back to ``name`` (harness_label). A1's status.placeholder_key reads
    # this label off the foreground harness; community harnesses get their name.
    label: str = ""
    # Vector C / seam #5: model is cursor's axis ALONE. Cursor is a host you run a
    # chosen model through; the others *are* the model. The stat bar appends
    # `· <model>` only when this is True (cursor), and surface-aware `/model` only
    # acts inside a model_selectable harness mode.
    model_selectable: bool = False


HARNESS_SPECS: dict[str, HarnessSpec] = {
    "cursor": HarnessSpec(
        name="cursor",
        binaries=("cursor-agent",),
        binary_env="XLII_CURSOR_AGENT_BIN",
        tier="cross_agent",
        auth_hint="cursor-agent login",
        default_model="composer-2.5",
        label="cursor",
        model_selectable=True,
    ),
    "claude": HarnessSpec(
        name="claude",
        binaries=("claude",),
        binary_env="XLII_CLAUDE_BIN",
        tier="cross_org",
        auth_hint="ANTHROPIC_API_KEY or claude login",
        default_model="claude-sonnet-4-6",
        label="claude code",
    ),
    "codex": HarnessSpec(
        name="codex",
        binaries=("codex",),
        binary_env="XLII_CODEX_BIN",
        tier="cross_org",
        auth_hint="ChatGPT plan or OPENAI_API_KEY",
        default_model="gpt-5.3-codex",
        label="codex",
    ),
    "grok": HarnessSpec(
        name="grok",
        binaries=("grok",),
        binary_env="XLII_GROK_BIN",
        tier="same_vendor",
        auth_hint="SuperGrok OAuth or XAI_API_KEY",
        default_model="grok-build-0.1",
        label="grok build",
    ),
}


def list_harness_names() -> list[str]:
    return list(HARNESS_SPECS.keys())


def register_harness(
    spec: HarnessSpec,
    *,
    acp: bool = False,
    acp_argv_suffix: tuple[str, ...] = ("acp",),
) -> None:
    """Register a community harness from ``.xlii/harness.local.py``."""
    if spec.name in _BUILTIN_HARNESSES:
        from xlii.ui import console

        console.print(
            f"[yellow]Warning:[/yellow] ignoring harness.local.py registration "
            f"of built-in harness {spec.name!r}"
        )
        return
    HARNESS_SPECS[spec.name] = spec
    if _ACTIVE_XLI is not None and spec.name not in _BUILTIN_HARNESSES:
        _LOCAL_BY_XLI.setdefault(_ACTIVE_XLI, set()).add(spec.name)
    if acp:
        from xlii.harness import acp_session

        acp_session._EXTRA_ACP_SUFFIX[spec.name] = acp_argv_suffix


def _unload_local_harnesses(key: str) -> None:
    from xlii.harness import acp_session

    for name in _LOCAL_BY_XLI.pop(key, set()):
        HARNESS_SPECS.pop(name, None)
        acp_session._EXTRA_ACP_SUFFIX.pop(name, None)
    _LOCAL_LOADED.pop(key, None)


def _activate_xli(key: str) -> None:
    global _ACTIVE_XLI
    if _ACTIVE_XLI == key:
        return
    if _ACTIVE_XLI is not None:
        _unload_local_harnesses(_ACTIVE_XLI)
    _ACTIVE_XLI = key


def load_local_harnesses(xli_dir: Path) -> None:
    """Load optional community harness adapters from ``.xlii/harness.local.py``."""
    key = str(Path(xli_dir).resolve())
    _activate_xli(key)
    path = Path(xli_dir) / "harness.local.py"
    if not path.exists():
        # File removed while this project stays active: drop any harnesses it
        # previously registered so /consult --via, /delegate, and /status stop
        # listing names that no longer exist.
        if key in _LOCAL_BY_XLI or key in _LOCAL_LOADED:
            _unload_local_harnesses(key)
        return
    mtime = path.stat().st_mtime
    if _LOCAL_LOADED.get(key) == mtime:
        return
    if key in _LOCAL_BY_XLI or key in _LOCAL_LOADED:
        _unload_local_harnesses(key)
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location("xlii_harness_local", path)
        if spec is None or spec.loader is None:
            return
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if hasattr(mod, "register"):
            mod.register(register_harness)
        _LOCAL_LOADED[key] = mtime
    except Exception as e:
        from xlii.ui import console

        console.print(f"[yellow]Warning:[/yellow] failed to load {path}: {e}")

def harness_meta(name: str) -> dict[str, Any]:
    spec = HARNESS_SPECS.get(name)
    if spec is None:
        raise ValueError(f"unknown harness: {name!r} (choose: {', '.join(HARNESS_SPECS)})")
    return {
        "name": spec.name,
        "tier": spec.tier,
        "default_model": spec.default_model,
        "auth_hint": spec.auth_hint,
        "label": harness_label(name),
        "model_selectable": spec.model_selectable,
    }


def harness_label(name: str) -> str:
    """The foreground-mode label for ``name`` (seam #5) — the spec's ``label``
    when set, else the bare name. This is the word the input frame / status bar
    wears while the harness is the foreground mode (`cursor`, `claude code`)."""
    spec = HARNESS_SPECS.get(name.lower())
    if spec is None:
        return name
    return spec.label or spec.name


def harness_model_selectable(name: str) -> bool:
    """True when ``name`` is a *host* whose model the user picks (cursor) rather
    than a harness that *is* its model (claude/codex/grok). Drives the `· <model>`
    stat suffix (seam #5) and surface-aware `/model`."""
    spec = HARNESS_SPECS.get(name.lower())
    return bool(spec and spec.model_selectable)


def harness_label_to_name(label: str) -> str | None:
    """Reverse a foreground label (`claude code`) back to its harness name
    (`claude`). Used by routing/status to resolve a mode word to a harness."""
    want = (label or "").strip().lower()
    for spec in HARNESS_SPECS.values():
        if want in (spec.name.lower(), (spec.label or spec.name).lower()):
            return spec.name
    return None


def harness_hint(name: str) -> str:
    """The bare-input contract shown in the input box while ``name`` is the
    foreground harness mode (seam #5): bare input drives the harness session,
    `?` still summons xlii's own AI, ``/off`` leaves (same as every overlay)."""
    label = harness_label(name)
    return f"type to drive {label} · ? for xlii · ! to run · /off"


def register_harness_hints() -> None:
    """Register an input hint (seam #5, via the existing ``register_hint``) for
    every harness's foreground mode word, so the input box honestly reflects the
    harness-as-a-mode contract once A1's placeholder_key surfaces the label.
    Idempotent — re-registering the same word just refreshes it."""
    from xlii.hints import register_hint

    for spec in HARNESS_SPECS.values():
        register_hint(harness_label(spec.name), harness_hint(spec.name))


def resolve_binary(spec: HarnessSpec) -> str | None:
    override = os.environ.get(spec.binary_env)
    if override:
        return override if os.path.exists(override) else None
    for name in spec.binaries:
        found = shutil.which(name)
        if found:
            return found
    return None


def detect_all() -> list[tuple[str, bool, str]]:
    """Return ``(name, available, hint)`` for each registered harness."""
    out: list[tuple[str, bool, str]] = []
    for spec in HARNESS_SPECS.values():
        cli = resolve_binary(spec)
        if cli:
            out.append((spec.name, True, cli))
        else:
            out.append((spec.name, False, spec.auth_hint))
    return out


def harness_status_suffixes() -> list[str]:
    """Extra /status lines for detected harnesses."""
    lines: list[str] = []
    for name, ok, hint in detect_all():
        if ok:
            spec = HARNESS_SPECS[name]
            lines.append(
                f"  harness/{name}:  [green]available[/green] ({hint}) · tier {spec.tier}"
            )
        else:
            lines.append(f"  harness/{name}:  [dim]not on PATH ({hint})[/dim]")
    return lines


def resolve_harness(name: str):
    """Import and return the adapter module for ``name``."""
    if name not in HARNESS_SPECS:
        raise ValueError(f"unknown harness: {name!r}")
    if name == "cursor":
        from xlii.harness import cursor as mod
        return mod
    if name == "claude":
        from xlii.harness import claude as mod
        return mod
    if name == "codex":
        from xlii.harness import codex as mod
        return mod
    if name == "grok":
        from xlii.harness import grok_build as mod
        return mod
    from xlii.harness.acp_session import AcpAskAdapter, acp_harness_names

    if name in acp_harness_names():
        return AcpAskAdapter(name)
    raise ValueError(f"unknown harness: {name!r}")
