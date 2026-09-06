"""Panes on the face (typed-workbenches B1) — the FaceDeck and its wire shapes."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xlii import active_session, tasks as T
from xlii.face_panes import STREAM_VIEW, FaceDeck, _FaceTurnSink
from xlii.workbench import BUILTIN_WORKBENCHES, WorkbenchType
from xlii.ws_protocol import serialize_event


@pytest.fixture(autouse=True)
def _ambient_session():
    """The addressing builtins reach the project via the ambient session seam
    (production: serve_face swaps it in; tests must set + restore it)."""
    prev = active_session.set_active_session(None)
    yield
    active_session.set_active_session(prev)


class _FakeServer:
    """The FaceServer seam FaceDeck consumes: state, send, submit."""

    def __init__(self, state):
        self.state = state
        self.sent = []
        self.submitted = []

    def send(self, obj):
        self.sent.append(obj)

    def pin_canvas_work(self, address):
        title = str(address).split("://", 1)[-1]
        self.send({
            "type": "focus_state",
            "items": [{
                "address": address,
                "title": title,
                "kind": "canvas",
                "once": False,
            }],
        })

    def submit(self, text):
        self.submitted.append(text)
        return True

    def events(self, type_name):
        return [o for o in self.sent if o.get("type") == type_name]

    def chrome_state(self):
        cfg = getattr(self.state, "cfg", None)
        raw = str(getattr(cfg, "tui_panel_side", "") or "right").lower()
        return {
            "type": "chrome_state",
            "pane_side": "left" if raw == "left" else "right",
            "pane_width_pct": int(getattr(cfg, "panel_width_pct", 0) or 0),
        }

    def live_stream_id(self):
        row = self.live_stream_row()
        return str((row or {}).get("id") or "")

    def live_stream_row(self):
        return getattr(self, "_live_stream", None)

    def open_streams(self):
        return list(getattr(self, "_open_streams", []) or [])

    def emit_stream_peek(self, slot, sid):
        self.send({"type": "stream_peek", "slot": slot, "id": sid})

    def forget_open_stream(self, sid):
        want = (sid or "").strip()
        self._open_streams = [
            r for r in getattr(self, "_open_streams", []) if r.get("id") != want
        ]
        self.forgotten = getattr(self, "forgotten", []) + [want]

    def enter_open_stream(self, sid):
        self.entered = sid
        if not getattr(self, "_enter_ok", False):
            return False
        for r in getattr(self, "_open_streams", []) or []:
            if r.get("id") == sid:
                self._live_stream = r
                break
        return True


def _state(tmp_path, workbench):
    root = tmp_path / "proj"
    root.mkdir()
    xli = root / ".xlii"
    xli.mkdir()
    return SimpleNamespace(
        workbench=workbench,
        shell_cwd=None,
        project=SimpleNamespace(project_root=root, xli_dir=xli),
    )


def _deck(tmp_path, type_name="code"):
    state = _state(tmp_path, BUILTIN_WORKBENCHES[type_name])
    active_session.set_active_session(state)
    server = _FakeServer(state)
    return FaceDeck(server), server, state


def _pane(snapshot_ev, pane_id):
    return next(p for p in snapshot_ev["panes"] if p["id"] == pane_id)


def test_ensure_pane_does_not_rebuild_dock_when_already_packed(tmp_path):
    """Opening home/files must not remount git/vfs on every click."""
    deck, _server, _state = _deck(tmp_path, "home")
    assert deck.ensure_pane("home")
    deck.send_snapshot()
    key = deck._mounted_for
    assert key is not None
    assert deck.ensure_pane("home")
    assert deck._mounted_for is key


def test_face_config_cycles_tier_and_yolo(tmp_path):
    from xlii.panes.face_config import FaceConfigPane

    sess = SimpleNamespace(chat_tier=None, yolo=False, budget_note=None)
    saves: list[int] = []
    cfg = SimpleNamespace(
        retrieval_mode="hybrid", fabric_pull_interval_s=900,
        orchestrator=lambda: "grok-build-0.1",
        chat=lambda: "grok-4.3",
        worker=lambda: "grok-build-0.1",
        chat_tier="off",
        save=lambda: saves.append(1),
    )
    state = _state(tmp_path, BUILTIN_WORKBENCHES["code"])
    state.agent = SimpleNamespace(session=sess, cfg=cfg)
    state.cfg = cfg
    state.yolo = False
    active_session.set_active_session(state)
    pane = FaceConfigPane("faceconfig://")
    rendered = pane.render()
    labels = [r.text for r in rendered.rows]
    assert any(t.startswith("── account") for t in labels)
    assert any("orchestrator ·" in t for t in labels)
    assert any("chat tier · off" in t for t in labels)
    # select the tier leaf and cycle
    leaf_ids = [r.address for r in rendered.rows if r.kind == "leaf"]
    tier_i = next(i for i, a in enumerate(leaf_ids) if a.endswith("/tier"))
    pane.select_index(tier_i)
    assert pane.apply_selection() is True
    assert sess.chat_tier == "auto"
    assert cfg.chat_tier == "auto"
    assert saves
    tier_row = next(r for r in pane.render().rows if r.address.endswith("/tier"))
    assert tier_row.choices and any(c[0] == "expert" for c in tier_row.choices)
    assert pane.apply_value("faceconfig://tier", "expert") is True
    assert sess.chat_tier == "expert"


def test_face_config_lists_desk_knobs(tmp_path):
    from xlii.panes.face_config import FaceConfigPane

    cfg = SimpleNamespace(
        retrieval_mode="hybrid", fabric_pull_interval_s=900,
        orchestrator=lambda: "m", chat=lambda: "m", worker=lambda: "m",
        editor="", image_editor="", browser="", tui_terminal="",
        max_tool_iterations=20, max_chat_tool_iterations=8,
        max_worker_iterations=10, import_foreign_skills=True,
        fallback_persona="",
        save=lambda: None,
    )
    state = _state(tmp_path, BUILTIN_WORKBENCHES["code"])
    state.agent = SimpleNamespace(
        session=SimpleNamespace(chat_tier=None, yolo=False, budget_note=None),
        cfg=cfg,
    )
    state.cfg = cfg
    active_session.set_active_session(state)
    labels = [r.text for r in FaceConfigPane("faceconfig://").render().rows]
    assert any(t.startswith("editor ·") for t in labels)
    assert any(t.startswith("image editor ·") for t in labels)
    assert any(t.startswith("os browser ·") for t in labels)
    assert any(t.startswith("terminal ·") for t in labels)
    assert any(t.startswith("new terminal ·") for t in labels)
    assert any(t.startswith("open terminal ·") for t in labels)
    assert any(t.startswith("tool iterations ·") for t in labels)
    assert any(t.startswith("mojo ·") for t in labels)
    assert any(t.startswith("panel side ·") for t in labels)
    assert any(t.startswith("panel width ·") for t in labels)
    pane = FaceConfigPane("faceconfig://")
    leaves = [r for r in pane.render().rows if r.kind == "leaf"]
    i = next(n for n, r in enumerate(leaves) if r.address.endswith("/mojo"))
    pane.select_index(i)
    acts = pane.actions()
    assert acts and acts[0].outcome.text == "/name "


def test_face_config_cycles_editor(tmp_path, monkeypatch):
    from xlii.panes.face_config import FaceConfigPane

    monkeypatch.setattr("xlii.desk.installed", lambda cands: ["nano", "pluma"])
    cfg = SimpleNamespace(
        retrieval_mode="hybrid", fabric_pull_interval_s=900,
        orchestrator=lambda: "m", chat=lambda: "m", worker=lambda: "m",
        editor="", image_editor="", browser="", tui_terminal="",
        max_tool_iterations=20, max_chat_tool_iterations=8,
        max_worker_iterations=10, import_foreign_skills=True,
        saves=0,
    )
    cfg.save = lambda: setattr(cfg, "saves", cfg.saves + 1)
    state = _state(tmp_path, BUILTIN_WORKBENCHES["code"])
    state.agent = SimpleNamespace(
        session=SimpleNamespace(chat_tier=None, yolo=False, budget_note=None),
        cfg=cfg,
    )
    state.cfg = cfg
    active_session.set_active_session(state)
    pane = FaceConfigPane("faceconfig://")
    rendered = pane.render()
    leaf_ids = [r.address for r in rendered.rows if r.kind == "leaf"]
    i = next(n for n, a in enumerate(leaf_ids) if a.endswith("/editor"))
    pane.select_index(i)
    assert pane.apply_selection() is True
    assert cfg.editor == "nano"
    assert cfg.saves == 1


def test_face_config_cycles_panel_side_and_width(tmp_path):
    from xlii.panes.face_config import FaceConfigPane

    cfg = SimpleNamespace(
        retrieval_mode="hybrid", fabric_pull_interval_s=900,
        orchestrator=lambda: "m", chat=lambda: "m", worker=lambda: "m",
        tui_panel_side="right", panel_width_pct=0,
        saves=0,
    )
    cfg.save = lambda: setattr(cfg, "saves", cfg.saves + 1)
    state = _state(tmp_path, BUILTIN_WORKBENCHES["code"])
    state.agent = SimpleNamespace(
        session=SimpleNamespace(chat_tier=None, yolo=False, budget_note=None),
        cfg=cfg,
    )
    state.cfg = cfg
    active_session.set_active_session(state)
    pane = FaceConfigPane("faceconfig://")
    leaves = [r.address for r in pane.render().rows if r.kind == "leaf"]
    pane.select_index(next(i for i, a in enumerate(leaves) if a.endswith("/side")))
    side_row = next(r for r in pane.render().rows if (r.address or "").endswith("/side"))
    assert side_row.tone == "knob"
    assert pane.apply_selection() is True
    assert cfg.tui_panel_side == "left"
    pane.select_index(next(i for i, a in enumerate(
        [r.address for r in pane.render().rows if r.kind == "leaf"]
    ) if a.endswith("/width")))
    assert pane.apply_selection() is True
    assert cfg.panel_width_pct == 25


def test_face_config_cycles_new_terminal_dest(tmp_path):
    from xlii.panes.face_config import FaceConfigPane

    cfg = SimpleNamespace(
        retrieval_mode="hybrid", fabric_pull_interval_s=900,
        orchestrator=lambda: "m", chat=lambda: "m", worker=lambda: "m",
        tui_terminal_cwd="project", tui_terminal_cwd_path="",
        saves=0,
    )
    cfg.save = lambda: setattr(cfg, "saves", cfg.saves + 1)
    state = _state(tmp_path, BUILTIN_WORKBENCHES["code"])
    state.agent = SimpleNamespace(
        session=SimpleNamespace(chat_tier=None, yolo=False, budget_note=None),
        cfg=cfg,
    )
    state.cfg = cfg
    active_session.set_active_session(state)
    pane = FaceConfigPane("faceconfig://")
    leaves = [r.address for r in pane.render().rows if r.kind == "leaf"]
    pane.select_index(next(i for i, a in enumerate(leaves) if a.endswith("/termcwd")))
    assert pane.apply_selection() is True
    assert cfg.tui_terminal_cwd == "home"
    pane.select_index(next(i for i, a in enumerate(
        [r.address for r in pane.render().rows if r.kind == "leaf"]
    ) if a.endswith("/termcwd")))
    assert pane.apply_selection() is True
    assert cfg.tui_terminal_cwd == "root"
    labels = [r.text for r in pane.render().rows]
    assert any("new terminal · root" in t for t in labels)
    pane.select_index(next(i for i, a in enumerate(
        [r.address for r in pane.render().rows if r.kind == "leaf"]
    ) if a.endswith("/termcwd")))
    assert pane.apply_selection() is True
    assert cfg.tui_terminal_cwd == "custom"
    ev = pane.pop_wire()
    assert ev and ev["type"] == "claim_line"
    assert ev["submit"] == "set_terminal_cwd_path"


def test_config_enter_pushes_chrome_side(tmp_path):
    """Enter on panel-side (not only the Cycle button) must move the dock."""
    cfg = SimpleNamespace(
        retrieval_mode="hybrid", fabric_pull_interval_s=900,
        orchestrator=lambda: "m", chat=lambda: "m", worker=lambda: "m",
        tui_panel_side="left", panel_width_pct=50,
        save=lambda: None,
    )
    deck, server, state = _deck(tmp_path, "code")
    state.cfg = cfg
    state.agent = SimpleNamespace(
        session=SimpleNamespace(chat_tier=None, yolo=False, budget_note=None),
        cfg=cfg,
    )
    assert deck.ensure_pane("config")
    deck.send_snapshot()
    snap = server.events("pane_deck")[-1]
    cfg_pane = _pane(snap, "config")
    idx = next(i for i, r in enumerate(cfg_pane["rows"])
               if (r.get("address") or "").endswith("/side"))
    deck.handle({"pane": "config", "op": "select", "index": idx})
    deck.handle({"pane": "config", "op": "key", "key": "enter"})
    assert cfg.tui_panel_side == "right"
    chromes = server.events("chrome_state")
    assert chromes, "enter must push chrome_state so the dock flips"
    assert chromes[-1].get("pane_side") == "right"


def test_face_config_loads_account_snapshot(tmp_path, monkeypatch):
    from xlii.panes.face_config import FaceConfigPane

    monkeypatch.setattr(
        "xlii.cmds.account.account_snapshot",
        lambda: {"team": "Personal", "tier": "100", "spend": "$1 / $10 (10%)",
                 "keys": "2 active / 2", "prepaid": "$5.00", "models": "all"},
    )
    state = _state(tmp_path, BUILTIN_WORKBENCHES["code"])
    state.agent = SimpleNamespace(
        session=SimpleNamespace(chat_tier=None, yolo=False, budget_note=None),
        cfg=SimpleNamespace(
            retrieval_mode="hybrid", fabric_pull_interval_s=0,
            orchestrator=lambda: "m", chat=lambda: "m", worker=lambda: "m",
        ),
    )
    active_session.set_active_session(state)
    pane = FaceConfigPane("faceconfig://")
    pane.select_index(0)  # first leaf is load-account
    pane.apply_selection()
    labels = [r.text for r in pane.render().rows]
    assert any("Personal" in t for t in labels)
    assert any("credits ·" in t for t in labels)


# --- availability -------------------------------------------------------------


def test_chat_type_mounts_companion_research_panes(tmp_path):
    deck, server, _ = _deck(tmp_path, "chat")
    assert deck.available()
    snap = serialize_event(deck.snapshot())
    # chat pack absorbs research doors: fetch · keep · show panes
    assert [p["id"] for p in snap["panes"]] == [
        "menu", "locker", "bookmarks", "sources", "wiki", "artifacts", "canvas",
        "results", "plugins",
    ]


def test_code_type_mounts_its_pane_set(tmp_path):
    deck, _, _ = _deck(tmp_path, "code")
    assert deck.available()
    snap = serialize_event(deck.snapshot())
    assert snap["workbench"] == "code"
    assert {p["id"] for p in snap["panes"]} == {
        "explorer", "git", "tasks", "plan", "jobs", "farm", "skills", "menu"
    }


def test_planned_panes_are_skipped_not_fatal(tmp_path):
    wb = WorkbenchType("half", panes=("explorer", "holodeck"))  # no such pane (yet)
    state = _state(tmp_path, wb)
    deck = FaceDeck(_FakeServer(state))
    snap = serialize_event(deck.snapshot())
    assert [p["id"] for p in snap["panes"]] == ["explorer"]


# --- projection ----------------------------------------------------------------


def test_explorer_rows_mirror_the_project_root(tmp_path):
    deck, _, state = _deck(tmp_path)
    (state.project.project_root / "hello.txt").write_text("hi")
    snap = serialize_event(deck.snapshot())
    explorer = _pane(snap, "explorer")
    assert any("hello.txt" in r["text"] for r in explorer["rows"])
    assert all(set(r) >= {"text", "address", "kind", "selected"} for r in explorer["rows"])


def test_failed_mount_degrades_to_a_note(tmp_path):
    from xlii.face_panes import _FailedPane

    deck, _, _ = _deck(tmp_path)
    deck._ensure_dock()
    deck._dock.place("git", _FailedPane("git", "not a git repo"))
    snap = serialize_event(deck.snapshot())
    git = _pane(snap, "git")
    assert git["note"] == "not a git repo" and git["empty"]


# --- inbound ops -----------------------------------------------------------------


def test_skills_select_survives_reproject(tmp_path, monkeypatch):
    """Clicking a non-first skill must stick after FaceDeck remounts the pane."""
    monkeypatch.setattr("xlii.skills.load_skills", lambda *a, **k: {
        "alpha": SimpleNamespace(name="alpha", scope="stock",
                                 short_description="", description=""),
        "zeta": SimpleNamespace(name="zeta", scope="grok",
                                short_description="", description=""),
    })
    deck, server, _ = _deck(tmp_path, "code")
    deck.send_snapshot()
    snap = server.events("pane_deck")[-1]
    skills = _pane(snap, "skills")
    idx = next(i for i, r in enumerate(skills["rows"]) if "zeta" in r["text"])
    deck.handle({"pane": "skills", "op": "select", "index": idx})
    snap = server.events("pane_deck")[-1]
    skills = _pane(snap, "skills")
    selected = [r["text"] for r in skills["rows"] if r["selected"]]
    assert len(selected) == 1
    assert "zeta" in selected[0]


def test_select_marks_the_row(tmp_path):
    deck, _, state = _deck(tmp_path)
    (state.project.project_root / "a.txt").write_text("a")
    (state.project.project_root / "b.txt").write_text("b")
    deck.send_snapshot()
    snap = deck._server.events("pane_deck")[-1]
    idx = next(i for i, r in enumerate(_pane(snap, "explorer")["rows"]) if "a.txt" in r["text"])
    deck.handle({"pane": "explorer", "op": "select", "index": idx})
    snaps = deck._server.events("pane_deck")
    explorer = _pane(snaps[-1], "explorer")
    selected = [r for r in explorer["rows"] if r["selected"]]
    assert len(selected) == 1
    assert "a.txt" in selected[0]["text"]
    assert explorer["actions"]  # a selection offers actions


def test_explorer_hidden_toggle_via_key(tmp_path):
    deck, server, state = _deck(tmp_path)
    (state.project.project_root / "a.txt").write_text("a")
    (state.project.project_root / ".env").write_text("s")
    deck.send_snapshot()
    rows = _pane(server.events("pane_deck")[-1], "explorer")["rows"]
    assert any("hidden" in r["text"] and r["kind"] == "caption" for r in rows)
    assert not any(".env" in r["text"] for r in rows)
    deck.handle({"pane": "explorer", "op": "key", "key": "hidden"})
    rows = _pane(server.events("pane_deck")[-1], "explorer")["rows"]
    assert any(".env" in r["text"] for r in rows)
    assert any(r["text"] == "hidden · on" for r in rows)


def test_enter_on_a_container_navigates(tmp_path):
    deck, _, state = _deck(tmp_path)
    sub = state.project.project_root / "sub"
    sub.mkdir()
    deck.send_snapshot()
    snap = deck._server.events("pane_deck")[-1]
    idx = next(i for i, r in enumerate(_pane(snap, "explorer")["rows"]) if "sub" in r["text"])
    deck.handle({"pane": "explorer", "op": "select", "index": idx})
    deck.handle({"pane": "explorer", "op": "key", "key": "enter"})
    snap = deck._server.events("pane_deck")[-1]
    explorer = _pane(snap, "explorer")
    assert "sub" in explorer["title"]


def test_parent_address_walks_nested_scheme(tmp_path):
    from xlii.addressing import Address
    from xlii.face_panes import _parent_address

    assert str(_parent_address(Address.parse("plugins://demo/source"))) == "plugins://demo"
    assert str(_parent_address(Address.parse("plugins://demo"))) == "plugins://"
    assert _parent_address(Address.parse("locker://")) is None


def test_parent_address_fences_session_files_root(tmp_path):
    from xlii.addressing import Address
    from xlii.face_panes import _parent_address

    _face_deck, _server, state = _deck(tmp_path)
    root = state.project.project_root
    assert _parent_address(Address.parse(f"file://{root}")) is None
    parent = _parent_address(Address.parse(f"file://{root / 'sub'}"))
    assert parent is not None
    assert str(parent) == f"file://{root}"


def test_back_at_scheme_root_opens_home(tmp_path):
    deck, _server, _ = _deck(tmp_path, "code")
    assert deck.open_pane("locker")
    assert "locker" in deck.slot_tuple()[:2]
    deck.handle({"pane": "locker", "op": "key", "key": "back"})
    assert "home" in deck.slot_tuple()[:2]


def test_explorer_back_at_files_root_opens_home_not_host(tmp_path):
    """Project Files must not walk out of the desk — Back at the Files root
    bottoms out at the hub, same as other scheme roots."""
    from xlii.addressing import Address
    from xlii.panes.explorer import ExplorerPane

    deck, _server, state = _deck(tmp_path)
    assert deck.open_pane("explorer")
    assert "explorer" in deck.slot_tuple()[:2]
    root = Address.parse(f"file://{state.project.project_root}")
    assert ExplorerPane._parent(root) is None
    deck.handle({"pane": "explorer", "op": "key", "key": "back"})
    assert "home" in deck.slot_tuple()[:2]


def test_unknown_pane_and_op_are_errors(tmp_path):
    deck, server, _ = _deck(tmp_path)
    deck.handle({"pane": "nope", "op": "select", "index": 0})
    deck.handle({"pane": "explorer", "op": "dance"})
    msgs = [e["message"] for e in server.events("error")]
    assert any("no pane" in m for m in msgs)
    assert any("select|key|action" in m for m in msgs)


# --- outcomes over the wire -------------------------------------------------------


def test_tasks_load_action_emits_the_first_prefill(tmp_path):
    deck, server, state = _deck(tmp_path)
    T.tasks_dir(state.project.xli_dir).mkdir(parents=True, exist_ok=True)
    (T.tasks_dir(state.project.xli_dir) / "nightly.toml").write_text(
        'name="nightly"\n[[step]]\nrun="printf hi"\n'
    )
    deck.handle({"pane": "tasks", "op": "select", "index": 0})
    snap = server.events("pane_deck")[-1]
    tasks = _pane(snap, "tasks")
    names = [a["name"] for a in tasks["actions"]]
    assert "load" in names
    deck.handle({"pane": "tasks", "op": "action", "name": "load"})
    prefill = server.events("prefill")
    assert prefill and prefill[-1]["text"] == "/tasks run nightly"


def test_pdf_context_folds_extracted_text(tmp_path, monkeypatch):
    from xlii import pdf_text
    from xlii.face_panes import _with_context

    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-1.4 stub")
    monkeypatch.setattr(pdf_text, "extract_pdf_text", lambda path, **k: "EXTRACTED BODY")
    text = _with_context("Summarize this PDF.", f"file://{p}")
    assert "Summarize this PDF." in text and "EXTRACTED BODY" in text


def test_enqueue_turn_rides_the_one_input_queue(tmp_path):
    _, server, state = _deck(tmp_path)
    leaf = state.project.project_root / "note.md"
    leaf.write_text("remember this")
    sink = _FaceTurnSink(server)
    sink.submit("Summarize this file.", context=f"file://{leaf}")
    assert len(server.submitted) == 1
    text = server.submitted[0]
    assert "Summarize this file." in text and "remember this" in text  # context folded


def test_view_action_emits_feed_view_for_leaf(tmp_path):
    deck, server, state = _deck(tmp_path)
    (state.project.project_root / "a.txt").write_text("a")
    deck.send_snapshot()
    snap = server.events("pane_deck")[-1]
    idx = next(i for i, r in enumerate(_pane(snap, "explorer")["rows"]) if "a.txt" in r["text"])
    deck.handle({"pane": "explorer", "op": "select", "index": idx})
    deck.handle({"pane": "explorer", "op": "action", "name": "view"})
    views = server.events("feed_view")
    assert len(views) == 1
    assert "a.txt" in views[0]["address"] or "a.txt" in views[0].get("text", "")
    explorer = _pane(server.events("pane_deck")[-1], "explorer")
    assert "a.txt" not in explorer["title"]  # leaf view projects to the main feed


def test_view_pdf_opens_other_slot_not_feed(tmp_path):
    """F3 on a PDF: listing stays, viewer takes the other slot, no feed_view."""
    from tests.test_panes_pdf import _minimal_pdf

    deck, server, state = _deck(tmp_path)
    root = state.project.project_root
    sub = root / "papers"
    sub.mkdir()
    (sub / "note.txt").write_text("keep")
    (sub / "paper.pdf").write_bytes(_minimal_pdf("Inside the paper"))
    deck.open_pane("explorer")
    snap = server.events("pane_deck")[-1]
    idx = next(i for i, r in enumerate(_pane(snap, "explorer")["rows"]) if "papers" in r["text"])
    deck.handle({"pane": "explorer", "op": "select", "index": idx})
    deck.handle({"pane": "explorer", "op": "key", "key": "enter"})
    snap = server.events("pane_deck")[-1]
    explorer = _pane(snap, "explorer")
    assert "papers" in explorer["title"]
    idx = next(i for i, r in enumerate(explorer["rows"]) if "paper.pdf" in r["text"])
    deck.handle({"pane": "explorer", "op": "select", "index": idx})
    deck.handle({"pane": "explorer", "op": "action", "name": "view"})
    assert not server.events("feed_view")
    snap = server.events("pane_deck")[-1]
    ids = [p["id"] for p in snap["panes"]]
    assert "pdf" in ids
    assert "papers" in _pane(snap, "explorer")["title"]  # listing did not remount to root
    slots = server.events("slot_state")
    assert slots
    views = {slots[-1].get("a"), slots[-1].get("b")}
    assert views == {"pdf", "explorer"}
    catalog = deck.slot_catalog()
    assert any(r["id"] == "pdf" for r in catalog)


def test_pdf_scan_projects_image_b64(tmp_path, monkeypatch):
    from tests.test_panes_pdf import _minimal_pdf
    from xlii.panes import pdf as pdf_mod

    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000a49444154789c6360000002000154a24f8b0000000049454e44ae426082"
    )
    monkeypatch.setattr(pdf_mod, "render_pdf_page", lambda *a, **k: png)
    deck, server, state = _deck(tmp_path)
    (state.project.project_root / "scan.pdf").write_bytes(_minimal_pdf("x"))
    deck.open_pane("explorer")
    snap = server.events("pane_deck")[-1]
    idx = next(i for i, r in enumerate(_pane(snap, "explorer")["rows"]) if "scan.pdf" in r["text"])
    deck.handle({"pane": "explorer", "op": "select", "index": idx})
    # Force the viewer to treat the page as textless so it rasters.
    deck.handle({"pane": "explorer", "op": "action", "name": "view"})
    pdf_pane = deck._dock.slots.get("pdf")
    assert pdf_pane is not None
    pdf_pane._pages = ("",)
    pdf_pane._page = 1
    pdf_pane._raster = {}
    deck.send_snapshot()
    pane = _pane(server.events("pane_deck")[-1], "pdf")
    assert pane.get("image_b64")


def test_open_taskmake_from_solo_stream_takes_large_slot(tmp_path):
    """Panels → Task maker: form occupies a, stream parks in b."""
    deck, server, _state = _deck(tmp_path)
    assert deck.open_pane("taskmake")
    a, b, focus = deck.slot_tuple()
    assert a == "taskmake" and b == STREAM_VIEW and focus == "a"
    snap = server.events("pane_deck")[-1]
    assert "taskmake" in [p["id"] for p in snap["panes"]]


def test_tasks_new_opens_maker_other_slot(tmp_path):
    """Tasks → New: listing stays, form takes the other slot, no feed_view."""
    deck, server, _state = _deck(tmp_path)
    assert deck.open_pane("tasks")
    assert deck.slot_tuple()[:2] == (STREAM_VIEW, "tasks")
    deck.handle({"pane": "tasks", "op": "action", "name": "new"})
    views = {deck.slot_tuple()[0], deck.slot_tuple()[1]}
    assert views == {"tasks", "taskmake"}
    assert not server.events("feed_view")
    snap = server.events("pane_deck")[-1]
    ids = [p["id"] for p in snap["panes"]]
    assert "taskmake" in ids and "tasks" in ids


def test_plugins_click_selects_the_row_under_the_cursor(tmp_path, monkeypatch):
    """Rendered click index includes the keys caption; the landed row
    must be the plugin the mouse was on, not the one above it."""
    from xlii.panes import plugins as plugins_pane

    class _P:
        def __init__(self, pid):
            self.id = pid

        def auth_env_vars(self):
            return ["K"] if self.id == "alpha" else []

        def effect_trust(self):
            return ("read-only", "subscription")

        def description(self):
            return ""

    monkeypatch.setattr(plugins_pane, "_subscribed", lambda: {"alpha"})
    monkeypatch.setattr("xlii.plugin.list_plugins", lambda: [_P("alpha"), _P("beta")])
    monkeypatch.setattr("xlii.plugin_form.missing_vault_vars", lambda p: ["K"])
    deck, server, _state = _deck(tmp_path)
    assert deck.open_pane("plugins")
    deck.send_snapshot()
    snap = server.events("pane_deck")[-1]
    pane = _pane(snap, "plugins")
    assert pane["rows"][0]["kind"] == "caption"
    idx = next(i for i, r in enumerate(pane["rows"]) if r.get("address") == "plugins://beta")
    deck.handle({"pane": "plugins", "op": "select", "index": idx})
    deck.send_snapshot()
    painted = _pane(server.events("pane_deck")[-1], "plugins")
    selected = [r for r in painted["rows"] if r.get("selected")]
    assert [r["address"] for r in selected] == ["plugins://beta"]


def test_plugins_new_opens_maker_other_slot(tmp_path):
    deck, server, _state = _deck(tmp_path)
    assert deck.open_pane("plugins")
    deck.handle({"pane": "plugins", "op": "action", "name": "new"})
    views = {deck.slot_tuple()[0], deck.slot_tuple()[1]}
    assert views == {"plugins", "pluginmake"}
    assert not server.events("feed_view")


def test_plugin_form_closes_on_success(tmp_path):
    deck, _server, _state = _deck(tmp_path)
    assert deck.open_plugin_form("bluesky_login", "login")
    assert "pluginform" in deck.slot_tuple()
    assert deck.close_plugin_form() is True
    assert "pluginform" not in deck.slot_tuple()
    assert deck._form_address is None


def test_taskmake_back_restores_stream(tmp_path):
    deck, _server, _state = _deck(tmp_path)
    deck.open_pane("tasks")
    deck.handle({"pane": "tasks", "op": "action", "name": "new"})
    assert "taskmake" in deck.slot_tuple()
    deck.handle({"pane": "taskmake", "op": "key", "key": "back"})
    a, b, _ = deck.slot_tuple()
    assert "taskmake" not in (a, b)
    assert STREAM_VIEW in (a, b)


def test_pdf_back_restores_stream(tmp_path):
    from tests.test_panes_pdf import _minimal_pdf

    deck, server, state = _deck(tmp_path)
    (state.project.project_root / "paper.pdf").write_bytes(_minimal_pdf("x"))
    deck.open_pane("explorer")
    snap = server.events("pane_deck")[-1]
    idx = next(i for i, r in enumerate(_pane(snap, "explorer")["rows"]) if "paper.pdf" in r["text"])
    deck.handle({"pane": "explorer", "op": "select", "index": idx})
    deck.handle({"pane": "explorer", "op": "action", "name": "view"})
    assert "pdf" in deck.slot_tuple()
    deck.handle({"pane": "pdf", "op": "key", "key": "back"})
    a, b, _ = deck.slot_tuple()
    assert "pdf" not in (a, b)
    assert STREAM_VIEW in (a, b) or a == STREAM_VIEW or b == STREAM_VIEW


def test_reveal_canvas_opens_other_slot(tmp_path):
    from xlii.artifacts import artifacts_dir, write_artifact

    deck, server, state = _deck(tmp_path)
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000a49444154789c6360000002000154a24f8b0000000049454e44ae426082"
    )
    rel = write_artifact(state.project.project_root, png, ext="png")
    name = rel.rsplit("/", 1)[-1]
    path = artifacts_dir(state.project.project_root) / name
    deck.open_pane("explorer")
    assert deck.reveal_canvas(str(path))
    assert "canvas" in deck.slot_tuple()
    snap = server.events("pane_deck")[-1]
    pane = _pane(snap, "canvas")
    assert name in pane["title"] or any(name in r["text"] for r in pane["rows"])
    assert pane.get("image_b64")
    focus = server.events("focus_state")[-1]
    assert focus["items"]
    assert focus["items"][0]["title"] == name
    assert focus["items"][0]["once"] is False
    assert str(focus["items"][0]["address"]).startswith("canvas://")


def test_canvas_chosen_make_survives_snapshot(tmp_path, monkeypatch):
    """A chosen make stays the work — dock refresh must not remount latest."""
    from xlii.artifacts import write_artifact

    deck, server, state = _deck(tmp_path)
    root = state.project.project_root
    monkeypatch.setattr("xlii.active_session.active_session", lambda: state)
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000a49444154789c6360000002000154a24f8b0000000049454e44ae426082"
    )
    older = write_artifact(root, png, ext="png")
    newer = write_artifact(root, png, ext="png")
    older_name = older.rsplit("/", 1)[-1]
    newer_name = newer.rsplit("/", 1)[-1]
    assert deck.reveal_canvas(str(root / older))
    pane = _pane(server.events("pane_deck")[-1], "canvas")
    assert older_name in pane["title"]
    deck.send_snapshot()
    after = _pane(server.events("pane_deck")[-1], "canvas")
    assert older_name in after["title"]
    assert newer_name not in after["title"]


def test_artifacts_on_canvas_reveals_that_make(tmp_path, monkeypatch):
    """Opening a canvas-worthy artifact puts that file on the canvas and focuses it."""
    from xlii.artifacts import write_artifact

    deck, server, state = _deck(tmp_path, "chat")
    root = state.project.project_root
    monkeypatch.setattr("xlii.active_session.active_session", lambda: state)
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000a49444154789c6360000002000154a24f8b0000000049454e44ae426082"
    )
    older = write_artifact(root, png, ext="png")
    newer = write_artifact(root, png, ext="png")
    older_name = older.rsplit("/", 1)[-1]
    newer_name = newer.rsplit("/", 1)[-1]
    deck.open_pane("artifacts")
    snap = server.events("pane_deck")[-1]
    rows = _pane(snap, "artifacts")["rows"]
    idx = next(i for i, r in enumerate(rows) if older_name in r["text"])
    deck.handle({"pane": "artifacts", "op": "select", "index": idx})
    deck.handle({"pane": "artifacts", "op": "action", "name": "open"})
    a, b, focus = deck.slot_tuple()
    assert "canvas" in (a, b)
    assert (a if focus == "a" else b) == "canvas"
    after = _pane(server.events("pane_deck")[-1], "canvas")
    assert older_name in after["title"]
    assert newer_name not in after["title"]
    assert after.get("image_b64")
    focus = server.events("focus_state")[-1]
    assert focus["items"][0]["title"] == older_name
    assert focus["items"][0]["once"] is False


def test_canvas_edit_prefills_named_ref(tmp_path, monkeypatch):
    from xlii.artifacts import write_artifact

    deck, server, state = _deck(tmp_path, "chat")
    root = state.project.project_root
    monkeypatch.setattr("xlii.active_session.active_session", lambda: state)
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000a49444154789c6360000002000154a24f8b0000000049454e44ae426082"
    )
    rel = write_artifact(root, png, ext="png")
    name = rel.rsplit("/", 1)[-1]
    deck.reveal_canvas(str(root / rel))
    deck.handle({"pane": "canvas", "op": "action", "name": "edit"})
    prefills = [o for o in server.sent if o.get("type") == "prefill"]
    assert prefills
    text = prefills[-1]["text"]
    assert "/image edit" in text
    assert f"--ref {name}" in text


def test_rebuild_when_the_workbench_changes(tmp_path):
    deck, server, state = _deck(tmp_path)
    deck.send_snapshot()
    assert server.events("pane_deck")[-1]["workbench"] == "code"
    state.workbench = BUILTIN_WORKBENCHES["chat"]
    snap = serialize_event(deck.snapshot())
    assert [p["id"] for p in snap["panes"]] == [
        "menu", "locker", "bookmarks", "sources", "wiki", "artifacts", "canvas",
        "results", "plugins",
    ]
    state.workbench = BUILTIN_WORKBENCHES["home"]
    snap = serialize_event(deck.snapshot())
    assert {p["id"] for p in snap["panes"]} == {
        "projects", "home", "locker", "menu", "plugins",
    }


def test_switching_to_a_paneless_type_takes_the_strip_down_once(tmp_path):
    """A pane-less type must CLEAR the client's deck, then stay quiet.

    Since F3 every builtin row mounts at least the menu tab, so the pane-less
    case is a user-authored row (workbench.toml with no panes)."""
    deck, server, state = _deck(tmp_path)
    deck.send_snapshot()
    state.workbench = WorkbenchType(name="bare", panes=())
    deck.send_snapshot()
    decks = server.events("pane_deck")
    assert decks[-1]["panes"] == [] and decks[-1]["workbench"] == "bare"
    before = len(decks)
    deck.send_snapshot()  # still pane-less — nothing more on the wire
    assert len(server.events("pane_deck")) == before


def test_reprojection_picks_up_files_written_after_mount(tmp_path):
    """Re-projection IS the refresh: a turn's new file shows without a remount."""
    deck, server, state = _deck(tmp_path)
    deck.send_snapshot()
    (state.project.project_root / "late.txt").write_text("written by a turn")
    deck.send_snapshot()
    explorer = _pane(server.events("pane_deck")[-1], "explorer")
    assert any("late.txt" in r["text"] for r in explorer["rows"])


