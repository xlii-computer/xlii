"""Session attachment by address (xlii.attach) — the inverse of a provider.

Headless: attach/detach an addressable item onto a fake REPLState's /doc channel, dispatched by
scheme. skills:// is client #1; docs:// rides the same channel; unknown schemes no-op."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from xlii import attach


class _FakeState:
    """A minimal REPLState: attach_doc/detach_doc over a list of (name, body) pairs."""

    def __init__(self, root=None):
        self.attached_docs: list = []
        self.project = SimpleNamespace(project_root=root)

    def attach_doc(self, name, body):
        self.attached_docs = [(n, b) for n, b in self.attached_docs if n != name] + [(name, body)]

    def detach_doc(self, name):
        before = len(self.attached_docs)
        self.attached_docs = [(n, b) for n, b in self.attached_docs if n != name]
        return len(self.attached_docs) < before


def test_attach_detach_skill_roundtrip(monkeypatch):
    monkeypatch.setattr("xlii.skills.load_skills",
                        lambda *a, **k: {"deploy": SimpleNamespace(name="deploy", description="ship")})
    monkeypatch.setattr("xlii.skills.render_skill", lambda sk: f"# {sk.name}\n{sk.description}")
    st = _FakeState()
    a = "skills://deploy"

    assert attach.is_attached(st, a) is False
    assert attach.attach_address(st, a) is True
    assert attach.is_attached(st, a) is True
    from xlii.skills import active_skill_names
    assert active_skill_names(st.attached_docs) == {"deploy"}
    assert attach.detach_address(st, a) is True
    assert attach.is_attached(st, a) is False


def test_attach_unknown_skill_is_false(monkeypatch):
    monkeypatch.setattr("xlii.skills.load_skills", lambda *a, **k: {})
    st = _FakeState()
    assert attach.attach_address(st, "skills://nope") is False


def test_attach_detach_doc_roundtrip(monkeypatch):
    import xlii.doc as docmod

    monkeypatch.setattr(docmod, "DOCS_DIR", Path("/tmp/__xlii_doc_test__"))
    # patch Doc to a fake that exists + reads
    monkeypatch.setattr("xlii.doc.Doc",
                        lambda name: SimpleNamespace(exists=lambda: name == "conventions",
                                                     read=lambda: "be kind"))
    st = _FakeState()
    assert attach.attach_address(st, "docs://conventions") is True
    assert attach.is_attached(st, "docs://conventions") is True
    assert attach.detach_address(st, "docs://conventions") is True
    assert attach.attach_address(st, "docs://ghost") is False  # missing doc


def test_attachable_gate():
    assert attach.attachable("skills://x")
    assert attach.attachable("docs://x")
    assert attach.attachable("wiki://x")
    assert attach.attachable("mark://x")        # Decision #1: bookmarks = both verbs (attach rides)
    assert not attach.attachable("file:///x")
    assert not attach.attachable("config://global")
    assert not attach.attachable("jobs://t1")


def _wiki_state(tmp_path):
    st = _FakeState()
    st.project = SimpleNamespace(project_root=None, xli_dir=tmp_path)
    return st


def test_attach_detach_wiki_roundtrip(tmp_path):
    from xlii import wiki as W

    W.write_page(tmp_path, "arch", "# One kernel\n\nbody", sources=["conv://p/t1"])
    st = _wiki_state(tmp_path)
    a = "wiki://arch"

    assert attach.is_attached(st, a) is False
    assert attach.attach_address(st, a) is True
    assert attach.is_attached(st, a) is True
    names = [n for n, _ in st.attached_docs]
    assert names == [attach.WIKI_ATTACH_PREFIX + "arch"]   # rides its own prefix, not a doc name
    assert attach.detach_address(st, a) is True
    assert attach.is_attached(st, a) is False
    assert attach.attach_address(st, "wiki://ghost") is False


def test_attach_wiki_unverified_banner_and_provenance(tmp_path):
    from xlii import wiki as W

    W.write_page(tmp_path, "draft", "claim!", sources=["file://notes.md#L4"])
    st = _wiki_state(tmp_path)
    attach.attach_address(st, "wiki://draft")
    body = st.attached_docs[0][1]
    assert "UNVERIFIED" in body                            # verify-before-trust rides along
    assert "file://notes.md#L4" in body                    # provenance rides along

    W.mark_verified(tmp_path, "draft")
    attach.attach_address(st, "wiki://draft")              # re-attach refreshes the body
    body = st.attached_docs[0][1]
    assert "UNVERIFIED" not in body                        # promoted → no banner
    assert "file://notes.md#L4" in body


def test_attach_wiki_without_a_project_is_false():
    st = _FakeState()                                      # no xli_dir on the project
    assert attach.attach_address(st, "wiki://arch") is False


def test_unknown_scheme_and_stateless_are_false():
    st = _FakeState()
    assert attach.attach_address(st, "file:///x") is False
    assert attach.detach_address(st, "file:///x") is False
    assert attach.attach_address(None, "skills://x") is False
    assert attach.is_attached(None, "skills://x") is False


def _mark_state(monkeypatch, tmp_path, *, active_marks=(), personas=None):
    """A _FakeState with a live turn store + on-disk personas carrying marks."""
    import xlii.persona
    from xlii.transcript import mark_last_turn, write_turn

    pdir = tmp_path / "personas"
    monkeypatch.setattr(xlii.persona, "PERSONAS_DIR", pdir)
    monkeypatch.setattr(xlii.persona, "CHAT_STATE_DIR", tmp_path / "chat")
    pdir.mkdir(parents=True, exist_ok=True)
    for name, marks in (personas or {}).items():
        (pdir / f"{name}.md").write_text(f"You are {name}.")
        p = xlii.persona.Persona(name)
        for mk in marks:
            write_turn(p.turns_dir, "q", "a")
            mark_last_turn(p.turns_dir, mk)

    st = _FakeState()
    if active_marks:
        active_td = tmp_path / "active"
        active_td.mkdir(exist_ok=True)
        for mk in active_marks:
            write_turn(active_td, "q", "a")
            mark_last_turn(active_td, mk)
        st.profile = SimpleNamespace(memory=SimpleNamespace(turns_dir=active_td))
    return st


def test_attach_mark_active_store_roundtrip(monkeypatch, tmp_path):
    st = _mark_state(monkeypatch, tmp_path, active_marks=["note"])
    a = "mark://note"

    assert attach.is_attached(st, a) is False
    assert attach.attach_address(st, a) is True
    assert attach.is_attached(st, a) is True
    name, body = st.attached_docs[0]
    assert name == attach.MARK_ATTACH_PREFIX + "note"   # rides its own prefix, like skills:
    assert body.startswith("# mark: note")              # the recall span, rendered markdown
    assert attach.detach_address(st, a) is True
    assert attach.is_attached(st, a) is False


def test_attach_mark_unique_persona_mark_resolves_globally(monkeypatch, tmp_path):
    # The mark lives ONLY in bob's store — the global pane can still attach it bare.
    st = _mark_state(monkeypatch, tmp_path, personas={"bob": ["auth-insight"]})
    assert attach.attach_address(st, "mark://auth-insight") is True
    assert [n for n, _ in st.attached_docs] == [attach.MARK_ATTACH_PREFIX + "auth-insight"]


def test_attach_mark_clash_requires_a_qualifier(monkeypatch, tmp_path):
    st = _mark_state(monkeypatch, tmp_path, personas={"bob": ["plan"], "sol": ["plan"]})

    assert attach.attach_address(st, "mark://plan") is False        # ambiguous — qualify
    assert attach.attach_address(st, "mark://bob:plan") is True
    assert [n for n, _ in st.attached_docs] == [attach.MARK_ATTACH_PREFIX + "plan"]  # no baggage
    assert attach.is_attached(st, "mark://sol:plan") is True        # same rider either way
    assert attach.detach_address(st, "mark://sol:plan") is True
    assert attach.is_attached(st, "mark://plan") is False


def test_attach_mark_unknown_is_false(monkeypatch, tmp_path):
    st = _mark_state(monkeypatch, tmp_path, personas={"bob": ["x"]})
    assert attach.attach_address(st, "mark://ghost") is False
    assert attach.attach_address(st, "mark://bob:ghost") is False


def test_attach_mark_bare_name_with_a_colon_stays_whole(monkeypatch, tmp_path):
    # "ratio 3:1" is not a persona qualifier — the bare-name rule must not split it.
    st = _mark_state(monkeypatch, tmp_path, active_marks=["ratio 3:1"])
    assert attach.attach_address(st, "mark://ratio 3:1") is True
    assert [n for n, _ in st.attached_docs] == [attach.MARK_ATTACH_PREFIX + "ratio 3:1"]
    assert attach.detach_address(st, "mark://ratio 3:1") is True


def test_dock_routes_attach_detach_to_session_sink(monkeypatch):
    monkeypatch.setattr("xlii.skills.load_skills",
                        lambda *a, **k: {"deploy": SimpleNamespace(name="deploy", description="d")})
    monkeypatch.setattr("xlii.skills.render_skill", lambda sk: "body")
    from xlii.panes import ATTACH, DETACH, Outcome
    from xlii.panes.dock import Dock

    st = _FakeState()

    class _Sink:
        def attach(self, address):
            attach.attach_address(st, address)

        def detach(self, address):
            attach.detach_address(st, address)

    dock = Dock(slots=("A",))
    dock.set_session_sink(_Sink())
    dock.dispatch(Outcome(ATTACH, "skills://deploy"), from_slot="A")
    assert attach.is_attached(st, "skills://deploy")
    dock.dispatch(Outcome(DETACH, "skills://deploy"), from_slot="A")
    assert not attach.is_attached(st, "skills://deploy")


def test_dock_attach_without_sink_raises():
    import pytest

    from xlii.panes import ATTACH, Outcome
    from xlii.panes.dock import Dock

    dock = Dock(slots=("A",))
    with pytest.raises(NotImplementedError):
        dock.dispatch(Outcome(ATTACH, "skills://x"), from_slot="A")


class _LockerState:
    """A REPLState fake with a locker (attached_files + remove_file) and the /doc channel."""

    def __init__(self):
        self.attached_files = [{"name": "cat.png", "path": "/tmp/cat.png", "kind": "image"}]
        self.attached_docs: list = []

    def detach_doc(self, name):
        return False

    def remove_file(self, name_or_path):
        before = len(self.attached_files)
        self.attached_files = [e for e in self.attached_files
                               if e["name"] != name_or_path and e["path"] != name_or_path]
        return len(self.attached_files) < before


def test_locker_detach_and_is_attached():
    st = _LockerState()
    assert attach.is_attached(st, "locker://cat.png") is True
    assert attach.detach_address(st, "locker://cat.png") is True
    assert attach.is_attached(st, "locker://cat.png") is False
    # locker is NOT in the attach gate — a locker entry is already attached (detach only)
    assert not attach.attachable("locker://cat.png")


def test_dock_routes_show_media_to_media_sink():
    from xlii.panes import SHOW_MEDIA, Outcome
    from xlii.panes.dock import Dock

    shown = []
    dock = Dock(slots=("A",))
    dock.set_media_sink(SimpleNamespace(show=lambda a: shown.append(a)))
    dock.dispatch(Outcome(SHOW_MEDIA, "file:///tmp/cat.png"), from_slot="A")
    assert shown == ["file:///tmp/cat.png"]


def test_dock_show_media_without_sink_raises():
    import pytest

    from xlii.panes import SHOW_MEDIA, Outcome
    from xlii.panes.dock import Dock

    with pytest.raises(NotImplementedError):
        Dock(slots=("A",)).dispatch(Outcome(SHOW_MEDIA, "file:///x.png"), from_slot="A")


class _FocusState(_FakeState):
    """Locker-capable fake for next-turn focus (file:// → attach_file once)."""

    def __init__(self, root):
        super().__init__(root)
        self.attached_files = []
        self.project = SimpleNamespace(project_root=root, xli_dir=root / ".xlii")

    def attach_file(self, path, *, once=False):
        entry = {
            "name": Path(path).name,
            "path": str(Path(path).resolve()),
            "enabled": True,
            "once": once,
        }
        self.attached_files.append(entry)
        return entry

    def set_file_enabled(self, name_or_path, enabled):
        hit = False
        for e in self.attached_files:
            if e["name"] == name_or_path or e["path"] == name_or_path:
                e["enabled"] = bool(enabled)
                hit = True
        return hit


def test_focus_file_stages_once_inside_project(tmp_path):
    from xlii.addressing.builtins.register import register_builtins

    register_builtins()
    (tmp_path / ".xlii").mkdir()
    f = tmp_path / "notes.md"
    f.write_text("body\n", encoding="utf-8")
    st = _FocusState(tmp_path)
    item = attach.focus_address(st, f"file://{f}")
    assert item is not None
    assert item["once"] is True
    assert item["title"] == "notes.md"
    assert st.attached_files[0]["once"] is True
    assert Path(st.last_focus["path"]).name == "notes.md"
    assert attach.unfocus_address(st, item) is True
    assert st.attached_files[0]["enabled"] is False


def test_focus_file_outside_project_is_none(tmp_path, tmp_path_factory):
    from xlii.addressing.builtins.register import register_builtins

    register_builtins()
    (tmp_path / ".xlii").mkdir()
    outsider = tmp_path_factory.mktemp("out") / "secret.txt"
    outsider.write_text("nope\n", encoding="utf-8")
    st = _FocusState(tmp_path)
    assert attach.focus_address(st, f"file://{outsider}") is None
    assert st.attached_files == []


def test_focus_scratch_desk_allows_any_previewed_file(tmp_path, tmp_path_factory):
    """Project-less scratch is roam — viewing a file is the consent."""
    from xlii.addressing.builtins.register import register_builtins

    register_builtins()
    (tmp_path / ".xlii").mkdir()
    outsider = tmp_path_factory.mktemp("out") / "notes.txt"
    outsider.write_text("ok\n", encoding="utf-8")
    st = _FocusState(tmp_path)
    st.scratch = True
    item = attach.focus_address(st, f"file://{outsider}")
    assert item is not None
    assert item["title"] == "notes.txt"
    assert st.attached_files[0]["once"] is True


def test_focus_scratch_named_project_allows_outside_file(tmp_path, tmp_path_factory):
    from xlii.addressing.builtins.register import register_builtins

    register_builtins()
    (tmp_path / ".xlii").mkdir()
    outsider = tmp_path_factory.mktemp("out") / "pic.txt"
    outsider.write_text("x\n", encoding="utf-8")
    st = _FocusState(tmp_path)
    st.project.name = "scratch/home"
    item = attach.focus_address(st, f"file://{outsider}")
    assert item is not None
    assert item["title"] == "pic.txt"


def test_focus_canvas_and_artifacts_use_store_path(tmp_path, monkeypatch):
    from xlii.addressing.builtins.register import register_builtins
    from xlii.artifacts import write_artifact

    register_builtins()
    (tmp_path / ".xlii").mkdir()
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000a49444154789c6360000002000154a24f8b0000000049454e44ae426082"
    )
    sess = _FocusState(tmp_path)
    monkeypatch.setattr("xlii.active_session.active_session", lambda: sess)
    rel = write_artifact(tmp_path, png, ext="png")
    name = rel.rsplit("/", 1)[-1]
    for addr in (f"artifacts://{name}", f"canvas://{name}"):
        item = attach.focus_address(sess, addr)
        assert item is not None, addr
        assert item["once"] is True
        assert item["title"] == name
    durable = attach.focus_address(sess, f"canvas://{name}", once=False)
    assert durable is not None and durable["once"] is False
    assert durable["role"] == "canvas"
