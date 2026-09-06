"""Unit tests for the uniform addressing layer (xlii.addressing)."""

from __future__ import annotations

import os

from xlii.addressing import Address, Resolution, providers, resolve, vfs_list, vfs_read


def test_parse_sftp_absolute_double_slash():
    a = Address.parse("sftp://xliiec2//home/admin/serve-sandbox")
    assert a.scheme == "sftp"
    assert a.key == "xliiec2"
    assert a.subpath == "/home/admin/serve-sandbox"


def test_parse_via_nested_inner_address():
    a = Address.parse("via://xliiec2/sftp://appbox/srv/apps/fuel.xlii-code.com")
    assert a.scheme == "via"
    assert a.key == "xliiec2"
    assert a.subpath == "sftp://appbox/srv/apps/fuel.xlii-code.com"


def test_parse_explicit_scheme():
    a = Address.parse("project://foo/src/x")
    assert a.scheme == "project"
    assert a.target == "foo/src/x"
    assert a.key == "foo"
    assert a.subpath == "src/x"


def test_parse_query():
    a = Address.parse("collection://abc?match=sub&n=3")
    assert a.scheme == "collection"
    assert a.target == "abc"
    assert a.query == {"match": "sub", "n": "3"}


# --- #anchor fragment (pane-layer-spec primitive) ----------------------------


def test_parse_anchor_basic():
    a = Address.parse("conv://nick/turn-3#mark:thesis")
    assert a.scheme == "conv"
    assert a.target == "nick/turn-3"
    assert a.anchor == "mark:thesis"
    assert a.key == "nick" and a.subpath == "turn-3"  # anchor doesn't disturb key/subpath


def test_parse_anchor_line_span():
    a = Address.parse("file://notes.md#L42-51")
    assert a.target == "notes.md" and a.anchor == "L42-51"


def test_parse_query_and_anchor_together():
    a = Address.parse("collection://abc?match=sub&n=3#frag")
    assert a.query == {"match": "sub", "n": "3"}
    assert a.anchor == "frag"
    assert a.target == "abc"


def test_parse_no_anchor_is_empty():
    assert Address.parse("file://x").anchor == ""


def test_anchor_round_trips_through_str():
    for s in ("conv://a/b#mark:x", "file://notes.md#L1-9", "collection://abc?match=sub#frag"):
        assert str(Address.parse(s)) == s


def test_str_without_query_or_anchor_is_unchanged():
    assert str(Address.parse("file:///home/x")) == "file:///home/x"


def test_bare_path_with_anchor_routes_to_file():
    a = Address.parse("./notes.md#L5")
    assert a.scheme == "file" and a.target == "./notes.md" and a.anchor == "L5"


def test_bare_pathlike_routes_to_file():
    assert Address.parse("./x").scheme == "file"
    assert Address.parse("/abs/x").scheme == "file"
    assert Address.parse("~/x").scheme == "file"


def test_bare_name_uses_default_scheme():
    assert Address.parse("myproj", default_scheme="project").scheme == "project"


def test_bare_name_no_default_has_empty_scheme():
    assert Address.parse("notapath_name").scheme == ""


def test_builtin_providers_registered():
    assert {"file", "project", "persona", "docs", "skills", "mark", "jobs", "locker", "via"} <= set(providers())


def test_via_list_wraps_inner_addresses(monkeypatch):
    from xlii.addressing.builtins import via as V

    inner = "sftp://appbox/srv/apps/fuel.xlii-code.com"

    def fake_hop(node, op, addr):
        assert node == "xliiec2" and op == "list" and addr == inner
        return {"ok": True, "nodes": [
            {"address": inner + "/index.html", "name": "index.html",
             "kind": "leaf", "size": 12},
            {"address": inner + "/assets", "name": "assets",
             "kind": "container", "size": None},
        ]}

    monkeypatch.setattr(V, "hop_op", fake_hop)
    nodes = vfs_list("via://xliiec2/sftp://appbox/srv/apps/fuel.xlii-code.com")
    names = [n.name for n in nodes]
    assert names[0] == "assets"  # containers first
    assert nodes[0].address == "via://xliiec2/sftp://appbox/srv/apps/fuel.xlii-code.com/assets"
    assert "index.html" in names
    html = next(n for n in nodes if n.name == "index.html")
    assert html.address.endswith("/index.html")
    assert html.address.startswith("via://xliiec2/")


