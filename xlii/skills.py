"""Skills — user-authored procedural workflows (cursor-workflows.md A1).

A skill is ``<dir>/SKILL.md`` with YAML frontmatter (``name``, ``description``,
optional ``triggers``/``globs``) + a markdown body of steps, commands, and
constraints. Skills live per-project under ``.xlii/skills/<name>/SKILL.md`` and
globally under ``<config>/skills/<name>/SKILL.md``; a project skill overrides a
global one of the same name. The layout follows the Agent Skills open standard so
skills are portable between xlii and other agents (e.g. Cursor).

Because the format is the shared Agent Skills standard, we also auto-discover
skills installed by other agents — grok-build (``~/.grok/skills``), Claude Code
(``~/.claude/skills`` + its plugin skill dirs), and project-local ``.grok``/
``.claude`` copies — exactly the way grok-build picks up Claude's. Imported
skills sit BELOW xlii's own in precedence (``stock < imported < global <
project``) so your own skills always win a name collision, and the index tags
each import with its origin (e.g. ``[grok]``). Set ``import_foreign_skills:
false`` in config.json to turn the whole import off.

A skill carrying ``disable-model-invocation: true`` (an Agent Skills convention)
is honored: it stays attachable via ``/skill`` but is not advertised to the model
in the turn preamble.

Discovery is an INDEX (name + description only) injected into the agent preamble —
the full body is pulled on demand via ``/skill <name>``, which attaches it to the
session like ``/doc`` (dynamic context: don't pay for every skill's body upfront).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from xlii.config import global_config_dir
from xlii.frontmatter import parse_frontmatter

# Attached skills ride the /doc attachment channel under this name prefix so they
# inline into the system prompt and are distinguishable from real docs.
SKILL_ATTACH_PREFIX = "skill:"


# xlii's own scopes; anything else (grok/claude/claude-plugin) is an import and
# gets an origin tag in the index.
XLII_SCOPES = frozenset({"stock", "global", "project"})


@dataclass(frozen=True)
class Skill:
    name: str
    description: str  # full (extended) description — shown by `/skill show`
    body: str
    path: Path
    scope: str  # "stock"|"global"|"project" (xlii) or "grok"|"claude"|"claude-plugin"
    short_description: str = ""  # brief one-liner for the quickview list + preamble
    model_invocable: bool = True  # False when frontmatter sets disable-model-invocation
    model: Optional[str] = None  # optional pin when attached (/skill or role loadout)


def project_skills_dir(project_root: Path) -> Path:
    return Path(project_root) / ".xlii" / "skills"


def global_skills_dir() -> Path:
    return global_config_dir() / "skills"


def stock_skills_dir() -> Path:
    """Skills shipped with the package (R3) — lowest precedence; a global/project
    skill of the same name overrides one of these. Lets shipped starter roles
    reference a real skill (`grounded-analysis`) that resolves out of the box."""
    return Path(__file__).resolve().parent / "stock_skills"


_DEFAULT_SKILL_TEMPLATE = """\
---
name: {name}
description: {description}
---

# Skill: {name}

Steps:
1. …
"""


def create_skill(name: str, *, project_root: "Path | None" = None,
                 description: str = "", body: "str | None" = None) -> Path:
    """Author a new skill at ``<name>/SKILL.md`` — project-local (``.xlii/skills``) when a project
    root is given, else global (``<config>/skills``). Returns the SKILL.md path. Raises ``ValueError``
    on a bad name, ``FileExistsError`` if it already exists. Client #1 = F7 'New… → Skill'."""
    from xlii.doc import is_valid_name

    if not is_valid_name(name):
        raise ValueError(
            f"invalid skill name: {name!r}. Use letters, digits, _ . - only "
            "(start with letter/digit; max 64 chars)."
        )
    base = project_skills_dir(project_root) if project_root else global_skills_dir()
    md = base / name / "SKILL.md"
    if md.exists():
        raise FileExistsError(f"skill {name!r} already exists at {md}")
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(body if body is not None else _DEFAULT_SKILL_TEMPLATE.format(
        name=name, description=description or f"{name} skill"))
    return md


def delete_skill(name: str, *, project_root: "Path | None" = None) -> bool:
    """Remove a user-authored skill dir (project first when a root is given, else global). Returns
    True if removed. Stock/imported skills live elsewhere and are intentionally NOT deletable here —
    they're not in the project/global dirs, so they simply report not-found."""
    import shutil

    bases = ([project_skills_dir(project_root)] if project_root else []) + [global_skills_dir()]
    for base in bases:
        d = base / name
        if (d / "SKILL.md").exists():
            shutil.rmtree(d)
            return True
    return False


def _is_truthy(v) -> bool:
    return v is True or (isinstance(v, str) and v.strip().lower() in {"true", "yes", "1"})