def test_enter_on_a_leaf_falls_back_to_the_primary_action(tmp_path):
    """The Dock surface's rule: an Enter local nav declines runs actions()[0]."""
    deck, server, state = _deck(tmp_path)
    (state.project.project_root / "a.txt").write_text("a")
    deck.send_snapshot()
    snap = server.events("pane_deck")[-1]
    idx = next(i for i, r in enumerate(_pane(snap, "explorer")["rows"]) if "a.txt" in r["text"])
    deck.handle({"pane": "explorer", "op": "select", "index": idx})
    deck.handle({"pane": "explorer", "op": "key", "key": "enter"})
    views = server.events("feed_view")
    assert len(views) == 1
    assert "a.txt" in views[0]["address"] or "a.txt" in views[0].get("text", "")


def test_show_media_only_inlines_images(tmp_path):
    from xlii.face_panes import _FaceMediaSink

    _, server, state = _deck(tmp_path)
    doc = state.project.project_root / "paper.pdf"
    doc.write_bytes(b"%PDF-1.4 not an image")
    _FaceMediaSink(server).show(f"file://{doc}")
    out = server.events("file_out")[-1]
    assert out["kind"] == "pdf" and not out.get("b64") and out["path"] == str(doc)

    png = state.project.project_root / "shot.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    _FaceMediaSink(server).show(f"file://{png}")
    out = server.events("file_out")[-1]
    assert out["kind"] == "image" and out["b64"]
    assert out["path"] == str(png)
    assert out["address"].endswith(png.name)


