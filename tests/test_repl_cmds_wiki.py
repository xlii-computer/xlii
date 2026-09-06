"""/wiki — the REPL authoring command, driven headless through its handler.

A fake console records output; a fake state carries the project's xli_dir (and, for the AI verbs,
a stubbed completer via monkeypatching ``wiki_author.session_completer``). ``open_for_edit`` is
neutralised so ``new``/``edit`` never spawn a real editor."""

from __future__ import annotations

import contextlib
from types import SimpleNamespace

import pytest

from xlii import wiki as W
from xlii import wiki_author
from xlii.repl_cmds import wiki as wcmd


class FakeConsole:
    def __init__(self):
        self.out: list[str] = []

    def print(self, *a, **k):
        self.out.append(" ".join(str(x) for x in a))

    def status(self, *a, **k):
        return contextlib.nullcontext()

    @property
    def text(self) -> str:
        return "\n".join(self.out)


@pytest.fixture(autouse=True)
def _no_editor(monkeypatch):
    # new/edit call xlii.editor.open_for_edit — make it a no-op so tests never block on $EDITOR.
    import xlii.editor

    monkeypatch.setattr(xlii.editor, "open_for_edit", lambda path: 0)


def _run(line, xli_dir, **state_kw):
    state = SimpleNamespace(project=SimpleNamespace(xli_dir=xli_dir), pool=None, cfg=None, **state_kw)
    console = FakeConsole()
    wcmd._handler(line, {"state": state, "console": console})
    return console.text


def test_new_then_list_shows_unverified(tmp_path):
    _run("/wiki new arch", tmp_path)
    assert W.page_exists(tmp_path, "arch")
    out = _run("/wiki list", tmp_path)
    assert "arch" in out and "?" in out          # trust marker for unverified


def test_manual_promote_flips_marker(tmp_path):
    _run("/wiki new arch", tmp_path)
    out = _run("/wiki verify arch --promote", tmp_path)
    assert "marked verified" in out
    assert W.read_page(tmp_path, "arch").verified is True


def test_show_and_rm(tmp_path):
    W.write_page(tmp_path, "arch", "# Arch\n\nthe body line", sources=["conv://p/t1"])
    shown = _run("/wiki show arch", tmp_path)
    assert "the body line" in shown and "conv://p/t1" in shown
    out = _run("/wiki rm arch", tmp_path)
    assert "removed" in out and not W.page_exists(tmp_path, "arch")


def test_distill_without_model_reports_gracefully(tmp_path):
    out = _run(f"/wiki distill x file://{tmp_path}", tmp_path)   # pool/cfg are None → no completer
    assert "no model available" in out


def test_distill_with_stubbed_completer_writes_unverified(tmp_path, monkeypatch):
    src = tmp_path / "n.txt"
    src.write_text("a recorded fact")
    monkeypatch.setattr(
        wiki_author, "session_completer",
        lambda state: (lambda messages: "# X\n\na recorded fact"),
    )
    out = _run(f"/wiki distill x file://{src}", tmp_path)
    assert "wrote" in out
    page = W.read_page(tmp_path, "x")
    assert page.verified is False and page.sources == [f"file://{src}"]


def test_verify_with_stubbed_completer_promotes(tmp_path, monkeypatch):
    src = tmp_path / "n.txt"
    src.write_text("fact")
    W.write_page(tmp_path, "k", "# K\n\nfact", sources=[f"file://{src}"])
    monkeypatch.setattr(
        wiki_author, "session_completer",
        lambda state: (lambda messages: "VERIFIED\nall good"),
    )
    out = _run("/wiki verify k", tmp_path)
    assert "verified" in out.lower()
    assert W.read_page(tmp_path, "k").verified is True


def test_outside_a_project_is_a_gentle_nudge(tmp_path):
    state = SimpleNamespace(project=None, pool=None, cfg=None)
    console = FakeConsole()
    wcmd._handler("/wiki list", {"state": state, "console": console})
    assert "project" in console.text.lower()


def test_bad_subcommand_prints_usage(tmp_path):
    out = _run("/wiki frobnicate", tmp_path)
    assert "usage" in out.lower()
