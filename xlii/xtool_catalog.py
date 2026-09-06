"""Project-sniffed lint/format tool catalog (Track J kernel).

In-code stock recipes grouped by language ecosystem. Each entry carries an
``argv_template`` (may include ``<path>`` / ``<dir>`` placeholders), a ``binary``
for ``shutil.which`` gating, and ``fix_flag`` when the seeded line is destructive
(``--fix`` / ``--write`` / ``-w``). Project-aware ordering surfaces the
fingerprint's group first without hiding others.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
from typing import Iterable

GROUP_ORDER: tuple[str, ...] = ("python", "javascript", "rust", "go", "other")

GROUP_LABELS: dict[str, str] = {
    "python": "Python",
    "javascript": "JavaScript/TypeScript",
    "rust": "Rust",
    "go": "Go",
    "other": "Other",
}

_FINGERPRINT_GROUP: dict[str, str] = {
    "python": "python",
    "node": "javascript",
    "rust": "rust",
    "go": "go",
    "php": "other",
    "ruby": "other",
    "java": "other",
    "cpp": "other",
    "dotnet": "other",
}


@dataclass(frozen=True)
class XToolEntry:
    """One catalog row: stable id, human label, argv template, group, binary gate."""

    id: str
    label: str
    argv_template: str
    group: str
    binary: str
    fix_flag: bool = False
    legacy: bool = False


def _e(
    id: str,
    label: str,
    argv_template: str,
    group: str,
    binary: str,
    *,
    fix_flag: bool = False,
    legacy: bool = False,
) -> XToolEntry:
    return XToolEntry(id, label, argv_template, group, binary, fix_flag, legacy)


CATALOG: tuple[XToolEntry, ...] = (
    _e("ruff-check", "Ruff check", "ruff check <path>", "python", "ruff"),
    _e("ruff-check-fix", "Ruff check (fix)", "ruff check --fix <path>", "python", "ruff", fix_flag=True),
    _e("ruff-format", "Ruff format", "ruff format <path>", "python", "ruff"),
    _e("ruff-format-check", "Ruff format (check only)", "ruff format --check <path>", "python", "ruff"),
    _e("flake8", "Flake8", "flake8 <path>", "python", "flake8", legacy=True),
    _e("pylint", "Pylint", "pylint <path>", "python", "pylint", legacy=True),
    _e("black", "Black", "black <path>", "python", "black", legacy=True),
    _e("isort", "isort", "isort <path>", "python", "isort", legacy=True),
    _e("biome-check", "Biome check", "biome check <path>", "javascript", "biome"),
    _e("biome-check-write", "Biome check --write", "biome check --write <path>", "javascript", "biome", fix_flag=True),
    _e("biome-format", "Biome format", "biome format --write <path>", "javascript", "biome", fix_flag=True),
    _e("eslint", "ESLint", "eslint <path>", "javascript", "eslint", legacy=True),
    _e("prettier", "Prettier", "prettier --write <path>", "javascript", "prettier", fix_flag=True, legacy=True),
    _e("oxlint", "Oxlint", "oxlint <path>", "javascript", "oxlint", legacy=True),
    _e("rustfmt", "rustfmt", "rustfmt <path>", "rust", "rustfmt"),
    _e("cargo-fmt", "cargo fmt", "cargo fmt", "rust", "cargo"),
    _e("cargo-clippy", "cargo clippy", "cargo clippy", "rust", "cargo"),
    _e("gofmt", "gofmt", "gofmt -w <path>", "go", "gofmt", fix_flag=True),
    _e("goimports", "goimports", "goimports -w <path>", "go", "goimports", fix_flag=True),
    _e("golangci-lint", "golangci-lint", "golangci-lint run", "go", "golangci-lint"),
    _e("phpcs", "phpcs", "phpcs <path>", "other", "phpcs"),
    _e("phpcbf", "phpcbf", "phpcbf <path>", "other", "phpcbf", fix_flag=True),
    _e("rubocop", "RuboCop", "rubocop <path>", "other", "rubocop"),
    _e("checkstyle", "Checkstyle", "checkstyle <path>", "other", "checkstyle"),
    _e("clang-tidy", "clang-tidy", "clang-tidy <path>", "other", "clang-tidy"),
    _e("stylelint", "Stylelint", "stylelint <path>", "other", "stylelint"),
)

_BY_ID: dict[str, XToolEntry] = {e.id: e for e in CATALOG}


def tool_available(binary: str) -> bool:
    """True when ``binary`` resolves on PATH (``shutil.which``)."""
    return shutil.which(binary) is not None


def entry_available(entry: XToolEntry) -> bool:
    return tool_available(entry.binary)


def ordered_groups(fingerprints: Iterable[str] | None = None) -> list[str]:
    """Catalog groups with the project's fingerprint group first, others following."""
    fps = list(fingerprints or ())
    preferred: list[str] = []
    seen: set[str] = set()
    for fp in fps:
        grp = _FINGERPRINT_GROUP.get(fp)
        if grp and grp not in seen:
            preferred.append(grp)
            seen.add(grp)
    rest = [g for g in GROUP_ORDER if g not in seen]
    return preferred + rest