# --- the FaceServer integration seam ----------------------------------------------


def test_face_server_deck_property_and_reader_route(tmp_path):
    from xlii.serve_face import FaceServer

    state = _state(tmp_path, BUILTIN_WORKBENCHES["code"])
    server = FaceServer(boot=SimpleNamespace(state=state))
    assert server.deck.available()
    server.send = lambda obj: None  # no client; deck ops must not raise
    server.deck.handle({"pane": "explorer", "op": "select", "index": 0})


def test_face_menu_follows_posture_scope(tmp_path):
    from xlii.repl_cmds import register_all
    from xlii.serve_face import FaceServer

    register_all()
    state = _state(tmp_path, BUILTIN_WORKBENCHES["chat"])
    active_session.set_active_session(state)
    server = FaceServer(boot=SimpleNamespace(state=state))
    server.send = lambda obj: None

    assert state.command_scope == "chat"
    snap = serialize_event(server.deck.snapshot())
    menu = _pane(snap, "menu")
    row_text = "\n".join(r["text"] for r in menu["rows"])
    assert "/status" in row_text
    assert "/providers" not in row_text
    assert "/workbench" not in row_text

    server._set_posture("code")
    snap = serialize_event(server.deck.snapshot())
    menu = _pane(snap, "menu")
    row_text = "\n".join(r["text"] for r in menu["rows"])
    assert state.command_scope == "code"
    assert "/providers" in row_text
    assert "/workbench" in row_text