def test_via_read_decodes_b64(monkeypatch):
    import base64
    from xlii.addressing.builtins import via as V

    def fake_hop(node, op, addr):
        assert op == "read"
        return {"ok": True, "b64": base64.b64encode(b"<html>").decode("ascii")}

    monkeypatch.setattr(V, "hop_op", fake_hop)
    assert vfs_read("via://xliiec2/sftp://appbox/srv/apps/fuel.xlii-code.com/index.html") == b"<html>"


def test_via_write_hops_b64(monkeypatch):
    import base64
    from xlii.addressing import supports_write, vfs_write
    from xlii.addressing.builtins import via as V

    seen: dict[str, object] = {}

    def fake_hop(node, op, addr, extra=None):
        seen.update(node=node, op=op, addr=addr, extra=extra or {})
        return {"ok": True}

    monkeypatch.setattr(V, "hop_op", fake_hop)
    assert supports_write("via")
    vfs_write("via://xliiec2/sftp://appbox/srv/apps/fuel.xlii-code.com/index.html", b"<h1>")
    assert seen["op"] == "write"
    assert seen["node"] == "xliiec2"
    assert seen["addr"] == "sftp://appbox/srv/apps/fuel.xlii-code.com/index.html"
    assert base64.b64decode(seen["extra"]["b64"]) == b"<h1>"


def test_resolve_unknown_scheme():
    r = resolve("nope://x")
    assert not r.ok
    assert "no provider" in r.reason


def test_resolve_no_scheme_no_default():
    r = resolve("notapath_token")
    assert not r.ok


def test_file_provider_hit_and_miss(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    r = resolve(f"file://{f}")
    assert r.ok and r.kind == "file"
    assert r.path == f.resolve()
    r2 = resolve(f"file://{tmp_path}/missing")
    assert not r2.ok
    assert r2.reason == "no such path"


def test_project_provider_non_project_dir(tmp_path):
    r = resolve(f"project://{tmp_path}")
    assert r.kind == "project"
    assert not r.ok  # a bare tmp dir is not an initialized xlii project
    assert r.path == tmp_path.resolve()
    assert r.reason == "not an xlii project"


def test_project_provider_cwd(tmp_path):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        r = resolve("project://.")
        assert r.kind == "project"
        assert r.path == tmp_path.resolve()
    finally:
        os.chdir(cwd)


def test_persona_provider_invalid_name():
    r = resolve("persona://__nope__")
    assert not r.ok
    assert "invalid" in r.reason


def test_resolution_is_frozen():
    r = Resolution(ok=True, address=Address.parse("file://."))
    try:
        r.ok = False  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("Resolution should be frozen")


# --- VFS surface (V2) ---------------------------------------------------------


def test_supports_vfs():
    from xlii.addressing import supports_vfs

    assert supports_vfs("file")  # FileProvider implements stat/list/read
    assert supports_vfs("project")  # browseable: project root + subpath
    assert supports_vfs("conv")  # browseable: the project's turns dir
    assert supports_vfs("persona")  # now browseable — list personas / read a prompt (F6 doorway)
    assert not supports_vfs("nope")


def test_vfs_list_orders_containers_first(tmp_path):
    from xlii.addressing import vfs_list

    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "sub").mkdir()
    (tmp_path / "B.txt").write_text("x")
    nodes = vfs_list(f"file://{tmp_path}")
    assert {n.name: n.kind for n in nodes} == {
        "a.txt": "leaf",
        "B.txt": "leaf",
        "sub": "container",
    }
    assert nodes[0].kind == "container"  # dirs first
    assert [n.name for n in nodes[1:]] == ["a.txt", "B.txt"]  # case-insensitive name sort