def _unquote(v: str) -> str:
    """Strip one layer of surrounding quotes — our flat frontmatter parser leaves
    YAML quoting in place (e.g. ``short-description: "x"`` -> ``'"x"'``)."""
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1].strip()
    return v


def _brief(desc: str, limit: int = 100) -> str:
    """A short one-liner derived from a (possibly long) description: the first
    sentence, else a truncation. Used when a skill authors no short-description."""
    desc = " ".join(_unquote(desc).split())
    if not desc or len(desc) <= limit:
        return desc
    cut = desc.find(". ")
    if cut != -1 and cut + 1 <= limit:
        return desc[:cut + 1]
    return desc[:limit - 1].rstrip() + "…"


def _load_one(skill_md: Path, scope: str) -> Optional[Skill]:
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return None
    meta, body = parse_frontmatter(text)
    # Only a string name is meaningful; a list/number (malformed frontmatter) would
    # stringify to garbage, so fall back to the directory name instead.
    raw_name = meta.get("name")
    name = (raw_name.strip() if isinstance(raw_name, str) else "") or skill_md.parent.name
    if not name:
        return None
    description = _unquote(str(meta.get("description") or ""))
    # Brief = an authored short-description (Agent Skills `metadata.short-description`,
    # which our flat parser surfaces as a top-level key), else the first sentence.
    raw_short = meta.get("short-description") or meta.get("short_description")
    short = _unquote(str(raw_short)) if raw_short else _brief(description)
    disabled = meta.get("disable-model-invocation", meta.get("disable_model_invocation"))
    raw_model = meta.get("model")
    skill_model = raw_model.strip() if isinstance(raw_model, str) and raw_model.strip() else None
    return Skill(
        name=name,
        description=description,
        body=body.strip(),
        path=skill_md,
        scope=scope,
        short_description=short,
        model_invocable=not _is_truthy(disabled),
        model=skill_model,
    )


def _scan(dir_: Path, scope: str) -> dict[str, Skill]:
    out: dict[str, Skill] = {}
    if not dir_.is_dir():
        return out
    try:
        subs = sorted(p for p in dir_.iterdir() if p.is_dir())
    except OSError:
        return out
    for sub in subs:
        md = sub / "SKILL.md"
        if md.is_file():
            sk = _load_one(md, scope)
            if sk is not None:
                out[sk.name] = sk
    return out


def _claude_plugin_skill_dirs() -> list[Path]:
    """Every ``<plugin>/skills`` dir under installed Claude Code marketplaces.

    Layout: ``~/.claude/plugins/marketplaces/<market>/{plugins,external_plugins}/
    <plugin>/skills/<name>/SKILL.md``. ``.bak`` marketplace copies are skipped so
    we don't double-count a backed-up marketplace."""
    root = Path.home() / ".claude" / "plugins" / "marketplaces"
    out: list[Path] = []
    if not root.is_dir():
        return out
    try:
        markets = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return out
    for market in markets:
        if market.name.endswith(".bak"):
            continue
        for group in ("plugins", "external_plugins"):
            gdir = market / group
            if not gdir.is_dir():
                continue
            try:
                plugins = sorted(p for p in gdir.iterdir() if p.is_dir())
            except OSError:
                continue
            out.extend(p / "skills" for p in plugins if (p / "skills").is_dir())
    return out


def foreign_skill_dirs(project_root: Optional[Path] = None) -> list[tuple[Path, str]]:
    """Imported-skill source dirs as ``(dir, scope)`` pairs, lowest precedence
    first. Later entries override earlier on a name collision, so a user's
    top-level ``~/.grok`` skill beats a bundled Claude plugin skill of the same
    name, and project copies beat home copies."""
    home = Path.home()
    dirs: list[tuple[Path, str]] = [(d, "claude-plugin") for d in _claude_plugin_skill_dirs()]
    dirs.append((home / ".claude" / "skills", "claude"))
    dirs.append((home / ".grok" / "skills", "grok"))
    if project_root is not None:
        root = Path(project_root)
        dirs.append((root / ".claude" / "skills", "claude"))
        dirs.append((root / ".grok" / "skills", "grok"))
    return dirs


def foreign_import_enabled() -> bool:
    """Read ``import_foreign_skills`` from config.json (default True). Read
    directly (not via GlobalConfig.load) to stay cheap on the per-turn path and
    to respect a test's ``XLII_CONFIG_DIR`` override."""
    try:
        import json
        data = json.loads((global_config_dir() / "config.json").read_text())
        return bool(data.get("import_foreign_skills", True))
    except Exception:
        return True


