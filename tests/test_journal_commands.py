"""JRN-1 slash surface: /journal (status + code/wiki toggles).

`/journal` is code-REPL only and toggles the SILENT background recorder + the
self-building wiki. The old `/askjo` advisor voice is retired: asking about the
project is now `/mojo` (iXaac + fused journal/wiki — see tests/test_mojo.py), and
`/askjo` survives only as a hidden alias of `/mojo`.
"""

from __future__ import annotations

from types import SimpleNamespace

import xlii.journal as J
from xlii.commands import find_repl_command
from xlii.config import ProjectConfig
from xlii.repl_cmds import register_all
from xlii.repl_cmds.journal import _journal_handler
from xlii.sync import init_project
from tests.helpers import FakeConsole, make_cfg

register_all()


class _FakeColls:
    def create(self, name, field_definitions=None):
        return SimpleNamespace(collection_id="jc")

    def upload_document(self, collection_id, name, data, fields=None):
        return SimpleNamespace(file_metadata=SimpleNamespace(file_id="f"))

    def search(self, **k):
        return SimpleNamespace(results=[])


class _Pool:
    def __init__(self):
        self._c = SimpleNamespace(xai=SimpleNamespace(collections=_FakeColls()), chat=None)

    def primary(self):
        return self._c

    def journal_client(self):
        return None


def _ctx(tmp_path, *, code_on=False):
    init_project(None, tmp_path, name="proj", existing_collection_id="cmain")
    proj = ProjectConfig.load(tmp_path)
    j = J.ProjectJournal(project=proj, pool=_Pool(), cfg=make_cfg(), code_on=code_on, batch_size=5)
    return {"console": FakeConsole(), "state": SimpleNamespace(journal=j, project=proj)}, j


def test_journal_is_code_only_and_askjo_is_a_mojo_alias():
    # /journal (the recorder toggle) stays code-only.
    assert find_repl_command("/journal", "code") is not None
    assert find_repl_command("/journal", "chat") is None
    # /askjo is retired as its own command — it now resolves to the /mojo verb
    # (which lives in both code and chat).
    ak = find_repl_command("/askjo", "code")
    assert ak is not None and ak.name == "mojo"
    assert find_repl_command("/askjo", "chat").name == "mojo"


def test_journal_bare_shows_status(tmp_path):
    ctx, _j = _ctx(tmp_path)
    _journal_handler("/journal", ctx)
    text = ctx["console"].text
    assert "Project Shadow" in text and "code journal" in text


def test_journal_code_on_off_auto(tmp_path):
    ctx, j = _ctx(tmp_path)
    _journal_handler("/journal --code-on", ctx)
    assert j.is_recording() is True
    _journal_handler("/journal --code-off", ctx)
    assert j.is_recording() is False
    _journal_handler("/journal --code-auto", ctx)
    assert j.is_recording() is True
    assert J.read_journal_auto(ctx["state"].project) is True


def test_journal_chat_flag_points_to_jrn2(tmp_path):
    ctx, _j = _ctx(tmp_path)
    _journal_handler("/journal --chat-on", ctx)
    assert any("JRN-2" in line or "xlii journal install" in line
               for line in ctx["console"].lines)


def test_journal_unknown_flag_warns(tmp_path):
    ctx, _j = _ctx(tmp_path)
    _journal_handler("/journal --bogus", ctx)
    assert any("unknown flag" in line for line in ctx["console"].lines)
