"""Typed workbenches (proposals/typed-workbenches.md, phase B0) — the registry as data.

A workbench type is a *declarative bundle* projected over the ONE existing
session: pane set + default face posture + persona + ambient bundle + input
affordances. Types are views over the substrate, never new backends. This
module holds the v1 type catalog as plain data — no behavior subclasses, no
per-type Python modules; adding a type is adding a registry row (here, or in
the per-project override).

Per-project files (under ``.xlii/``):

- ``workbench.toml`` — optional human-authored registry override (the
  ``aliases.toml`` precedent: declarative TOML, read via stdlib tomllib).
  Rows merge over the builtin catalog by name — a row naming a builtin
  replaces only the columns it sets; a new name adds a type::

      [[type]]
      name = "chat"
      ambient = "custom tip"

      [[type]]
      name = "notes"
      panes = ["wiki", "tasks"]
      default_posture = "chat"
      ambient = "wiki + project notes"

- ``workbench.json`` — machine-managed active type (``{"active": "<name>"}``,
  the ``loop-active.json`` precedent), written by ``/workbench <type>``.

B0 scope: the registry is resolved at session boot and switched via
``/workbench`` — persist + report ONLY. Posture flips, persona rebinds, and
ambient changes are B2; panes on the face are B1.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

POSTURES = ("chat", "code")
"""The two face postures that already exist: ``[M]`` mojo and ``[$]`` code."""

DEFAULT_TYPE = "chat"
"""A project with no recorded type is on today's face — chat, unchanged."""

OVERRIDE_FILE = "workbench.toml"
ACTIVE_FILE = "workbench.json"

PLANNED_PANES = frozenset()
"""Pane names a row references before the pane exists. Empty since F3 —
``sources`` shipped (``sources://`` + SourcesPane). Keep the mechanism: a v1
row may name a planned pane, and the deck skips it until it lands; a type
whose pane set is mostly placeholders is a label, not a type."""


# Quick-launch button ids (three-faces.md Q0/Q1). Dumb doors only — not a mode.
# Face + future TUI F-key packs resolve labels via QUICK_LAUNCH_META.
QUICK_LAUNCH_META: dict[str, tuple[str, str]] = {
    # id → (label, action kind hint for clients)
    "plugins": ("plugins", "pane:plugins"),
    "bookmarks": ("bookmarks", "pane:bookmarks"),
    "ref": ("ref", "seed:/ref "),
    "attach": ("attach", "attach:files"),
    "attachments": ("files", "seed:/attachments"),
    "plan": ("plan", "pane:plan"),
    "git": ("git", "pane:git"),
    "jobs": ("jobs", "pane:jobs"),
    "farm": ("farm", "pane:farm"),
    "tasks": ("tasks", "pane:tasks"),
    "explorer": ("files", "pane:explorer"),
    "wiki": ("wiki", "pane:wiki"),
    "sources": ("kept sources", "pane:sources"),
    "results": ("lookups", "pane:results"),
    "artifacts": ("artifacts", "pane:artifacts"),
    "locker": ("attachments", "pane:locker"),
    "menu": ("cmds", "pane:menu"),
    "projects": ("projects", "pane:projects"),
    # Home desk: switch to a registered project (opens projects:// list).
    "switch": ("switch", "pane:projects"),
    # Legacy id kept so old packs / overrides still resolve.
    "join": ("switch", "pane:projects"),
    # Panel door (slash verb is /skill singular; pane + scheme are skills).
    "skills": ("skills", "pane:skills"),
    # Chat power-tool doors (fetch · keep · show — not a fourth pack).
    "browser": ("browser", "browser:open"),
    # v1: open real panes + teach; real graph/whiteboard come later.
    "kg": ("kg", "research:kg"),
    "canvas": ("canvas", "research:canvas"),
}

# Old active-type names map onto the three product packs.
LEGACY_TYPE_ALIASES: dict[str, str] = {
    "research": "chat",   # research doors folded into chat
    "general": "home",    # home-ish desk → home pack
}


@dataclass(frozen=True)
class WorkbenchType:
    """One row of the workbench registry — pure data, no behavior.

    Product law (three-faces.md): the successful job of a type is the
    ``quick_launch`` strip pack (+ optional pane *offers*). It is not a face
    and must not read as mode/posture identity.
    """

    name: str
    panes: tuple[str, ...] = ()
    default_posture: str = "chat"  # legacy B2; demoted — do not treat as mode
    persona: Optional[str] = None  # legacy B2 bind; demoted
    ambient: str = ""
    affordances: tuple[str, ...] = ()
    # Ordered quick-strip button ids (see QUICK_LAUNCH_META).
    quick_launch: tuple[str, ...] = ()


