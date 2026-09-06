"""Shared project registry lookup helpers.

Both CLI launchers and in-session slash commands need the same "project name or
path" semantics. Keeping that policy here prevents `/project switch foo` from
landing somewhere different than `xlii code foo`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from xlii.config import PROJECT_DIR_NAME, PROJECT_CONFIG_FILE, ProjectConfig
from xlii.registry import Registry, RegistryEntry


@dataclass(frozen=True)
class ProjectResolution:
    query: str
    matches: list[RegistryEntry] = field(default_factory=list)
    exact: bool = False
    path: Path | None = None
    project: ProjectConfig | None = None
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.project is not None

    @property
    def ambiguous(self) -> bool:
        return len(self.matches) > 1

    @property
    def entry(self) -> RegistryEntry | None:
        return self.matches[0] if len(self.matches) == 1 else None


def _maybe_migrate_project_state(project_root: Path) -> None:
    try:
        from xlii.legacy_migrate import migrate_project_state

        migrate_project_state(project_root)
    except (ImportError, OSError, ValueError):
        # Migration is opportunistic: an unmigrated project resolves from its current layout.
        pass


def project_is_alive(entry: RegistryEntry) -> bool:
    root = Path(entry.path)
    if not root.is_dir():
        return False
    _maybe_migrate_project_state(root)
    return (root / PROJECT_DIR_NAME / PROJECT_CONFIG_FILE).is_file()


def resolve_registered_project(query: str, *, registry: Registry | None = None) -> ProjectResolution:
    """Resolve a project registry query by exact name, then unique substring.

    The resolver intentionally accepts registry names only. Path-like launch
    resolution still belongs to `xlii code`, where non-project preview/init
    behavior can be gated interactively.
    """
    q = (query or "").strip()
    if not q:
        return ProjectResolution(query=q, reason="empty query")

    reg = registry or Registry.load()
    exact = [e for e in reg.entries if e.name == q]
    if exact:
        return _load_match(q, exact[0], exact=True)

    q_lower = q.lower()
    matches = [
        e for e in reg.entries
        if q_lower in e.name.lower() or q_lower in e.path.lower()
    ]
    if not matches:
        return ProjectResolution(query=q, reason="no matches")
    if len(matches) > 1:
        return ProjectResolution(query=q, matches=sorted(matches, key=lambda e: e.name.lower()))
    return _load_match(q, matches[0], exact=False)


def _load_match(query: str, entry: RegistryEntry, *, exact: bool) -> ProjectResolution:
    path = Path(entry.path)
    if not project_is_alive(entry):
        return ProjectResolution(
            query=query,
            matches=[entry],
            exact=exact,
            path=path,
            reason="project path is missing or not initialized",
        )
    project = ProjectConfig.load(path.resolve())
    if project is None:
        return ProjectResolution(
            query=query,
            matches=[entry],
            exact=exact,
            path=path,
            reason="project config could not be loaded",
        )
    return ProjectResolution(query=query, matches=[entry], exact=exact, path=path, project=project)
