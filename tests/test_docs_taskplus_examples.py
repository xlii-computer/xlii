"""Guard: the Task+ authoring docs stay in sync with the engine.

The scaffold, the /tasks new --from draft prompt, and docs/help/commands/tasks.md
all now teach the Task+ dialect (params, on_success/on_failure, [[edge]] verdicts,
split/join). If the engine's accepted syntax drifts, these tests break so the docs
get updated with it — the whole point of baking the docs in.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import xlii.tasks as T

HELP_DOC = Path(__file__).resolve().parents[1] / "docs" / "help" / "commands" / "tasks.md"


def _load(tmp_path: Path, body: str) -> T.Pipeline:
    xli = tmp_path / ".xlii"
    T.tasks_dir(xli).mkdir(parents=True, exist_ok=True)
    (T.tasks_dir(xli) / "x.toml").write_text('name = "x"\n' + body)
    return T.load_pipeline(xli, "x")  # load_pipeline runs full validation


def test_scaffold_skeleton_emits_real_tokens_and_loads(tmp_path):
    path = T.scaffold_pipeline(tmp_path / ".xlii", "demo")
    txt = path.read_text()
    # The %NAME% sentinel (not str.format) keeps {{ }} tokens verbatim.
    assert "%NAME%" not in txt
    assert 'name = "demo"' in txt
    assert "{{prev}}" in txt and "{{base}}" in txt  # double-braced carry/param tokens
    assert 'TASK+ {"branch": "clean"}' in txt  # verdict JSON = single braces
    assert "when = { branch = \"clean\" }" in txt  # edge inline table = single braces
    p = T.load_pipeline(tmp_path / ".xlii", "demo")
    assert p.name == "demo"
    assert [s.kind for s in p.steps] == [T.KIND_SHELL]  # only the live echo step


def test_documented_params_pattern_validates(tmp_path):
    p = _load(tmp_path, '[params.base]\ndefault = "HEAD~1"\n[[step]]\nrun = "git diff {{base}}"\n')
    by_name = {pm.name: pm for pm in p.params}
    assert "base" in by_name and by_name["base"].default == "HEAD~1"


def test_documented_shell_rc_pattern_validates(tmp_path):
    p = _load(
        tmp_path,
        '[[step]]\nid = "tests"\nrun = "python -m pytest -q"\non_failure = "triage"\n'
        '[[step]]\nid = "triage"\nask = "summarize"\n',
    )
    assert p.steps[0].on_failure == "triage"


def test_documented_verdict_pattern_validates(tmp_path):
    p = _load(
        tmp_path,
        '[[step]]\nid = "classify"\nask = "decide, FINAL line TASK+"\n'
        '[[step]]\nid = "clean"\nask = "a"\n'
        '[[step]]\nid = "dirty"\nask = "b"\n'
        '[[edge]]\nfrom = "classify"\nwhen = { branch = "clean" }\nto = "clean"\n'
        '[[edge]]\nfrom = "classify"\nwhen = { branch = "dirty" }\nto = "dirty"\n',
    )
    assert len(p.edges) == 2


def test_documented_split_join_pattern_validates(tmp_path):
    p = _load(
        tmp_path,
        '[[step]]\nid = "fan"\nsplit = ["lint", "types"]\njoin = "report"\npolicy = "all"\n'
        '[[step]]\nid = "lint"\nrun = "ruff check ."\n'
        '[[step]]\nid = "types"\nrun = "mypy ."\n'
        '[[step]]\nid = "report"\nask = "Summarize the lint and type results above."\n',
    )
    assert p.steps[0].is_split()
    assert p.steps[0].split == ["lint", "types"]
    assert p.steps[0].join == "report"


def test_help_doc_toml_blocks_are_valid_toml():
    """Every ```toml fence in the help page must at least parse as TOML —
    catches a stray brace/quote typo in the authoring examples."""
    text = HELP_DOC.read_text()
    blocks = re.findall(r"```toml\n(.*?)```", text, re.DOTALL)
    assert blocks, "expected fenced toml examples in the /tasks help page"
    for i, block in enumerate(blocks):
        tomllib.loads('name = "x"\n' + block)  # raises TOMLDecodeError on a typo
