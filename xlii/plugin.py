"""Plugin management — markdown files describing external APIs/services.

A plugin is a markdown file with YAML frontmatter at
`~/.config/xlii/plugins/<id>.md`. The frontmatter holds machine-readable
metadata (id, name, description, categories, effect, trust, auth, actions).
A file without ``actions:`` is not a plugin.

Subscription is per-project (or per-persona): `<project>/.xlii/plugins.txt`
lists active plugin IDs, one per line. The agent only sees subscribed
plugins via `plugin_search` and `plugin_get` — keeps the active set
bounded as the catalog grows.

This module ships the plugin catalog, subscription file, and lint. Invocation
is ``plugin_call`` (HTTP + form + vault). A file without ``actions:`` is not a plugin.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any, Optional

from xlii.config import GLOBAL_CONFIG_DIR
from xlii.frontmatter import parse_frontmatter  # noqa: F401  (re-exported for callers)

PLUGINS_DIR = GLOBAL_CONFIG_DIR / "plugins"

# Per-project / per-persona subscription file lives at:
#   <project_root>/.xlii/plugins.txt
# One plugin id per line; comment lines start with #.
SUBSCRIPTION_FILENAME = "plugins.txt"

# Validation — plugin IDs go on disk as filenames; keep them tame.
_VALID_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def is_valid_id(name: str) -> bool:
    return bool(_VALID_ID.match(name))


# Default risk levels — see proposal for semantics.
RISK_LOW = "low"        # read-only public APIs
RISK_MEDIUM = "medium"  # credentialed reads, rate-limited writes
RISK_HIGH = "high"      # public-facing writes (post tweet, send DM, etc.)
VALID_RISKS = {RISK_LOW, RISK_MEDIUM, RISK_HIGH}


# Starter template — opens in $EDITOR when the user runs `xlii plugin --new`.
# The example is a real-ish weather API so the user sees the expected shape.
DEFAULT_PLUGIN_TEMPLATE = """---
id: <fill-in>
name: <Display Name>
description: <one-line summary of what this API does>
categories: [misc]
effect: read-only
trust: subscription
auth_type: none
auth_env_vars: []
actions:
  - id: ping
    description: Stub GET — replace url and params
    method: GET
    url: https://example.com/
    params: {}
    output: interpret
---

# <Display Name>

<Prose description: what this API does.>

## Auth

Secrets go through a form action (``secret: true`` + ``store: true``).
Never put keys in this file. Never ask the agent to paste a password.

## Usage