def pack_pane_ids(wb: Optional["WorkbenchType"] = None) -> tuple[str, ...]:
    """Pane ids this pack offers: declared panes + quick-launch ``pane:`` doors.

    The face slot dropdown and Keep menu are this list (plus stream), not
    the whole ``FACE_PANE_LABELS`` map. Non-pane doors (attach, browser, kg)
    stay off it. Keep then hides lab doors already on Project/Tools.
    """
    if wb is None:
        return ()
    seen: list[str] = []
    for pid in wb.panes or ():
        if pid and pid not in seen:
            seen.append(pid)
    for qid in wb.quick_launch or ():
        meta = QUICK_LAUNCH_META.get(qid)
        if not meta:
            continue
        action = meta[1]
        if not action.startswith("pane:"):
            continue
        pid = action.split(":", 1)[1].strip()
        if pid and pid not in seen:
            seen.append(pid)
    return tuple(seen)


def quick_launch_buttons(wb: Optional["WorkbenchType"] = None) -> list[dict[str, str]]:
    """Wire-ready ``[{id, label, action}, …]`` for the active pack."""
    ids = (wb.quick_launch if wb is not None else ()) or ()
    if not ids and wb is not None:
        # Fallback: first few panes as doors if pack unset.
        ids = tuple(p for p in (wb.panes or ()) if p in QUICK_LAUNCH_META)[:6]
    out: list[dict[str, str]] = []
    for qid in ids:
        meta = QUICK_LAUNCH_META.get(qid)
        if not meta:
            continue
        label, action = meta
        out.append({"id": qid, "label": label, "action": action})
    return out


def fkey_pack_rows(wb: Optional["WorkbenchType"] = None) -> list[tuple[str, str, str]]:
    """Pack doors as a labeled list (not the live F-key bar).

    The F-row is the commander verbs. This helper is the workbench *place*
    list if a tighter UI wants it. F1 is always ``help``; F2… follow
    :func:`quick_launch_buttons`. When *wb* is None, use the default pack.
    """
    if wb is None:
        wb = BUILTIN_WORKBENCHES.get(DEFAULT_TYPE)
    rows: list[tuple[str, str, str]] = [("F1", "help", "cmd:help")]
    for i, b in enumerate(quick_launch_buttons(wb)[:9]):
        label = (b.get("label") or b.get("id") or "")[:12]
        rows.append((f"F{i + 2}", label, b.get("action") or ""))
    return rows


BUILTIN_WORKBENCHES: dict[str, WorkbenchType] = {
    t.name: t
    for t in (
        # Product law: three packs only — home | chat | code.
        # chat — companion + research power doors (fetch · keep · show), not a mode
        WorkbenchType(
            "chat",
            panes=(
                "menu", "locker", "bookmarks", "sources", "wiki",
                "artifacts", "canvas", "results",
            ),
            default_posture="chat",
            persona=None,
            ambient=(
                "journal/wiki · plugins · kg · canvas · browser · "
                "locker/results — research on chat"
            ),
            affordances=("send-to-wiki", "save-with-note"),
            quick_launch=(
                "plugins",
                "kg",
                "canvas",
                "browser",
                "locker",
                "bookmarks",
                "ref",
                "sources",
                "results",
                "artifacts",
                "wiki",
                "attach",
                "attachments",
                "menu",
            ),
        ),
        WorkbenchType(
            "code",
            panes=("explorer", "git", "tasks", "plan", "jobs", "farm", "skills", "menu"),
            default_posture="code",
            persona=None,
            ambient="repo map + open tasks",
            affordances=("uploads", "plan gate"),
            quick_launch=("plan", "git", "jobs", "tasks", "explorer", "attach", "skills"),
        ),
        # home — 7am desk / scratch surface: switch into a real project first
        WorkbenchType(
            "home",
            panes=("projects", "home", "locker", "menu"),
            default_posture="chat",
            persona=None,
            ambient="Home — switch to a project · plugins · light keep",
            affordances=(),
            quick_launch=(
                "switch", "projects", "plugins", "locker", "attach", "menu",
            ),
        ),
    )
}


def _pane_module_names() -> set[str]:
    """The headless pane types that exist today — the modules under xlii/panes/."""
    panes_dir = Path(__file__).parent / "panes"
    return {p.stem for p in panes_dir.glob("*.py") if not p.stem.startswith("_")}