def test_face_chat_slash_input_uses_chat_command_scope(tmp_path, monkeypatch):
    from xlii.serve_face import FaceServer

    state = _state(tmp_path, BUILTIN_WORKBENCHES["chat"])
    server = FaceServer(boot=SimpleNamespace(state=state))
    called = {}

    def fake_process_repl_input(seen_state, text):
        called["scope"] = seen_state.command_scope
        called["text"] = text
        return None, True

    monkeypatch.setattr("xlii.repl.process_repl_input", fake_process_repl_input)
    assert server._run_chat_input("/help")
    assert called == {"scope": "chat", "text": "/help"}


def test_switch_to_a_paneless_type_clears_the_client_deck(tmp_path):
    """A deck already on the client must be told to go away — an empty
    snapshot hides it; a fresh pane-less connection still says nothing."""
    deck, server, state = _deck(tmp_path)
    deck.send_snapshot()
    assert server.events("pane_deck")[-1]["panes"]
    state.workbench = WorkbenchType(name="bare", panes=())
    deck.send_snapshot()
    assert server.events("pane_deck")[-1]["panes"] == []
    deck.send_snapshot()  # still pane-less: no repeat
    assert len(server.events("pane_deck")) == 2


def test_explorer_remounts_when_the_shell_cwd_moves(tmp_path):
    deck, server, state = _deck(tmp_path)
    sub = state.project.project_root / "elsewhere"
    sub.mkdir()
    (sub / "over_here.txt").write_text("x")
    snap = serialize_event(deck.snapshot())
    assert not any("over_here.txt" in r["text"] for r in _pane(snap, "explorer")["rows"])
    state.shell_cwd = str(sub)
    snap = serialize_event(deck.snapshot())
    assert any("over_here.txt" in r["text"] for r in _pane(snap, "explorer")["rows"])


