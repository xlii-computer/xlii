"""Role descriptors — named specialist bundles over the persona loadout
(proposals/roles.md).

R1 is the **catalog**: a role is a persona-shaped markdown file (frontmatter
loadout + identity body) living under a dedicated `roles/` dir. This module finds,
parses, and summarizes them. *Activation* (become-in-chat / equip-in-code) is R2 —
nothing here mutates session state.

Search path (a project role overrides a global one of the same name, mirroring
skills and personas):

    <project>/.xlii/roles/<name>.md      project-local
    <config>/roles/<name>.md             global  (XLII_CONFIG_DIR-aware)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from xlii.config import global_config_dir
from xlii.frontmatter import parse_frontmatter

# Loadout keys a role descriptor may carry. R1 displays them; R2 materializes them
# through the existing Loadout / _apply_persona_loadout path. `skills` is the one
# key R0 added; the rest already existed for personas. `hint` is display-only
# (the input-hint registry reads it via `Role.hint()`); it is not materialized.
ROLE_LOADOUT_KEYS = ("skills", "docs", "plugins", "model", "temperature", "profile", "hint")

# Code-equip attaches a role's IDENTITY body as a doc under this prefix, so the
# agent adopts the role's stance (not just its loadout). Detached on `/role off`.
# Mirrors SKILL_ATTACH_PREFIX; chat-become uses the identity as the system prompt
# and never attaches this doc.
ROLE_ATTACH_PREFIX = "role:"


def stock_roles_dir() -> Path:
    """Starter roles shipped with the package (R3) — lowest precedence; a global
    or project role of the same name overrides one of these."""
    return Path(__file__).resolve().parent / "stock_roles"


def global_roles_dir() -> Path:
    return global_config_dir() / "roles"


def project_roles_dir(project_root: Path) -> Path:
    return Path(project_root) / ".xlii" / "roles"


@dataclass
class Role:
    """A role descriptor on disk. Holds no session state — pure catalog object."""

    name: str
    path: Path
    scope: str = "global"  # "global" | "project"

    def read(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def loadout(self) -> dict:
        """Parsed frontmatter loadout (skills/docs/plugins/model/…)."""
        meta, _ = parse_frontmatter(self.read())
        return meta

    def identity(self) -> str:
        """The instruction body the model would adopt (frontmatter stripped)."""
        _, body = parse_frontmatter(self.read())
        return body.strip()

    def description(self) -> str:
        """A one-line summary: the `description:` loadout key, else the first
        non-heading line of the identity body."""
        desc = str(self.loadout().get("description") or "").strip()
        if desc:
            return desc
        for line in self.identity().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line[:100]
        return "(no description)"

    def hint(self) -> str:
        """The role's input-hint override (seam #3): the `hint:` loadout key, or
        '' when the descriptor declares none. The hint registry (`xlii.hints`)
        shows this in the input placeholder while the role is equipped in code,
        so a role can teach its own one-line affordance for what it does."""
        return str(self.loadout().get("hint") or "").strip()

    def validate(self) -> Optional[str]:
        """Return a human error string if the descriptor is malformed, else None.

        A role needs *something* — a loadout or an identity body. A file that opens
        a `---` frontmatter block but never closes it (or has no `key: value`
        pairs) parses to an empty loadout; that's the common authoring mistake, so
        it's reported clearly rather than silently listed as an empty role."""
        try:
            text = self.read()
        except OSError as e:
            return f"{self.path.name}: cannot read ({e})"
        meta, body = parse_frontmatter(text)
        if text.lstrip().startswith("---") and not meta:
            return (f"{self.path.name}: malformed frontmatter "
                    "(unterminated '---' or no 'key: value' pairs)")
        if not meta and not body.strip():
            return f"{self.path.name}: empty descriptor (no loadout and no identity)"
        return None


def _scan(d: Path, scope: str, into: dict) -> None:
    if not d.is_dir():
        return
    for p in sorted(d.glob("*.md")):
        if p.is_file():
            into[p.stem] = Role(name=p.stem, path=p, scope=scope)