def entries_in_group(group: str, *, legacy: bool | None = None) -> list[XToolEntry]:
    """Rows for one group; ``legacy=None`` returns all, else filters primary vs More…."""
    out: list[XToolEntry] = []
    for e in CATALOG:
        if e.group != group:
            continue
        if legacy is None or e.legacy is legacy:
            out.append(e)
    return out


def lookup(tool_id: str) -> XToolEntry | None:
    """Resolve a tool id (exact) or a slug-normalized label."""
    key = tool_id.strip().lower()
    if key in _BY_ID:
        return _BY_ID[key]
    slug = re.sub(r"[^a-z0-9]+", "-", key).strip("-")
    if slug in _BY_ID:
        return _BY_ID[slug]
    for e in CATALOG:
        if e.label.lower() == key:
            return e
    return None


def resolve_tool_id(tool_id: str, *, fix: bool = False) -> str:
    """Map a base id + ``--fix`` to the destructive variant when one exists."""
    base = tool_id.strip().lower()
    if not fix:
        return base
    entry = lookup(base)
    if entry is not None and entry.fix_flag:
        return entry.id
    for suffix in ("-fix", "-write"):
        alt = f"{base}{suffix}"
        if alt in _BY_ID:
            return alt
    for e in CATALOG:
        if e.id.startswith(base) and e.fix_flag:
            return e.id
    return base


def _fill_placeholders(template: str, *, dock_path: Path | None) -> str:
    if "<path>" not in template and "<dir>" not in template:
        return template
    if dock_path is None:
        return template
    path = dock_path.expanduser()
    if path.is_dir():
        dir_val = str(path)
        path_val = str(path)
    else:
        path_val = str(path)
        dir_val = str(path.parent)
    return template.replace("<path>", path_val).replace("<dir>", dir_val)


def format_argv(entry: XToolEntry, *, dock_path: Path | None = None) -> str:
    """The shell line to seed (placeholders filled from a Dock file when given)."""
    return _fill_placeholders(entry.argv_template, dock_path=dock_path)


def project_fingerprints(project_root: Path | None) -> list[str]:
    """Best-effort fingerprint list for ordering (empty when unknown)."""
    if project_root is None:
        return []
    try:
        from xlii.project_fingerprint import detect_project_fingerprint, load_project_profile

        prof = load_project_profile(project_root) or detect_project_fingerprint(project_root)
        return list(prof.fingerprints)
    except Exception:
        return []


def ls_lines(fingerprints: Iterable[str] | None = None) -> list[str]:
    """Grouped listing with availability markers for ``/xtool ls``."""
    lines: list[str] = []
    for group in ordered_groups(fingerprints):
        rows = entries_in_group(group)
        if not rows:
            continue
        lines.append(f"[{GROUP_LABELS.get(group, group)}]")
        for e in rows:
            mark = "ok" if entry_available(e) else "missing"
            fix = " (fix)" if e.fix_flag else ""
            lines.append(f"  {e.id}: {e.label}{fix} [{mark}]")
        lines.append("")
    return lines
