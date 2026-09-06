"""Debug-mode harness config and context collection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from xlii.atomicio import write_text_atomic
from xlii.tool_context import MAX_OUTPUT_BYTES

DEFAULT_DEBUG_HARNESS: dict[str, Any] = {
    "analyze": {"via": "claude", "model": "claude-sonnet-4-6"},
    "fix": {"via": "cursor", "mode": "agent", "model": "composer-2.5"},
}


def harness_config_path(xli_dir: Path) -> Path:
    return Path(xli_dir) / "debug" / "harness.json"


def load_debug_harness_config(xli_dir: Path) -> dict[str, Any]:
    path = harness_config_path(xli_dir)
    if not path.exists():
        return dict(DEFAULT_DEBUG_HARNESS)
    try:
        data = json.loads(path.read_text(errors="replace"))
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_DEBUG_HARNESS)
    if not isinstance(data, dict):
        return dict(DEFAULT_DEBUG_HARNESS)
    merged = dict(DEFAULT_DEBUG_HARNESS)
    for key, default in DEFAULT_DEBUG_HARNESS.items():
        if key not in data:
            continue
        val = data[key]
        if isinstance(val, dict):
            merged[key] = {**default, **val}
    return merged


def save_debug_harness_config(xli_dir: Path, data: dict[str, Any]) -> None:
    path = harness_config_path(xli_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, json.dumps(data, indent=2) + "\n")


def _read_text_capped(path: Path, *, max_bytes: int = MAX_OUTPUT_BYTES) -> str:
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    if len(raw) <= max_bytes:
        return raw.decode("utf-8", errors="replace")
    return (
        raw[:max_bytes].decode("utf-8", errors="replace")
        + f"\n…[truncated {len(raw) - max_bytes} bytes]"
    )


def collect_debug_context(xli_dir: Path) -> str:
    """Gather debug session artifacts for harness consult/delegate."""
    parts: list[str] = []
    debug_dir = Path(xli_dir) / "debug"
    session = debug_dir / "session.json"
    if session.exists():
        text = _read_text_capped(session)
        if text.strip():
            parts.append(f"[debug session]\n{text}")
    if debug_dir.is_dir():
        for fp in sorted(debug_dir.glob("*.log")):
            text = _read_text_capped(fp)
            if text.strip():
                parts.append(f"[{fp.name}]\n{text}")
    return "\n\n".join(parts)