def load_roles(project_root: Optional[Path] = None) -> dict:
    """Every role by name. Precedence (lowest→highest): stock (bundled) < global
    < project — a more specific scope overrides a shipped starter of the same name."""
    roles: dict = {}
    _scan(stock_roles_dir(), "stock", roles)
    _scan(global_roles_dir(), "global", roles)
    if project_root is not None:
        _scan(project_roles_dir(Path(project_root)), "project", roles)
    return roles


def load_role(name: str, project_root: Optional[Path] = None) -> Optional[Role]:
    return load_roles(project_root).get(name)


def format_role_summary(role: Role) -> list[str]:
    """Lines for `show <name>`: location, each present loadout key, malformed
    warning (if any), and the identity head."""
    lines = [f"role: {role.name}  [{role.scope}]  {role.path}"]
    lo = role.loadout()
    for key in ROLE_LOADOUT_KEYS:
        val = lo.get(key)
        if val in (None, "", []):
            continue
        shown = ", ".join(str(v) for v in val) if isinstance(val, list) else str(val)
        lines.append(f"  {key}: {shown}")
    err = role.validate()
    if err:
        lines.append(f"  [malformed] {err}")
    head = role.identity().splitlines()[0].strip() if role.identity() else ""
    if head:
        lines.append(f"  identity: {head[:100]}")
    return lines


# --------------------------------------------------------------------------- #
#  Plugin risk-gate — activation side (merge seam #3, Vector E)
# --------------------------------------------------------------------------- #
#
# A role may *declare* high-risk plugins (that's the point of shareable roles),
# but auto-attaching/invoking them when the role activates needs an elevated
# session (`/admin unlock`). This is the activation-side half of seam #3 — B owns
# the catalog-side `hint:` key (a different function above); these are the "two
# non-overlapping insertion points, same file" the merge contract names.
#
# Pure policy: it decides which declared plugins are safe to materialize vs. held
# back. It changes no subscriptions and touches no session state. The activation
# path consumes the split:
#
#     from xlii.role import gate_loadout_plugins
#     allowed, gated = gate_loadout_plugins(
#         loadout.get("plugins"), elevated=getattr(state, "elevated", False)
#     )
#     for pid in allowed:  # _apply_persona_loadout's existing subscribe loop
#         ...
#     for pid in gated:    # warn instead of attaching
#         console.print(f"loadout: plugin {pid!r} is high-risk — /admin unlock to attach")
#
# That one-line wiring at `_apply_persona_loadout` (xlii/cmds/sessions.py) and
# `_deactivate_role` (xlii/repl_cmds/role.py) is the integration touch-point;
# those files are outside Vector E's lane, so the gate ships here, fully tested,
# for the integrator to consume.


def plugin_needs_elevation(plugin_id: str) -> bool:
    """True if auto-attaching ``plugin_id`` requires elevation (it's high-risk).

    Uses the structured manifest's (effect, trust). Fail-closed: a plugin
    that isn't installed, or has no actions, is treated as high-risk."""
    from xlii.plugin import Plugin
    from xlii.plugin_manifest import effect_trust_is_high_risk

    p = Plugin(id=str(plugin_id))
    if not p.exists():
        return True
    manifest = p.manifest()
    if manifest is None:
        return True
    return effect_trust_is_high_risk(manifest.effect, manifest.trust)


def gate_loadout_plugins(plugin_ids, *, elevated: bool) -> tuple[list[str], list[str]]:
    """Split a role/persona's declared plugin ids into ``(allowed, gated)``.

    ``allowed`` are safe to auto-attach now; ``gated`` are high-risk and held back
    until the session is elevated. When ``elevated`` is True nothing is gated.
    Order-preserving; ``None``/empty input yields two empty lists."""
    allowed: list[str] = []
    gated: list[str] = []
    for pid in plugin_ids or []:
        pid = str(pid)
        if elevated or not plugin_needs_elevation(pid):
            allowed.append(pid)
        else:
            gated.append(pid)
    return allowed, gated
