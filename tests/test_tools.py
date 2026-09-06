"""Tier-0 coverage for xlii/tools.py: the file/shell tools + the safety surface.

These are the implementations behind everything the agent does to your machine,
and were previously untested beyond the two shellgate cases. Run directly:
    ./venv/bin/python -m pytest tests/test_tools.py

Conventions: pure functions over a ToolContext + tmp_path; the bash gate is
driven through the `_confirm` seam (as in test_shellgate.py). No network.
"""

import re
from types import SimpleNamespace

import pytest

from tests.helpers import FakeConsole, make_tool_ctx
from xlii import tools
from xlii.tool_context import INTENT_NETWORK, authorize_shell, normalize_declared_intent


# --------------------------------------------------------------------------- #
#  A2 dynamic context: large output spills to .xlii/scratch/tool-output/
# --------------------------------------------------------------------------- #

def test_large_output_spills_to_scratch(tmp_path):
    (tmp_path / "big.txt").write_text("line\n" * 5000)  # ~25 KB over a tiny threshold
    ctx = make_tool_ctx(tmp_path)
    ctx.cfg = SimpleNamespace(tool_output_spill_threshold=500)

    r = tools.t_read_file(ctx, {"path": "big.txt"})
    assert ".xlii/scratch/tool-output/" in r.content
    assert "read_file(" in r.content  # the recovery hint

    m = re.search(r"\.xlii/scratch/tool-output/[^\s`]+\.txt", r.content)
    assert m, "spill path should appear in the preview"
    spilled = (tmp_path / m.group(0)).read_text()
    assert spilled.count("line") >= 5000  # FULL output preserved on disk, not lost


def test_spill_disabled_falls_back_to_truncate(tmp_path):
    (tmp_path / "big.txt").write_text("x" * 50_000)
    ctx = make_tool_ctx(tmp_path)
    ctx.cfg = SimpleNamespace(tool_output_spill_threshold=0)  # disabled

    r = tools.t_read_file(ctx, {"path": "big.txt"})
    assert "truncated" in r.content              # lossy inline fallback
    assert ".xlii/scratch" not in r.content      # nothing spilled


def test_small_output_stays_inline(tmp_path):
    (tmp_path / "s.txt").write_text("hello\nworld\n")
    r = tools.t_read_file(make_tool_ctx(tmp_path), {"path": "s.txt"})
    assert "hello" in r.content
    assert "scratch" not in r.content


def test_spill_preview_never_inflates(tmp_path):
    """Regression: a small threshold must not produce a preview LARGER than the
    source (the old max(threshold//2, 1000) floor duplicated the whole text)."""
    ctx = make_tool_ctx(tmp_path)
    ctx.cfg = SimpleNamespace(tool_output_spill_threshold=200)
    text = "Z" * 1500
    out = tools._cap_output(ctx, text)
    assert len(out) < len(text)                        # shrinks, not inflates
    assert ".xlii/scratch/tool-output/" in out
    assert out.count("Z" * 200) <= 1 or "spilled to" in out  # not the whole body twice


# --------------------------------------------------------------------------- #
#  read / write / edit
# --------------------------------------------------------------------------- #

def test_read_file_is_line_numbered(tmp_path):
    (tmp_path / "a.txt").write_text("first\nsecond\n")
    r = tools.t_read_file(make_tool_ctx(tmp_path), {"path": "a.txt"})
    assert not r.is_error
    assert "1\tfirst" in r.content and "2\tsecond" in r.content


def test_read_file_offset_and_limit(tmp_path):
    (tmp_path / "a.txt").write_text("l1\nl2\nl3\nl4\n")
    r = tools.t_read_file(make_tool_ctx(tmp_path), {"path": "a.txt", "offset": 1, "limit": 2})
    assert "l2" in r.content and "l3" in r.content
    assert "l1" not in r.content and "l4" not in r.content
    # offset is reflected in the 1-based line number
    assert "2\tl2" in r.content


def test_read_file_missing_and_not_a_file(tmp_path):
    ctx = make_tool_ctx(tmp_path)
    assert tools.t_read_file(ctx, {"path": "nope.txt"}).is_error
    (tmp_path / "sub").mkdir()
    assert tools.t_read_file(ctx, {"path": "sub"}).is_error


