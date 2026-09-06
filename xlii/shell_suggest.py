"""Precompiled shell suggestions — fish-style ghost text from habits.

The-fold Vector F (input completions v1). THE LAW: **no model and no I/O in the
input loop.** The intelligence runs at COMPILE time and produces an inert table;
the keystroke path (:func:`ghost_suggestion`) is a dumb prefix scan over that
static in-memory table and never knows an AI authored the entries. This mirrors
the index-time/query-time split the tree already lives by (the journal's batched
beat, Collections embed-at-upload, help corpus bundled ahead of ``/help``).

Design, per the settled spec (``proposal-queue.md`` §6):

* **AI at compile, dumb matcher at serve.** :func:`compile_table` curates a
  redacted, frecency-ranked, deduped table. An optional ``distiller`` callable —
  built by :func:`make_model_distiller` from the user's own chat client — may
  refine it at compile time; it is best-effort and falls back to the deterministic
  table on any failure (graceful nothing). The serve path is identical either way.
* **Batched beat, not the journal's 5-turn beat.** :func:`record_shell_command`
  counts *shell commands* (not turns); a compile fires on a ~30–40-command window
  or at session close (:func:`session_close_compile`), whichever comes first.
* **Stale is fine; failure is gracefully nothing.** No table → no ghost text.
  Zero gate, zero wait, zero cost in the loop.
* **Safety (non-negotiable):** completion *inserts, never executes* (the widget
  requires Enter to run). **Redaction at compile** — shell history carries secrets
  (tokens in curl headers); :func:`redact` scrubs them and :func:`contains_probable_secret`
  drops any command whose secret can't be safely bounded, before anything lands in
  the table.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Optional

from xlii.atomicio import write_text_atomic
from xlii.config import global_config_dir

# Compile fires every N shell commands (clamped; env-overridable for tests/tuning).
SHELL_SUGGEST_WINDOW_DEFAULT = 35
SHELL_SUGGEST_WINDOW_MIN = 5
SHELL_SUGGEST_WINDOW_MAX = 200
# Rolling history buffer + served table caps (curation drops the tail).
_HISTORY_CAP = 500
_SERVE_CAP = 60

# A distiller refines a candidate list at compile time; returns a cleaned list or
# None to keep the deterministic table.
Distiller = Callable[[list[str]], Optional[list[str]]]


def shell_suggest_window() -> int:
    """Compile window, honoring ``XLII_SHELL_SUGGEST_WINDOW`` (clamped)."""
    raw = os.environ.get("XLII_SHELL_SUGGEST_WINDOW", "").strip()
    if raw:
        try:
            return max(SHELL_SUGGEST_WINDOW_MIN, min(SHELL_SUGGEST_WINDOW_MAX, int(raw)))
        except ValueError:
            # A non-numeric XLII_SHELL_SUGGEST_WINDOW falls through to the default below.
            pass
    return SHELL_SUGGEST_WINDOW_DEFAULT


def shell_suggest_ai_enabled() -> bool:
    """Whether the compile-time AI distiller is enabled — OPT-IN via
    ``XLII_SHELL_SUGGEST_AI`` (unset/false ⇒ deterministic, zero model spend). The
    serve path is byte-identical either way; this only decides whether the periodic
    off-loop compile refines the table with a cheap-model pass. Deterministic-by-
    default keeps the 'no unannounced model spend' contract the feature rests on."""
    return os.environ.get("XLII_SHELL_SUGGEST_AI", "").strip().lower() in ("1", "true", "yes", "on")


# --------------------------------------------------------------------------- #
#  Redaction — the safety keystone. Runs at record time AND again at compile.
# --------------------------------------------------------------------------- #
_REDACT = "<redacted>"

# Ordered scrubbers: each replaces a secret VALUE with the placeholder, keeping the
# command's useful shape. Applied in sequence.
_REDACTORS: tuple[tuple[re.Pattern[str], str], ...] = (
    # `Bearer <token>` (Authorization headers)
    (re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._\-+/=]{4,})"), r"\1" + _REDACT),
    # `Header-Name: <value>` for auth-ish headers (curl -H "...: ...")
    (re.compile(r"(?i)((?:authorization|x-api-key|api-key|x-auth-token|proxy-authorization|cookie)\s*:\s*)([^\"'\s][^\"']*)"),
     r"\1" + _REDACT),
    # `--token=v` / `--password v` / `--api-key v` and friends
    (re.compile(r"(?i)(--?(?:token|password|passwd|pwd|api[-_]?key|apikey|secret|access[-_]?token|client[-_]?secret|auth[-_]?token)(?:[=\s]))(\S+)"),
     r"\1" + _REDACT),
    # env assignment whose NAME hints a secret: `GITHUB_TOKEN=…`, `MY_SECRET=…`
    (re.compile(r"(?i)\b([A-Za-z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API[_-]?KEY|APIKEY|ACCESS[_-]?KEY|AUTH)[A-Za-z0-9_]*=)(\S+)"),
     r"\1" + _REDACT),
    # URL credentials `scheme://user:pass@host`
    (re.compile(r"(://[^/\s:@]+:)([^/\s@]+)(@)"), r"\1" + _REDACT + r"\3"),
    # `-u user:pass` / `--user …` / `-a user:pass` / `--auth user:pass` (curl, httpie)
    (re.compile(r"(?i)((?:-u|--user|-a|--auth)\s+[^\s:]+:)(\S+)"), r"\1" + _REDACT),
    # URL query secrets `?token=…&api_key=…`
    (re.compile(r"(?i)([?&](?:api[-_]?key|apikey|token|access[-_]?token|secret|password|auth|sig|signature)=)([^&\s\"']+)"),
     r"\1" + _REDACT),
    # Known token shapes anywhere (provider prefixes + JWTs)
    (re.compile(r"\b(sk-[A-Za-z0-9]{6,}|ghp_[A-Za-z0-9]{16,}|gho_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}|xox[baprs]-[A-Za-z0-9-]{8,}|AKIA[0-9A-Z]{12,}|AIza[0-9A-Za-z_\-]{20,}|eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,})\b"),
     _REDACT),
)

# A surviving high-entropy blob (mixed letters+digits, long) after redaction ⇒ we
# couldn't bound the secret, so the whole command is dropped rather than risk a leak.
_BLOB = re.compile(r"[A-Za-z0-9+/=_\-]{28,}")


def redact(cmd: str) -> str:
    """Scrub known secret shapes from a shell command, keeping its structure."""
    out = cmd
    for pat, repl in _REDACTORS:
        out = pat.sub(repl, out)
    return out


def contains_probable_secret(s: str) -> bool:
    """True when a secret-shaped blob survived redaction (⇒ drop the command).
    Conservative on purpose — the safety story matters doubly if the table ever
    rides a synced channel."""
    for m in _BLOB.finditer(s):
        tok = m.group(0)
        if any(c.isalpha() for c in tok) and any(c.isdigit() for c in tok):
            return True
    return False


def _clean(cmd: str) -> Optional[str]:
    """Redact then gate a single command. Returns the safe string, or ``None`` to
    drop it (empty, or a secret that couldn't be bounded)."""
    cmd = (cmd or "").strip()
    if not cmd:
        return None
    red = redact(cmd)
    if contains_probable_secret(red):
        return None
    return red


# --------------------------------------------------------------------------- #
#  Storage — history buffer (compile input) + served table (compile output).
#  Both under <config>/shell_suggest/ so XLII_CONFIG_DIR isolates them in tests.
# --------------------------------------------------------------------------- #
def _base_dir(config_dir: Optional[Any] = None) -> Path:
    root = Path(config_dir) if config_dir is not None else global_config_dir()
    return root / "shell_suggest"


def _table_path(config_dir: Optional[Any] = None) -> Path:
    return _base_dir(config_dir) / "table.json"


def _history_path(config_dir: Optional[Any] = None) -> Path:
    return _base_dir(config_dir) / "history.json"


def load_table(config_dir: Optional[Any] = None) -> list[str]:
    """Load the served table (frecency-ranked command strings). Missing/broken →
    ``[]`` (graceful nothing → no ghost text)."""
    try:
        data = json.loads(_table_path(config_dir).read_text())
    except (OSError, ValueError):
        return []
    if isinstance(data, dict):
        data = data.get("commands", [])
    if not isinstance(data, list):
        return []
    return [c for c in data if isinstance(c, str) and c]


def store_table(table: list[str], config_dir: Optional[Any] = None) -> None:
    path = _table_path(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, json.dumps({"commands": list(table)}, ensure_ascii=False))


def _load_history(config_dir: Optional[Any] = None) -> dict:
    try:
        data = json.loads(_history_path(config_dir).read_text())
    except (OSError, ValueError):
        data = {}
    entries = data.get("entries") if isinstance(data, dict) else None
    return {
        "seq": int(data.get("seq", 0)) if isinstance(data, dict) else 0,
        "count": int(data.get("count", 0)) if isinstance(data, dict) else 0,
        "entries": entries if isinstance(entries, dict) else {},
    }


def _store_history(state: dict, config_dir: Optional[Any] = None) -> None:
    path = _history_path(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, json.dumps(state, ensure_ascii=False))


def _frecency(rec: dict, now_seq: int) -> float:
    """Frecency = repetition weight × recency decay. A command run often and
    recently ranks above a stale one-off. ``now_seq`` is the current monotonic
    record counter (deterministic — no wall clock, so tests are reproducible)."""
    count = max(1, int(rec.get("count", 1)))
    age = max(0, now_seq - int(rec.get("seq", 0)))
    # Gentle decay: halve the weight roughly every 60 commands of age.
    return count / (1.0 + age / 60.0)


# --------------------------------------------------------------------------- #
#  Compile — curate the buffer into the served table (redact · normalize ·
#  frecency-rank · dedup · cap), then optionally refine via an AI distiller.
# --------------------------------------------------------------------------- #
def compile_table(entries: dict, *, distiller: Optional[Distiller] = None) -> list[str]:
    """Curate ``entries`` (``cmd -> {"count", "seq"}``) into a ranked, redacted,
    capped list. Curation beats raw frecency: re-redact, drop unbounded secrets,
    prefer repeated commands over one-off noise, cap the tail. An optional
    ``distiller`` refines the result at compile time (best-effort)."""
    now_seq = max((int(r.get("seq", 0)) for r in entries.values()), default=0)

    scored: list[tuple[float, str]] = []
    for cmd, rec in entries.items():
        safe = _clean(cmd)
        if safe is None:
            continue
        scored.append((_frecency(rec, now_seq), safe))
    scored.sort(key=lambda t: (-t[0], t[1]))

    # Dedup (redaction can collapse variants), preserving best-frecency order.
    seen: set[str] = set()
    ranked: list[str] = []
    for _score, cmd in scored:
        if cmd not in seen:
            seen.add(cmd)
            ranked.append(cmd)
    ranked = ranked[:_SERVE_CAP]

    if distiller is not None:
        refined = _run_distiller(distiller, ranked)
        if refined:
            ranked = refined[:_SERVE_CAP]
    return ranked


def _run_distiller(distiller: Distiller, candidates: list[str]) -> Optional[list[str]]:
    """Call the compile-time AI distiller, best-effort. Any failure falls back to
    the deterministic table (the graceful-nothing contract) — mirrors the journal's
    best-effort model seam. Output is re-scrubbed: a distiller must never be able to
    re-introduce a secret."""
    try:
        out = distiller(list(candidates))
    except Exception:
        # Best-effort enrichment: a distiller hiccup must never break the compile
        # (which then never breaks the shell path). Deterministic table stands.
        return None
    if not out:
        return None
    cleaned = [c for c in (_clean(x) for x in out) if c]
    return cleaned or None


def make_model_distiller(clients: Any, model: str) -> Distiller:
    """Build a distiller backed by the user's OWN chat client (no cross-vendor
    judge), mirroring the journal's one-shot completion seam. It curates the
    candidate commands; the serve path never knows a model was involved. Failures
    return ``None`` so :func:`compile_table` keeps the deterministic table."""
    system = (
        "You curate a shell-command autosuggestion table. Given a list of commands, "
        "return a cleaned list: drop noisy one-offs, normalize near-duplicate variants "
        "to one canonical form, and keep the useful habitual commands. Preserve exact "
        "command text otherwise. Output one command per line, no numbering, no prose. "
        "NEVER include secrets, tokens, passwords, or keys."
    )

    def distill(candidates: list[str]) -> Optional[list[str]]:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": "\n".join(candidates)},
        ]
        try:
            resp = clients.chat.chat.completions.create(
                model=model, messages=messages, temperature=0.2,
            )
            text = (resp.choices[0].message.content or "").strip()
        except (AttributeError, IndexError, OSError):
            return None
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return lines or None

    return distill


