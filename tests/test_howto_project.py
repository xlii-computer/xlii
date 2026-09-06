"""/howto project — per-project help topic overrides (howto corpus Phase 3)."""

from __future__ import annotations

import io
from types import SimpleNamespace

from rich.console import Console

from xlii.repl_cmds import howto, register_all

register_all()


def _ctx(tmp_path):
    xli = tmp_path / ".xlii"
    xli.mkdir(parents=True, exist_ok=True)
    sio = io.StringIO()
    ctx = {
        "console": Console(file=sio, force_terminal=False),
        "state": SimpleNamespace(attached_docs=[], howto_mode=False,
                                 attach_doc=lambda *a, **k: None,
                                 detach_doc=lambda *a, **k: False),
        "project": SimpleNamespace(xli_dir=xli),
        "command_scope": "code",
    }
    return ctx, sio, xli


def _topics_dir(xli):
    return xli / "help" / "topics"


def test_list_empty_nudges(tmp_path):
    ctx, sio, _ = _ctx(tmp_path)
    assert howto._howto_handler("/howto project", ctx) is True
    assert "no project help topics" in sio.getvalue()


def test_new_scaffolds_override_file(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(howto, "open_for_edit", lambda p: opened.append(p) or 0,
                        raising=False)
    # patch the lazily-imported editor too
    import xlii.editor
    monkeypatch.setattr(xlii.editor, "open_for_edit", lambda p: opened.append(p) or 0)

    ctx, sio, xli = _ctx(tmp_path)
    assert howto._howto_handler("/howto project new deploy", ctx) is True
    f = _topics_dir(xli) / "deploy.md"
    assert f.exists()
    assert "# deploy" in f.read_text()
    assert "overrides" in f.read_text()
    assert opened and opened[-1] == f          # handed to $EDITOR
    assert "created" in sio.getvalue()


def test_new_rejects_bad_name(tmp_path):
    ctx, sio, xli = _ctx(tmp_path)
    assert howto._howto_handler("/howto project new BAD NAME", ctx) is True
    assert "lowercase" in sio.getvalue()
    assert not _topics_dir(xli).exists()


def test_list_shows_authored_topics(tmp_path, monkeypatch):
    import xlii.editor
    monkeypatch.setattr(xlii.editor, "open_for_edit", lambda p: 0)
    ctx, _, xli = _ctx(tmp_path)
    howto._howto_handler("/howto project new deploy", ctx)

    ctx2, sio2, _ = _ctx(tmp_path)
    ctx2["project"] = ctx["project"]
    assert howto._howto_handler("/howto project list", ctx2) is True
    assert "deploy" in sio2.getvalue()


def test_edit_missing_creates_then_opens(tmp_path, monkeypatch):
    import xlii.editor
    opened = []
    monkeypatch.setattr(xlii.editor, "open_for_edit", lambda p: opened.append(p) or 0)
    ctx, sio, xli = _ctx(tmp_path)
    assert howto._howto_handler("/howto project edit local-setup", ctx) is True
    assert (_topics_dir(xli) / "local-setup.md").exists()
    assert opened


def test_rm_removes_and_falls_back(tmp_path, monkeypatch):
    import xlii.editor
    monkeypatch.setattr(xlii.editor, "open_for_edit", lambda p: 0)
    ctx, _, xli = _ctx(tmp_path)
    howto._howto_handler("/howto project new deploy", ctx)
    f = _topics_dir(xli) / "deploy.md"
    assert f.exists()

    ctx2, sio2, _ = _ctx(tmp_path)
    ctx2["project"] = ctx["project"]
    assert howto._howto_handler("/howto project rm deploy", ctx2) is True
    assert not f.exists()
    assert "removed" in sio2.getvalue()


def test_override_wins_in_the_read_path(tmp_path):
    # The whole point: help_corpus reads project override before the bundle.
    from xlii.help_corpus import load_manifest, load_topic_body

    ctx, _, xli = _ctx(tmp_path)
    manifest = load_manifest()
    # pick a real bundled topic id to shadow
    tid = next(iter(manifest.topic_ids()))
    d = _topics_dir(xli)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{tid}.md").write_text("PROJECT OVERRIDE BODY")

    body, source = load_topic_body(manifest, tid, xli_dir=xli)
    assert "PROJECT OVERRIDE BODY" in body
    assert "project" in source.lower()