def validate_registry(registry: Optional[dict[str, WorkbenchType]] = None) -> list[str]:
    """Integrity problems in a registry (empty = sound), as readable strings.

    Checks the required columns: a non-empty name, a posture that exists, and
    pane names plausible against ``xlii/panes/`` (an existing pane module or a
    named :data:`PLANNED_PANES` placeholder)."""
    reg = BUILTIN_WORKBENCHES if registry is None else registry
    known_panes = _pane_module_names() | PLANNED_PANES
    problems: list[str] = []
    for key, t in reg.items():
        if not t.name or t.name != key:
            problems.append(f"{key!r}: row name {t.name!r} must equal its registry key")
        if t.default_posture not in POSTURES:
            problems.append(
                f"{t.name!r}: default_posture {t.default_posture!r} "
                f"not one of {list(POSTURES)}"
            )
        for pane in t.panes:
            if pane not in known_panes:
                problems.append(f"{t.name!r}: unknown pane {pane!r} (no xlii/panes module)")
    return problems


def _merge_row(base: Optional[WorkbenchType], raw: dict) -> Optional[WorkbenchType]:
    """One override TOML row merged over a builtin (or a fresh row). None when
    the row is malformed — a bad override must never brick session boot."""
    name = str(raw.get("name", "") or "").strip()
    if not name:
        return None
    posture = raw.get("default_posture")
    if posture is not None and posture not in POSTURES:
        return None

    def _str_tuple(key: str) -> Optional[tuple[str, ...]]:
        val = raw.get(key)
        if val is None:
            return None
        if not isinstance(val, list) or not all(isinstance(v, str) for v in val):
            raise ValueError(key)
        return tuple(val)

    try:
        panes = _str_tuple("panes")
        affordances = _str_tuple("affordances")
        quick_launch = _str_tuple("quick_launch")
    except ValueError:
        return None
    ambient = raw.get("ambient")
    if ambient is not None and not isinstance(ambient, str):
        return None
    persona = raw.get("persona")
    if persona is not None and not isinstance(persona, str):
        return None

    row = base if base is not None else WorkbenchType(name)
    return replace(
        row,
        **{
            k: v
            for k, v in (
                ("panes", panes),
                ("default_posture", posture),
                ("persona", persona),
                ("ambient", ambient),
                ("affordances", affordances),
                ("quick_launch", quick_launch),
            )
            if v is not None
        },
    )


def load_registry(xli_dir: Optional[Path] = None) -> dict[str, WorkbenchType]:
    """The builtin catalog merged with the project override
    (``.xlii/workbench.toml``), in catalog order (override-added types last).

    Malformed rows are skipped — the override is user-authored data, and a
    typo in it must degrade that row, never the session."""
    registry = dict(BUILTIN_WORKBENCHES)
    if xli_dir is None:
        return registry
    path = Path(xli_dir) / OVERRIDE_FILE
    if not path.is_file():
        return registry
    try:
        data = tomllib.loads(path.read_text())
    except (tomllib.TOMLDecodeError, OSError):
        return registry
    raw = data.get("type")
    if not isinstance(raw, list):
        return registry
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "") or "").strip()
        row = _merge_row(registry.get(name), entry)
        if row is not None:
            registry[row.name] = row
    return registry


def get_workbench(name: str, xli_dir: Optional[Path] = None) -> Optional[WorkbenchType]:
    """Look up one type by name (case-insensitive). None when unknown."""
    return load_registry(xli_dir).get(name.strip().lower())


def list_workbenches(xli_dir: Optional[Path] = None) -> list[WorkbenchType]:
    """Every type the project can switch to, in catalog order."""
    return list(load_registry(xli_dir).values())


def load_active_type(
    xli_dir: Optional[Path], registry: Optional[dict[str, WorkbenchType]] = None
) -> str:
    """The project's active workbench type (``.xlii/workbench.json``).

    :data:`DEFAULT_TYPE` when unset, unreadable, or naming a type the registry
    no longer has — a stale record degrades to today's face, never an error."""
    if xli_dir is None:
        return DEFAULT_TYPE
    try:
        data = json.loads((Path(xli_dir) / ACTIVE_FILE).read_text())
        name = str(data.get("active", "") or "").strip().lower()
    except (json.JSONDecodeError, OSError, AttributeError):
        return DEFAULT_TYPE
    name = LEGACY_TYPE_ALIASES.get(name, name)
    reg = load_registry(xli_dir) if registry is None else registry
    return name if name in reg else DEFAULT_TYPE


def save_active_type(xli_dir: Path, name: str) -> None:
    """Persist the active type (the machine-managed half of the pair)."""
    from xlii.atomicio import write_text_atomic

    write_text_atomic(
        Path(xli_dir) / ACTIVE_FILE, json.dumps({"active": name}, indent=2) + "\n"
    )


def resolve_active(xli_dir: Optional[Path]) -> WorkbenchType:
    """The active type as a registry row — what session boot hangs on the session."""
    registry = load_registry(xli_dir)
    return registry[load_active_type(xli_dir, registry)]
