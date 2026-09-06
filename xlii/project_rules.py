"""Glob-scoped project rules (terminal-native-toolkit Phase 5).

Rules live at ``.xlii/rules/*.md`` with optional frontmatter ``globs:``. A rule
with no ``globs`` always applies; otherwise it is inlined only when a file in
the session scope matches. Scope is the union of locker attachments and paths
touched this session (``dirty_paths``).
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Optional

from xlii.frontmatter import parse_frontmatter


@dataclass(frozen=True)
class ProjectRule:
    name: str
    globs: tuple[str, ...]  # empty → always apply
    body: str
    path: Path


def rules_dir(xli_dir: Path) -> Path:
    return Path(xli_dir) / "rules"


def to_project_relative(path: str | Path, project_root: Path) -> Optional[str]:
    """Normalize a path to a project-root-relative posix string, or None."""
    root = project_root.resolve()
    p = Path(path)
    try:
        if p.is_absolute():
            return p.resolve().relative_to(root).as_posix()
        return (root / p).resolve().relative_to(root).as_posix()
    except ValueError:
        return None


def _glob_pattern_to_regex(pattern: str) -> str:
    """Convert a path glob (with ``**``) to a full-path regex."""
    out: list[str] = ["^"]
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                if i + 2 < len(pattern) and pattern[i + 2] == "/":
                    out.append("(?:.*/)?")
                    i += 3
                else:
                    out.append(".*")
                    i += 2
            else:
                out.append("[^/]*")
                i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    out.append("$")
    return "".join(out)


def path_matches_glob(rel_path: str, pattern: str) -> bool:
    """Match a project-relative path against a glob pattern."""
    rel = rel_path.replace("\\", "/")
    pat = pattern.replace("\\", "/")
    if pat.endswith("/**"):
        prefix = pat[:-3].rstrip("/")
        if not prefix:
            return True
        return rel == prefix or rel.startswith(prefix + "/")
    if "**" in pat:
        return re.match(_glob_pattern_to_regex(pat), rel) is not None
    if "/" not in pat:
        return fnmatch.fnmatchcase(PurePosixPath(rel).name, pat)
    try:
        return PurePosixPath(rel).match(pat)
    except ValueError:
        return fnmatch.fnmatchcase(rel, pat)


def _parse_globs(meta: dict) -> tuple[str, ...]:
    raw = meta.get("globs")
    if raw is None:
        return ()
    if isinstance(raw, list):
        return tuple(str(g).strip() for g in raw if str(g).strip())
    if isinstance(raw, str) and raw.strip():
        return (raw.strip(),)
    return ()


def load_rules(xli_dir: Path) -> list[ProjectRule]:
    """Load every ``.md`` rule under ``.xlii/rules/`` (sorted by name)."""
    dir_ = rules_dir(xli_dir)
    if not dir_.is_dir():
        return []
    out: list[ProjectRule] = []
    for path in sorted(dir_.glob("*.md")):
        if path.name.upper() == "README.MD":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        meta, body = parse_frontmatter(text)
        content = body.strip()
        if not content:
            continue
        out.append(
            ProjectRule(
                name=path.stem,
                globs=_parse_globs(meta),
                body=content,
                path=path,
            )
        )
    return out


def rule_applies(rule: ProjectRule, scope_paths: set[str]) -> bool:
    if not rule.globs:
        return True
    if not scope_paths:
        return False
    for path in scope_paths:
        for pattern in rule.globs:
            if path_matches_glob(path, pattern):
                return True
    return False


def matching_rules(rules: Iterable[ProjectRule], scope_paths: set[str]) -> list[ProjectRule]:
    return [r for r in rules if rule_applies(r, scope_paths)]


def format_rules_addendum(rules: list[ProjectRule]) -> str:
    if not rules:
        return ""
    lines = [
        "[RULES] Project rules for files in scope this session "
        + "(from `.xlii/rules/`; always-apply rules have no `globs:` frontmatter):",
        "",
    ]
    for rule in rules:
        lines.append(f"## {rule.name}")
        lines.append("")
        lines.append(rule.body.rstrip())
        lines.append("")
    return "\n".join(lines).rstrip()


def rules_addendum(xli_dir: Path, scope_paths: set[str]) -> str:
    matched = matching_rules(load_rules(xli_dir), scope_paths)
    return format_rules_addendum(matched)


def current_scope_paths(session, project_root: Path) -> set[str]:
    """Session scope: touched paths + enabled locker attachments."""
    paths: set[str] = set(getattr(session, "scope_paths", None) or ())
    for entry in getattr(session, "attached_files", []) or []:
        if not entry.get("enabled"):
            continue
        rel = to_project_relative(entry.get("path", ""), project_root)
        if rel:
            paths.add(rel)
    return paths


def extend_scope_from_dirty(session, dirty: Iterable[str], project_root: Path) -> None:
    """Fold a turn's dirty paths into the session scope for subsequent turns."""
    scope = getattr(session, "scope_paths", None)
    if scope is None:
        session.scope_paths = set()
        scope = session.scope_paths
    for raw in dirty:
        if raw == "__rescan__":
            continue
        rel = to_project_relative(raw, project_root)
        if rel:
            scope.add(rel)


def scaffold_rules(project_root: Path, xli_dir: Path) -> list[str]:
    """Create ``.xlii/rules/`` on init; import ``AGENTS.md`` when present.

    Returns human-readable notes for the init banner (empty when nothing new)."""
    dir_ = rules_dir(xli_dir)
    notes: list[str] = []
    created_dir = not dir_.is_dir()
    dir_.mkdir(parents=True, exist_ok=True)

    readme = dir_ / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Project rules\n\n"
            "Add `*.md` files here. Optional YAML frontmatter:\n\n"
            "```yaml\n"
            "---\n"
            "globs: [frontend/**, src/**/*.tsx]\n"
            "---\n"
            "```\n\n"
            "Rules without `globs:` always apply. Rules with `globs:` are injected "
            "only when a matching file is attached or touched this session.\n",
            encoding="utf-8",
        )
        notes.append("rules: scaffolded .xlii/rules/")

    agents = project_root / "AGENTS.md"
    dest = dir_ / "agents.md"
    if agents.is_file() and not dest.exists():
        text = agents.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)
        if meta:
            dest.write_text(text, encoding="utf-8")
        else:
            dest.write_text(
                "---\n# Imported from AGENTS.md on xlii init\n---\n\n"
                f"{text.strip()}\n",
                encoding="utf-8",
            )
        notes.append("rules: imported AGENTS.md → .xlii/rules/agents.md")
    elif created_dir and not notes:
        notes.append("rules: scaffolded .xlii/rules/")
    return notes