def test_visual_slots_default_stream_and_open_fills_b(tmp_path):
    deck, _server, _state = _deck(tmp_path)
    assert deck.slot_tuple() == ("stream", "", "a")
    assert deck.open_pane("explorer")
    assert deck.slot_tuple() == ("stream", "explorer", "b")


def test_visual_slots_reject_mirrors(tmp_path):
    deck, _server, _state = _deck(tmp_path)
    assert deck.open_pane("wiki")
    assert deck.set_slot("a", "wiki") is False
    assert deck.slot_tuple()[0] == "stream"
    assert deck.slot_tuple()[1] == "wiki"


def test_visual_slots_solo_pick_opens_the_empty_side(tmp_path):
    """Full-screen stream + pick Files → stream | files, not replace stream."""
    deck, _server, _state = _deck(tmp_path)
    assert deck.set_slot("a", "explorer")
    assert deck.slot_tuple()[:2] == ("stream", "explorer")


def test_visual_slots_close_last_becomes_stream(tmp_path):
    deck, _server, _state = _deck(tmp_path)
    deck.set_slot("a", "explorer")
    deck.close_slot("a")  # leave files solo
    views = [v for v in deck.slot_tuple()[:2] if v]
    assert views == ["explorer"]
    solo = "b" if deck.slot_tuple()[1] == "explorer" else "a"
    assert deck.close_slot(solo)
    assert "stream" in deck.slot_tuple()[:2]


