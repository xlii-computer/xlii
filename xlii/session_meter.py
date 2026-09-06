"""Session cost meter — cumulative per-session token/USD totals and soft budget.

Phase 4 of terminal-native-toolkit: rolls up TurnStats at turn-finish, surfaces
via /cost --session, the TUI profile bar, and an optional XLII_BUDGET soft cap.
Session-only (not persisted to session.json).
"""

from __future__ import annotations

import json
import os
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from xlii.cost import format_cost, format_tokens

# Baked fallback — longest prefix wins. Live GET /v1/models context_length
# overrides this when the catalog is reachable.
_CONTEXT_WINDOWS: tuple[tuple[str, int], ...] = (
    ("grok-4.6", 500_000),
    ("grok-4.5", 500_000),
    ("grok-4.3", 1_000_000),
    ("grok-4.20", 1_000_000),
    ("grok-4", 1_000_000),
    ("grok-code", 256_000),
    ("grok-build", 256_000),
    ("grok-3", 131_072),
    ("grok-2", 131_072),
)

_CACHE_NAME = "model_windows.json"
_CACHE_TTL_S = 12 * 3600
_live: dict[str, int] = {}
_live_at: float = 0.0
_live_lock = threading.Lock()
_refresh_started = threading.Event()


def _cache_path() -> Path:
    from xlii.config import global_config_dir

    return global_config_dir() / _CACHE_NAME


def _prefix_match(model: str, table: tuple[tuple[str, int], ...]) -> Optional[int]:
    name = model or ""
    if not name:
        return None
    best_key = ""
    best_win: Optional[int] = None
    for key, win in table:
        if key and key in name and len(key) > len(best_key):
            best_key = key
            best_win = win
    return best_win


@lru_cache(maxsize=1)
def _load_context_windows() -> tuple[tuple[str, int], ...]:
    raw = os.environ.get("XLII_TUI_CONTEXT_WINDOWS", "").strip()
    if not raw:
        return _CONTEXT_WINDOWS
    parsed: list[tuple[str, int]] = []
    for part in raw.split(","):
        item = part.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key:
            continue
        try:
            win = int(value)
        except ValueError:
            continue
        if win > 0:
            parsed.append((key, win))
    return tuple(parsed) if parsed else _CONTEXT_WINDOWS


def _load_live_windows() -> dict[str, int]:
    """In-memory catalog, else on-disk cache if still fresh."""
    global _live, _live_at
    now = time.time()
    with _live_lock:
        if _live and (now - _live_at) < _CACHE_TTL_S:
            return dict(_live)
    try:
        raw = json.loads(_cache_path().read_text())
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    windows = raw.get("windows") if isinstance(raw, dict) else None
    if not isinstance(windows, dict):
        return {}
    fetched = raw.get("fetched_at")
    try:
        age_ok = True
        if isinstance(fetched, (int, float)):
            age_ok = (now - float(fetched)) < _CACHE_TTL_S
    except (TypeError, ValueError):
        age_ok = True
    cleaned = {
        str(k): int(v)
        for k, v in windows.items()
        if isinstance(k, str) and str(k) and _positive_int(v)
    }
    if not cleaned:
        return {}
    if age_ok:
        with _live_lock:
            _live = cleaned
            _live_at = now
    return cleaned


def _positive_int(v: Any) -> bool:
    try:
        return int(v) > 0
    except (TypeError, ValueError):
        return False


def _save_live_windows(windows: dict[str, int]) -> None:
    global _live, _live_at
    now = time.time()
    with _live_lock:
        _live = dict(windows)
        _live_at = now
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"fetched_at": now, "windows": windows}))
    except OSError:
        # An unwritable cache means the next process re-fetches the windows.
        pass


def refresh_model_windows(*, force: bool = False) -> dict[str, int]:
    """Pull ``context_length`` from ``/v1/models``. Empty on any failure."""
    if not force:
        have = _load_live_windows()
        if have:
            return have
    try:
        from xlii.bootstrap import discover_model_windows
        from xlii.config import GlobalConfig

        cfg = GlobalConfig.load()
        try:
            keys = [kp.api_key for kp in cfg.key_pairs() if kp.api_key]
        except Exception:
            keys = []
        mgmt = getattr(cfg, "management_api_key", None) or ""
        if not keys and not mgmt:
            return {}
        windows = discover_model_windows(
            mgmt, getattr(cfg, "team_id", None) or "", chat_keys=keys
        )
    except Exception:
        return {}
    if windows:
        _save_live_windows(windows)
    return windows


def reset_model_window_cache() -> None:
    """Test seam — drop in-memory + on-disk catalog so baked/env tables win."""
    global _live, _live_at
    with _live_lock:
        _live.clear()
        _live_at = 0.0
    _refresh_started.clear()
    _load_context_windows.cache_clear()
    try:
        _cache_path().unlink(missing_ok=True)
    except OSError:
        # The in-memory caches are already cleared above; a stale file is re-read and replaced.
        pass


def kick_model_window_refresh() -> None:
    """One background catalog refresh per process. HUD never waits on it."""
    if _refresh_started.is_set():
        return
    _refresh_started.set()
    threading.Thread(
        target=lambda: refresh_model_windows(force=True),
        name="xlii-model-windows",
        daemon=True,
    ).start()


def ktok(n: int) -> str:
    """Compact token count: 102_400 → '102K', 1_000_000 → '1M'."""
    n = max(0, int(n))
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    return f"{round(n / 1000)}K"