def build_shell_distiller(state: Any) -> Optional[Distiller]:
    """The opt-in distiller seam: build one from a REPL/app ``state`` (its ``.pool``
    + ``.cfg``), or ``None``.

    OFF unless :func:`shell_suggest_ai_enabled` (``XLII_SHELL_SUGGEST_AI``) —
    deterministic table, zero model spend by default. When enabled it's billed to
    the PRIMARY chat key (the dedicated journal key is reserved for the journal) on
    the cheap worker model. Any missing piece — no key, no model, a test double with
    no pool — returns ``None`` so the deterministic table always stands. Called on
    the off-loop record path; the resulting model call never touches the UI thread."""
    if not shell_suggest_ai_enabled():
        return None
    pool = getattr(state, "pool", None)
    cfg = getattr(state, "cfg", None)
    if pool is None or cfg is None:
        return None
    try:
        clients = pool.primary()
        model = cfg.worker()
    except Exception:
        return None
    if not clients or not model:
        return None
    return make_model_distiller(clients, model)


# --------------------------------------------------------------------------- #
#  Record — the counter + trigger. Called OFF the keystroke path (after a shell
#  command has already run). Wire it at the shell-execute seam (integrator hook).
# --------------------------------------------------------------------------- #
def record_shell_command(
    cmd: str,
    *,
    distiller: Optional[Distiller] = None,
    config_dir: Optional[Any] = None,
) -> bool:
    """Record one executed shell command toward the compile window. Redacts before
    buffering (defense in depth), bumps frecency, and fires a compile when the
    window is reached. Returns ``True`` iff a compile fired this call.

    Off-loop: this does file I/O and (on compile) may call the model — never call
    it from the input/keystroke path."""
    safe = _clean(cmd)
    state = _load_history(config_dir)
    state["seq"] += 1
    state["count"] += 1
    if safe is not None:
        rec = state["entries"].get(safe) or {"count": 0, "seq": 0}
        rec["count"] = int(rec.get("count", 0)) + 1
        rec["seq"] = state["seq"]
        state["entries"][safe] = rec
        _prune_history(state)

    fired = False
    if state["count"] >= shell_suggest_window():
        table = compile_table(state["entries"], distiller=distiller)
        store_table(table, config_dir)
        state["count"] = 0
        fired = True
    _store_history(state, config_dir)
    return fired