def test_visual_slots_two_panes_hides_stream(tmp_path):
    deck, _server, _state = _deck(tmp_path)
    deck.open_pane("explorer")
    assert deck.set_slot("a", "git")
    a, b, _ = deck.slot_tuple()
    assert a == "git" and b == "explorer"
    assert "stream" not in (a, b)


def test_swap_slots_exchanges_views(tmp_path):
    deck, _server, _state = _deck(tmp_path)
    deck.open_pane("explorer")
    assert deck.set_slot("a", "git")
    assert deck.slot_tuple()[:2] == ("git", "explorer")
    assert deck.swap_slots()
    assert deck.slot_tuple()[:2] == ("explorer", "git")
    deck.close_slot("b")
    assert deck.swap_slots() is False


def test_slot_catalog_leads_with_stream(tmp_path):
    deck, _server, _state = _deck(tmp_path)
    cat = deck.slot_catalog()
    assert cat[0]["id"] == "stream"
    assert cat[0]["label"] == "Home Stream"
    assert cat[1]["id"] == "home"
    assert cat[1]["label"] == "Home Hub"
    ids = {r["id"] for r in cat}
    assert "explorer" in ids and "git" in ids
    assert "wiki" not in ids  # chat view — not on the code pack
    assert "switch" not in ids and "attach" not in ids
    assert deck.set_slot("b", "attach") is False
    assert deck.set_slot("b", "switch") is False
    assert deck.slot_tuple()[1] == ""


