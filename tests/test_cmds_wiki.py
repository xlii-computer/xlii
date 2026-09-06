"""``xlii wiki`` — the shell CLI, exercised by calling the cmd functions with argparse-style
namespaces. ``_xli_dir`` is monkeypatched to a tmp project so the tests never depend on cwd or
touch a real project registry."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xlii import wiki as W
from xlii.cmds import wiki as wcli


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(wcli, "_xli_dir", lambda: tmp_path)
    return tmp_path


def test_cli_parser_assembles_and_routes():
    from xlii.cli import build_parser

    ns = build_parser().parse_args(["wiki", "new", "arch"])
    assert ns.command == "wiki" and ns.wiki_cmd == "new" and ns.name == "arch"
    assert ns.func is wcli.cmd_new


def test_cli_new_list_verify_rm_lifecycle(project):
    assert wcli.cmd_new(SimpleNamespace(name="arch")) == 0
    assert W.page_exists(project, "arch")
    assert W.read_page(project, "arch").verified is False

    assert wcli.cmd_list(SimpleNamespace()) == 0

    assert wcli.cmd_verify(SimpleNamespace(name="arch")) == 0
    assert W.read_page(project, "arch").verified is True

    assert wcli.cmd_rm(SimpleNamespace(name="arch", yes=True)) == 0
    assert not W.page_exists(project, "arch")


def test_cli_rm_requires_confirmation(project):
    W.write_page(project, "arch", "body")
    assert wcli.cmd_rm(SimpleNamespace(name="arch", yes=False)) == 1   # refused without --yes
    assert W.page_exists(project, "arch")


def test_cli_new_rejects_bad_name(project):
    assert wcli.cmd_new(SimpleNamespace(name="bad/name")) == 1
    assert not W.page_exists(project, "bad/name")


def test_cli_outside_a_project_errors(monkeypatch):
    monkeypatch.setattr(wcli, "_xli_dir", lambda: None)
    assert wcli.cmd_list(SimpleNamespace()) == 1
    assert wcli.cmd_new(SimpleNamespace(name="x")) == 1
