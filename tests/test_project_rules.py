"""Tests for glob-scoped project rules (terminal-native-toolkit Phase 5)."""

from __future__ import annotations

from pathlib import Path

from xlii.agent import Agent, SessionState
from xlii.project_rules import (
    current_scope_paths,
    extend_scope_from_dirty,
    load_rules,
    matching_rules,
    path_matches_glob,
    rules_addendum,
    scaffold_rules,
    to_project_relative,
)
from xlii.turn_prompt import effective_system_prompt


def _write_rule(rules_dir: Path, name: str, meta: str, body: str) -> None:
    rules_dir.mkdir(parents=True, exist_ok=True)
    (rules_dir / f"{name}.md").write_text(f"---\n{meta}\n---\n{body}\n", encoding="utf-8")


def test_path_matches_glob_directory_prefix():
    assert path_matches_glob("frontend/app.tsx", "frontend/**")
    assert path_matches_glob("frontend/deep/x.js", "frontend/**")
    assert not path_matches_glob("backend/app.tsx", "frontend/**")


def test_path_matches_glob_fnmatch():
    assert path_matches_glob("src/foo.py", "*.py")
    assert path_matches_glob("src/foo.py", "src/*.py")
    assert not path_matches_glob("src/deep/foo.py", "src/*.py")


def test_path_matches_glob_double_star_cross_dirs():
    assert path_matches_glob("src/x.tsx", "src/**/*.tsx")
    assert path_matches_glob("src/a/b.tsx", "src/**/*.tsx")
    assert path_matches_glob("src/a/b/c.tsx", "src/**/*.tsx")
    assert path_matches_glob("c.py", "**/*.py")


def test_to_project_relative_preserves_dotfiles(tmp_path):
    dot = tmp_path / ".github" / "ci.yml"
    dot.parent.mkdir()
    dot.write_text("x", encoding="utf-8")
    rel = to_project_relative(".github/ci.yml", tmp_path)
    assert rel == ".github/ci.yml"
    assert to_project_relative(str(dot), tmp_path) == ".github/ci.yml"
    assert to_project_relative("../outside", tmp_path) is None


def test_agent_effective_prompt_without_project():
    agent = Agent.__new__(Agent)
    agent.base_system_prompt = "BASE"
    agent.session = SessionState()
    agent.attached_docs = []
    agent.active_mode = None
    prompt = Agent._effective_system_prompt(agent)
    assert prompt == "BASE"


def test_load_rules_parses_globs_and_body(tmp_path):
    rules = tmp_path / ".xlii" / "rules"
    _write_rule(rules, "fe", "globs: [frontend/**]", "Use React here.")
    _write_rule(rules, "global", "", "Always follow tests.")
    loaded = load_rules(tmp_path / ".xlii")
    by_name = {r.name: r for r in loaded}
    assert by_name["fe"].globs == ("frontend/**",)
    assert "React" in by_name["fe"].body
    assert by_name["global"].globs == ()


def test_matching_rules_respects_globs_and_always_apply(tmp_path):
    rules = tmp_path / ".xlii" / "rules"
    _write_rule(rules, "fe", "globs: [frontend/**]", "fe rule")
    _write_rule(rules, "global", "", "global rule")
    loaded = load_rules(tmp_path / ".xlii")

    none = matching_rules(loaded, set())
    assert [r.name for r in none] == ["global"]

    fe = matching_rules(loaded, {"frontend/app.tsx"})
    assert {r.name for r in fe} == {"fe", "global"}


def test_rules_addendum_format(tmp_path):
    rules = tmp_path / ".xlii" / "rules"
    _write_rule(rules, "global", "", "Ship small diffs.")
    text = rules_addendum(tmp_path / ".xlii", set())
    assert "[RULES]" in text
    assert "## global" in text
    assert "Ship small diffs." in text


def test_scope_paths_from_locker_and_dirty(tmp_path):
    session = SessionState()
    root = tmp_path
    session.attached_files = [
        {"path": str(root / "frontend" / "app.tsx"), "enabled": True},
    ]
    scope = current_scope_paths(session, root)
    assert "frontend/app.tsx" in scope

    extend_scope_from_dirty(session, {"backend/api.py", "__rescan__"}, root)
    assert "backend/api.py" in session.scope_paths


def test_prompt_pipeline_includes_matched_rules(tmp_path):
    rules = tmp_path / ".xlii" / "rules"
    _write_rule(rules, "global", "", "Always test.")
    addendum = rules_addendum(tmp_path / ".xlii", set())
    prompt = effective_system_prompt("BASE\n\n" + addendum, [], None)
    assert "BASE" in prompt
    assert "[RULES]" in prompt
    assert "Always test." in prompt


def test_scaffold_rules_imports_agents_md(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# Agent guide\nBe careful.\n", encoding="utf-8")
    xli = tmp_path / ".xlii"
    notes = scaffold_rules(tmp_path, xli)
    assert any("AGENTS.md" in n for n in notes)
    dest = xli / "rules" / "agents.md"
    assert dest.is_file()
    assert "Be careful." in dest.read_text(encoding="utf-8")


def test_scaffold_rules_does_not_overwrite_existing_agents_import(tmp_path):
    (tmp_path / "AGENTS.md").write_text("NEW\n", encoding="utf-8")
    xli = tmp_path / ".xlii"
    rules = xli / "rules"
    rules.mkdir(parents=True)
    (rules / "agents.md").write_text("OLD\n", encoding="utf-8")
    scaffold_rules(tmp_path, xli)
    assert (rules / "agents.md").read_text(encoding="utf-8") == "OLD\n"


def test_scope_unlocks_globbed_rule_on_next_prompt(tmp_path):
    rules = tmp_path / ".xlii" / "rules"
    _write_rule(rules, "fe", "globs: [frontend/**]", "Frontend only.")
    session = SessionState()
    assert "Frontend only." not in rules_addendum(tmp_path / ".xlii", current_scope_paths(session, tmp_path))
    extend_scope_from_dirty(session, {"frontend/index.tsx"}, tmp_path)
    text = rules_addendum(tmp_path / ".xlii", current_scope_paths(session, tmp_path))
    assert "Frontend only." in text