def test_vfs_read_returns_bytes(tmp_path):
    from xlii.addressing import vfs_read

    (tmp_path / "f").write_text("hello")
    assert vfs_read(f"file://{tmp_path}/f") == b"hello"


def test_vfs_stat(tmp_path):
    from xlii.addressing import vfs_stat

    f = tmp_path / "f"
    f.write_text("xy")
    n = vfs_stat(f"file://{f}")
    assert n.kind == "leaf" and n.size == 2


def test_vfs_unsupported_scheme_raises():
    import pytest

    from xlii.addressing import vfs_list

    with pytest.raises(NotImplementedError):
        vfs_list("nope://x")  # no provider registered for this scheme


def test_project_vfs_browses_cwd_project(tmp_path):
    import os

    from xlii.addressing import vfs_list, vfs_read

    (tmp_path / "a.py").write_text("x")
    (tmp_path / "sub").mkdir()
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        nodes = vfs_list("project://.")
        assert {n.name for n in nodes} == {"a.py", "sub"}
        # navigation stays in the project:// scheme
        assert all(n.address.startswith("project://") for n in nodes)
        assert vfs_read("project://./a.py") == b"x"
    finally:
        os.chdir(cwd)


def test_conv_vfs_lists_turns(tmp_path):
    import os

    from xlii.addressing import resolve, vfs_list, vfs_read

    turns = tmp_path / ".xlii" / "turns"
    turns.mkdir(parents=True)
    (turns / "20260101T000000Z-0001.md").write_text("# turn one")
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        assert resolve("conv://.").ok
        nodes = vfs_list("conv://.")
        assert [n.name for n in nodes] == ["20260101T000000Z-0001.md"]
        assert vfs_read("conv://./20260101T000000Z-0001.md") == b"# turn one"
    finally:
        os.chdir(cwd)


def test_conv_vfs_inflight_node_from_ambient_conversation(tmp_path):
    """Phase 6: ConvProvider lists a real __inflight__.md Node from Conversation."""
    import os

    from xlii.active_session import set_active_session
    from xlii.addressing import vfs_exists, vfs_list, vfs_read
    from xlii.conversation import INFLIGHT_LEAF, Conversation, ensure_conversation

    turns = tmp_path / ".xlii" / "turns"
    turns.mkdir(parents=True)
    (turns / "20260101T000000Z-0001.md").write_text("# turn one")
    state = type("S", (), {})()
    state.conversation = Conversation(turns_dir=turns)
    state.profile = None
    ensure_conversation(state)
    state.conversation.start_turn("live question")
    state.conversation.append_chunk("partial answer")
    prev = set_active_session(state)
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        names = [n.name for n in vfs_list("conv://.")]
        assert INFLIGHT_LEAF in names
        live = next(n for n in vfs_list("conv://.") if n.name == INFLIGHT_LEAF)
        assert live.extra.get("live") is True
        assert vfs_exists(f"conv://./{INFLIGHT_LEAF}")
        body = vfs_read(f"conv://./{INFLIGHT_LEAF}").decode()
        assert "live question" in body and "partial answer" in body
        state.conversation.complete_turn("done")
        assert INFLIGHT_LEAF not in [n.name for n in vfs_list("conv://.")]
    finally:
        os.chdir(cwd)
        set_active_session(prev)


def test_supports_write():
    from xlii.addressing import supports_write

    assert supports_write("file")
    assert supports_write("project")
    assert supports_write("via")
    assert not supports_write("conv")  # browseable but read-only
    assert not supports_write("persona")


def test_vfs_write_read_exists(tmp_path):
    from xlii.addressing import vfs_exists, vfs_read, vfs_write

    addr = f"file://{tmp_path}/new.txt"
    assert not vfs_exists(addr)
    vfs_write(addr, b"data")
    assert vfs_exists(addr)
    assert vfs_read(addr) == b"data"