def test_write_file_creates_parents_and_marks_dirty(tmp_path):
    ctx = make_tool_ctx(tmp_path)
    r = tools.t_write_file(ctx, {"path": "deep/nested/x.txt", "content": "hi"})
    assert not r.is_error
    assert (tmp_path / "deep/nested/x.txt").read_text() == "hi"
    assert "deep/nested/x.txt" in ctx.dirty_paths


def test_edit_file_requires_unique_match(tmp_path):
    (tmp_path / "f.txt").write_text("x x x")
    ctx = make_tool_ctx(tmp_path)
    # ambiguous without replace_all
    r = tools.t_edit_file(ctx, {"path": "f.txt", "old_string": "x", "new_string": "y"})
    assert r.is_error and "not unique" in r.content
    # replace_all succeeds
    r = tools.t_edit_file(ctx, {"path": "f.txt", "old_string": "x", "new_string": "y",
                                "replace_all": True})
    assert not r.is_error
    assert (tmp_path / "f.txt").read_text() == "y y y"
    assert "f.txt" in ctx.dirty_paths


def test_edit_file_old_string_not_found(tmp_path):
    (tmp_path / "f.txt").write_text("hello")
    r = tools.t_edit_file(make_tool_ctx(tmp_path),
                          {"path": "f.txt", "old_string": "absent", "new_string": "z"})
    assert r.is_error and "not found" in r.content


def test_edit_file_single_unique_replacement(tmp_path):
    (tmp_path / "f.txt").write_text("alpha beta gamma")
    ctx = make_tool_ctx(tmp_path)
    r = tools.t_edit_file(ctx, {"path": "f.txt", "old_string": "beta", "new_string": "BETA"})
    assert not r.is_error
    assert (tmp_path / "f.txt").read_text() == "alpha BETA gamma"


# --------------------------------------------------------------------------- #
#  path-escape safety (the security surface)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("tool,extra", [
    (tools.t_read_file, {}),
    (tools.t_write_file, {"content": "x"}),
])
def test_tools_refuse_to_escape_project_root(tmp_path, tool, extra):
    ctx = make_tool_ctx(tmp_path)
    with pytest.raises(ValueError, match="escapes project root"):
        tool(ctx, {"path": "../../etc/passwd", **extra})


def test_absolute_path_escape_refused(tmp_path):
    ctx = make_tool_ctx(tmp_path)
    with pytest.raises(ValueError, match="escapes project root"):
        tools.t_read_file(ctx, {"path": "/etc/passwd"})


# --------------------------------------------------------------------------- #
#  list_dir / glob / grep — ignore-aware
# --------------------------------------------------------------------------- #

def test_list_dir_marks_directories(tmp_path):
    (tmp_path / "file.txt").write_text("x")
    (tmp_path / "adir").mkdir()
    r = tools.t_list_dir(make_tool_ctx(tmp_path), {"path": "."})
    assert "adir/" in r.content
    assert "file.txt" in r.content


def test_list_dir_follows_files_mount(tmp_path, monkeypatch):
    import xlii.addressing as addressing
    import xlii.desk_files as desk_files

    monkeypatch.setattr(
        desk_files, "files_mount_address",
        lambda _p: "sftp://xliiec2/home/admin/app",
    )
    monkeypatch.setattr(
        addressing, "vfs_list",
        lambda _addr, **_kw: [
            addressing.Node(address="sftp://xliiec2/home/admin/app/src", name="src", kind="container"),
            addressing.Node(address="sftp://xliiec2/home/admin/app/main.py", name="main.py", kind="leaf"),
        ],
    )
    r = tools.t_list_dir(make_tool_ctx(tmp_path), {"path": "."})
    assert not r.is_error
    assert "src/" in r.content
    assert "main.py" in r.content


