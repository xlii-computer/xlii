"""Face: View on a leaf projects into the main feed (feed_view), not only the dock."""

from __future__ import annotations

from types import SimpleNamespace

from xlii.addressing.builtins.register import register_builtins
from xlii.face_panes import FaceDeck
from xlii.panes import RETARGET_SLOT, Outcome
from xlii.workbench import BUILTIN_WORKBENCHES


def test_retarget_leaf_emits_feed_view(tmp_path):
    register_builtins()
    f = tmp_path / "hello.txt"
    f.write_text("line one\nline two\n", encoding="utf-8")
    xli = tmp_path / ".xlii"
    xli.mkdir()
    state = SimpleNamespace(
        workbench=BUILTIN_WORKBENCHES["code"],
        project=SimpleNamespace(project_root=tmp_path, xli_dir=xli, name="p"),
        shell_cwd=tmp_path,
        scratch=False,
    )
    sent = []
    server = SimpleNamespace(state=state, send=lambda o: sent.append(o))
    deck = FaceDeck(server)
    deck.open_pane("explorer")
    outcome = Outcome(RETARGET_SLOT, f"file://{f}", text="")
    deck._dispatch("explorer", outcome)
    views = [o for o in sent if o.get("type") == "feed_view"]
    assert len(views) == 1
    assert views[0]["kind"] == "file"
    assert "line one" in views[0]["text"]
    assert views[0]["address"].endswith("hello.txt") or "hello.txt" in views[0]["address"]


def test_retarget_pdf_opens_viewer_slot_not_feed(tmp_path):
    from tests.test_panes_pdf import _minimal_pdf

    register_builtins()
    f = tmp_path / "paper.pdf"
    f.write_bytes(_minimal_pdf("Inside"))
    xli = tmp_path / ".xlii"
    xli.mkdir()
    state = SimpleNamespace(
        workbench=BUILTIN_WORKBENCHES["code"],
        project=SimpleNamespace(project_root=tmp_path, xli_dir=xli, name="p"),
        shell_cwd=tmp_path,
        scratch=False,
    )
    sent = []
    server = SimpleNamespace(state=state, send=lambda o: sent.append(o))
    deck = FaceDeck(server)
    deck.open_pane("explorer")
    deck._dispatch("explorer", Outcome(RETARGET_SLOT, f"file://{f}", text=""))
    assert not [o for o in sent if o.get("type") == "feed_view"]
    decks = [o for o in sent if o.get("type") == "pane_deck"]
    assert decks and any(p.get("id") == "pdf" for p in decks[-1].get("panes", []))
    assert "pdf" in deck.slot_tuple()


def test_plugin_source_view_emits_feed_view(tmp_path, monkeypatch):
    from xlii import plugin as plugin_mod

    register_builtins()
    plug_dir = tmp_path / "plugs"
    plug_dir.mkdir()
    (plug_dir / "demo.md").write_text("---\nid: demo\n---\n# how it's written\n", encoding="utf-8")
    monkeypatch.setattr(plugin_mod, "PLUGINS_DIR", plug_dir)
    xli = tmp_path / ".xlii"
    xli.mkdir()
    state = SimpleNamespace(
        workbench=BUILTIN_WORKBENCHES["code"],
        project=SimpleNamespace(project_root=tmp_path, xli_dir=xli, name="p"),
        shell_cwd=tmp_path,
        scratch=False,
    )
    sent = []
    server = SimpleNamespace(state=state, send=lambda o: sent.append(o))
    deck = FaceDeck(server)
    deck._dispatch("plugins", Outcome(RETARGET_SLOT, "plugins://demo/source", text=""))
    views = [o for o in sent if o.get("type") == "feed_view"]
    assert len(views) == 1
    assert "how it's written" in views[0]["text"]
    assert views[0]["title"] == "demo.md"
    assert views[0]["kind"] == "plugin"