def test_pane_catalog_keeps_shared_doors_stable(tmp_path):
    """Same door stays in the same place when the pack filter changes."""
    deck, _server, state = _deck(tmp_path)
    state.scratch = True
    ranks = {}
    for name in ("home", "code", "chat"):
        state.workbench = BUILTIN_WORKBENCHES[name]
        ids = [r["id"] for r in deck.pane_catalog()]
        ranks[name] = ids
        assert "home" not in ids, ids
        assert "projects" not in ids
    shared = set(ranks["home"]) & set(ranks["code"]) & set(ranks["chat"])
    def _rel(seq):
        return [x for x in seq if x in shared]
    assert _rel(ranks["home"]) == _rel(ranks["code"]) == _rel(ranks["chat"])


def test_home_hub_opens_this_slot_by_default(tmp_path):
    """Default: hub row replaces Home so back can return. Stream stays."""
    deck, server, _state = _deck(tmp_path)
    assert deck.open_pane("home")
    assert deck.slot_tuple()[:2] == (STREAM_VIEW, "home")
    snap = server.events("pane_deck")[-1]
    home = _pane(snap, "home")
    stream = next(r for r in home["rows"] if r["address"] == "home://stream")
    assert stream.get("tone") == "empty"
    idx = next(i for i, r in enumerate(home["rows"]) if r["address"] == "home://files")
    deck.handle({"pane": "home", "op": "select", "index": idx})
    deck.handle({"pane": "home", "op": "key", "key": "enter"})
    assert set(deck.slot_tuple()[:2]) == {STREAM_VIEW, "explorer"}
    assert "home" not in deck.slot_tuple()[:2]


def test_home_hub_other_panel_keeps_hub(tmp_path):
    """Knob: open in the other panel — hub stays, Files takes the tape slot."""
    from types import SimpleNamespace

    deck, server, state = _deck(tmp_path)
    state.cfg = SimpleNamespace(hub_open="other", save=lambda: None)
    assert deck.open_pane("home")
    snap = server.events("pane_deck")[-1]
    home = _pane(snap, "home")
    idx = next(i for i, r in enumerate(home["rows"]) if r["address"] == "home://files")
    deck.handle({"pane": "home", "op": "select", "index": idx})
    deck.handle({"pane": "home", "op": "key", "key": "enter"})
    assert set(deck.slot_tuple()[:2]) == {"home", "explorer"}
    snap = server.events("pane_deck")[-1]
    home = _pane(snap, "home")
    files = next(r for r in home["rows"] if r["address"] == "home://files")
    assert files.get("tone") == "empty"


