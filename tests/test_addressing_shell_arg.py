"""The export seam — ``to_shell_arg`` (the inverse of ``resolve``): per-scheme outcomes,
cwd-relativity, line anchors, and quoting edges. Every registered builtin scheme has a
defined, tested outcome (the campaign's "done when")."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace

from xlii.addressing import Address, ShellExport, _PROVIDERS, providers, to_shell_arg


def _session_with(monkeypatch, **attrs):
    """Install a minimal duck-typed ambient session (see xlii.active_session)."""
    from xlii import active_session

    monkeypatch.setattr(active_session, "_ACTIVE", SimpleNamespace(**attrs))


def _project_session(monkeypatch, root: Path):
    """An ambient session rooted at ``root`` (xli_dir for wiki/plan/tasks providers)."""
    _session_with(monkeypatch, project=SimpleNamespace(xli_dir=root / ".xlii"))


def _snap(arg) -> str:
    """A content-kind ShellArg's snapshot text — always deletes the temp (failure-safe)."""
    try:
        return arg.path.read_text()
    finally:
        arg.path.unlink()


# --- the engine: dispatch, errors, the every-scheme invariant ------------------


def test_every_builtin_scheme_declares_shell_export():
    # The campaign's "done when": every registered scheme has a defined outcome.
    undeclared = [s for s, p in providers().items() if not callable(getattr(p, "shell_export", None))]
    assert undeclared == []


def test_unknown_scheme_is_a_clean_error():
    arg = to_shell_arg("nope://x")
    assert not arg.ok and "no provider" in arg.reason


def test_no_scheme_and_no_default_is_a_clean_error():
    arg = to_shell_arg("bare-name")
    assert not arg.ok and "no scheme" in arg.reason


def test_undeclared_provider_is_a_clean_error_not_a_guess(monkeypatch):
    class Mute:  # a provider that never declared the capability
        scheme = "zzmute"

        def resolve(self, address):  # pragma: no cover - never called by the engine
            raise AssertionError

    monkeypatch.setitem(_PROVIDERS, "zzmute", Mute())
    arg = to_shell_arg("zzmute://thing")
    assert not arg.ok and "does not export" in arg.reason


def test_bogus_export_kind_is_a_clean_error(monkeypatch):
    class Bogus:
        scheme = "zzbogus"

        def shell_export(self, address):
            return ShellExport(kind="hologram")

    monkeypatch.setitem(_PROVIDERS, "zzbogus", Bogus())
    arg = to_shell_arg("zzbogus://thing")
    assert not arg.ok and "unknown export kind" in arg.reason


