"""Tests for /doc --refresh and the /doc-list 'edited on disk' staleness marker.

`/doc <name>` stores a content *snapshot* keyed by name; editing the underlying
doc mid-session leaves that snapshot stale until refreshed. `/doc --refresh`
re-reads `Doc(name)` and replaces the inlined copy in place.
"""

from types import SimpleNamespace

import xlii.doc
from xlii.commands import dispatch_repl_command
from xlii.repl_cmds import register_all
from tests.helpers import FakeConsole

register_all()


def _ctx(state):
    return {"console": FakeConsole(), "state": state, "command_scope": "code"}


def _write_doc(tmp_path, monkeypatch, name, body):
    monkeypatch.setattr(xlii.doc, "DOCS_DIR", tmp_path / "docs")
    d = xlii.doc.Doc(name)
    d.write(body)
    return d


def test_refresh_rereads_edited_doc(tmp_path, monkeypatch):
    d = _write_doc(tmp_path, monkeypatch, "conv", "v1 rules")
    state = SimpleNamespace(attached_docs=[])
    dispatch_repl_command("/doc conv", _ctx(state))
    assert state.attached_docs == [("conv", "v1 rules")]

    d.write("v2 rules — edited")                 # edit the underlying doc
    ctx = _ctx(state)
    dispatch_repl_command("/doc --refresh", ctx)
    assert state.attached_docs == [("conv", "v2 rules — edited")]
    assert "refreshed" in ctx["console"].text.lower()


def test_refresh_by_name_refreshes_only_that_one(tmp_path, monkeypatch):
    monkeypatch.setattr(xlii.doc, "DOCS_DIR", tmp_path / "docs")
    a, b = xlii.doc.Doc("a"), xlii.doc.Doc("b")
    a.write("a1"); b.write("b1")
    state = SimpleNamespace(attached_docs=[])
    dispatch_repl_command("/doc a", _ctx(state))
    dispatch_repl_command("/doc b", _ctx(state))

    a.write("a2"); b.write("b2")
    dispatch_repl_command("/doc --refresh a", _ctx(state))
    assert dict(state.attached_docs) == {"a": "a2", "b": "b1"}   # only a refreshed


def test_refresh_unchanged_reports_up_to_date(tmp_path, monkeypatch):
    _write_doc(tmp_path, monkeypatch, "conv", "same")
    state = SimpleNamespace(attached_docs=[])
    dispatch_repl_command("/doc conv", _ctx(state))
    ctx = _ctx(state)
    dispatch_repl_command("/doc --refresh", ctx)
    assert state.attached_docs == [("conv", "same")]
    assert "up to date" in ctx["console"].text.lower()


def test_refresh_keeps_cached_copy_when_source_gone(tmp_path, monkeypatch):
    d = _write_doc(tmp_path, monkeypatch, "conv", "body")
    state = SimpleNamespace(attached_docs=[])
    dispatch_repl_command("/doc conv", _ctx(state))
    d.path.unlink()                              # source doc deleted
    ctx = _ctx(state)
    dispatch_repl_command("/doc --refresh", ctx)
    assert state.attached_docs == [("conv", "body")]   # snapshot preserved
    assert "no longer exists" in ctx["console"].text.lower()


def test_refresh_unknown_name(tmp_path, monkeypatch):
    _write_doc(tmp_path, monkeypatch, "conv", "body")
    state = SimpleNamespace(attached_docs=[])
    dispatch_repl_command("/doc conv", _ctx(state))
    ctx = _ctx(state)
    dispatch_repl_command("/doc --refresh nope", ctx)
    assert "no doc attached named" in ctx["console"].text.lower()
    assert state.attached_docs == [("conv", "body")]


def test_list_marks_edited_doc_stale(tmp_path, monkeypatch):
    d = _write_doc(tmp_path, monkeypatch, "conv", "v1")
    state = SimpleNamespace(attached_docs=[])
    dispatch_repl_command("/doc conv", _ctx(state))

    fresh = _ctx(state)
    dispatch_repl_command("/doc", fresh)
    assert "edited on disk" not in fresh["console"].text   # no marker while in sync

    d.write("v2")                                # edit underlying file
    stale = _ctx(state)
    dispatch_repl_command("/doc", stale)
    assert "edited on disk" in stale["console"].text       # marker appears