def _prune_history(state: dict) -> None:
    """Bound the buffer — drop the lowest-frecency entries past the cap."""
    entries = state["entries"]
    if len(entries) <= _HISTORY_CAP:
        return
    now_seq = state["seq"]
    keep = sorted(entries.items(), key=lambda kv: _frecency(kv[1], now_seq), reverse=True)[:_HISTORY_CAP]
    state["entries"] = dict(keep)


def session_close_compile(config_dir: Optional[Any] = None, *, distiller: Optional[Distiller] = None) -> bool:
    """Force a final compile at session close (the 'whichever comes first' arm).
    No-op with no buffered history. Returns ``True`` iff it compiled."""
    state = _load_history(config_dir)
    if not state["entries"]:
        return False
    table = compile_table(state["entries"], distiller=distiller)
    store_table(table, config_dir)
    state["count"] = 0
    _store_history(state, config_dir)
    return True


# --------------------------------------------------------------------------- #
#  Serve — the ONLY function the input loop calls. Pure prefix scan, no I/O.
# --------------------------------------------------------------------------- #
def ghost_suggestion(prefix: str, table: list[str]) -> Optional[str]:
    """Fish-style: return the completion SUFFIX for ``prefix`` (the dim tail shown
    after the cursor), or ``None``. Prefix match over the frecency-ranked table
    (first/best wins). No model, no I/O — a bounded scan over ~60 in-memory strings.

    ``table`` empty (never compiled) → ``None`` → no ghost text, gracefully."""
    if not prefix or not prefix.strip():
        return None
    for cmd in table:
        if len(cmd) > len(prefix) and cmd.startswith(prefix):
            return cmd[len(prefix):]
    return None
