"""U2 of the upload locker (proposals/upload-locker.md): the `/upload` popup
producer. The tkinter GUI itself isn't unit-tested (needs a display); we cover
the manifest contract, `main()` with a stubbed picker, manifest ingestion, the
inline-path path, and the headless fallback. Disk-only, no network, no GUI.
"""

import xlii.repl_cmds.locker as locker_mod
import xlii.upload_popup as U
from xlii.commands import dispatch_repl_command
from xlii.repl import REPLState
from xlii.repl_cmds import register_all
from tests.helpers import FakeConsole, make_agent

register_all()


def _state(tmp_path, *, sub="proj"):
    root = tmp_path / sub
    (root / ".xlii").mkdir(parents=True, exist_ok=True)
    agent = make_agent(root)
    return REPLState(console=FakeConsole(), agent=agent, project=agent.project,
                     cfg=agent.cfg, pool=agent.pool)


def _img(tmp_path, name="a.png", data=b"PNG"):
    p = tmp_path / name
    p.write_bytes(data)
    return p


# --------------------------------------------------------------------------- #
#  manifest contract
# --------------------------------------------------------------------------- #

def test_manifest_roundtrip(tmp_path):
    m = tmp_path / "m.json"
    U.write_manifest(str(m), ["/a/b.png", "/c/d.txt"])
    assert U.read_manifest(str(m)) == ["/a/b.png", "/c/d.txt"]


def test_read_manifest_missing_or_corrupt(tmp_path):
    assert U.read_manifest(str(tmp_path / "nope.json")) == []
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert U.read_manifest(str(bad)) == []


def test_main_writes_picked_files(tmp_path, monkeypatch):
    img = _img(tmp_path)
    monkeypatch.setattr(U, "pick_files", lambda: [str(img)])
    m = tmp_path / "out.json"
    assert U.main(["prog", str(m)]) == 0
    assert U.read_manifest(str(m)) == [str(img)]


def test_main_handles_picker_failure(tmp_path, monkeypatch):
    def boom():
        raise RuntimeError("no display")
    monkeypatch.setattr(U, "pick_files", boom)
    m = tmp_path / "out.json"
    assert U.main(["prog", str(m)]) == 1     # clean signal for the REPL to fall back
    assert U.read_manifest(str(m)) == []


def test_main_usage_without_manifest():
    assert U.main(["prog"]) == 2


# --------------------------------------------------------------------------- #
#  ingestion into the locker
# --------------------------------------------------------------------------- #

def test_ingest_manifest_attaches(tmp_path):
    state = _state(tmp_path)
    a, b = _img(tmp_path, "a.png"), _img(tmp_path, "b.txt", b"hi")
    m = tmp_path / "m.json"
    U.write_manifest(str(m), [str(a), str(b)])
    names = locker_mod._ingest_upload_manifest(state, str(m), once=False)
    assert set(names) == {"a.png", "b.txt"}
    assert len(state.attached_files) == 2
    assert all(e["enabled"] for e in state.attached_files)


# --------------------------------------------------------------------------- #
#  /upload command
# --------------------------------------------------------------------------- #

def test_upload_inline_paths(tmp_path):
    state = _state(tmp_path)
    img = _img(tmp_path, "shot.png")
    dispatch_repl_command(f"/upload {img}", state.as_context_dict())
    assert any(e["name"] == "shot.png" and e["enabled"] for e in state.attached_files)


def test_upload_inline_once(tmp_path):
    state = _state(tmp_path)
    img = _img(tmp_path, "once.png")
    dispatch_repl_command(f"/upload {img} --once", state.as_context_dict())
    assert state.attached_files[0]["once"] is True


def test_upload_inline_missing_file_not_added(tmp_path):
    state = _state(tmp_path)
    dispatch_repl_command("/upload /no/such/file.png", state.as_context_dict())
    assert state.attached_files == []


def test_upload_headless_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(locker_mod, "_has_display", lambda: False)
    state = _state(tmp_path)
    dispatch_repl_command("/upload", state.as_context_dict())  # no inline paths, no display
    assert state.attached_files == []                          # nothing staged
    assert "display" in state.console.text.lower()             # the hint was printed
