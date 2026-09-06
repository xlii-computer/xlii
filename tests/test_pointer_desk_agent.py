"""Pointer-desk agent mode: Files mount is the codebase, not the local stub."""

from __future__ import annotations

from types import SimpleNamespace

from tests.helpers import make_msg, make_project, make_tool_ctx
from xlii import tools
from xlii.turn_prompt import build_code_system_prompt


MOUNT = "sftp://appbox/srv/apps/jobsearch.xlii-code.com"


def _pointer_ctx(tmp_path, monkeypatch, *, kind="code"):
    import xlii.desk_files as desk_files

    monkeypatch.setattr(desk_files, "files_mount_address", lambda _p: MOUNT)
    ctx = make_tool_ctx(tmp_path)
    ctx.project.kind = kind
    ctx.project.files_root = MOUNT
    return ctx


def test_list_dir_on_pointer_desk_sees_remote_shape(tmp_path, monkeypatch):
    import xlii.addressing as addressing

    ctx = _pointer_ctx(tmp_path, monkeypatch)
    (tmp_path / ".xlii").mkdir()
    monkeypatch.setattr(
        addressing,
        "vfs_list",
        lambda _addr, **_kw: [
            addressing.Node(address=f"{MOUNT}/index.html", name="index.html", kind="leaf"),
            addressing.Node(address=f"{MOUNT}/script.js", name="script.js", kind="leaf"),
            addressing.Node(address=f"{MOUNT}/style.css", name="style.css", kind="leaf"),
            addressing.Node(address=f"{MOUNT}/board.json", name="board.json", kind="leaf"),
            addressing.Node(address=f"{MOUNT}/SCHEMA.md", name="SCHEMA.md", kind="leaf"),
        ],
    )
    r = tools.t_list_dir(ctx, {"path": "."})
    assert not r.is_error
    for name in ("index.html", "script.js", "style.css", "board.json", "SCHEMA.md"):
        assert name in r.content
    assert ".xlii" not in r.content


def test_bash_ls_stub_refused_on_pointer_desk(tmp_path, monkeypatch):
    ctx = _pointer_ctx(tmp_path, monkeypatch)
    r = tools.t_bash(ctx, {"command": "ls -la", "intent": "read-only"})
    assert r.is_error
    assert "list_dir" in r.content
    assert "Files mount" in r.content
    assert MOUNT in r.content
    assert "exit" not in r.content  # never ran


def test_bash_allows_remote_ops_and_local_metadata(tmp_path, monkeypatch):
    ctx = _pointer_ctx(tmp_path, monkeypatch)
    (tmp_path / ".xlii").mkdir()
    r = tools.t_bash(ctx, {"command": "echo hello", "intent": "read-only"})
    assert not r.is_error
    assert "hello" in r.content

    listing = tools.t_bash(ctx, {"command": "ls .xlii", "intent": "read-only"})
    assert not listing.is_error


def test_write_and_edit_follow_files_mount(tmp_path, monkeypatch):
    import xlii.addressing as addressing

    ctx = _pointer_ctx(tmp_path, monkeypatch)
    written: dict[str, bytes] = {}

    def fake_write(addr, data, **_kw):
        written[str(addr)] = data

    monkeypatch.setattr(addressing, "vfs_write", fake_write)
    monkeypatch.setattr(
        addressing, "vfs_read",
        lambda addr, **_kw: written.get(str(addr), b"hello world"),
    )

    w = tools.t_write_file(ctx, {"path": "index.html", "content": "<h1>hi</h1>"})
    assert not w.is_error
    assert written[f"{MOUNT}/index.html"] == b"<h1>hi</h1>"
    assert not (tmp_path / "index.html").exists()

    e = tools.t_edit_file(ctx, {
        "path": "index.html",
        "old_string": "<h1>hi</h1>",
        "new_string": "<h1>hey</h1>",
    })
    assert not e.is_error
    assert written[f"{MOUNT}/index.html"] == b"<h1>hey</h1>"
    assert not (tmp_path / "index.html").exists()


def test_write_stays_local_for_xlii_and_collections(tmp_path, monkeypatch):
    ctx = _pointer_ctx(tmp_path, monkeypatch)
    (tmp_path / ".xlii").mkdir()
    meta = tools.t_write_file(ctx, {"path": ".xlii/notes.md", "content": "stub only"})
    assert not meta.is_error
    assert (tmp_path / ".xlii" / "notes.md").read_text() == "stub only"

    pile = _pointer_ctx(tmp_path, monkeypatch, kind="collection")
    r = tools.t_write_file(pile, {"path": "pile.txt", "content": "local"})
    assert not r.is_error
    assert (tmp_path / "pile.txt").read_text() == "local"


def test_code_prompt_contains_files_block_for_pointer_desk(tmp_path):
    (tmp_path / ".xlii").mkdir()
    project = SimpleNamespace(
        project_root=tmp_path,
        xli_dir=tmp_path / ".xlii",
        local_only=True,
        files_root=MOUNT,
        kind="code",
        name="jobsearch",
    )
    prompt = build_code_system_prompt(project)
    assert "[FILES]" in prompt
    assert MOUNT in prompt
    assert "NOT the codebase" in prompt
    assert "list_dir" in prompt
    assert "bash ls" in prompt.lower() or "ls/find/tree" in prompt


def test_worker_inherits_files_mount_prompt(tmp_path, monkeypatch):
    from xlii.worker_agent import WorkerAgent

    project = make_project(tmp_path)
    project.kind = "code"
    project.files_root = MOUNT
    seen: dict[str, str] = {}

    def create(**kwargs):
        seen["system"] = kwargs["messages"][0]["content"]
        return SimpleNamespace(
            choices=[SimpleNamespace(message=make_msg("the app is on the mount", None))],
            usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0),
        )

    clients = SimpleNamespace(
        chat=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
    )
    cfg = SimpleNamespace(
        max_worker_iterations=1,
        get_model_for_role=lambda role="worker": "worker-model",
        worker_temp=lambda: 0.0,
        pricing={},
    )
    text, _call = WorkerAgent(clients=clients, project=project, cfg=cfg, role="explore").run(
        "what files exist?"
    )
    assert text == "the app is on the mount"
    assert "[FILES]" in seen["system"]
    assert MOUNT in seen["system"]
    assert "NOT the codebase" in seen["system"]
