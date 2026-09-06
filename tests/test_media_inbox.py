"""The media inbox (tauri-face V5) — persist · fabric leg · media:// · /media.

The whole seam offline: persist_inbound moves+sidecars, the daemon serves the
turn from the persisted path and cleans only temp copies, pull_node_media
mirrors binary+sidecar pairs idempotently, the media:// provider lists with
capture metadata, and /media attach lands a file in the Tray.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from xlii import media_in


# ------------------------------------------------------------ persist_inbound


def test_persist_moves_files_and_writes_sidecars(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    a = src / "shot.png"
    a.write_bytes(b"PNG")
    store = tmp_path / "media"

    out = media_in.persist_inbound(
        [a], caption="the nav is wrong", sender="me@phone",
        ts=1_753_300_000.0, source_redacted="aesgcm://x#<redacted>",
        media_dir=store)

    assert len(out) == 1
    dest = out[0]
    assert dest.parent == store
    assert dest.name.endswith("-shot.png")
    assert dest.read_bytes() == b"PNG"
    assert not a.exists(), "source must MOVE, not copy"
    meta = json.loads(dest.with_name(dest.name + ".meta.json").read_text())
    assert meta["caption"] == "the nav is wrong"
    assert meta["sender"] == "me@phone"
    assert meta["ts"] == 1_753_300_000.0
    assert "<redacted>" in meta["source_redacted"]


def test_persist_collision_suffixes(tmp_path):
    store = tmp_path / "media"
    for i in range(2):
        src = tmp_path / f"in{i}"
        src.mkdir()
        f = src / "same.png"
        f.write_bytes(b"X%d" % i)
        media_in.persist_inbound(
            [f], caption="", sender="s", ts=1_753_300_000.0,
            source_redacted="", media_dir=store)
    files = sorted(p.name for p in store.iterdir()
                   if not p.name.endswith(".meta.json"))
    assert len(files) == 2 and files[0] != files[1]


def test_persist_best_effort_per_file(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    good = src / "ok.png"
    good.write_bytes(b"OK")
    gone = src / "vanished.png"  # never created — move will fail
    out = media_in.persist_inbound(
        [gone, good], caption="", sender="s", ts=1_753_300_000.0,
        source_redacted="", media_dir=tmp_path / "media")
    assert [p.read_bytes() for p in out] == [b"OK"]


def test_persist_returns_moved_file_when_sidecar_write_fails(tmp_path, monkeypatch):
    src = tmp_path / "in"
    src.mkdir()
    f = src / "ok.png"
    f.write_bytes(b"OK")
    orig_write_text = Path.write_text

    def fail_sidecar(path, *args, **kwargs):
        if path.name.endswith(".meta.json"):
            raise OSError("simulated sidecar failure")
        return orig_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_sidecar)
    out = media_in.persist_inbound(
        [f], caption="", sender="s", ts=1_753_300_000.0,
        source_redacted="", media_dir=tmp_path / "media")

    assert len(out) == 1
    assert out[0].read_bytes() == b"OK"
    assert not f.exists(), "source must still MOVE when only metadata fails"


# ------------------------------------------------------------ daemon persist


def test_daemon_persist_media_lands_in_persona_store(tmp_path, monkeypatch):
    import pytest

    pytest.importorskip("omemo")
    pytest.importorskip("slixmpp_omemo")
    import xlii.persona as persona_mod
    from xlii.daemon import CommandDaemon

    monkeypatch.setattr(persona_mod, "CHAT_STATE_DIR", tmp_path / "chat")
    src = tmp_path / "tmpmedia"
    src.mkdir()
    f = src / "arrows.png"
    f.write_bytes(b"IMG")

    daemon = object.__new__(CommandDaemon)  # no XMPP init — method under test
    daemon.cfg = SimpleNamespace(fallback_persona="scout")
    key_hex = "ab" * 44  # 88 hex chars — XEP-0454's 12-byte-IV fragment shape
    source = f"aesgcm://share.test/f/arrows.png#{key_hex}"
    out = CommandDaemon._persist_media(
        daemon, [str(f)], "look here", "owner@xmpp", source)
    assert len(out) == 1
    dest = Path(out[0])
    assert dest.parent == tmp_path / "chat" / "scout" / "media"
    assert dest.read_bytes() == b"IMG"
    meta = json.loads(dest.with_name(dest.name + ".meta.json").read_text())
    assert meta["sender"] == "owner@xmpp"
    assert key_hex not in meta["source_redacted"], \
        "crypto material must be redacted"
    assert "<redacted>" in meta["source_redacted"]


def test_daemon_persist_failure_falls_back_to_temp_paths(tmp_path, monkeypatch):
    import pytest

    pytest.importorskip("omemo")
    pytest.importorskip("slixmpp_omemo")
    import xlii.persona as persona_mod
    from xlii.daemon import CommandDaemon

    monkeypatch.setattr(persona_mod, "CHAT_STATE_DIR", tmp_path / "chat")
    daemon = object.__new__(CommandDaemon)
    daemon.cfg = SimpleNamespace(fallback_persona="scout")
    # A path that cannot be moved → persist returns [] → serve from temp.
    out = CommandDaemon._persist_media(
        daemon, [str(tmp_path / "never-existed.png")], "", "s", "")
    assert out == [str(tmp_path / "never-existed.png")]


def test_daemon_persist_partial_failure_keeps_unmoved_temp_paths(tmp_path, monkeypatch):
    import pytest
    import shutil

    pytest.importorskip("omemo")
    pytest.importorskip("slixmpp_omemo")
    import xlii.persona as persona_mod
    from xlii.daemon import CommandDaemon

    monkeypatch.setattr(persona_mod, "CHAT_STATE_DIR", tmp_path / "chat")
    src = tmp_path / "tmpmedia"
    src.mkdir()
    good = src / "ok.png"
    good.write_bytes(b"OK")
    failed = src / "still-temp.png"
    failed.write_bytes(b"TEMP")
    orig_move = shutil.move

    def fail_one(src_path, dst_path, *args, **kwargs):
        if str(src_path).endswith("still-temp.png"):
            raise OSError("simulated move failure")
        return orig_move(src_path, dst_path, *args, **kwargs)

    monkeypatch.setattr(shutil, "move", fail_one)
    daemon = object.__new__(CommandDaemon)
    daemon.cfg = SimpleNamespace(fallback_persona="scout")

    out = CommandDaemon._persist_media(
        daemon, [str(good), str(failed)], "caption", "owner@xmpp", "")

    assert len(out) == 2
    assert any(Path(p).parent == tmp_path / "chat" / "scout" / "media" for p in out)
    assert str(failed) in out
    assert failed.exists(), "dispatch must see the temp before cleanup removes it"


# -------------------------------------------------------------- fabric leg


class _Conn:
    def __init__(self, files):
        self.files = files  # name → bytes

    def listdir(self, path=""):
        return [(n, False, len(b)) for n, b in self.files.items()]

    def read(self, path):
        return self.files[path.rsplit("/", 1)[1]]


def test_pull_node_media_binary_and_sidecar_pairing(tmp_path):
    from xlii.fabric import pull_node_media

    png = bytes(range(256))  # binary — not utf-8
    conn = _Conn({
        "20260724T010101Z-shot.png": png,
        "20260724T010101Z-shot.png.meta.json": b'{"sender": "me@phone"}',
    })
    local = tmp_path / "media"
    res = pull_node_media(conn, "chat/ixaac/media", local, "node1")
    assert res.pulled == 2 and not res.errors
    f = local / "node1-20260724T010101Z-shot.png"
    assert f.read_bytes() == png
    # Pairing survives the node re-key: <file>.meta.json still sits beside it.
    assert (local / (f.name + ".meta.json")).is_file()

    again = pull_node_media(conn, "chat/ixaac/media", local, "node1")
    assert again.pulled == 0 and again.skipped == 2  # idempotent


def test_pull_node_media_missing_dir_is_empty_pull(tmp_path):
    from xlii.fabric import pull_node_media

    class _Missing:
        def listdir(self, path=""):
            raise FileNotFoundError(path)

        def read(self, path):
            raise AssertionError("never")

    res = pull_node_media(_Missing(), "chat/x/media", tmp_path, "n")
    assert res.pulled == 0 and res.skipped == 0 and not res.errors


def test_pull_node_media_rejects_unsafe_names(tmp_path):
    from xlii.fabric import pull_node_media

    conn = _Conn({"../escape.png": b"X"})
    res = pull_node_media(conn, "chat/x/media", tmp_path / "m", "n")
    assert res.pulled == 0 and res.errors


def test_remote_media_dir_shape():
    from xlii.fabric import remote_media_dir

    assert remote_media_dir("", "ixaac") == ".xlii/chat/ixaac/media"
    assert remote_media_dir("/custom/chat/", "bob") == "custom/chat/bob/media"


# ---------------------------------------------------------- media:// + /media


def _seed_store(root: Path) -> Path:
    store = root / "chat" / "ixaac" / "media"
    store.mkdir(parents=True)
    f = store / "20260724T010101Z-shot.png"
    f.write_bytes(b"PNG")
    f.with_name(f.name + ".meta.json").write_text(
        json.dumps({"caption": "fix the nav", "sender": "me@phone",
                    "ts": 1.0, "source_redacted": ""}))
    return store


def _ambient(monkeypatch, tmp_path):
    import xlii.persona as persona_mod
    from xlii import active_session

    monkeypatch.setattr(persona_mod, "CHAT_STATE_DIR", tmp_path / "chat")
    state = SimpleNamespace(project=None, cfg=None, attached_files=[])
    state.attach_file = lambda p, **k: state.attached_files.append(str(p))
    prev = active_session.set_active_session(state)
    monkeypatch.setattr(persona_mod, "resolve_default_persona",
                        lambda **kw: "ixaac")
    return state, prev


def test_media_provider_lists_with_sidecar_meta(tmp_path, monkeypatch):
    from xlii import active_session
    from xlii.addressing import Address
    from xlii.addressing.builtins.media import MediaProvider

    _seed_store(tmp_path)
    state, prev = _ambient(monkeypatch, tmp_path)
    try:
        rows = MediaProvider().list(Address.parse("media://"))
        assert len(rows) == 1  # the sidecar is metadata, never a row
        node = rows[0]
        assert node.name == "20260724T010101Z-shot.png"
        assert node.extra["caption"] == "fix the nav"
        assert node.extra["sender"] == "me@phone"
        data = MediaProvider().read(Address.parse(f"media://{node.name}"))
        assert data == b"PNG"
    finally:
        active_session.set_active_session(prev)


def test_media_provider_containment_and_bare_seat(tmp_path, monkeypatch):
    from xlii import active_session
    from xlii.addressing import Address
    from xlii.addressing.builtins.media import MediaProvider

    _seed_store(tmp_path)
    state, prev = _ambient(monkeypatch, tmp_path)
    try:
        assert MediaProvider().exists(Address.parse("media://../escape")) is False
    finally:
        active_session.set_active_session(prev)
    # No ambient session: media:// is persona-global BY DESIGN (unlike the
    # project-scoped artifacts://) — the default-persona store still lists.
    assert len(MediaProvider().list(Address.parse("media://"))) == 1


def test_media_cmd_lists_and_attaches(tmp_path, monkeypatch, capsys):
    from xlii import active_session
    from xlii.repl_cmds.media import h_media

    _seed_store(tmp_path)
    state, prev = _ambient(monkeypatch, tmp_path)
    printed: list[str] = []
    console = SimpleNamespace(print=lambda *a, **k: printed.append(" ".join(map(str, a))))
    try:
        assert h_media("/media", {"console": console, "state": state}) is True
        assert any("shot.png" in line for line in printed)
        assert any("me@phone" in line for line in printed)

        assert h_media("/media attach 1",
                       {"console": console, "state": state}) is True
        assert len(state.attached_files) == 1
        assert state.attached_files[0].endswith("shot.png")

        printed.clear()
        assert h_media("/media attach nope",
                       {"console": console, "state": state}) is True
        assert any("no such item" in line for line in printed)
    finally:
        active_session.set_active_session(prev)


def test_media_cmd_empty_store_nudges_fabric_pull(tmp_path, monkeypatch):
    from xlii import active_session
    from xlii.repl_cmds.media import h_media

    state, prev = _ambient(monkeypatch, tmp_path)  # no store seeded
    printed: list[str] = []
    console = SimpleNamespace(print=lambda *a, **k: printed.append(" ".join(map(str, a))))
    try:
        h_media("/media", {"console": console, "state": state})
        assert any("fabric pull" in line for line in printed)
    finally:
        active_session.set_active_session(prev)
