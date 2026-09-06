"""Project browser P0: ecosystem fingerprint and profile cache."""

import json

from xlii.project_fingerprint import (
    detect_project_fingerprint,
    load_project_profile,
    write_project_profile,
)


def test_python_project_assigns_core_zones(tmp_path):
    (tmp_path / ".xlii").mkdir()
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "__init__.py").write_text("")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_pkg.py").write_text("")

    profile = detect_project_fingerprint(tmp_path, detected_at="2026-06-21T00:00:00+00:00")

    assert profile.fingerprints == ["python"]
    assert "pyproject.toml" in profile.zones["config"]
    assert "src/" in profile.zones["source"]
    assert "tests/" in profile.zones["tests"]
    assert profile.hints["test_runner"] == "pytest"


def test_polyglot_one_level_markers(tmp_path):
    (tmp_path / "apps" / "web").mkdir(parents=True)
    (tmp_path / "apps" / "web" / "package.json").write_text("{}")
    (tmp_path / "services" / "api").mkdir(parents=True)
    (tmp_path / "services" / "api" / "go.mod").write_text("module example.test/api\n")

    profile = detect_project_fingerprint(tmp_path, detected_at="2026-06-21T00:00:00+00:00")

    assert profile.fingerprints == ["go", "node"]


def test_detect_package_roots_for_monorepo(tmp_path):
    from xlii.project_fingerprint import detect_package_roots

    (tmp_path / "apps" / "web").mkdir(parents=True)
    (tmp_path / "apps" / "web" / "package.json").write_text("{}")
    (tmp_path / "services" / "api").mkdir(parents=True)
    (tmp_path / "services" / "api" / "go.mod").write_text("module example.test/api\n")

    roots = detect_package_roots(tmp_path)
    assert "apps/web/" in roots
    assert "services/api/" in roots


def test_detect_package_roots_empty_for_single_package(tmp_path):
    from xlii.project_fingerprint import detect_package_roots

    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    assert detect_package_roots(tmp_path) == []  # root markers don't count as packages


def test_detect_package_roots_excludes_generated_dirs(tmp_path):
    from xlii.project_fingerprint import detect_package_roots

    (tmp_path / "packages" / "web").mkdir(parents=True)
    (tmp_path / "packages" / "web" / "package.json").write_text("{}")
    (tmp_path / "node_modules" / "lib").mkdir(parents=True)   # ignored
    (tmp_path / "node_modules" / "lib" / "package.json").write_text("{}")
    (tmp_path / "build").mkdir()                              # ignored
    (tmp_path / "build" / "Makefile").write_text("\n")

    roots = detect_package_roots(tmp_path)
    assert "packages/web/" in roots
    assert not any("node_modules" in r for r in roots)
    assert "build/" not in roots


def test_generic_falls_back_to_top_level_dirs(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "notes").mkdir()

    profile = detect_project_fingerprint(tmp_path, detected_at="2026-06-21T00:00:00+00:00")

    assert profile.fingerprints == ["generic"]
    assert profile.zones["source"] == ["docs/", "notes/"]


def test_summary_line_orients(tmp_path):
    from xlii.project_fingerprint import summary_line

    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    profile = detect_project_fingerprint(tmp_path, detected_at="2026-06-21T00:00:00+00:00")

    line = summary_line(profile)
    assert "python project" in line
    assert "tests in tests/" in line
    assert "test runner: pytest" in line


def test_system_prompt_includes_cached_skeleton(tmp_path):
    from types import SimpleNamespace

    from xlii.agent import build_code_system_prompt
    from xlii.project_fingerprint import refresh_project_profile

    (tmp_path / ".xlii").mkdir()
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    (tmp_path / "src").mkdir()
    refresh_project_profile(tmp_path)  # writes .xlii/project-profile.json

    project = SimpleNamespace(
        project_root=tmp_path, xli_dir=tmp_path / ".xlii", local_only=False
    )
    prompt = build_code_system_prompt(project)
    assert "[PROJECT]" in prompt
    assert "python project" in prompt


def test_system_prompt_omits_skeleton_when_no_cache(tmp_path):
    from types import SimpleNamespace

    from xlii.agent import build_code_system_prompt

    (tmp_path / ".xlii").mkdir()
    project = SimpleNamespace(
        project_root=tmp_path, xli_dir=tmp_path / ".xlii", local_only=False
    )
    prompt = build_code_system_prompt(project)
    assert "[PROJECT]" not in prompt  # no profile cached → no blurb (cache-only, no detect)


def test_profile_cache_roundtrip_is_stable(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    profile = detect_project_fingerprint(tmp_path, detected_at="2026-06-21T00:00:00+00:00")
    path = write_project_profile(tmp_path, profile)

    loaded = load_project_profile(tmp_path)

    assert loaded == profile
    assert json.loads(path.read_text())["version"] == 1
