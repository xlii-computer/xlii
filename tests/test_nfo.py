"""Unit tests for the /nfo project-status splash generator (xlii/repl_cmds/nfo.py)."""

from types import SimpleNamespace

from rich.console import Console

import xlii.wiki_author as wa
from xlii.repl_cmds import nfo


def _ctx(tmp_path, console=None):
    (tmp_path / ".xlii").mkdir(exist_ok=True)
    proj = SimpleNamespace(name="demo", project_root=tmp_path, xli_dir=tmp_path / ".xlii")
    state = SimpleNamespace(project=proj, journal=None, pool=None, cfg=None)
    return {"console": console or Console(record=True, width=80), "state": state, "project": proj}


def _mock_complete(monkeypatch, text="demo · main\n------\nNow: building /nfo"):
    monkeypatch.setattr(wa, "session_completer", lambda st, **k: (lambda msgs: text))


def test_registered_in_both_repls():
    from xlii.commands import find_repl_command
    from xlii.repl_cmds import register_all
    register_all()
    assert find_repl_command("/nfo", repl="code") is not None
    assert find_repl_command("/nfo", repl="chat") is not None


def test_generate_writes_project_splash(tmp_path, monkeypatch):
    _mock_complete(monkeypatch)
    nfo._cmd_nfo("/nfo", _ctx(tmp_path))
    target = tmp_path / ".xlii" / "splash.nfo"
    assert target.exists()
    assert target.read_text().startswith("demo · main")


def test_print_previews_without_writing(tmp_path, monkeypatch):
    _mock_complete(monkeypatch)
    con = Console(record=True, width=80)
    nfo._cmd_nfo("/nfo --print", _ctx(tmp_path, con))
    assert not (tmp_path / ".xlii" / "splash.nfo").exists()
    assert "Now: building /nfo" in con.export_text()


def test_clear_removes_target(tmp_path, monkeypatch):
    _mock_complete(monkeypatch)
    ctx = _ctx(tmp_path)
    nfo._cmd_nfo("/nfo", ctx)
    tgt = tmp_path / ".xlii" / "splash.nfo"
    assert tgt.exists()
    nfo._cmd_nfo("/nfo --clear", ctx)
    assert not tgt.exists()


def test_scope_flags_resolve_paths(tmp_path, monkeypatch):
    proj = SimpleNamespace(name="d", project_root=tmp_path, xli_dir=tmp_path / ".xlii")
    assert nfo._target_path(proj, "project") == tmp_path / ".xlii" / "splash.nfo"
    assert nfo._target_path(proj, "root") == tmp_path / "xlii.nfo"
    monkeypatch.setenv("XLII_CONFIG_DIR", str(tmp_path / "cfg"))
    assert nfo._target_path(proj, "global") == tmp_path / "cfg" / "splash.nfo"


def test_global_scope_writes_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_CONFIG_DIR", str(tmp_path / "cfg"))
    _mock_complete(monkeypatch, "x · y\n--\nNow: z")
    nfo._cmd_nfo("/nfo --global", _ctx(tmp_path))
    assert (tmp_path / "cfg" / "splash.nfo").exists()
    assert not (tmp_path / ".xlii" / "splash.nfo").exists()  # global scope, not the project file


def test_parse_flags_and_errors():
    _, err = nfo._parse("/nfo --bogus")
    assert err and "unknown flag" in err
    _, err = nfo._parse("/nfo --focus")
    assert err and "focus" in err
    opts, err = nfo._parse('/nfo --full --focus "the auth refactor" --root')
    assert err is None
    assert opts["full"] and opts["focus"] == "the auth refactor" and opts["scope"] == "root"


def test_clean_strips_fence_and_caps_lines():
    assert nfo._clean("```\nhi\nthere\n```") == "hi\nthere"
    big = "\n".join(str(i) for i in range(nfo._MAX_LINES + 50))
    assert len(nfo._clean(big).splitlines()) == nfo._MAX_LINES


def test_no_model_warns_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(wa, "session_completer", lambda st, **k: None)
    con = Console(record=True, width=80)
    nfo._cmd_nfo("/nfo", _ctx(tmp_path, con))
    assert "no model available" in con.export_text()
    assert not (tmp_path / ".xlii" / "splash.nfo").exists()