def test_plugin_source_view_reveals_hidden_stream(tmp_path, monkeypatch):
    """Solo plugins + View source must open a stream slot (feed is off-screen)."""
    from xlii import plugin as plugin_mod
    from xlii.panes import Outcome
    from xlii.panes.dock import RETARGET_SLOT

    register_builtins()
    plug_dir = tmp_path / "plugs"
    plug_dir.mkdir()
    (plug_dir / "demo.md").write_text("---\nid: demo\n---\n# src\n", encoding="utf-8")
    monkeypatch.setattr(plugin_mod, "PLUGINS_DIR", plug_dir)
    xli = tmp_path / ".xlii"
    xli.mkdir()
    state = SimpleNamespace(
        workbench=BUILTIN_WORKBENCHES["code"],
        project=SimpleNamespace(project_root=tmp_path, xli_dir=xli, name="p"),
        shell_cwd=tmp_path,
        scratch=False,
    )
    sent = []
    server = SimpleNamespace(
        state=state, send=lambda o: sent.append(o),
        chrome_state=lambda: {"type": "chrome_state"},
    )
    deck = FaceDeck(server)
    assert deck.open_pane("plugins")
    # hide stream — plugins only
    if deck.slot_tuple()[0] == "stream":
        deck.close_slot("a")
    else:
        deck.close_slot("b")
    assert "stream" not in deck.slot_tuple()[:2]
    deck._dispatch("plugins", Outcome(RETARGET_SLOT, "plugins://demo/source", text=""))
    assert "stream" in deck.slot_tuple()[:2]
    assert "plugins" in deck.slot_tuple()[:2]


def test_face_view_action_on_bookmarks_reads_persona_mark(tmp_path, monkeypatch):
    from xlii import active_session
    from xlii.persona import Persona
    from xlii.transcript import mark_last_turn, write_turn

    register_builtins()
    import xlii.persona as persona_mod

    monkeypatch.setattr(persona_mod, "PERSONAS_DIR", tmp_path / "personas")
    monkeypatch.setattr(persona_mod, "CHAT_STATE_DIR", tmp_path / "chat")
    (tmp_path / "personas").mkdir()
    (tmp_path / "personas" / "bob.md").write_text("You are bob.")
    p = Persona("bob")
    write_turn(p.turns_dir, "what about auth", "use the token")
    mark_last_turn(p.turns_dir, "auth-insight")
    xli = tmp_path / ".xlii"
    xli.mkdir()
    state = SimpleNamespace(
        workbench=BUILTIN_WORKBENCHES["chat"],
        project=SimpleNamespace(project_root=tmp_path, xli_dir=xli, name="p"),
        shell_cwd=tmp_path,
        scratch=False,
        profile=SimpleNamespace(memory=SimpleNamespace(turns_dir=tmp_path / "empty-turns")),
    )
    (tmp_path / "empty-turns").mkdir()
    prev = active_session.set_active_session(state)
    try:
        sent = []
        server = SimpleNamespace(
            state=state, send=lambda o: sent.append(o),
            chrome_state=lambda: {"type": "chrome_state"},
        )
        deck = FaceDeck(server)
        deck.open_pane("bookmarks")
        deck.handle({
            "pane": "bookmarks", "op": "action", "name": "view",
        })
        views = [o for o in sent if o.get("type") == "feed_view"]
        assert views, sent
        assert "token" in views[0]["text"] or "auth" in views[0]["text"].lower()
    finally:
        active_session.set_active_session(prev)


def test_retarget_container_still_morphs_dock(tmp_path):
    register_builtins()
    sub = tmp_path / "sub"
    sub.mkdir()
    xli = tmp_path / ".xlii"
    xli.mkdir()
    state = SimpleNamespace(
        workbench=BUILTIN_WORKBENCHES["code"],
        project=SimpleNamespace(project_root=tmp_path, xli_dir=xli, name="p"),
        shell_cwd=tmp_path,
        scratch=False,
    )
    sent = []
    server = SimpleNamespace(state=state, send=lambda o: sent.append(o))
    deck = FaceDeck(server)
    deck.open_pane("explorer")
    outcome = Outcome(RETARGET_SLOT, f"file://{sub}", text="")
    deck._dispatch("explorer", outcome)
    assert not any(o.get("type") == "feed_view" for o in sent)


def test_retarget_image_emits_feed_view_with_b64(tmp_path):
    register_builtins()
    img = tmp_path / "dot.png"
    # 1x1 PNG
    img.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
        b"\x00\x0cIDATx\x9cc``\x00\x00\x00\x04\x00\x01\xf6\x178U"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    xli = tmp_path / ".xlii"
    xli.mkdir()
    state = SimpleNamespace(
        workbench=BUILTIN_WORKBENCHES["code"],
        project=SimpleNamespace(project_root=tmp_path, xli_dir=xli, name="p"),
        shell_cwd=tmp_path,
        scratch=False,
    )
    sent = []
    server = SimpleNamespace(state=state, send=lambda o: sent.append(o))
    deck = FaceDeck(server)
    deck.open_pane("explorer")
    outcome = Outcome(RETARGET_SLOT, f"file://{img}", text="")
    deck._dispatch("explorer", outcome)
    views = [o for o in sent if o.get("type") == "feed_view"]
    assert len(views) == 1
    assert views[0]["kind"] == "image"
    assert views[0]["b64"]