def load_skills(
    project_root: Optional[Path] = None,
    *,
    import_foreign: Optional[bool] = None,
) -> dict[str, Skill]:
    """All discoverable skills by name. Precedence (low→high):
    ``stock < imported (grok/claude) < global < project`` — xlii's own skills
    always override an imported one, and project beats global. ``import_foreign``
    defaults to the ``import_foreign_skills`` config flag."""
    if import_foreign is None:
        import_foreign = foreign_import_enabled()
    skills = _scan(stock_skills_dir(), "stock")
    if import_foreign:
        for d, scope in foreign_skill_dirs(project_root):
            skills.update(_scan(d, scope))
    skills.update(_scan(global_skills_dir(), "global"))
    if project_root is not None:
        skills.update(_scan(project_skills_dir(project_root), "project"))
    return skills


def skill_index_line(skills: dict[str, Skill], *, model_facing: bool = False) -> str:
    """One line per skill (name + description) for an index. Imported skills carry
    an origin tag (e.g. ``[grok]``); xlii's own are untagged.

    ``model_facing`` trims the list to what the model should be told about: skills
    that opt out via ``disable-model-invocation`` are dropped, and the numerous
    bundled Claude *plugin* skills are kept off the per-turn preamble (still fully
    usable on demand via ``/skill``) so the system prompt doesn't balloon."""
    items = sorted(skills.values(), key=lambda s: s.name)
    if model_facing:
        items = [s for s in items if s.model_invocable and s.scope != "claude-plugin"]
    if not items:
        return ""
    out = []
    for s in items:
        tag = "" if s.scope in XLII_SCOPES else f" [{s.scope}]"
        brief = s.short_description or s.description or "(no description)"
        out.append(f"- {s.name}{tag}: {brief}")
    return "\n".join(out)


def render_skill(skill: Skill) -> str:
    """The body inlined when a skill is attached to the session."""
    head = f"# Skill: {skill.name}"
    if skill.description:
        head += f"\n\n{skill.description}"
    return f"{head}\n\n{skill.body}".strip()


def skill_detail_lines(skill: Skill) -> list[str]:
    """The extended view (`/skill show <name>`): origin, full description, body,
    and source path — read-only, distinct from the brief quickview list."""
    tag = "" if skill.scope in XLII_SCOPES else f" [dim]({skill.scope})[/dim]"
    flag = "" if skill.model_invocable else "  [dim](manual-only)[/dim]"
    lines = [f"[bold cyan]{skill.name}[/bold cyan]{tag}{flag}"]
    if skill.description:
        lines.append(skill.description)
    lines.append(f"[dim]{skill.path}[/dim]")
    if skill.body:
        lines.append("[dim]" + "─" * 48 + "[/dim]")
        lines.append(skill.body)
    return lines


def active_skill_names(attached_docs) -> set[str]:
    """Names of skills currently attached (read off the /doc channel)."""
    return {
        n[len(SKILL_ATTACH_PREFIX):]
        for n, _ in (attached_docs or [])
        if isinstance(n, str) and n.startswith(SKILL_ATTACH_PREFIX)
    }


def partition_attached_docs(
    attached_docs,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Split the ``/doc`` attachment channel into ``(docs, skills)`` (seam #7).

    Skills ride ``attached_docs`` under :data:`SKILL_ATTACH_PREFIX` so they inline
    into the system prompt like docs, which is why a flat read mis-counts them as
    docs and lets ``/doc`` list/detach surface a skill the user never attached as
    one. This is the single home of that ``skill:`` split so every consumer agrees:
    the input-frame ``doc``/``skill`` tabs (a correct doc count + a distinct skill
    tab) and ``/doc`` scoping (docs only).

    A **true partition** — ``docs + skills`` reconstructs the input, every entry
    kept **verbatim** in exactly one list: ``docs`` are the entries whose name does
    NOT carry the prefix; ``skills`` are the prefixed entries with the ``skill:``
    prefix **retained**, so a consumer can match them against the stored
    ``attached_docs`` names (``/doc`` scoping) without re-adding it. Strip the
    prefix at the display/load site (A2's skill preview already does). Defensive —
    a malformed entry (not a 2-sequence, or a non-string name) is treated as a
    plain doc, so a bad attachment can never raise on the status/render path."""
    docs: list[tuple[str, str]] = []
    skills: list[tuple[str, str]] = []
    for entry in (attached_docs or []):
        name = entry[0] if isinstance(entry, (tuple, list)) and entry else None
        if isinstance(name, str) and name.startswith(SKILL_ATTACH_PREFIX):
            skills.append(tuple(entry))  # type: ignore[arg-type]  # verbatim, prefix kept
        elif isinstance(entry, (tuple, list)):
            docs.append(tuple(entry))  # type: ignore[arg-type]
        else:
            docs.append(entry)
    return docs, skills
