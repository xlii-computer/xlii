"""/send — hand a file (or the last output) to an external program.

Covers target resolution (last→artifact, last→locker fallback, reply/shell
materialization, explicit path, file://, locker://name), program lookup, the
GUI-detached vs TTY-handover launch split, and the --wait round-trip.
Subprocess + shutil.which are monkeypatched to record instead of launching.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import xlii.repl_cmds.send as S
from xlii.artifacts import ImagineSession, write_artifact, write_session
from xlii.commands import dispatch_repl_command, find_repl_command
from xlii.config import GlobalConfig
from xlii.repl import REPLState
from xlii.repl_cmds import register_all
from xlii.shell_toolkit import capture_output, record_last_shell
from xlii.tui.events import ShellRan
from tests.helpers import FakeConsole, make_agent

register_all()


def _state(tmp_path):
    root = tmp_path / "proj"
    (root / ".xlii").mkdir(parents=True, exist_ok=True)
    cfg = GlobalConfig()
    agent = make_agent(root, cfg=cfg)
    return REPLState(console=FakeConsole(), agent=agent, project=agent.project,
                     cfg=cfg, pool=agent.pool)


def _file(tmp_path, name="pic.png", data=b"DATA"):
    p = tmp_path / name
    p.write_bytes(data)
    return p


@pytest.fixture
def rec(monkeypatch):
    """Record launches instead of spawning. which() succeeds for every program
    except 'nonesuch'."""
    calls = {"popen": [], "call": []}
    monkeypatch.setattr(S.shutil, "which",
                        lambda name: None if name == "nonesuch" else f"/usr/bin/{name}")

    def fake_popen(argv, **kw):
        calls["popen"].append(list(argv))
        return object()

    def fake_call(argv, **kw):
        calls["call"].append(list(argv))
        return 0

    monkeypatch.setattr(S.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(S.subprocess, "call", fake_call)
    return calls


def _send(state, line):
    dispatch_repl_command(line, state.as_context_dict())
    return state.console.text


def _launched(rec):
    """The single launched argv across popen+call (exactly one is expected)."""
    both = rec["popen"] + rec["call"]
    assert len(both) == 1, f"expected one launch, got {both}"
    return both[0]


# --------------------------------------------------------------------------- #

def test_registered_in_both_repls():
    assert find_repl_command("/send", "code") is not None
    assert find_repl_command("/send", "chat") is not None


def test_last_resolves_to_newest_artifact(tmp_path, rec):
    state = _state(tmp_path)
    root = state.project.project_root
    rels = [write_artifact(root, b"IMG1", ext="png"), write_artifact(root, b"IMG2", ext="png")]
    write_session(root, ImagineSession(prompt="a bird", paths=rels))

    _send(state, "/send feh last")

    argv = _launched(rec)
    assert argv[-1].endswith(rels[-1].rsplit("/", 1)[-1])  # newest artifact
    assert "feh" in argv[0]


def test_focus_target_uses_last_focus_pin(tmp_path, rec):
    state = _state(tmp_path)
    img = _file(tmp_path, "focused.png")
    state.last_focus = {"path": str(img), "address": f"file://{img}", "title": "focused.png"}
    _send(state, "/send feh focus")
    assert _launched(rec)[-1] == str(img)


def test_last_prefers_focus_over_artifact(tmp_path, rec):
    state = _state(tmp_path)
    root = state.project.project_root
    rels = [write_artifact(root, b"IMG", ext="png")]
    write_session(root, ImagineSession(prompt="a bird", paths=rels))
    focused = _file(tmp_path, "the-pin.txt")
    state.last_focus = {"path": str(focused), "title": "the-pin.txt"}
    _send(state, "/send pluma last")
    assert _launched(rec)[-1] == str(focused)


def test_focus_target_empty_explains(tmp_path, rec):
    state = _state(tmp_path)
    out = _send(state, "/send feh focus")
    assert "nothing focused" in out.lower()
    assert not (rec["popen"] or rec["call"])


def test_last_falls_back_to_locker(tmp_path, rec):
    state = _state(tmp_path)
    img = _file(tmp_path, "shot.png")
    state.attach_file(img)

    _send(state, "/send feh last")

    assert _launched(rec)[-1] == str(img.resolve())


def test_reply_materializes_to_temp_md(tmp_path, rec):
    state = _state(tmp_path)
    capture_output(state, "the model's reply text", source="answer", label="reply")

    _send(state, "/send pluma reply")

    argv = _launched(rec)
    out = Path(argv[-1])
    assert out.suffix == ".md" and out.is_file()
    assert out.read_text() == "the model's reply text"


def test_shell_materializes_to_temp_txt(tmp_path, rec):
    state = _state(tmp_path)
    record_last_shell(state, ShellRan(
        command="ls", cwd=Path("/tmp"), stdout="out-a\nout-b\n", stderr="", returncode=0))

    _send(state, "/send pluma shell")

    out = Path(_launched(rec)[-1])
    assert out.suffix == ".txt" and out.is_file()
    assert "out-a" in out.read_text() and "out-b" in out.read_text()


def test_reply_nothing_captured(tmp_path, rec):
    state = _state(tmp_path)
    out = _send(state, "/send pluma reply")
    assert "nothing to send" in out.lower()
    assert not (rec["popen"] or rec["call"])


def test_explicit_path(tmp_path, rec):
    state = _state(tmp_path)
    p = _file(tmp_path, "art.png")
    _send(state, f"/send gimp {p}")
    assert _launched(rec)[-1] == str(p.resolve())


def test_file_scheme_address(tmp_path, rec):
    state = _state(tmp_path)
    p = _file(tmp_path, "note.txt")
    _send(state, f"/send gimp file://{p}")
    assert _launched(rec)[-1] == str(p.resolve())


def test_locker_scheme_by_name(tmp_path, rec):
    state = _state(tmp_path)
    img = _file(tmp_path, "render.png")
    state.attach_file(img)
    _send(state, "/send eog locker://render.png")
    assert _launched(rec)[-1] == str(img.resolve())


def test_missing_file_launches_nothing(tmp_path, rec):
    state = _state(tmp_path)
    out = _send(state, "/send gimp /no/such/file.png")
    assert "file not found" in out.lower()
    assert not (rec["popen"] or rec["call"])


def test_missing_program(tmp_path, rec):
    state = _state(tmp_path)
    state.attach_file(_file(tmp_path))
    out = _send(state, "/send nonesuch last")
    assert "program not found" in out.lower()
    assert not (rec["popen"] or rec["call"])


def test_gui_program_is_detached(tmp_path, rec):
    state = _state(tmp_path)
    state.attach_file(_file(tmp_path))
    _send(state, "/send feh last")
    assert rec["popen"] and not rec["call"]      # non-interactive → detached Popen


def test_interactive_program_gets_terminal(tmp_path, rec):
    state = _state(tmp_path)
    p = _file(tmp_path, "notes.txt")
    _send(state, f"/send vim {p}")
    assert rec["call"] and not rec["popen"]      # vim is full-screen → subprocess.call


def test_wait_blocks_and_reattaches(tmp_path, rec):
    state = _state(tmp_path)
    p = _file(tmp_path, "edit.png")
    out = _send(state, f"/send gimp {p} --wait")
    assert rec["call"] and not rec["popen"]      # --wait → blocking call
    assert any(e["path"] == str(p.resolve()) for e in state.attached_files)
    assert "synced" in out.lower()


def test_no_program_uses_os_opener(tmp_path, rec):
    state = _state(tmp_path)
    img = _file(tmp_path, "open-me.png")
    state.attach_file(img)
    _send(state, "/send last")                    # no program → OS default opener
    argv = _launched(rec)
    assert argv[-1] == str(img.resolve())
    assert any(tok in argv[0] for tok in ("xdg-open", "open", "start"))


def test_bare_send_prints_usage(tmp_path, rec):
    state = _state(tmp_path)
    out = _send(state, "/send")
    assert "usage" in out.lower()
    assert not (rec["popen"] or rec["call"])
