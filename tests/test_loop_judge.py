"""Shell judge adapter tests."""

from __future__ import annotations

from pathlib import Path

from xlii.loop_judge import JudgeProfile, run_harness_judge, run_shell_judge


def test_shell_judge_pass(tmp_path):
    v = run_shell_judge(JudgeProfile("tests", "shell", "true"), cwd=Path(tmp_path))
    assert v.passed is True
    assert v.exit_code == 0


def test_shell_judge_fail(tmp_path):
    v = run_shell_judge(JudgeProfile("tests", "shell", "false"), cwd=Path(tmp_path))
    assert v.passed is False
    assert v.exit_code != 0
    assert v.signature.startswith("exit:")


def test_harness_judge_config_error_leaves_model_blank(tmp_path):
    # kind=harness with no harness name is a config error; the unavailable
    # verdict must not be mislabeled with the Cursor default model since the
    # harness (and therefore its default model) is unknown.
    prof = JudgeProfile(name="bad", kind="harness")
    v = run_harness_judge(prof, bundle=None, xli_dir=Path(tmp_path))
    assert v.passed is False
    assert v.signature == "unavailable|config"
    assert v.model == ""