def test_vfs_write_unsupported_raises():
    import pytest

    from xlii.addressing import vfs_write

    with pytest.raises(NotImplementedError):
        vfs_write("conv://./x", b"nope")  # conv is read-only


def test_vfs_mkdir_delete(tmp_path):
    from xlii.addressing import vfs_delete, vfs_exists, vfs_mkdir, vfs_write

    d = f"file://{tmp_path}/d"
    vfs_mkdir(d)
    assert vfs_exists(d)
    vfs_write(f"file://{tmp_path}/d/a.txt", b"x")
    vfs_delete(f"file://{tmp_path}/d/a.txt")
    assert not vfs_exists(f"file://{tmp_path}/d/a.txt")
    vfs_delete(d)  # now empty
    assert not vfs_exists(d)


def test_vfs_delete_recursive(tmp_path):
    import pytest

    from xlii.addressing import vfs_delete, vfs_exists, vfs_mkdir, vfs_write

    vfs_mkdir(f"file://{tmp_path}/d")
    vfs_write(f"file://{tmp_path}/d/a.txt", b"x")
    with pytest.raises(OSError):
        vfs_delete(f"file://{tmp_path}/d")  # non-empty without recursive
    vfs_delete(f"file://{tmp_path}/d", recursive=True)
    assert not vfs_exists(f"file://{tmp_path}/d")


def test_config_provider_browses_project_json(tmp_path):
    import json
    import os

    from xlii.addressing import supports_vfs, supports_write, vfs_list, vfs_read

    cfg = tmp_path / ".xlii"
    cfg.mkdir()
    (cfg / "project.json").write_text(json.dumps({"name": "demo", "model": "grok", "nested": {"a": 1}}))
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        assert supports_vfs("config") and not supports_write("config")  # browseable, read-only
        names = {n.name: n.kind for n in vfs_list("config://project")}
        assert names == {"name": "leaf", "model": "leaf", "nested": "container"}
        assert vfs_read("config://project/model") == b"grok\n"
        assert vfs_read("config://project/nested/a") == b"1\n"
    finally:
        os.chdir(cwd)


def test_config_provider_redacts_plaintext_keys(tmp_path, monkeypatch):
    import json

    import xlii.config as cfg
    from xlii.addressing import vfs_read

    gf = tmp_path / "config.json"
    gf.write_text(json.dumps({
        "keys": [{"label": "p", "api_key": "xai-SECRET-should-not-leak"}],
        "model": "grok",
    }))
    monkeypatch.setattr(cfg, "GLOBAL_CONFIG_FILE", gf)
    raw = vfs_read("config://global").decode()
    assert "xai-SECRET-should-not-leak" not in raw
    assert "[redacted]" in raw
    assert vfs_read("config://global/keys/0/api_key") == b"[redacted]\n"


def test_xlii_provider_is_browseable_readonly():
    from xlii.addressing import supports_vfs, supports_write

    assert supports_vfs("xlii")  # the kernel browses itself
    assert not supports_write("xlii")  # but isn't edited through cp


def test_xlii_root_lists_schemes_and_version():
    from xlii.addressing import vfs_list

    names = {n.name: n.kind for n in vfs_list("xlii://")}
    assert names == {"schemes": "container", "version": "leaf"}


def test_xlii_schemes_lists_every_registered_provider_reflexively():
    from xlii.addressing import providers, vfs_list

    listed = {n.name for n in vfs_list("xlii://schemes")}
    assert listed == set(providers())  # every provider mounted...
    assert "xlii" in listed  # ...including the kernel itself
    assert all(n.kind == "leaf" for n in vfs_list("xlii://schemes"))


def test_xlii_version_reads_running_version():
    from xlii import __version__
    from xlii.addressing import vfs_read

    assert vfs_read("xlii://version") == (__version__ + "\n").encode()


