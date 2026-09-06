"""Tests for the in-session /edit dispatcher."""

from types import SimpleNamespace

import xlii.persona
import xlii.repl_cmds.chat as edit_mod
from xlii.commands import dispatch_repl_command, find_repl_command
from xlii.repl_cmds import register_all
from tests.helpers import FakeConsole

register_all()


def _ctx(command_scope="code", persona=None):
    return {
        "console": FakeConsole(),
        "state": SimpleNamespace(persona=persona),
        "persona": persona,
        "command_scope": command_scope,
    }


def test_edit_is_cross_repl():
    assert find_repl_command("/edit", "code") is not None
    assert find_repl_command("/edit", "chat") is not None


def test_edit_id_creates_and_opens_persona(tmp_path, monkeypatch):
    monkeypatch.setattr(xlii.persona, "PERSONAS_DIR", tmp_path / "personas")
    opened = []
    monkeypatch.setattr(edit_mod, "open_in_editor", lambda path: opened.append(path) or 0)

    ctx = _ctx("code")
    assert dispatch_repl_command("/edit --id bob", ctx) is True

    prompt = tmp_path / "personas" / "bob.md"
    assert prompt.exists()
    assert opened == [prompt]
    assert "/chat --id bob" in ctx["console"].text


def test_edit_id_new_refuses_existing_persona(tmp_path, monkeypatch):
    monkeypatch.setattr(xlii.persona, "PERSONAS_DIR", tmp_path / "personas")
    xlii.persona.create_persona("bob")
    opened = []
    monkeypatch.setattr(edit_mod, "open_in_editor", lambda path: opened.append(path) or 0)

    ctx = _ctx("code")
    assert dispatch_repl_command("/edit --id bob --new", ctx) is True

    assert opened == []
    assert "already exists" in ctx["console"].text


def test_bare_edit_requires_persona_outside_chat(tmp_path, monkeypatch):
    monkeypatch.setattr(xlii.persona, "PERSONAS_DIR", tmp_path / "personas")
    monkeypatch.setattr(edit_mod, "open_in_editor", lambda path: 0)

    ctx = _ctx("code")
    assert dispatch_repl_command("/edit", ctx) is True

    assert "usage: /edit --id" in ctx["console"].text


# --------------------------------------------------------------------------- #
#  Phase 2 — /edit --file (project files, jailed)
# --------------------------------------------------------------------------- #

def _file_ctx(tmp_path, *, shell_cwd=None):
    return {
        "console": FakeConsole(),
        "state": SimpleNamespace(
            persona=None,
            project=SimpleNamespace(project_root=tmp_path),
            shell_cwd=shell_cwd,
        ),
        "persona": None,
        "command_scope": "code",
    }