```
/plugin subscribe <id>
/plugin call <id>.ping
```
"""


# --------------------------------------------------------------------------- #
#  Plugin
# --------------------------------------------------------------------------- #

@dataclass
class Plugin:
    """One plugin loaded from disk. Lazy: parses frontmatter on demand."""
    id: str

    @property
    def path(self) -> Path:
        return PLUGINS_DIR / f"{self.id}.md"

    def exists(self) -> bool:
        return self.path.exists()

    def read_raw(self) -> str:
        """Runtime text. Stock ids always come from the package — an old
        copy in ``PLUGINS_DIR`` is not a second source of truth."""
        stock = stock_markdown(self.id)
        if stock:
            return stock
        return self._raw

    @cached_property
    def _raw(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8")
        except (FileNotFoundError, PermissionError, OSError):
            return ""
        except Exception:
            # Unexpected encoding or other I/O error — degrade gracefully
            return ""

    @cached_property
    def parsed(self) -> tuple[dict, str]:
        return parse_frontmatter(self.read_raw())

    def metadata(self) -> dict:
        """Parse frontmatter metadata. Uses YAML for plugins with actions:
        blocks (the simple parser can't handle nested dicts), falls back
        to the lightweight parser otherwise."""
        raw = self.read_raw()
        lines = raw.splitlines()
        if not lines or lines[0].strip() != "---":
            return {}
        end = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end = i
                break
        if end is None:
            return {}
        fm_text = "\n".join(lines[1:end])
        # If there's an actions: block, use YAML for correct nested parsing.
        if "\nactions:" in fm_text or fm_text.startswith("actions:"):
            try:
                import yaml
                meta = yaml.safe_load(fm_text)
                return meta if isinstance(meta, dict) else {}
            except Exception:
                # YAML parse failure — fall back to lightweight frontmatter parser
                pass
        return self.parsed[0]

    def manifest(self):
        """Structured action manifest, or None if this file is not a plugin."""
        from xlii.plugin_manifest import parse_manifest
        return parse_manifest(self.read_raw())

    def body(self) -> str:
        return self.parsed[1]

    def name(self) -> str:
        return self.metadata().get("name") or self.id

    def description(self) -> str:
        return self.metadata().get("description") or ""

    def categories(self) -> list[str]:
        v = self.metadata().get("categories") or []
        return v if isinstance(v, list) else [str(v)]

    def risk(self) -> str:
        """Derived display label from effect/trust. Not a frontmatter field."""
        from xlii.plugin_manifest import (
            EFFECT_DESTRUCTIVE,
            EFFECT_EXTERNAL_WRITE,
            EFFECT_LOCAL_SYSTEM,
            TRUST_ALWAYS_CONFIRM,
        )

        effect, trust = self.effect_trust()
        if effect in (EFFECT_LOCAL_SYSTEM, EFFECT_DESTRUCTIVE) or trust == TRUST_ALWAYS_CONFIRM:
            return RISK_HIGH
        if effect == EFFECT_EXTERNAL_WRITE:
            return RISK_MEDIUM
        return RISK_LOW

    def effect_trust(self) -> tuple[str, str]:
        """Resolved (effect, trust) badges. No manifest → fail closed."""
        from xlii.plugin_manifest import (
            EFFECT_DESTRUCTIVE,
            TRUST_ALWAYS_CONFIRM,
        )

        m = self.manifest()
        if m is not None:
            return m.effect, m.trust
        return EFFECT_DESTRUCTIVE, TRUST_ALWAYS_CONFIRM

    def is_high_risk(self) -> bool:
        """Does auto-attaching/subscribing this plugin require elevation? Mirrors
        role.plugin_needs_elevation but keyed on the loaded Plugin (no re-read)."""
        from xlii.plugin_manifest import effect_trust_is_high_risk

        return effect_trust_is_high_risk(*self.effect_trust())

    def auth_env_vars(self) -> list[str]:
        v = self.metadata().get("auth_env_vars") or []
        return v if isinstance(v, list) else [str(v)]

    def size_bytes(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0


def list_plugins() -> list[Plugin]:
    """All installed plugins (the global catalog), sorted by id."""
    if not PLUGINS_DIR.exists():
        return []
    return [Plugin(id=p.stem) for p in sorted(PLUGINS_DIR.glob("*.md"))]


def create_plugin(plugin_id: str, *, content: Optional[str] = None) -> Plugin:
    if not is_valid_id(plugin_id):
        raise ValueError(
            f"invalid plugin id: {plugin_id!r}. Use letters, digits, _ . - only "
            "(start with letter/digit; max 64 chars)."
        )
    p = Plugin(id=plugin_id)
    if p.exists():
        raise FileExistsError(f"plugin {plugin_id!r} already exists at {p.path}")
    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    text = content if content is not None else DEFAULT_PLUGIN_TEMPLATE.replace(
        "<fill-in>", plugin_id
    )
    p.path.write_text(text)
    return p


def delete_plugin(plugin_id: str) -> bool:
    p = Plugin(id=plugin_id)
    if not p.exists():
        return False
    p.path.unlink()
    return True


def open_in_editor(path: Path) -> int:
    from xlii.editor import open_for_edit

    return open_for_edit(path)


# --------------------------------------------------------------------------- #
#  Stock plugin pack — opt-in seed catalog shipped with the package
# --------------------------------------------------------------------------- #

_STOCK_TEXT: dict[str, str] | None = None


def stock_markdown(plugin_id: str) -> str | None:
    """Bundled stock text for *plugin_id*, or None if it is not a stock plugin."""
    global _STOCK_TEXT
    if _STOCK_TEXT is None:
        _STOCK_TEXT = {pid: text for pid, text in list_stock_plugins()}
    return _STOCK_TEXT.get(plugin_id)


def list_stock_plugins() -> list[tuple[str, str]]:
    """Return [(plugin_id, markdown_content)] for every stock plugin shipped
    in xli/stock_plugins/. Sorted by id for deterministic install order.

    Read via importlib.resources so this works the same in editable installs
    and in wheel installs (the .md files are declared as package data in
    pyproject.toml).
    """
    from importlib.resources import files
    out: list[tuple[str, str]] = []
    pkg = files("xlii.stock_plugins")
    for entry in sorted(pkg.iterdir(), key=lambda p: p.name):
        if entry.name.endswith(".md"):
            out.append((entry.name[:-3], entry.read_text()))
    return out


def install_stock_plugins(*, force: bool = False) -> tuple[list[str], list[str]]:
    """Copy stock plugins into PLUGINS_DIR. Returns (installed, skipped).

    `force=False` (default) preserves any plugin the user has already edited
    — only missing plugin ids get written. `force=True` overwrites unconditionally
    (intended for upgrading the seed pack after the user has installed an old version).
    """
    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    installed: list[str] = []
    skipped: list[str] = []
    for pid, content in list_stock_plugins():
        dest = PLUGINS_DIR / f"{pid}.md"
        if dest.exists() and not force:
            skipped.append(pid)
            continue
        dest.write_text(content)
        installed.append(pid)
    return (installed, skipped)


# --------------------------------------------------------------------------- #
#  Subscription file (per-project)
# --------------------------------------------------------------------------- #

def load_subscriptions(xli_dir: Path) -> list[str]:
    """Read subscribed plugin IDs from `<xli_dir>/plugins.txt`.
    One id per line; '#' starts a comment. Whitespace stripped.
    Returns [] if the file doesn't exist.
    """
    f = xli_dir / SUBSCRIPTION_FILENAME
    if not f.exists():
        return []
    out: list[str] = []
    seen: set[str] = set()
    try:
        for raw in f.read_text().splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or line in seen:
                continue
            if not is_valid_id(line):
                continue
            seen.add(line)
            out.append(line)
    except OSError:
        return []
    return out


def load_subscriptions_for(*xli_dirs: "Path | str | None") -> list[str]:
    """Union of ``plugins.txt`` files, first-seen order. Skips None / missing."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in xli_dirs:
        if raw is None:
            continue
        for pid in load_subscriptions(Path(raw)):
            if pid not in seen:
                seen.add(pid)
                out.append(pid)
    return out


def live_subscriptions(project: Any, session: Any = None) -> list[str]:
    """What the agent may actually call this turn.

    The pane writes the **desk** folder's ``plugins.txt``. Talk / ``/mojo``
    run on the persona's own project (memory island) — without this union
    the dots lie. Extra dirs ride ``session.plugin_xli_dirs``.
    """
    dirs: list[Any] = [getattr(project, "xli_dir", None)]
    extra = getattr(session, "plugin_xli_dirs", None) or ()
    dirs.extend(extra)
    return load_subscriptions_for(*dirs)


def save_subscriptions(xli_dir: Path, plugin_ids: list[str]) -> Path:
    """Persist subscribed plugin IDs. Returns the file path."""
    xli_dir.mkdir(parents=True, exist_ok=True)
    f = xli_dir / SUBSCRIPTION_FILENAME
    header = (
        "# xlii plugin subscriptions for this project — one plugin id per line.\n"
        "# Manage with `/plugin subscribe <id>` / `/plugin unsubscribe <id>` from any REPL.\n\n"
    )
    body = "\n".join(plugin_ids) + ("\n" if plugin_ids else "")
    f.write_text(header + body)
    return f


def add_subscription(xli_dir: Path, plugin_id: str) -> bool:
    """Add `plugin_id` to subscriptions if not already there. Returns True
    if added, False if already subscribed."""
    current = load_subscriptions(xli_dir)
    if plugin_id in current:
        return False
    current.append(plugin_id)
    save_subscriptions(xli_dir, current)
    return True


def remove_subscription(xli_dir: Path, plugin_id: str) -> bool:
    """Remove `plugin_id` from subscriptions. Returns True if removed,
    False if it wasn't subscribed."""
    current = load_subscriptions(xli_dir)
    if plugin_id not in current:
        return False
    current = [p for p in current if p != plugin_id]
    save_subscriptions(xli_dir, current)
    return True


def subscribed_plugins(xli_dir: Path) -> list[Plugin]:
    """Resolve subscribed plugin IDs to Plugin objects; silently skip ones
    that no longer exist on disk (orphan subscription)."""
    out = []
    for pid in load_subscriptions(xli_dir):
        p = Plugin(id=pid)
        if p.exists():
            out.append(p)
    return out


def can_subscribe(state: Any, p: Plugin) -> tuple[bool, Optional[str]]:
    """Whether a UI may *subscribe* plugin ``p`` in this session.

    High-risk plugins (local-system / destructive / always-confirm) need an
    elevated session (``/admin unlock``) — click/menu surfaces must not be the
    easiest way past the same gate ``role.gate_loadout_plugins`` enforces for
    auto-attach. Returns ``(allowed, refusal_reason)``; unsubscribe is always
    allowed and skips this.
    """
    if p.is_high_risk() and not getattr(state, "elevated", False):
        effect, trust = p.effect_trust()
        return False, f"{p.id} is high-risk ({effect} · {trust}) — /admin unlock to subscribe"
    return True, None


def toggle_subscription(state: Any, pid: str) -> tuple[str, str]:
    """Toggle ``pid``'s project subscription, honouring the high-risk gate on the
    subscribe direction. Returns ``(outcome, message)`` where outcome is one of
    ``subscribed`` / ``unsubscribed`` / ``gated`` / ``no-project`` — so a caller
    (face, TUI panel, or test) can react without re-deriving the decision.
    """
    project = getattr(state, "project", None)
    xli = getattr(project, "xli_dir", None) if project is not None else None
    if xli is None:
        return "no-project", "no project to subscribe into"
    if pid in set(load_subscriptions(xli)):
        remove_subscription(xli, pid)
        return "unsubscribed", f"unsubscribed {pid}"
    p = Plugin(id=pid)
    allowed, reason = can_subscribe(state, p)
    if not allowed:
        return "gated", reason or f"{pid} is gated"
    add_subscription(xli, pid)
    return "subscribed", f"subscribed {pid}"


# --------------------------------------------------------------------------- #
#  Lint (validation policy over the stock pack + installed catalog)
# --------------------------------------------------------------------------- #

@dataclass
class LintFinding:
    """One plugin's lint result; ``problems`` empty means the plugin passes."""
    plugin_id: str
    origin: str            # "stock" or "installed"
    problems: list[str]


def lint_plugins() -> list[LintFinding]:
    """Validate every installed plugin + the bundled stock pack.

    Checks: frontmatter parses, id matches filename, effect/trust valid,
    description present, actions parse to at least one action. Pure policy —
    returns typed findings and prints nothing; `xlii plugin --lint` renders
    them and exits 1 on any failure (wired into CI over the stock pack).
    """
    from xlii.plugin_manifest import parse_manifest

    findings: list[LintFinding] = []

    def check(pid: str, raw: str, origin: str) -> None:
        problems: list[str] = []
        meta: dict = {}
        lines = raw.splitlines()
        if lines and lines[0].strip() == "---":
            try:
                import yaml
                end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
                meta = yaml.safe_load("\n".join(lines[1:end])) or {}
            except Exception as e:
                problems.append(f"frontmatter does not parse: {e}")
        else:
            problems.append("missing frontmatter (--- block)")

        if meta:
            from xlii.plugin_manifest import VALID_EFFECTS, VALID_TRUSTS

            declared = meta.get("id")
            if declared != pid:
                problems.append(f"id {declared!r} != filename {pid!r}")
            if not meta.get("description"):
                problems.append("missing description")
            effect = meta.get("effect")
            trust = meta.get("trust")
            if effect not in VALID_EFFECTS:
                problems.append(f"invalid effect: {effect!r}")
            if trust not in VALID_TRUSTS:
                problems.append(f"invalid trust: {trust!r}")
            m = parse_manifest(raw)
            if m is None or not m.actions:
                problems.append("missing actions (not a plugin)")

        findings.append(LintFinding(plugin_id=pid, origin=origin, problems=problems))

    for pid, content in list_stock_plugins():
        check(pid, content, "stock")
    for p in list_plugins():
        try:
            check(p.id, p.read_raw(), "installed")
        except OSError as e:
            findings.append(LintFinding(
                plugin_id=p.id, origin="installed", problems=[f"unreadable: {e}"]))

    return findings


# --------------------------------------------------------------------------- #
#  Search (the core of /get and plugin_search)
# --------------------------------------------------------------------------- #

# Marker the agent sees in `plugin_search` results when nothing matches.
# Hard-coded so the model can be system-prompted to recognize and report it
# rather than fabricate plugin output.
NO_PLUGIN_MATCH_MARKER = "NO_PLUGIN_MATCH"


_STOPWORDS = {
    "a", "an", "and", "or", "but", "the", "to", "of", "in", "on", "at",
    "for", "from", "with", "by", "is", "are", "was", "were", "be", "been",
    "i", "me", "my", "you", "your", "we", "our", "this", "that", "these",
    "those", "it", "its", "do", "does", "did", "have", "has", "had",
    "get", "find", "send", "post", "show", "tell", "give", "make",
    "what", "where", "when", "who", "how", "why",
}


def _mash(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _edit_distance(a: str, b: str, cap: int = 2) -> int:
    """Levenshtein, aborted past *cap*. Returns cap+1 when over."""
    if a == b:
        return 0
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        row_min = i
        for j, cb in enumerate(b, 1):
            ins = cur[j - 1] + 1
            delete = prev[j] + 1
            sub = prev[j - 1] + (0 if ca == cb else 1)
            v = min(ins, delete, sub)
            cur.append(v)
            if v < row_min:
                row_min = v
        if row_min > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def _typo_hit(tok: str, target: str) -> bool:
    """True when *tok* is a near-miss of a compact identifier (*target*).

    Short words stay exact-only — 'get' must not match 'gdelt'. One edit is
    enough for 5–7 letters; two edits for longer ids (hackernews / haclernews).
    """
    n = _mash(tok)
    t = _mash(target)
    if not n or not t or len(n) < 5 or len(t) < 5:
        return False
    cap = 1 if min(len(n), len(t)) < 8 else 2
    return _edit_distance(n, t, cap) <= cap


def search_plugins(intent: str, plugins: list[Plugin], limit: int = 5) -> list[tuple[Plugin, float]]:
    """Score subscribed plugins against an intent string and return top N.

    L1 implementation: lowercase keyword overlap weighted by category and
    description matches. Crude but works for tens of plugins. At scale (200+)
    swap for a real RAG over a per-user catalog Collection — flagged in the
    proposal under "Scale considerations."

    Tokens of length < 3 and common stopwords are dropped — otherwise "a"
    and "the" cause spurious matches against any description containing them.

    A token that is a 1–2 character typo of a plugin id or name still counts
    as an id hit (``/get haclernews`` → subscribed ``hackernews``).
    """
    if not plugins or not intent.strip():
        return []
    tokens = [
        t.lower() for t in re.findall(r"[a-zA-Z0-9]+", intent)
        if len(t) >= 3 and t.lower() not in _STOPWORDS
    ]
    if not tokens:
        return []

    scored: list[tuple[Plugin, float]] = []
    for p in plugins:
        try:
            meta = p.metadata()
        except OSError:
            continue
        haystack_parts = [
            p.id.lower(),
            (meta.get("name") or "").lower(),
            (meta.get("description") or "").lower(),
            " ".join(meta.get("categories") or []).lower(),
        ]
        # Include action ids, descriptions, AND param names + descriptions
        # in the haystack. Param descriptions often contain example values
        # (e.g. "bitcoin, ethereum" in coingecko price.ids) that the
        # plugin-level description doesn't mention.
        action_text = ""
        action_ids: list[str] = []
        manifest = p.manifest()
        if manifest:
            parts = []
            for a in manifest.actions:
                parts.append(a.id)
                action_ids.append(a.id)
                parts.append(a.description)
                if a.response_shape:
                    parts.append(a.response_shape)
                for pname, pspec in a.params.items():
                    parts.append(pname)
                    if pspec.description:
                        parts.append(pspec.description)
            action_text = " ".join(parts).lower()
        haystack = " ".join(haystack_parts) + " " + action_text
        if not haystack.strip():
            continue
        # Score: count of tokens that appear, weighted by where they hit.
        score = 0.0
        for tok in tokens:
            if tok in haystack_parts[0]:  # id match
                score += 3.0
            elif tok in haystack_parts[1]:  # name match
                score += 2.5
            elif any(_typo_hit(tok, c) for c in (
                haystack_parts[0], haystack_parts[1], *action_ids,
            )):
                score += 2.75  # near-miss on id / name / action
            elif tok in haystack_parts[3]:  # category match
                score += 2.0
            elif tok in haystack_parts[2]:  # description match
                score += 1.0
            elif tok in action_text:       # action-level match
                score += 1.5
        if score > 0:
            scored.append((p, score))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:limit]