def test_xlii_scheme_leaf_reports_capabilities():
    from xlii.addressing import vfs_read

    caps = vfs_read("xlii://schemes/file").decode()
    assert "scheme: file" in caps
    assert "browseable: yes" in caps
    assert "writable: yes" in caps
    conv_caps = vfs_read("xlii://schemes/conv").decode()
    assert "browseable: yes" in conv_caps and "writable: no" in conv_caps


def test_xlii_container_read_and_missing_address():
    import pytest

    from xlii.addressing import resolve, vfs_read

    with pytest.raises(IsADirectoryError):
        vfs_read("xlii://schemes")  # a container isn't catable
    assert not resolve("xlii://nope").ok
    with pytest.raises(FileNotFoundError):
        vfs_read("xlii://schemes/__no_such_scheme__")


# --- docs:// provider (Phase 1 doorway) --------------------------------------


def test_docs_provider_lists_and_reads(tmp_path, monkeypatch):
    import xlii.doc as docmod

    monkeypatch.setattr(docmod, "DOCS_DIR", tmp_path)
    (tmp_path / "conventions.md").write_text("# Conventions\nbe kind")
    (tmp_path / "glossary.md").write_text("terms")

    from xlii.addressing import classify, supports_vfs, supports_write, vfs_list, vfs_read, vfs_stat

    assert supports_vfs("docs") and not supports_write("docs")  # browseable, read-only
    nodes = vfs_list("docs://")
    assert {n.name: n.kind for n in nodes} == {"conventions": "leaf", "glossary": "leaf"}
    assert all(classify(n) == "doc" for n in nodes)  # bucket under the docs chip
    assert vfs_read("docs://conventions").decode().startswith("# Conventions")
    assert vfs_stat("docs://").kind == "container"


def test_docs_provider_missing_doc():
    from xlii.addressing import resolve

    r = resolve("docs://__no_such_doc__")
    assert not r.ok and "no doc" in r.reason


def test_skills_provider_lists_and_reads(monkeypatch):
    from types import SimpleNamespace

    fake = {
        "deploy": SimpleNamespace(name="deploy", short_description="ship it", description="Full deploy."),
        "test": SimpleNamespace(name="test", short_description="run tests", description="Full test."),
    }
    monkeypatch.setattr("xlii.skills.load_skills", lambda *a, **k: fake)
    from xlii.addressing import classify, supports_vfs, supports_write, vfs_list, vfs_read

    assert supports_vfs("skills") and not supports_write("skills")
    nodes = vfs_list("skills://")
    assert {n.name for n in nodes} == {"deploy", "test"}
    assert all(classify(n) == "skill" for n in nodes)
    assert vfs_read("skills://deploy").decode() == "Full deploy."


def test_persona_provider_is_browseable(tmp_path, monkeypatch):
    import xlii.persona as pmod

    monkeypatch.setattr(pmod, "PERSONAS_DIR", tmp_path)
    (tmp_path / "ada.md").write_text("You are Ada.")
    (tmp_path / "bob.md").write_text("You are Bob.")
    from xlii.addressing import classify, supports_vfs, vfs_list, vfs_read

    assert supports_vfs("persona")
    nodes = vfs_list("persona://")
    assert {n.name for n in nodes} == {"ada", "bob"}
    assert all(classify(n) == "persona" for n in nodes)
    assert vfs_read("persona://ada").decode() == "You are Ada."


# --- mark:// provider (Phase 2c doorway) — over the ambient session -----------


def _session_with_turns(turns_dir):
    """A minimal fake REPLState whose profile.memory.turns_dir is `turns_dir`."""
    from types import SimpleNamespace

    return SimpleNamespace(profile=SimpleNamespace(memory=SimpleNamespace(turns_dir=turns_dir)))