def test_slot_catalog_follows_the_workbench(tmp_path):
    deck, _server, state = _deck(tmp_path, "chat")
    ids = {r["id"] for r in deck.slot_catalog()}
    assert ids == {
        "stream", "home", "menu", "locker", "bookmarks", "sources", "wiki",
        "canvas", "results", "plugins",
    }
    assert "explorer" not in ids
    state.workbench = BUILTIN_WORKBENCHES["code"]
    deck.constrain_slots_to_pack()
    code_ids = {r["id"] for r in deck.slot_catalog()}
    assert "explorer" in code_ids and "git" in code_ids
    assert "wiki" not in code_ids and "plugins" not in code_ids


def test_slot_catalog_lists_other_open_streams(tmp_path):
    deck, server, _state = _deck(tmp_path)
    server._live_stream = {"id": "here", "label": "here"}
    server._open_streams = [
        {"id": "here", "label": "here"},
        {"id": "other", "label": "other-lab"},
    ]
    ids = [r["id"] for r in deck.slot_catalog()]
    assert ids[0] == "stream"
    assert "stream:other" in ids
    assert ids.index("stream:other") < ids.index("home")
    labels = {r["id"]: r["label"] for r in deck.slot_catalog()}
    assert labels["stream"] == "here"
    assert labels["stream:other"] == "other-lab"
    assert "stream:here" not in ids


def test_set_slot_peek_keeps_live_focus(tmp_path):
    deck, server, _state = _deck(tmp_path)
    server._open_streams = [{"id": "other", "label": "other"}]
    assert deck.set_slot("a", "stream:other")
    a, b, focus = deck.slot_tuple()
    assert a == "stream" and b == "stream:other"
    assert focus == "a"
    peeks = server.events("stream_peek")
    assert peeks and peeks[-1]["id"] == "other" and peeks[-1]["slot"] == "b"


def test_set_slot_live_dropdown_keeps_other_peek(tmp_path):
    """Picking a third tape in the live slot must not collapse both to it."""
    deck, server, _state = _deck(tmp_path)
    server._live_stream = {"id": "here", "label": "here"}
    server._open_streams = [
        {"id": "here", "label": "here"},
        {"id": "other", "label": "other"},
        {"id": "third", "label": "third"},
    ]
    server._enter_ok = True
    deck.set_slot("b", "stream:other")
    assert deck.slot_tuple()[:2] == ("stream", "stream:other")
    assert deck.set_slot("a", "stream:third")
    a, b, focus = deck.slot_tuple()
    assert a == "stream" and b == "stream:other"
    assert focus == "a"
    assert server.entered == "third"
    assert server.live_stream_id() == "third"


def test_coerce_live_peek_is_not_a_second_copy(tmp_path):
    deck, server, _state = _deck(tmp_path)
    server._live_stream = {"id": "here", "label": "here"}
    deck._slot_a = "stream"
    deck._slot_b = "stream:here"
    deck._coerce_live_peeks()
    a, b, _ = deck.slot_tuple()
    assert a == "stream"
    assert b != "stream" and b != "stream:here"


def test_set_slot_rejects_live_stream_mirror(tmp_path):
    deck, server, _state = _deck(tmp_path)
    server._live_stream = {"id": "here", "label": "here"}
    assert deck.set_slot("b", "stream:here") is False
    assert deck.slot_tuple()[1] == ""


def test_remap_streams_promotes_peek(tmp_path):
    deck, server, _state = _deck(tmp_path)
    server._open_streams = [
        {"id": "here", "label": "here"},
        {"id": "other", "label": "other"},
    ]
    deck.set_slot("b", "stream:other")
    deck.remap_streams("here", "other")
    assert deck.slot_tuple() == ("stream:here", "stream", "b")


def test_remap_streams_menu_switch_leaves_slots(tmp_path):
    deck, _server, _state = _deck(tmp_path)
    deck.remap_streams("here", "other")
    assert deck.slot_tuple() == ("stream", "", "a")


def test_remap_streams_home_becomes_hub(tmp_path):
    deck, server, _state = _deck(tmp_path)
    server._open_streams = [{"id": "other", "label": "other"}]
    deck.set_slot("b", "stream:other")
    deck.remap_streams("", "other")
    a, b, focus = deck.slot_tuple()
    assert {a, b} == {"home", "stream"}
    assert focus == "b"


def test_close_peek_forgets_open_stream(tmp_path):
    deck, server, _state = _deck(tmp_path)
    server._open_streams = [{"id": "other", "label": "other"}]
    deck.set_slot("b", "stream:other")
    assert deck.close_slot("b")
    assert deck.slot_tuple()[1] == ""
    assert server.forgotten == ["other"]
    assert server.open_streams() == []


def test_focus_slot_enters_peek(tmp_path):
    deck, server, _state = _deck(tmp_path)
    server._open_streams = [{"id": "other", "label": "other"}]
    server._enter_ok = True
    deck.set_slot("b", "stream:other")
    assert deck.focus_slot("b")
    assert server.entered == "other"


def test_constrain_keeps_peek_stream(tmp_path):
    deck, server, state = _deck(tmp_path, "code")
    server._open_streams = [{"id": "other", "label": "other"}]
    deck.set_slot("b", "stream:other")
    state.workbench = BUILTIN_WORKBENCHES["chat"]
    deck._sticky_panes = []
    deck.constrain_slots_to_pack()
    assert deck.slot_tuple()[1] == "stream:other"


def test_constrain_slots_evicts_off_pack_views(tmp_path):
    deck, _server, state = _deck(tmp_path, "code")
    deck.set_slot("a", "explorer")
    deck.set_slot("b", "git")
    state.workbench = BUILTIN_WORKBENCHES["chat"]
    deck._sticky_panes = []
    deck.constrain_slots_to_pack()
    a, b, focus = deck.slot_tuple()
    assert {a, b} == {"stream", ""}
    assert focus == "a"


def test_show_media_inlines_images_only(tmp_path):
    from xlii.face_panes import _FaceMediaSink

    deck, server, state = _deck(tmp_path)
    sink = _FaceMediaSink(server)
    png = state.project.project_root / "shot.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    pdf = state.project.project_root / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 tiny")
    sink.show(f"file://{png}")
    sink.show(f"file://{pdf}")
    outs = server.events("file_out")
    assert outs[0]["kind"] == "image" and outs[0].get("b64")
    assert outs[1]["kind"] == "pdf" and not outs[1].get("b64")
    assert outs[1]["path"] == str(pdf)