def test_glob_and_grep_skip_dot_git(tmp_path):
    # The same marker in a tracked file and inside .git/ — only the tracked one
    # should ever reach the model (ignore hygiene).
    (tmp_path / "main.py").write_text("SECRET_MARKER = 1\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("SECRET_MARKER\n")
    ctx = make_tool_ctx(tmp_path)

    g = tools.t_glob(ctx, {"pattern": "*.py"})
    assert "main.py" in g.content

    r = tools.t_grep(ctx, {"pattern": "SECRET_MARKER"})
    assert "main.py" in r.content
    assert ".git" not in r.content


def test_grep_no_matches(tmp_path):
    (tmp_path / "a.txt").write_text("nothing here")
    r = tools.t_grep(make_tool_ctx(tmp_path), {"pattern": "zzz_absent_zzz"})
    assert not r.is_error
    assert "main.py" not in r.content


# --------------------------------------------------------------------------- #
#  bash — intent validation, worker block, the gate, execution
# --------------------------------------------------------------------------- #

def test_bash_refuses_missing_or_invalid_intent(tmp_path):
    ctx = make_tool_ctx(tmp_path)
    assert tools.t_bash(ctx, {"command": "echo hi"}).is_error          # no intent
    r = tools.t_bash(ctx, {"command": "echo hi", "intent": "bogus"})
    assert r.is_error and "invalid `intent`" in r.content


def test_bash_runs_read_only_command(tmp_path):
    ctx = make_tool_ctx(tmp_path)
    r = tools.t_bash(ctx, {"command": "echo hello", "intent": "read-only"})
    assert not r.is_error
    assert "hello" in r.content and "exit 0" in r.content
    # Evidence sharpening: declared read-only AND classified read-only leaves
    # no dirty mark — a read-only bash turn can't satisfy edit claims, and
    # end-of-turn sync skips a pointless rescan.
    assert ctx.dirty_paths == set()


def test_bash_mutating_command_marks_rescan(tmp_path):
    ctx = make_tool_ctx(tmp_path, yolo=True, console=FakeConsole())
    r = tools.t_bash(ctx, {"command": "touch made.txt", "intent": "modifies-project"})
    assert not r.is_error
    assert "__rescan__" in ctx.dirty_paths


def test_bash_declared_read_only_lie_still_marks_rescan(tmp_path):
    """The model's word is not evidence: declared read-only on a command the
    classifier calls mutating keeps the conservative dirty mark."""
    ctx = make_tool_ctx(tmp_path, yolo=True, console=FakeConsole())
    r = tools.t_bash(ctx, {"command": "touch sneaky.txt", "intent": "read-only"})
    assert not r.is_error
    assert "__rescan__" in ctx.dirty_paths


def test_worker_cannot_run_non_read_only(tmp_path):
    ctx = make_tool_ctx(tmp_path, is_worker=True)
    r = tools.t_bash(ctx, {"command": "echo x", "intent": "network"})
    assert r.is_error and "read-only" in r.content


def test_worker_runs_read_only(tmp_path):
    ctx = make_tool_ctx(tmp_path, is_worker=True)
    r = tools.t_bash(ctx, {"command": "echo ok", "intent": "read-only"})
    assert not r.is_error and "ok" in r.content


def test_gated_intent_headless_without_console_is_refused(tmp_path):
    ctx = make_tool_ctx(tmp_path, console=None)  # no console = headless
    r = tools.t_bash(ctx, {"command": "echo x", "intent": "network"})
    assert r.is_error and "headless" in r.content


def test_gated_intent_denied_by_user(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_confirm", lambda prompt: "n")
    ctx = make_tool_ctx(tmp_path, console=FakeConsole())
    r = tools.t_bash(ctx, {"command": "echo x", "intent": "network"})
    assert r.is_error and "denied" in r.content


def test_yolo_bypasses_the_gate(tmp_path):
    ctx = make_tool_ctx(tmp_path, yolo=True, console=FakeConsole())
    # 'echo' classifies read-only, but declared network would normally gate;
    # yolo skips the prompt and the harmless echo actually runs.
    r = tools.t_bash(ctx, {"command": "echo offline", "intent": "network"})
    assert not r.is_error and "offline" in r.content


def test_bash_sudo_opens_terminal_does_not_capture(tmp_path, monkeypatch):
    """Agent sudo must not hang on a hidden TTY / DEVNULL capture."""
    launched = []
    monkeypatch.setattr("xlii.interactive.has_graphical_session", lambda: True)
    monkeypatch.setattr(
        "xlii.interactive.launch_in_external_terminal",
        lambda cwd, run="", preferred="": launched.append(run)
        or (True, "opened sudo in a new terminal"),
    )
    captured = []
    monkeypatch.setattr("xlii.shell_run.capture", lambda *a, **k: captured.append(1))
    monkeypatch.setattr(tools, "_confirm", lambda prompt: "y")
    ctx = make_tool_ctx(tmp_path, yolo=True, console=FakeConsole())
    r = tools.t_bash(ctx, {
        "command": "sudo apt install -y slack",
        "intent": "modifies-system",
    })
    assert launched == ["sudo apt install -y slack"]
    assert captured == []
    assert "sudo -S" in r.content
    assert "terminal" in r.content.lower()
    assert not r.is_error


def test_bash_sudo_refuses_when_no_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr("xlii.interactive.has_graphical_session", lambda: False)
    monkeypatch.setattr(tools, "_confirm", lambda prompt: "y")
    captured = []
    monkeypatch.setattr("xlii.shell_run.capture", lambda *a, **k: captured.append(1))
    ctx = make_tool_ctx(tmp_path, yolo=True, console=FakeConsole())
    r = tools.t_bash(ctx, {"command": "sudo id", "intent": "modifies-system"})
    assert r.is_error
    assert captured == []
    assert "password" in r.content.lower() or "terminal" in r.content.lower()


def test_bash_timeout_kills_and_reports_partial(tmp_path):
    ctx = make_tool_ctx(tmp_path)
    r = tools.t_bash(ctx, {"command": "sleep 3", "intent": "read-only", "timeout": 1})
    assert r.is_error and "TIMED OUT" in r.content
    assert "__rescan__" in ctx.dirty_paths


# --------------------------------------------------------------------------- #
#  registry / schema invariants (the safety contracts — pure + instant)
# --------------------------------------------------------------------------- #

def test_worker_registry_has_no_write_tools():
    assert "write_file" not in tools.WORKER_REGISTRY
    assert "edit_file" not in tools.WORKER_REGISTRY
    # and is otherwise a subset of the full registry
    assert set(tools.WORKER_REGISTRY) <= set(tools.REGISTRY)


def test_parallel_safe_excludes_mutators():
    for mutator in ("write_file", "edit_file", "bash"):
        assert mutator not in tools.PARALLEL_SAFE


def test_plan_mode_tools_are_read_only():
    for forbidden in ("write_file", "edit_file", "bash", "dispatch_subagent"):
        assert forbidden not in tools.PLAN_MODE_TOOLS
    assert tools.PLAN_MODE_TOOLS <= set(tools.REGISTRY)


def test_get_tool_fn_resolves_builtins_and_unknowns():
    assert tools.get_tool_fn("read_file") is tools.t_read_file
    assert tools.get_tool_fn("does_not_exist") is None


def test_tool_schemas_match_the_registry():
    names = {s["function"]["name"] for s in tools.tool_schemas()}
    # every advertised builtin schema dispatches to a real tool
    for name in names:
        assert tools.get_tool_fn(name) is not None


# --------------------------------------------------------------------------- #
#  the bash security decision, unit-tested WITHOUT spawning a subprocess —
#  the reason _check_intent_and_gate was split out of t_bash
# --------------------------------------------------------------------------- #

def test_gate_allows_read_only_returns_none(tmp_path):
    ctx = make_tool_ctx(tmp_path)
    assert tools._check_intent_and_gate(ctx, "echo hi", "read-only") is None


def test_gate_rejects_invalid_intent(tmp_path):
    r = tools._check_intent_and_gate(make_tool_ctx(tmp_path), "echo hi", "bogus")
    assert r is not None and r.is_error and "invalid `intent`" in r.content


def test_gate_blocks_worker_non_read_only(tmp_path):
    ctx = make_tool_ctx(tmp_path, is_worker=True)
    r = tools._check_intent_and_gate(ctx, "curl example.com", "network")
    assert r is not None and r.is_error and "read-only" in r.content


def test_gate_headless_refuses_risky_intent(tmp_path):
    ctx = make_tool_ctx(tmp_path, console=None)
    r = tools._check_intent_and_gate(ctx, "curl example.com", "network")
    assert r is not None and r.is_error and "headless" in r.content


def test_gate_user_denial(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_confirm", lambda prompt: "n")
    ctx = make_tool_ctx(tmp_path, console=FakeConsole())
    r = tools._check_intent_and_gate(ctx, "curl example.com", "network")
    assert r is not None and r.is_error and "denied" in r.content


def test_gate_user_approval_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_confirm", lambda prompt: "y")
    ctx = make_tool_ctx(tmp_path, console=FakeConsole())
    assert tools._check_intent_and_gate(ctx, "curl example.com", "network") is None


def test_gate_yolo_bypasses(tmp_path):
    ctx = make_tool_ctx(tmp_path, yolo=True, console=FakeConsole())
    assert tools._check_intent_and_gate(ctx, "curl example.com", "network") is None


def test_gate_yolo_still_confirms_modifies_system(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_confirm", lambda prompt: "n")
    ctx = make_tool_ctx(tmp_path, yolo=True, console=FakeConsole())
    r = tools._check_intent_and_gate(ctx, "sudo apt update", "modifies-system")
    assert r is not None and r.is_error and "denied" in r.content


def test_gate_escalates_underdeclared_intent(tmp_path, monkeypatch):
    # declared read-only, but classified as network (curl) -> the gate fires on
    # the stronger of the two; user denial proves the prompt happened.
    monkeypatch.setattr(tools, "_confirm", lambda prompt: "n")
    ctx = make_tool_ctx(tmp_path, console=FakeConsole())
    r = tools._check_intent_and_gate(ctx, "curl http://example.com", "read-only")
    assert r is not None and r.is_error and "denied" in r.content


def test_normalize_declared_intent_marks_invalid_values():
    assert normalize_declared_intent(None) == (None, False)
    assert normalize_declared_intent("") == (None, False)
    assert normalize_declared_intent(INTENT_NETWORK) == (INTENT_NETWORK, False)
    assert normalize_declared_intent("hallucinated") == (None, True)


def test_authorize_shell_logs_invalid_declared_intent(tmp_path, caplog):
    with caplog.at_level("WARNING"):
        auth = authorize_shell(
            "printf ok",
            project_root=tmp_path,
            declared_intent="hallucinated",
            yolo=False,
        )
    assert auth.allow is True
    assert auth.classified == "read-only"
    assert auth.effective == "read-only"
    assert any("invalid declared intent" in rec.message for rec in caplog.records)


# --------------------------------------------------------------------------- #
#  _server_tool skeleton (validate / call with sink / truncate / wrap errors)
# --------------------------------------------------------------------------- #

def test_server_tool_requires_value(tmp_path):
    r = tools._server_tool("web_search", "query", "", make_tool_ctx(tmp_path),
                           lambda *a, **k: "x")
    assert r.is_error and "'query' is required" in r.content


def test_server_tool_passes_value_kwargs_and_sink(tmp_path):
    ctx = make_tool_ctx(tmp_path)
    seen = {}

    def fake(clients, cfg, value, *, sink, **kw):
        seen.update(value=value, sink=sink, kw=kw)
        return "RESULT"

    r = tools._server_tool("web_search", "query", "hi", ctx, fake,
                           allowed_domains=["a.com"])
    assert not r.is_error and r.content == "RESULT"
    assert seen["value"] == "hi" and seen["sink"] is ctx
    assert seen["kw"] == {"allowed_domains": ["a.com"]}


def test_server_tool_wraps_exceptions(tmp_path):
    def boom(*a, **k):
        raise RuntimeError("nope")

    r = tools._server_tool("x_search", "query", "hi", make_tool_ctx(tmp_path), boom)
    assert r.is_error and "x_search failed: RuntimeError: nope" in r.content


# --------------------------------------------------------------------------- #
#  BUILTIN_TOOLS is THE single source of truth — derived structures track it
# --------------------------------------------------------------------------- #

def test_builtin_tools_drive_registry_and_schemas():
    builtin_names = [t.name for t in tools.BUILTIN_TOOLS]
    assert set(tools.REGISTRY) == set(builtin_names)
    for t in tools.BUILTIN_TOOLS:
        assert tools.REGISTRY[t.name] is t.handler
    # every builtin is advertised once, carrying its own description + parameters
    schemas = {s["function"]["name"]: s["function"] for s in tools.tool_schemas()}
    assert set(schemas) >= set(builtin_names)
    for t in tools.BUILTIN_TOOLS:
        assert schemas[t.name]["description"] == t.description
        assert schemas[t.name]["parameters"] == t.parameters


def test_derived_safety_sets_match_flags():
    assert set(tools.WORKER_REGISTRY) == {t.name for t in tools.BUILTIN_TOOLS if t.worker_safe}
    assert tools.PLAN_MODE_TOOLS == {t.name for t in tools.BUILTIN_TOOLS if t.plan_mode_safe}
    assert {t.name for t in tools.BUILTIN_TOOLS if t.parallel_safe} <= tools.PARALLEL_SAFE
    assert "dispatch_subagent" in tools.PARALLEL_SAFE
    # the two mutators must be excluded from every reduced surface
    for mut in ("write_file", "edit_file"):
        t = next(x for x in tools.BUILTIN_TOOLS if x.name == mut)
        assert not (t.worker_safe or t.parallel_safe or t.plan_mode_safe)