def test_edit_file_opens_existing(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr("xlii.editor.open_for_edit", lambda p, cfg=None: opened.append(p) or 0)
    (tmp_path / "f.py").write_text("x = 1\n")

    ctx = _file_ctx(tmp_path)
    assert dispatch_repl_command("/edit --file f.py", ctx) is True
    assert opened and str(opened[0]).endswith("f.py")


def test_edit_file_jails_escape(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr("xlii.editor.open_for_edit", lambda p, cfg=None: opened.append(p) or 0)

    ctx = _file_ctx(tmp_path)
    assert dispatch_repl_command("/edit --file ../escape.py", ctx) is True
    assert opened == []
    assert "outside" in ctx["console"].text.lower()


def test_edit_file_new_creates_empty_with_parents(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr("xlii.editor.open_for_edit", lambda p, cfg=None: opened.append(p) or 0)

    ctx = _file_ctx(tmp_path)
    assert dispatch_repl_command("/edit --file sub/new.py --new", ctx) is True
    assert (tmp_path / "sub" / "new.py").exists()
    assert opened and str(opened[0]).endswith("new.py")


def test_edit_file_refuses_directory(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr("xlii.editor.open_for_edit", lambda p, cfg=None: opened.append(p) or 0)
    (tmp_path / "adir").mkdir()

    ctx = _file_ctx(tmp_path)
    assert dispatch_repl_command("/edit --file adir", ctx) is True
    assert opened == []
    assert "directory" in ctx["console"].text.lower()


def test_edit_file_passes_cfg_and_reports_detached(tmp_path, monkeypatch):
    from xlii.editor import EDITOR_DETACHED

    seen = []
    monkeypatch.setattr(
        "xlii.editor.open_for_edit",
        lambda p, cfg=None: seen.append(cfg) or EDITOR_DETACHED,
    )
    monkeypatch.setattr("xlii.editor.resolve_editor", lambda cfg=None: "pluma")
    (tmp_path / "nfo.txt").write_text("hi\n")
    cfg = SimpleNamespace(editor="pluma")
    ctx = _file_ctx(tmp_path)
    ctx["cfg"] = cfg
    ctx["state"].cfg = cfg
    assert dispatch_repl_command("/edit --file nfo.txt", ctx) is True
    assert seen and seen[0] is cfg
    assert "opened" in ctx["console"].text and "pluma" in ctx["console"].text
    assert "✓" not in ctx["console"].text


def test_edit_file_needs_project(monkeypatch):
    monkeypatch.setattr("xlii.editor.open_for_edit", lambda p, cfg=None: 0)
    ctx = {
        "console": FakeConsole(),
        "state": SimpleNamespace(persona=None),  # no .project
        "persona": None,
        "command_scope": "code",
    }
    assert dispatch_repl_command("/edit --file foo.py", ctx) is True
    assert "needs a project" in ctx["console"].text


# --------------------------------------------------------------------------- #
#  Phase 3 — /edit --doc, --plugin
# --------------------------------------------------------------------------- #

def test_edit_doc_creates_and_opens(tmp_path, monkeypatch):
    import xlii.doc

    monkeypatch.setattr(xlii.doc, "DOCS_DIR", tmp_path / "docs")
    opened = []
    monkeypatch.setattr("xlii.editor.open_for_edit", lambda p, cfg=None: opened.append(p) or 0)

    ctx = _ctx("code")
    assert dispatch_repl_command("/edit --doc mydoc", ctx) is True
    assert (tmp_path / "docs" / "mydoc.md").exists()
    assert opened and str(opened[0]).endswith("mydoc.md")
    assert "/doc --refresh mydoc" in ctx["console"].text  # refresh reminder


def test_edit_plugin_creates_and_opens(tmp_path, monkeypatch):
    import xlii.plugin

    monkeypatch.setattr(xlii.plugin, "PLUGINS_DIR", tmp_path / "plugins")
    opened = []
    monkeypatch.setattr("xlii.editor.open_for_edit", lambda p, cfg=None: opened.append(p) or 0)

    ctx = _ctx("code")
    assert dispatch_repl_command("/edit --plugin myplug", ctx) is True
    assert (tmp_path / "plugins" / "myplug.md").exists()
    assert opened and str(opened[0]).endswith("myplug.md")


# --------------------------------------------------------------------------- #
#  Phase 4 — hot reload of the current persona prompt
# --------------------------------------------------------------------------- #

def test_edit_current_persona_hot_reloads_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(xlii.persona, "PERSONAS_DIR", tmp_path / "personas")
    xlii.persona.create_persona("bob")
    persona = xlii.persona.Persona("bob")
    agent = SimpleNamespace(base_system_prompt="OLD PROMPT")
    state = SimpleNamespace(persona=persona, agent=agent)

    def fake_editor(path):  # simulate the user editing the prompt in $EDITOR
        path.write_text("NEW BODY HERE\n")
        return 0

    monkeypatch.setattr(edit_mod, "open_in_editor", fake_editor)
    ctx = {"console": FakeConsole(), "state": state, "persona": persona, "command_scope": "chat"}

    assert dispatch_repl_command("/edit", ctx) is True
    assert "NEW BODY HERE" in agent.base_system_prompt  # live agent picked up the edit
    assert "reloaded" in ctx["console"].text.lower()


def test_edit_current_persona_without_agent_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(xlii.persona, "PERSONAS_DIR", tmp_path / "personas")
    xlii.persona.create_persona("bob")
    persona = xlii.persona.Persona("bob")
    state = SimpleNamespace(persona=persona)  # no live agent

    monkeypatch.setattr(edit_mod, "open_in_editor", lambda p: 0)
    ctx = {"console": FakeConsole(), "state": state, "persona": persona, "command_scope": "chat"}

    assert dispatch_repl_command("/edit", ctx) is True
    assert "restart" in ctx["console"].text.lower()
