"""Project browser P3: the tkinter popup's manifest contract + main().

The tkinter GUI itself isn't unit-tested (needs a display) — same standard as
test_upload.py. We cover the action-aware manifest (read/write, backward compat
with upload's shape, unknown-action fallback) and main() with a stubbed picker.
Disk-only, no network, no GUI.
"""

import xlii.browse_popup as B


def test_manifest_roundtrip_with_action(tmp_path):
    m = tmp_path / "m.json"
    B.write_manifest(str(m), ["/a/b.py", "/c/d.txt"], "reference")
    assert B.read_manifest(str(m)) == ("reference", ["/a/b.py", "/c/d.txt"])


def test_default_action_is_attach(tmp_path):
    m = tmp_path / "m.json"
    B.write_manifest(str(m), ["/x.py"])
    assert B.read_manifest(str(m)) == ("attach", ["/x.py"])


def test_read_missing_or_corrupt(tmp_path):
    assert B.read_manifest(str(tmp_path / "nope.json")) == ("attach", [])
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert B.read_manifest(str(bad)) == ("attach", [])


def test_upload_shape_reads_as_attach(tmp_path):
    """An upload-style manifest (no action key) ingests as attach — back compat."""
    m = tmp_path / "m.json"
    m.write_text('{"files": ["/abs/shot.png"]}')
    assert B.read_manifest(str(m)) == ("attach", ["/abs/shot.png"])


def test_unknown_action_falls_back_to_attach(tmp_path):
    m = tmp_path / "m.json"
    m.write_text('{"action": "nuke", "files": []}')
    assert B.read_manifest(str(m)) == ("attach", [])


def test_main_writes_action_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "pick", lambda root: ("reference", ["/abs/p.py"]))
    m = tmp_path / "out.json"
    assert B.main(["prog", str(m), "--root", str(tmp_path)]) == 0
    assert B.read_manifest(str(m)) == ("reference", ["/abs/p.py"])


def test_main_handles_picker_failure(tmp_path, monkeypatch):
    def boom(root):
        raise RuntimeError("no display")

    monkeypatch.setattr(B, "pick", boom)
    m = tmp_path / "out.json"
    assert B.main(["prog", str(m), "--root", str(tmp_path)]) == 1  # clean fallback signal
    assert B.read_manifest(str(m)) == ("cancel", [])


def test_main_usage_without_manifest():
    assert B.main(["prog"]) == 2
