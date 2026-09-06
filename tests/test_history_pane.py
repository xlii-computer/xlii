"""history:// pane + provider — face/TUI input-history panel."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from xlii.addressing import vfs_list, vfs_stat
from xlii.addressing.builtins.register import register_builtins
from xlii.panes.dock import Dock
from xlii.panes.history import HistoryPane
from xlii.ws_protocol import serialize_event


@pytest.fixture(autouse=True)
def _builtins():
    register_builtins()


def _write_history(xli: Path, lines: list[str]) -> None:
    # prompt_toolkit FileHistory format: each entry is #+BEGIN_... lines
    # FileHistory stores as:
    #   # 2020-...
    #   +line
    # Simpler: write the file the util can read via FileHistory.
    path = xli / "repl_history"
    chunks = []
    for ln in lines:
        chunks.append(f"#\n+{ln}\n")
    path.write_text("".join(chunks), encoding="utf-8")


def test_provider_lists_lines(tmp_path, monkeypatch):
    import xlii.active_session as active_session

    xli = tmp_path / ".xlii"
    xli.mkdir()
    _write_history(xli, ["echo one", "/status", "? what"])
    state = SimpleNamespace(project=SimpleNamespace(xli_dir=xli), shell_cwd=tmp_path)
    active_session.set_active_session(state)
    try:
        node = vfs_stat("history://")
        assert node.kind == "container"
        nodes = vfs_list("history://")
        assert len(nodes) >= 1
        texts = [n.extra.get("text") for n in nodes]
        assert "echo one" in texts or any("echo one" in str(t) for t in texts)
    finally:
        active_session.set_active_session(None)


def test_history_pane_prefills(tmp_path, monkeypatch):
    import xlii.active_session as active_session

    xli = tmp_path / ".xlii"
    xli.mkdir()
    _write_history(xli, ["first cmd", "second cmd"])
    state = SimpleNamespace(project=SimpleNamespace(xli_dir=xli), shell_cwd=tmp_path)
    active_session.set_active_session(state)
    try:
        pane = HistoryPane("history://")
        rendered = pane.render()
        assert not rendered.empty
        assert len(rendered.rows) >= 2
        acts = pane.actions()
        assert acts and acts[0].name == "prefill"
        assert acts[0].outcome.text  # full line for seed
        assert any(a.name == "clear" for a in acts)
        # select second
        assert pane.select_index(1)
        assert pane.actions()[0].outcome.text
    finally:
        active_session.set_active_session(None)


def test_history_pane_clear_typed_wipes_file(tmp_path, monkeypatch):
    import xlii.active_session as active_session

    xli = tmp_path / ".xlii"
    xli.mkdir()
    _write_history(xli, ["keep? no", "also no"])
    state = SimpleNamespace(project=SimpleNamespace(xli_dir=xli), shell_cwd=tmp_path)
    active_session.set_active_session(state)
    try:
        pane = HistoryPane("history://")
        assert pane.render().empty is False
        assert pane.clear_typed() is True
        rendered = pane.render()
        assert rendered.empty is True
        assert not (xli / "repl_history").exists()
        # empty pane still offers the clear door (face ↑/↓ may still hold lines)
        names = [a.name for a in pane.actions()]
        assert names == ["clear"]
    finally:
        active_session.set_active_session(None)


def test_face_history_clear_drops_typed_lines(tmp_path):
    import xlii.active_session as active_session
    from xlii.face_panes import FaceDeck
    from xlii.workbench import BUILTIN_WORKBENCHES

    xli = tmp_path / ".xlii"
    xli.mkdir()
    _write_history(xli, ["typed once"])
    state = SimpleNamespace(
        workbench=BUILTIN_WORKBENCHES["code"],
        project=SimpleNamespace(project_root=tmp_path, xli_dir=xli, name="p"),
        shell_cwd=tmp_path,
        scratch=False,
    )
    active_session.set_active_session(state)
    sent = []
    server = SimpleNamespace(state=state, send=lambda o: sent.append(o))
    try:
        deck = FaceDeck(server)
        assert deck.open_pane("history") is True
        deck.handle({"pane": "history", "op": "action", "name": "clear"})
        assert not (xli / "repl_history").exists()
        assert any(e.get("type") == "clear_input_history" for e in sent)
    finally:
        active_session.set_active_session(None)


def test_dock_mounts_history():
    dock = Dock(slots=("history",))
    pane = dock.open_address("history://", slot="history")
    assert isinstance(pane, HistoryPane)


def test_face_deck_opens_config(tmp_path):
    import xlii.active_session as active_session
    from xlii.face_panes import FaceDeck
    from xlii.workbench import BUILTIN_WORKBENCHES

    xli = tmp_path / ".xlii"
    xli.mkdir()
    state = SimpleNamespace(
        workbench=BUILTIN_WORKBENCHES["code"],
        project=SimpleNamespace(project_root=tmp_path, xli_dir=xli, name="p",
                                bound_persona=None),
        shell_cwd=tmp_path,
        scratch=False,
        agent=SimpleNamespace(session=SimpleNamespace(chat_tier="expert", yolo=False),
                              cfg=SimpleNamespace(orchestrator_model="grok")),
        cfg=SimpleNamespace(orchestrator_model="grok"),
    )
    active_session.set_active_session(state)
    sent = []
    server = SimpleNamespace(state=state, send=lambda o: sent.append(o))
    try:
        deck = FaceDeck(server)
        assert deck.open_pane("config") is True
        snap = serialize_event(deck.snapshot())
        assert any(p["id"] == "config" for p in snap["panes"])
    finally:
        active_session.set_active_session(None)


def test_face_deck_opens_history(tmp_path):
    import xlii.active_session as active_session
    from xlii.face_panes import FaceDeck
    from xlii.workbench import BUILTIN_WORKBENCHES

    xli = tmp_path / ".xlii"
    xli.mkdir()
    _write_history(xli, ["ls -la"])
    state = SimpleNamespace(
        workbench=BUILTIN_WORKBENCHES["code"],
        project=SimpleNamespace(project_root=tmp_path, xli_dir=xli, name="p"),
        shell_cwd=tmp_path,
        scratch=False,
    )
    active_session.set_active_session(state)
    sent = []
    server = SimpleNamespace(state=state, send=lambda o: sent.append(o))
    try:
        deck = FaceDeck(server)
        assert deck.open_pane("history") is True
        snap = serialize_event(deck.snapshot())
        ids = [p["id"] for p in snap["panes"]]
        assert "history" in ids
        hist = next(p for p in snap["panes"] if p["id"] == "history")
        assert hist["rows"]  # has history rows
    finally:
        active_session.set_active_session(None)