def test_mark_provider_lists_and_reads(tmp_path, monkeypatch):
    from xlii import active_session
    from xlii.addressing import classify, supports_vfs, supports_write, vfs_list, vfs_read, vfs_stat
    from xlii.transcript import mark_last_turn, write_turn

    td = tmp_path / "turns"
    td.mkdir()
    write_turn(td, "what is the thesis?", "the thesis is X.")
    assert mark_last_turn(td, "thesis")

    monkeypatch.setattr(active_session, "_ACTIVE", _session_with_turns(td))
    assert supports_vfs("mark") and not supports_write("mark")  # browseable, read-only
    assert vfs_stat("mark://").kind == "container"
    nodes = vfs_list("mark://")
    assert [n.name for n in nodes] == ["thesis"]
    assert all(classify(n) == "mark" for n in nodes)  # bucket under the [marks] chip
    body = vfs_read("mark://thesis").decode()
    assert "mark: thesis" in body and "the thesis is X." in body


def test_mark_provider_empty_without_session(monkeypatch):
    from xlii import active_session
    from xlii.addressing import resolve, vfs_list

    monkeypatch.setattr(active_session, "_ACTIVE", None)
    assert vfs_list("mark://") == []  # no session → no marks, no raise
    r = resolve("mark://ghost")
    assert not r.ok and "no mark" in r.reason


# --- jobs:// provider (Phase 2d doorway) — over the ambient session -----------


def _session_with_registry():
    from types import SimpleNamespace

    from xlii.jobs import JobRegistry

    return SimpleNamespace(job_registry=JobRegistry(max_workers=2))


def test_jobs_provider_lists_and_reads(monkeypatch):
    from xlii import active_session
    from xlii.addressing import classify, supports_vfs, supports_write, vfs_list, vfs_read, vfs_stat

    state = _session_with_registry()
    jid = state.job_registry.dispatch("task", "compute", lambda: "42", notify=False)
    state.job_registry.wait(jid, timeout=5)

    monkeypatch.setattr(active_session, "_ACTIVE", state)
    assert supports_vfs("jobs") and not supports_write("jobs")
    assert vfs_stat("jobs://").kind == "container"
    nodes = vfs_list("jobs://")
    assert len(nodes) == 1 and jid in nodes[0].name
    assert all(classify(n) == "job" for n in nodes)  # bucket under the [jobs] chip
    body = vfs_read(f"jobs://{jid}").decode()
    assert jid in body and "compute" in body and "42" in body


def test_jobs_provider_empty_without_session(monkeypatch):
    from xlii import active_session
    from xlii.addressing import resolve, vfs_list

    monkeypatch.setattr(active_session, "_ACTIVE", None)
    assert vfs_list("jobs://") == []
    r = resolve("jobs://t999")
    assert not r.ok and "no job" in r.reason


# --- locker:// provider (Phase 3 images doorway) — over the ambient session ---


def test_locker_provider_lists_attached_files(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from xlii import active_session
    from xlii.addressing import classify, supports_vfs, supports_write, vfs_list, vfs_read, vfs_stat

    img = tmp_path / "cat.png"
    img.write_bytes(b"\x89PNG...")
    files = [{"name": "cat.png", "path": str(img), "kind": "image", "enabled": True}]
    monkeypatch.setattr(active_session, "_ACTIVE", SimpleNamespace(attached_files=files))

    assert supports_vfs("locker") and not supports_write("locker")
    assert vfs_stat("locker://").kind == "container"
    nodes = vfs_list("locker://")
    assert [n.name for n in nodes] == ["cat.png"]
    assert all(classify(n) == "image" for n in nodes)   # bucket under the [images] chip
    assert vfs_read("locker://cat.png") == b"\x89PNG..."


def test_locker_provider_empty_without_session(monkeypatch):
    from xlii import active_session
    from xlii.addressing import resolve, vfs_list

    monkeypatch.setattr(active_session, "_ACTIVE", None)
    assert vfs_list("locker://") == []
    r = resolve("locker://ghost.png")
    assert not r.ok and "no attached file" in r.reason
