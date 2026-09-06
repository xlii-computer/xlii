"""xlii/outbox.py — the local delivery channel (tui-media-delivery P0).

The elephant-bug fix: local surfaces grant a session outbox (riding the same
apply_outbox_gate the phone uses) and drain it after every turn. Contracts:
grant is nesting-safe (never steals a caller's dir; releases only what it
granted), drain renders images via maybe_preview + moves everything to
delivered/ (idempotent, never raises), release tears down local grants only.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import xlii.outbox as ob


def _session(outbox=None):
    return SimpleNamespace(outbox_dir=outbox)


def test_grant_is_local_and_nesting_safe(tmp_path):
    s = _session()
    granted = ob.grant_local_outbox(s)
    assert granted is not None and s.outbox_dir == granted
    assert getattr(s, "_local_outbox") is True
    # A second grant (nested surface, e.g. /tui) does NOT re-grant…
    assert ob.grant_local_outbox(s) is None
    # …and a caller-granted dir (the daemon's --outbox) is never touched.
    d = _session(outbox=tmp_path)
    assert ob.grant_local_outbox(d) is None
    assert d.outbox_dir == tmp_path and not getattr(d, "_local_outbox", False)
    ob.release_local_outbox(s)


def test_drain_renders_images_and_moves_to_delivered(tmp_path, monkeypatch):
    previews = []
    monkeypatch.setattr("xlii.terminal_image.maybe_preview",
                        lambda path, **kw: previews.append((Path(path), kw)))
    s = _session()
    outbox = ob.grant_local_outbox(s)
    (outbox / "render.png").write_bytes(b"\x89PNG")
    (outbox / "report.pdf").write_bytes(b"%PDF")

    printed = []
    console = SimpleNamespace(print=lambda *a, **k: printed.append(a[0] if a else ""))
    n = ob.drain_outbox(s, console)
    assert n == 2
    # The image went through the /imagine preview seam, force=True.
    assert len(previews) == 1
    assert previews[0][0].name == "render.png" and previews[0][1]["force"] is True
    # Preview path is the STABLE delivered/ location (moved before preview).
    assert previews[0][0].parent.name == "delivered"
    # The non-image printed a delivery line.
    assert any("report.pdf" in str(p) for p in printed)
    # Everything moved out of the hot dir — a second drain is a no-op.
    assert ob.drain_outbox(s, console) == 0
    ob.release_local_outbox(s)


def test_drain_skips_non_local_and_missing(tmp_path):
    # Daemon-granted (no _local_outbox marker): drain must not touch it.
    d = _session(outbox=tmp_path)
    (tmp_path / "x.png").write_bytes(b"p")
    assert ob.drain_outbox(d, None) == 0
    assert (tmp_path / "x.png").exists()
    # Vanished dir: never raises.
    s = _session()
    granted = ob.grant_local_outbox(s)
    import shutil
    shutil.rmtree(granted)
    assert ob.drain_outbox(s, None) == 0
    ob.release_local_outbox(s)


def test_release_only_tears_down_local_grants(tmp_path):
    s = _session()
    granted = ob.grant_local_outbox(s)
    ob.release_local_outbox(s)
    assert s.outbox_dir is None and not granted.exists()
    # A caller-granted dir survives a (mistaken) release call.
    d = _session(outbox=tmp_path)
    ob.release_local_outbox(d)
    assert d.outbox_dir == tmp_path and tmp_path.exists()


def test_name_collisions_in_delivered_keep_both(tmp_path, monkeypatch):
    monkeypatch.setattr("xlii.terminal_image.maybe_preview", lambda *a, **k: None)
    s = _session()
    outbox = ob.grant_local_outbox(s)
    (outbox / "a.png").write_bytes(b"one")
    ob.drain_outbox(s, None)
    (outbox / "a.png").write_bytes(b"two")
    ob.drain_outbox(s, None)
    done = outbox / "delivered"
    assert (done / "a.png").read_bytes() == b"one"
    assert (done / "1-a.png").read_bytes() == b"two"
    ob.release_local_outbox(s)