def context_window(model: str) -> Optional[int]:
    """Cap for *model*: env override, then live catalog, then baked prefixes."""
    name = (model or "").strip()
    if not name:
        return None
    env_table = _load_context_windows()
    if env_table is not _CONTEXT_WINDOWS:
        hit = _prefix_match(name, env_table)
        if hit is not None:
            return hit
    live = _load_live_windows()
    if name in live:
        return live[name]
    return _prefix_match(name, _CONTEXT_WINDOWS)


def context_meter_text(state: Any, *, model: str = "") -> str:
    """``270K / 500K`` occupancy, plus cache % when the last turn reported it."""
    sess = getattr(getattr(state, "agent", None), "session", None)
    stats = getattr(sess, "last_turn_stats", None) if sess is not None else None
    used = int(getattr(stats, "context_tokens", 0) or 0)
    cached = int(getattr(stats, "cached_tokens", 0) or 0)
    model = (
        model
        or getattr(stats, "model", "")
        or getattr(getattr(stats, "orch", None), "model", "")
        or ""
    )
    cap = context_window(model)
    if not used and not cap:
        return ""
    left = ktok(used) if used else "0K"
    meter = f"{left} / {ktok(cap)}" if cap else left
    if used and cached:
        meter += f" · {round(100 * cached / used)}% cached"
    return meter


def session_usage_text(state: Any) -> str:
    """``sess 84K · $0.12`` — billed this session. Empty before any turn."""
    sess = getattr(getattr(state, "agent", None), "session", None)
    tokens = int(
        getattr(sess, "session_tokens", None)
        or getattr(state, "session_tokens", 0)
        or 0
    )
    spent = float(
        getattr(sess, "session_cost", None)
        or getattr(state, "session_cost", 0.0)
        or 0.0
    )
    if tokens <= 0 and spent <= 0:
        return ""
    bits: list[str] = []
    if tokens:
        bits.append(ktok(tokens))
    if spent:
        bits.append(format_cost(spent))
    return "sess " + " · ".join(bits) if bits else ""

if TYPE_CHECKING:
    from xlii.agent_stats import TurnStats


def turn_total_tokens(stats: "TurnStats") -> int:
    """All billed tokens this turn (orch + workers + judges).

    Defensive against a partial stats object (a turn that returned a minimal
    stub rather than a full TurnStats): a missing component contributes 0
    instead of raising AttributeError.
    """
    total = 0
    for part_name in ("orch", "workers", "judges"):
        part = getattr(stats, part_name, None)
        if part is not None:
            total += getattr(part, "total_tokens", 0)
    return total


def init_budget_from_env(session: Any) -> None:
    """Seed session.budget_usd from XLII_BUDGET once at session start."""
    if getattr(session, "budget_env_cleared", False):
        return
    if getattr(session, "budget_usd", None) is not None:
        return
    raw = os.environ.get("XLII_BUDGET", "").strip()
    if not raw:
        return
    try:
        val = float(raw)
    except ValueError:
        return
    if val > 0:
        session.budget_usd = val


def record_turn(session: Any, stats: Optional["TurnStats"]) -> None:
    """Accumulate a finished turn into session totals and stash last-turn stats."""
    if stats is None:
        return
    session.last_turn_stats = stats
    session.session_tokens += turn_total_tokens(stats)
    cost = stats.total_cost
    if cost is not None:
        session.session_cost += cost


def budget_warning(session: Any) -> Optional[str]:
    """Rich markup warning when session cost exceeds the soft budget, else None."""
    limit = getattr(session, "budget_usd", None)
    if limit is None:
        return None
    spent = float(getattr(session, "session_cost", 0.0) or 0.0)
    if spent <= limit:
        return None
    return (
        f"[yellow]session cost {format_cost(spent)} exceeds budget "
        f"{format_cost(limit)}[/yellow] — continuing (soft cap)"
    )


def session_cost_label(state: Any) -> str:
    """Compact cost segment for the status strip, or '' when nothing to show."""
    spent = float(getattr(state, "session_cost", 0.0) or 0.0)
    has_turn = getattr(getattr(state, "agent", None), "session", None)
    has_turn = has_turn is not None and getattr(has_turn, "last_turn_stats", None) is not None
    if spent <= 0 and not has_turn:
        return ""
    label = format_cost(spent)
    limit = getattr(state, "budget_usd", None)
    if limit is not None:
        label += f"/{format_cost(limit)}"
    return label


def format_turn_cost_line(stats: "TurnStats") -> str:
    """One-line summary of a single turn's tokens and cost."""
    tokens = turn_total_tokens(stats)
    cost = stats.total_cost
    if cost is None:
        return f"this turn: {format_tokens(tokens)} · cost unknown (no pricing)"
    return f"this turn: {format_tokens(tokens)} · {format_cost(cost)}"


def format_session_cost_line(session: Any) -> str:
    """One-line cumulative session summary."""
    tokens = int(getattr(session, "session_tokens", 0) or 0)
    spent = float(getattr(session, "session_cost", 0.0) or 0.0)
    limit = getattr(session, "budget_usd", None)
    if spent > 0 or tokens > 0:
        line = f"session: {format_tokens(tokens)} · {format_cost(spent)}"
    else:
        line = "session: no turns yet"
    if limit is not None:
        line += f" · budget {format_cost(limit)}"
    return line
