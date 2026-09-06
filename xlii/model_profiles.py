"""Named model profiles — reasoning-efficiency presets (model-routing R1).

A profile is orchestrator / worker / chat / help model ids. Built-ins ship
with xlii; operators can extend or override via ``model_profiles`` in
config.json.
"""

from __future__ import annotations

from typing import Any, Optional

from xlii.config import (
    DEFAULT_CHAT_MODEL,
    DEFAULT_HELP_MODEL,
    DEFAULT_MODEL,
    DEFAULT_WORKER_MODEL,
    GlobalConfig,
)

_PROFILE_KEYS = ("orchestrator", "worker", "chat", "help")

# Built-in presets — user ``model_profiles`` entries merge on top (override per key).
BUILTIN_MODEL_PROFILES: dict[str, dict[str, str]] = {
    "build": {
        "orchestrator": DEFAULT_MODEL,
        "worker": DEFAULT_WORKER_MODEL,
        "chat": DEFAULT_CHAT_MODEL,
        "help": DEFAULT_HELP_MODEL,
    },
    "reason": {
        "orchestrator": "grok-4.20-reasoning",
        "worker": DEFAULT_WORKER_MODEL,
        "chat": "grok-4.20-reasoning",
        "help": DEFAULT_HELP_MODEL,
    },
    "economy": {
        "orchestrator": DEFAULT_MODEL,
        "worker": DEFAULT_WORKER_MODEL,
        "chat": DEFAULT_MODEL,
        "help": DEFAULT_HELP_MODEL,
    },
    "vision": {
        "orchestrator": DEFAULT_CHAT_MODEL,
        "worker": DEFAULT_WORKER_MODEL,
        "chat": DEFAULT_CHAT_MODEL,
        "help": DEFAULT_HELP_MODEL,
    },
}


def effective_model_profiles(cfg: Any) -> dict[str, dict[str, str]]:
    """Built-in presets merged with user-defined ``model_profiles`` from config."""
    out: dict[str, dict[str, str]] = {
        name: dict(spec) for name, spec in BUILTIN_MODEL_PROFILES.items()
    }
    user = getattr(cfg, "model_profiles", None) or {}
    if not isinstance(user, dict):
        return out
    for name, spec in user.items():
        if not isinstance(name, str) or not isinstance(spec, dict):
            continue
        merged = dict(out.get(name, {}))
        for key in _PROFILE_KEYS:
            val = spec.get(key)
            if isinstance(val, str) and val.strip():
                merged[key] = val.strip()
        if merged:
            out[name.strip()] = merged
    return out


def get_model_profile(cfg: Any, name: str) -> dict[str, str]:
    profiles = effective_model_profiles(cfg)
    key = name.strip()
    if key not in profiles:
        raise KeyError(key)
    return profiles[key]


def apply_model_profile(
    cfg: GlobalConfig,
    name: str,
    *,
    persist: bool = True,
) -> dict[str, str]:
    """Apply a named profile to ``cfg`` (all role slots). Optionally save."""
    prof = get_model_profile(cfg, name)
    if prof.get("orchestrator"):
        cfg.orchestrator_model = prof["orchestrator"]
    if prof.get("worker"):
        cfg.worker_model = prof["worker"]
    if prof.get("chat"):
        cfg.chat_model = prof["chat"]
    if prof.get("help"):
        cfg.help_model = prof["help"]
    if persist:
        cfg.save()
    return prof


def snapshot_cfg_models(cfg: Any) -> dict[str, Optional[str]]:
    """Capture role model fields for session-scoped restore."""
    return {
        "orchestrator_model": getattr(cfg, "orchestrator_model", None),
        "worker_model": getattr(cfg, "worker_model", None),
        "chat_model": getattr(cfg, "chat_model", None),
        "help_model": getattr(cfg, "help_model", None),
    }


def restore_cfg_models(cfg: Any, snap: dict[str, Optional[str]]) -> None:
    for key in ("orchestrator_model", "worker_model", "chat_model", "help_model"):
        if key in snap:
            setattr(cfg, key, snap[key])


def profile_matches_cfg(cfg: Any, name: str) -> bool:
    try:
        prof = get_model_profile(cfg, name)
    except KeyError:
        return False
    if (
        cfg.get_model_for_role("orchestrator") != prof.get("orchestrator")
        or cfg.get_model_for_role("worker") != prof.get("worker")
        or cfg.get_model_for_role("chat") != prof.get("chat")
    ):
        return False
    # Older user profiles may omit help — only enforce when the profile sets it.
    if "help" in prof and cfg.get_model_for_role("help") != prof.get("help"):
        return False
    return True


def format_profile_line(name: str, prof: dict[str, str], *, active: bool = False) -> str:
    tag = " [cyan]← active[/cyan]" if active else ""
    help_bit = f"  help={prof.get('help', '?')}" if prof.get("help") else ""
    return (
        f"  · [bold]{name}[/bold]  "
        f"orch={prof.get('orchestrator', '?')}  "
        f"worker={prof.get('worker', '?')}  "
        f"chat={prof.get('chat', '?')}{help_bit}{tag}"
    )
