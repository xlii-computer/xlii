import stat

from xlii.hooks import hooks_for, run_hooks


class FakeConsole:
    def __init__(self):
        self.lines = []

    def print(self, msg):
        self.lines.append(str(msg))


def _write_hook(xli_dir, event, name, body):
    d = xli_dir / "hooks" / event
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    f.write_text(f"#!/bin/sh\n{body}\n")
    f.chmod(f.stat().st_mode | stat.S_IXUSR)
    return f


def test_hook_receives_json_and_output_is_shown(tmp_path):
    xli = tmp_path / ".xlii"
    _write_hook(xli, "post-turn", "10-echo.sh",
                'read payload; echo "got: $payload"')
    con = FakeConsole()
    run_hooks(xli, "post-turn", {"user_input": "hi"}, console=con)
    joined = "\n".join(con.lines)
    assert "post-turn" in joined or "10-echo" in joined
    assert '\\"user_input\\": \\"hi\\"' in joined or '"user_input": "hi"' in joined


def test_failing_hook_warns_but_does_not_raise(tmp_path):
    xli = tmp_path / ".xlii"
    _write_hook(xli, "pre-sync", "boom.sh", "echo nope >&2; exit 3")
    con = FakeConsole()
    run_hooks(xli, "pre-sync", {}, console=con)  # must not raise
    assert any("exited 3" in line for line in con.lines)


def test_non_executable_files_are_ignored(tmp_path):
    xli = tmp_path / ".xlii"
    d = xli / "hooks" / "pre-turn"
    d.mkdir(parents=True)
    (d / "notes.txt").write_text("not a hook")
    assert hooks_for(xli, "pre-turn") == []


def test_no_hooks_dir_is_free(tmp_path):
    run_hooks(tmp_path / ".xlii", "post-tool", {"tool": "bash"}, console=None)
