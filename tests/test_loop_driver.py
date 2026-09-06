"""Loop driver integration with mocked run_turn."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from xlii.loop import LoopController, run_loop_cli
from tests.helpers import FakeConsole


def test_run_loop_cli_passes_with_true_test(tmp_path):
    xli = tmp_path / ".xlii"
    xli.mkdir()
    ctrl = LoopController.start(
        xli_dir=xli,
        goal="green",
        judges=["tests"],
        max_cycles=3,
        test_command="true",
    )
    run_turn = MagicMock(return_value=("", set(), SimpleNamespace(tool_calls=0)))
    outcome = run_loop_cli(
        controller=ctrl,
        project_root=tmp_path,
        run_turn=run_turn,
        console=FakeConsole(),
    )
    assert outcome == "LOOP_PASS"
    assert run_turn.call_count == 1


def test_run_loop_cli_retries_until_cap(tmp_path):
    xli = tmp_path / ".xlii"
    xli.mkdir()
    counter = xli / "fail_counter"
    counter.write_text("0")
    py = tmp_path / "fail_incr.py"
    py.write_text(
        f"""import pathlib
p = pathlib.Path({str(counter)!r})
n = int(p.read_text() or '0')
p.write_text(str(n + 1))
print(f'fail-{{n}}', flush=True)
raise SystemExit(1)
"""
    )
    ctrl = LoopController.start(
        xli_dir=xli,
        goal="red",
        judges=["tests"],
        max_cycles=2,
        test_command=f"python3 {py}",
    )
    run_turn = MagicMock(return_value=("", set(), SimpleNamespace(tool_calls=0)))
    outcome = run_loop_cli(
        controller=ctrl,
        project_root=tmp_path,
        run_turn=run_turn,
        console=FakeConsole(),
    )
    assert outcome == "LOOP_CAP"
    assert run_turn.call_count == 2


def test_run_loop_cli_repeat_failure(tmp_path):
    xli = tmp_path / ".xlii"
    xli.mkdir()
    ctrl = LoopController.start(
        xli_dir=xli,
        goal="stuck",
        judges=["tests"],
        max_cycles=5,
        test_command="false",
    )
    run_turn = MagicMock(return_value=("", set(), SimpleNamespace(tool_calls=0)))
    outcome = run_loop_cli(
        controller=ctrl,
        project_root=tmp_path,
        run_turn=run_turn,
        console=FakeConsole(),
    )
    assert outcome == "LOOP_FAIL"
    assert run_turn.call_count == 2