def test_accepts_a_parsed_address(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    arg = to_shell_arg(Address.parse(f"file://{f}"), cwd=tmp_path)
    assert arg.ok and arg.value == "f.txt"


# --- quoting: owned by the task-args seam, safe to single-quote ----------------


def test_quoted_round_trips_hostile_values(tmp_path):
    for name in ("a b.txt", "it's.txt", 'two"quotes.txt', "naïve—file.txt", "$HOME.txt"):
        f = tmp_path / name
        f.write_text("x")
        arg = to_shell_arg(f"file://{f}", cwd=tmp_path)
        assert arg.ok and arg.value == name
        assert shlex.split(arg.quoted()) == [name]  # one token, exactly the raw value


def test_quoted_matches_the_task_args_seam(tmp_path):
    from xlii.tasks import KIND_SHELL, substitute

    f = tmp_path / "a b.txt"
    f.write_text("x")
    arg = to_shell_arg(f"file://{f}", cwd=tmp_path)
    assert arg.quoted() == substitute("{{v}}", {"v": arg.value}, kind=KIND_SHELL)


# --- file:// — the reference file-backed case ----------------------------------


def test_file_relative_inside_cwd_absolute_outside(tmp_path):
    f = tmp_path / "sub" / "f.txt"
    f.parent.mkdir()
    f.write_text("x")
    arg = to_shell_arg(f"file://{f}", cwd=tmp_path)
    assert arg.ok and arg.kind == "path" and arg.value == "sub/f.txt"
    assert arg.path == f.resolve()
    elsewhere = tmp_path / "sub"  # f is inside; tmp_path itself is not
    arg2 = to_shell_arg(f"file://{tmp_path / 'other.txt'}", cwd=elsewhere)
    assert arg2.ok and arg2.value == str(tmp_path / "other.txt")  # absolute — not under cwd


def test_file_line_anchors_render_editor_style(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    assert to_shell_arg(f"file://{f}#L42", cwd=tmp_path).value == "f.txt:42"
    assert to_shell_arg(f"file://{f}#L42-51", cwd=tmp_path).value == "f.txt:42"  # range → start
    assert to_shell_arg(f"file://{f}#section", cwd=tmp_path).value == "f.txt"  # not a line anchor
    assert to_shell_arg(f"file://{f}#42", cwd=tmp_path).value == "f.txt"  # grammar is L<NN>


def test_cwd_defaults_to_the_process_cwd(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    saved = Path.cwd()
    os.chdir(tmp_path)
    try:
        arg = to_shell_arg(f"file://{f}")  # no cwd passed
        assert arg.ok and arg.value == "f.txt"
    finally:
        os.chdir(saved)


def test_file_exports_a_missing_path_as_a_destination(tmp_path):
    # A path designator: `to_shell_arg` mirrors resolve() setting path on a miss.
    arg = to_shell_arg(f"file://{tmp_path}/new.txt", cwd=tmp_path)
    assert arg.ok and arg.value == "new.txt"


def test_bare_path_token_routes_to_file(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    saved = Path.cwd()
    os.chdir(tmp_path)
    try:
        arg = to_shell_arg(f"./{f.name}", cwd=tmp_path)  # bare-token rule: path-like → file
        assert arg.ok and arg.address.scheme == "file" and arg.value == "f.txt"
    finally:
        os.chdir(saved)


# --- project:// — the VFS grammar (key/subpath), not resolve()'s sniff ---------


def test_project_subpath_exports_the_leaf(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "x.py").write_text("pass\n")
    saved = Path.cwd()
    os.chdir(tmp_path)
    try:
        arg = to_shell_arg("project://./src/x.py", cwd=tmp_path)
        assert arg.ok and arg.value == "src/x.py"
        root = to_shell_arg("project://.", cwd=tmp_path)
        assert root.ok and root.value == "."
    finally:
        os.chdir(saved)


def test_project_escape_and_unknown_name_are_misses(tmp_path):
    saved = Path.cwd()
    os.chdir(tmp_path)
    try:
        esc = to_shell_arg("project://./../pwned", cwd=tmp_path)
        assert not esc.ok and "escapes" in esc.reason
    finally:
        os.chdir(saved)
    ghost = to_shell_arg("project://no-such-project-zz/x")
    assert not ghost.ok and "no such project" in ghost.reason


# --- conv:// — real turn leaves; the in-flight leaf materializes ----------------


def test_conv_leaf_and_root_export_paths(tmp_path):
    turns = tmp_path / ".xlii" / "turns"
    turns.mkdir(parents=True)
    turn = turns / "20260101T000000Z-0001.md"
    turn.write_text("# turn\n")
    saved = Path.cwd()
    os.chdir(tmp_path)
    try:
        leaf = to_shell_arg("conv://./20260101T000000Z-0001.md", cwd=tmp_path)
        assert leaf.ok and leaf.value == ".xlii/turns/20260101T000000Z-0001.md"
        root = to_shell_arg("conv://.", cwd=tmp_path)
        assert root.ok and root.value == ".xlii/turns"
    finally:
        os.chdir(saved)


def test_conv_inflight_materializes_a_snapshot(tmp_path, monkeypatch):
    from xlii.active_session import set_active_session
    from xlii.conversation import INFLIGHT_LEAF, Conversation, ensure_conversation

    turns = tmp_path / ".xlii" / "turns"
    turns.mkdir(parents=True)
    state = type("S", (), {})()
    state.conversation = Conversation(turns_dir=turns)
    state.profile = None
    ensure_conversation(state)
    state.conversation.start_turn("what gives?")
    state.conversation.append_chunk("live text")
    prev = set_active_session(state)
    saved = Path.cwd()
    os.chdir(tmp_path)
    try:
        arg = to_shell_arg(f"conv://./{INFLIGHT_LEAF}", cwd=tmp_path)
        assert arg.ok and arg.kind == "content" and arg.path.suffix == ".md"
        assert "live text" in _snap(arg)
    finally:
        os.chdir(saved)
        set_active_session(prev)


# --- config:// — root is its file; keyed nodes materialize the value -----------


def test_config_root_is_the_backing_file_keyed_nodes_materialize(tmp_path):
    proj = tmp_path / ".xlii"
    proj.mkdir()
    (proj / "project.json").write_text(json.dumps({"a": {"b": 1}, "model": "grok"}))
    saved = Path.cwd()
    os.chdir(tmp_path)
    try:
        root = to_shell_arg("config://project", cwd=tmp_path)
        assert root.ok and root.kind == "path" and root.value == ".xlii/project.json"
        leaf = to_shell_arg("config://project/model", cwd=tmp_path)
        assert leaf.ok and leaf.kind == "content" and leaf.path.suffix == ".txt"
        assert _snap(leaf) == "grok\n"
        container = to_shell_arg("config://project/a", cwd=tmp_path)
        assert container.ok and container.path.suffix == ".json"
        assert json.loads(_snap(container)) == {"b": 1}
        miss = to_shell_arg("config://project/nope", cwd=tmp_path)
        assert not miss.ok and "no key" in miss.reason
    finally:
        os.chdir(saved)


def test_config_global_root_and_unknown_root(tmp_path, monkeypatch):
    import xlii.config as cfg

    gf = tmp_path / "config.json"
    gf.write_text("{}")
    monkeypatch.setattr(cfg, "GLOBAL_CONFIG_FILE", gf)
    arg = to_shell_arg("config://global", cwd=tmp_path)
    assert arg.ok and arg.kind == "content"
    bad = to_shell_arg("config://nope")
    assert not bad.ok and "unknown root" in bad.reason


def test_conv_misses_cleanly_without_turns(tmp_path, monkeypatch):
    from xlii import active_session

    monkeypatch.setattr(active_session, "_ACTIVE", None)
    saved = Path.cwd()
    os.chdir(tmp_path)
    try:
        root = to_shell_arg("conv://.")  # no .xlii/turns here
        assert not root.ok and "no conversation" in root.reason
        (tmp_path / ".xlii" / "turns").mkdir(parents=True)
        ghost = to_shell_arg("conv://./ghost.md")
        assert not ghost.ok and "no such turn" in ghost.reason
    finally:
        os.chdir(saved)


def test_read_only_store_roots_require_the_store(tmp_path, monkeypatch):
    import xlii.doc as docmod

    monkeypatch.setattr(docmod, "DOCS_DIR", tmp_path / "ghost-docs")
    docs = to_shell_arg("docs://")
    assert not docs.ok and "no docs yet" in docs.reason
    _project_session(monkeypatch, tmp_path)  # a project that never planned or wiki'd
    plan = to_shell_arg("plan://")
    assert not plan.ok and "no plans yet" in plan.reason
    wiki = to_shell_arg("wiki://")
    assert not wiki.ok and "no wiki pages yet" in wiki.reason


def test_relative_file_target_binds_to_process_cwd_not_the_render_base(tmp_path):
    # The cwd kwarg is a RENDER base only — target resolution matches resolve()'s
    # semantics (process cwd), pinned so the contract can't silently drift.
    proc = tmp_path / "procwd"
    render = tmp_path / "render"
    proc.mkdir()
    render.mkdir()
    (proc / "rel.txt").write_text("x")
    saved = Path.cwd()
    os.chdir(proc)
    try:
        arg = to_shell_arg("file://rel.txt", cwd=render)
        assert arg.ok and arg.path == (proc / "rel.txt").resolve()
        assert arg.value == str((proc / "rel.txt").resolve())  # outside the render base → absolute
    finally:
        os.chdir(saved)


# --- docs:// / persona:// / skills:// — registry-named, file-backed ------------


def test_docs_export_paths(tmp_path, monkeypatch):
    import xlii.doc as docmod

    monkeypatch.setattr(docmod, "DOCS_DIR", tmp_path)
    (tmp_path / "guide.md").write_text("# guide\n")
    arg = to_shell_arg("docs://guide", cwd=tmp_path)
    assert arg.ok and arg.value == "guide.md"
    assert to_shell_arg("docs://", cwd=tmp_path).value == "."  # the store dir itself
    miss = to_shell_arg("docs://ghost")
    assert not miss.ok and "no such doc" in miss.reason


def test_persona_export_paths(tmp_path, monkeypatch):
    import xlii.persona as pmod

    monkeypatch.setattr(pmod, "PERSONAS_DIR", tmp_path)
    (tmp_path / "bob.md").write_text("you are bob\n")
    arg = to_shell_arg("persona://bob", cwd=tmp_path)
    assert arg.ok and arg.value == "bob.md"
    assert to_shell_arg("persona://", cwd=tmp_path).value == "."
    bad = to_shell_arg("persona://-bad")  # leading dash fails is_valid_name
    assert not bad.ok and "invalid persona name" in bad.reason
    miss = to_shell_arg("persona://ghost")
    assert not miss.ok and "no such persona" in miss.reason


def test_skills_export_the_skill_md(tmp_path, monkeypatch):
    skill_md = tmp_path / "grounded" / "SKILL.md"
    skill_md.parent.mkdir()
    skill_md.write_text("---\n---\nbody\n")
    fake = {"grounded": SimpleNamespace(name="grounded", path=skill_md)}
    monkeypatch.setattr("xlii.skills.load_skills", lambda *a, **k: fake)
    arg = to_shell_arg("skills://grounded", cwd=tmp_path)
    assert arg.ok and arg.value == "grounded/SKILL.md"
    root = to_shell_arg("skills://")
    assert root.ok and root.kind == "address" and root.value == "skills://"  # multi-scope palette
    miss = to_shell_arg("skills://ghost")
    assert not miss.ok and "no such skill" in miss.reason


# --- mark:// / jobs:// — session-registry content, materialized ----------------


def test_mark_materializes_the_span(tmp_path, monkeypatch):
    from xlii.transcript import mark_last_turn, write_turn

    td = tmp_path / "turns"
    td.mkdir()
    write_turn(td, "what is the thesis?", "the thesis is X.")
    assert mark_last_turn(td, "thesis")
    _session_with(monkeypatch, profile=SimpleNamespace(memory=SimpleNamespace(turns_dir=td)))
    arg = to_shell_arg("mark://thesis", cwd=tmp_path)
    assert arg.ok and arg.kind == "content" and arg.path.suffix == ".md"
    assert "the thesis is X." in _snap(arg)
    root = to_shell_arg("mark://")
    assert root.ok and root.kind == "address" and root.value == "mark://"


def test_mark_miss_without_session(monkeypatch):
    from xlii import active_session

    monkeypatch.setattr(active_session, "_ACTIVE", None)
    arg = to_shell_arg("mark://ghost")
    assert not arg.ok and "no such mark" in arg.reason


def test_jobs_materialize_a_report(monkeypatch):
    from xlii.jobs import JobRegistry

    state = SimpleNamespace(job_registry=JobRegistry(max_workers=2))
    jid = state.job_registry.dispatch("task", "compute", lambda: "42", notify=False)
    state.job_registry.wait(jid, timeout=5)
    from xlii import active_session

    monkeypatch.setattr(active_session, "_ACTIVE", state)
    arg = to_shell_arg(f"jobs://{jid}")
    assert arg.ok and arg.kind == "content" and arg.path.suffix == ".md"
    body = _snap(arg)
    assert jid in body and "42" in body
    assert to_shell_arg("jobs://").kind == "address"
    miss = to_shell_arg("jobs://t999")
    assert not miss.ok and "no such job" in miss.reason


# --- plan:// / tasks:// / wiki:// — project-scoped stores -----------------------


def test_plan_exports_the_plan_file(tmp_path, monkeypatch):
    plans = tmp_path / ".xlii" / "plans"
    plans.mkdir(parents=True)
    (plans / "current.md").write_text("# plan\n")
    _project_session(monkeypatch, tmp_path)
    arg = to_shell_arg("plan://current", cwd=tmp_path)
    assert arg.ok and arg.value == ".xlii/plans/current.md"
    assert to_shell_arg("plan://", cwd=tmp_path).value == ".xlii/plans"
    miss = to_shell_arg("plan://ghost")
    assert not miss.ok and "no plan file" in miss.reason


def test_plan_without_project_is_a_miss(monkeypatch):
    from xlii import active_session

    monkeypatch.setattr(active_session, "_ACTIVE", None)
    arg = to_shell_arg("plan://current")
    assert not arg.ok and "no active project" in arg.reason


def test_tasks_export_project_toml_then_stock(tmp_path, monkeypatch):
    from xlii import tasks as T

    tasks_dir = tmp_path / ".xlii" / "tasks"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "mine.toml").write_text('name = "mine"\n')
    _project_session(monkeypatch, tmp_path)
    arg = to_shell_arg("tasks://mine", cwd=tmp_path)
    assert arg.ok and arg.value == ".xlii/tasks/mine.toml"
    stock = sorted(T.stock_tasks_dir().glob("*.toml"))
    assert stock, "expected bundled stock tasks"
    sarg = to_shell_arg(f"tasks://{stock[0].stem}", cwd=tmp_path)
    assert sarg.ok and sarg.path == stock[0]  # project misses fall through to stock
    (tasks_dir / stock[0].name).write_text('name = "shadow"\n')
    shadow = to_shell_arg(f"tasks://{stock[0].stem}", cwd=tmp_path)
    assert shadow.ok and shadow.path == tasks_dir / stock[0].name  # project SHADOWS stock
    assert to_shell_arg("tasks://").kind == "address"  # spans project + stock — no one dir
    miss = to_shell_arg("tasks://ghost")
    assert not miss.ok and "no saved task" in miss.reason


def test_wiki_page_is_a_path_anchored_section_is_content(tmp_path, monkeypatch):
    from xlii import wiki as W

    xli = tmp_path / ".xlii"
    xli.mkdir()
    W.write_page(xli, "seam", "# seam\n\n## usage\n\nuse it well\n")
    _project_session(monkeypatch, tmp_path)
    arg = to_shell_arg("wiki://seam", cwd=tmp_path)
    assert arg.ok and arg.value == ".xlii/wiki/seam.md"
    section = to_shell_arg("wiki://seam#usage", cwd=tmp_path)
    assert section.ok and section.kind == "content" and "use it well" in _snap(section)
    assert to_shell_arg("wiki://", cwd=tmp_path).value == ".xlii/wiki"
    miss = to_shell_arg("wiki://ghost")
    assert not miss.ok and "no such page" in miss.reason


# --- git:// / map:// — computed views over the working tree ---------------------


def test_git_diff_materializes_containers_pass_through(tmp_path, monkeypatch):
    from xlii import active_session

    monkeypatch.setattr(active_session, "_ACTIVE", None)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "f.txt").write_text("hi\n")
    saved = Path.cwd()
    os.chdir(tmp_path)
    try:
        arg = to_shell_arg("git://diff/f.txt", cwd=tmp_path)
        assert arg.ok and arg.kind == "content" and arg.path.suffix == ".diff"
        assert "+hi" in _snap(arg)  # untracked renders as all-additions
        assert to_shell_arg("git://").kind == "address"
        assert to_shell_arg("git://diff").kind == "address"
        miss = to_shell_arg("git://branches")
        assert not miss.ok and "unknown git path" in miss.reason
    finally:
        os.chdir(saved)


def test_map_exports_real_paths(tmp_path, monkeypatch):
    from xlii import active_session

    monkeypatch.setattr(active_session, "_ACTIVE", None)
    (tmp_path / "f.txt").write_text("x")
    saved = Path.cwd()
    os.chdir(tmp_path)
    try:
        assert to_shell_arg("map://f.txt", cwd=tmp_path).value == "f.txt"
        assert to_shell_arg("map://", cwd=tmp_path).value == "."
        miss = to_shell_arg("map://ghost.txt", cwd=tmp_path)
        assert not miss.ok and "no such path" in miss.reason
    finally:
        os.chdir(saved)


# --- remote / xlii:// / home:// — synthetic and network-backed -------------------


def test_remote_pickers_pass_through_leaves_materialize(monkeypatch):
    fake_conn = SimpleNamespace(
        stat=lambda sub: (False, 3),
        read=lambda sub: b"log\n",
    )
    fake_manager = SimpleNamespace(get=lambda name: fake_conn, names=lambda: ["box"])
    monkeypatch.setattr("xlii.remotefs.manager", fake_manager)
    for scheme in ("ftp", "sftp", "dav", "smb", "remote"):  # one class, five doorways
        assert to_shell_arg(f"{scheme}://").kind == "address"  # the picker
        assert to_shell_arg(f"{scheme}://box").kind == "address"  # a login home
        arg = to_shell_arg(f"{scheme}://box/logs/app.log")
        assert arg.ok and arg.kind == "content" and arg.path.suffix == ".log"
        assert _snap(arg) == "log\n"
    fake_conn.stat = lambda sub: (True, None)
    assert to_shell_arg("ftp://box/logs").kind == "address"  # a remote dir stays an address


def test_remote_wire_failure_is_a_clean_error(monkeypatch):
    class Wire(Exception):  # paramiko's SSHException is NOT an OSError
        pass

    def boom(sub):
        raise Wire("channel dropped")

    fake_conn = SimpleNamespace(stat=boom, read=boom)
    fake_manager = SimpleNamespace(get=lambda name: fake_conn, names=lambda: ["box"])
    monkeypatch.setattr("xlii.remotefs.manager", fake_manager)
    arg = to_shell_arg("sftp://box/logs/app.log")
    assert not arg.ok and "channel dropped" in arg.reason


def test_remote_unknown_connection_is_a_clean_error():
    # The manager raises RuntimeError for an unconfigured connection — the engine
    # maps the whole remote failure family to ok=False, never a raise.
    arg = to_shell_arg("ftp://no-such-conn-zz/some/file.txt")
    assert not arg.ok and arg.reason


def test_xlii_leaves_materialize_containers_pass_through():
    from xlii import __version__

    arg = to_shell_arg("xlii://version")
    assert arg.ok and arg.kind == "content" and _snap(arg) == __version__ + "\n"
    assert to_shell_arg("xlii://").kind == "address"
    assert to_shell_arg("xlii://schemes").kind == "address"
    miss = to_shell_arg("xlii://nope")
    assert not miss.ok and "no such address" in miss.reason


def test_home_rows_pass_through():
    from xlii.home_catalog import HOME_CATALOG

    assert to_shell_arg("home://").kind == "address"
    row = to_shell_arg(f"home://{HOME_CATALOG[0].slug}")
    assert row.ok and row.kind == "address" and row.value == f"home://{HOME_CATALOG[0].slug}"
    miss = to_shell_arg("home://nope")
    assert not miss.ok and "no home row" in miss.reason


# --- locker:// — a pointer to a real file ---------------------------------------


def test_locker_passes_the_attached_path_through(tmp_path, monkeypatch):
    img = tmp_path / "diagram.png"
    img.write_bytes(b"\x89PNG fake")
    _session_with(
        monkeypatch,
        attached_files=[{"name": "diagram.png", "path": str(img), "kind": "image", "enabled": True}],
    )
    arg = to_shell_arg("locker://diagram.png", cwd=tmp_path)
    assert arg.ok and arg.kind == "path" and arg.value == "diagram.png"
    assert to_shell_arg("locker://").kind == "address"
    miss = to_shell_arg("locker://ghost.png")
    assert not miss.ok and "no such attached file" in miss.reason


def test_locker_guards_a_vanished_file(tmp_path, monkeypatch):
    _session_with(
        monkeypatch,
        attached_files=[{"name": "lost.png", "path": str(tmp_path / "lost.png"), "kind": "image"}],
    )
    arg = to_shell_arg("locker://lost.png")
    assert not arg.ok and "attached file is gone" in arg.reason
