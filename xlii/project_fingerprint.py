"""Project ecosystem fingerprinting for the human-facing project browser."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from xlii.atomicio import write_text_atomic

PROFILE_FILENAME = "project-profile.json"
PROFILE_VERSION = 1

MARKERS: dict[str, tuple[str, ...]] = {
    "python": (
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "requirements.txt",
        "Pipfile",
        "environment.yml",
    ),
    "node": ("package.json", "pnpm-workspace.yaml", "turbo.json"),
    "go": ("go.mod",),
    "rust": ("Cargo.toml",),
    "cpp": ("CMakeLists.txt", "meson.build", "Makefile", "configure.ac"),
    "php": ("composer.json",),
    "ruby": ("Gemfile",),
    "java": ("pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle.kts"),
}

GLOB_MARKERS: dict[str, tuple[str, ...]] = {
    "dotnet": ("*.sln", "*.csproj"),
}

ZONE_CANDIDATES: dict[str, tuple[str, ...]] = {
    "entry": (
        "src/xlii/cli.py",
        "src/main.py",
        "main.py",
        "app.py",
        "cli.py",
        "cmd/",
        "public/index.php",
    ),
    "config": (
        "pyproject.toml",
        "package.json",
        "go.mod",
        "Cargo.toml",
        "composer.json",
        "Gemfile",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "CMakeLists.txt",
        ".xlii/",
    ),
    "source": (
        "src/",
        "lib/",
        "app/",
        "internal/",
        "pkg/",
        "include/",
        "xlii/",
        "packages/",
    ),
    "tests": (
        "tests/",
        "test/",
        "__tests__/",
        "spec/",
        "src/test/",
    ),
    "tooling": (
        ".github/",
        ".gitlab-ci.yml",
        "Dockerfile",
        "docker-compose.yml",
        "Makefile",
        "scripts/",
    ),
    "generated": (
        ".venv/",
        "venv/",
        "node_modules/",
        "__pycache__/",
        ".pytest_cache/",
        ".mypy_cache/",
        ".ruff_cache/",
        "build/",
        "dist/",
        "target/",
        "vendor/",
        ".next/",
        ".turbo/",
    ),
}


@dataclass(frozen=True)
class ProjectFingerprint:
    version: int
    detected_at: str
    fingerprints: list[str]
    zones: dict[str, list[str]] = field(default_factory=dict)
    hints: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"


def profile_path(project_root: Path) -> Path:
    return project_root / ".xlii" / PROFILE_FILENAME


def _rel(path: Path, root: Path) -> str:
    rel = path.relative_to(root).as_posix()
    return rel + "/" if path.is_dir() else rel


def _exists(root: Path, rel: str) -> bool:
    return (root / rel.rstrip("/")).exists()


def _marker_roots(project_root: Path) -> Iterable[Path]:
    yield project_root
    try:
        children = sorted(p for p in project_root.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return
    for child in children:
        yield child
        try:
            grandchildren = sorted(p for p in child.iterdir() if p.is_dir() and not p.name.startswith("."))
        except OSError:
            continue
        yield from grandchildren


def _detect_ecosystems(project_root: Path) -> list[str]:
    found: set[str] = set()
    for root in _marker_roots(project_root):
        for ecosystem, markers in MARKERS.items():
            if any((root / marker).exists() for marker in markers):
                found.add(ecosystem)
        for ecosystem, patterns in GLOB_MARKERS.items():
            if any(next(root.glob(pattern), None) is not None for pattern in patterns):
                found.add(ecosystem)
    return sorted(found) or ["generic"]


def _has_marker(d: Path) -> bool:
    if any((d / m).exists() for markers in MARKERS.values() for m in markers):
        return True
    return any(
        next(d.glob(p), None) is not None
        for patterns in GLOB_MARKERS.values() for p in patterns
    )


def detect_package_roots(project_root: Path) -> list[str]:
    """Sub-directory roots (1–2 levels down) carrying their own ecosystem markers
    — the packages of a monorepo. Excludes the top-level root itself and any
    ignored directory (so ``node_modules/``/``build/``/``vendor/`` with a stray
    ``package.json``/``Makefile`` are NOT mistaken for packages, and we never
    descend into them). Returns sorted project-relative dir paths."""
    root = project_root.resolve()
    try:
        from xlii.ignore import load_ignore_spec

        spec = load_ignore_spec(root)
    except Exception:
        spec = None

    def ignored(rel: str) -> bool:
        return spec is not None and (spec.match_file(rel) or spec.match_file(rel + "/"))

    def child_dirs(d: Path) -> list[Path]:
        try:
            return sorted(p for p in d.iterdir() if p.is_dir() and not p.name.startswith("."))
        except OSError:
            return []

    found: list[str] = []
    for child in child_dirs(root):
        rel1 = child.relative_to(root).as_posix()
        if ignored(rel1):
            continue  # prune (and don't walk into) generated/vendored dirs
        if _has_marker(child):
            found.append(rel1 + "/")
        for grandchild in child_dirs(child):
            rel2 = grandchild.relative_to(root).as_posix()
            if ignored(rel2):
                continue
            if _has_marker(grandchild):
                found.append(rel2 + "/")
    return sorted(dict.fromkeys(found))


def _zone_paths(project_root: Path) -> dict[str, list[str]]:
    zones: dict[str, list[str]] = {}
    for zone, candidates in ZONE_CANDIDATES.items():
        hits = [_normalize_zone_path(rel) for rel in candidates if _exists(project_root, rel)]
        if hits:
            zones[zone] = sorted(dict.fromkeys(hits))

    if "source" not in zones:
        top_dirs = []
        for path in sorted(project_root.iterdir()) if project_root.exists() else []:
            if path.is_dir() and path.name not in {".git", ".xlii"}:
                top_dirs.append(_rel(path, project_root))
            if len(top_dirs) >= 5:
                break
        if top_dirs:
            zones["source"] = top_dirs
    return zones


def _normalize_zone_path(rel: str) -> str:
    return rel if rel.endswith("/") else rel


def _hints(project_root: Path, fingerprints: list[str]) -> dict[str, str]:
    hints: dict[str, str] = {}
    if "python" in fingerprints:
        hints["test_runner"] = "pytest" if _exists(project_root, "pyproject.toml") else "python -m pytest"
        if _exists(project_root, "requirements.txt"):
            hints["package_manager"] = "pip"
    if "node" in fingerprints:
        if _exists(project_root, "pnpm-lock.yaml") or _exists(project_root, "pnpm-workspace.yaml"):
            hints["package_manager"] = "pnpm"
        elif _exists(project_root, "yarn.lock"):
            hints["package_manager"] = "yarn"
        elif _exists(project_root, "package-lock.json"):
            hints["package_manager"] = "npm"
        hints.setdefault("test_runner", "npm test")
    if "go" in fingerprints:
        hints["test_runner"] = "go test ./..."
    if "rust" in fingerprints:
        hints["test_runner"] = "cargo test"
    return hints


def detect_project_fingerprint(project_root: Path, *, detected_at: str | None = None) -> ProjectFingerprint:
    root = project_root.resolve()
    fingerprints = _detect_ecosystems(root)
    timestamp = detected_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return ProjectFingerprint(
        version=PROFILE_VERSION,
        detected_at=timestamp,
        fingerprints=fingerprints,
        zones=_zone_paths(root),
        hints=_hints(root, fingerprints),
    )


def write_project_profile(project_root: Path, profile: ProjectFingerprint) -> Path:
    path = profile_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, profile.to_json(), mode=0o644)
    return path


def load_project_profile(project_root: Path) -> ProjectFingerprint | None:
    path = profile_path(project_root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return ProjectFingerprint(
            version=int(data.get("version") or PROFILE_VERSION),
            detected_at=str(data.get("detected_at") or ""),
            fingerprints=list(data.get("fingerprints") or []),
            zones={str(k): list(v) for k, v in (data.get("zones") or {}).items()},
            hints={str(k): str(v) for k, v in (data.get("hints") or {}).items()},
        )
    except (TypeError, ValueError):
        return None


def refresh_project_profile(project_root: Path) -> ProjectFingerprint:
    profile = detect_project_fingerprint(project_root)
    write_project_profile(project_root, profile)
    return profile


def summary_line(profile: ProjectFingerprint) -> str:
    """A one-line, human-readable orientation sentence for a fingerprint — the
    project-browser P5 system-prompt blurb and ``/status`` ``stack:`` line.

    e.g. ``python project; source in src/, xlii/; tests in tests/; test runner: pytest``
    """
    fps = "/".join(profile.fingerprints) if profile.fingerprints else "generic"
    bits = [f"{fps} project"]
    if profile.zones.get("source"):
        bits.append("source in " + ", ".join(profile.zones["source"]))
    if profile.zones.get("tests"):
        bits.append("tests in " + ", ".join(profile.zones["tests"]))
    if profile.hints.get("test_runner"):
        bits.append("test runner: " + profile.hints["test_runner"])
    return "; ".join(bits)
