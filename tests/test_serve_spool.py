"""serve-public V1 — serve-grants.json spool (atomicio, 0600, degrade-empty).

Pinned against the merge contract in proposals/serve-public-fleet.md.
Run:  pytest tests/test_serve_spool.py
"""

from __future__ import annotations

import json
import os
import stat

import pytest

from xlii.serve_gate import mint_code, normalize_code
from xlii.serve_spool import append_pending, drain_pending, spool_path, take_pending


def test_spool_path_is_under_state_dir(tmp_path):
    assert spool_path(tmp_path) == tmp_path / "serve-grants.json"


def test_append_drain_round_trip(tmp_path):
    code = mint_code()
    append_pending(tmp_path, code, ttl_s=300, mode="full", now=1_700_000_000.0)
    path = spool_path(tmp_path)
    assert path.is_file()
    # 0600 — no group/other bits
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert len(data["pending"]) == 1
    entry = data["pending"][0]
    assert entry["code"] == normalize_code(code)
    assert entry["ttl_s"] == 300
    assert entry["mode"] == "full"
    assert entry["minted_at"] == 1_700_000_000.0

    pending = drain_pending(tmp_path)
    assert pending == [entry]
    # Cleared after drain
    assert drain_pending(tmp_path) == []
    cleared = json.loads(path.read_text(encoding="utf-8"))
    assert cleared["pending"] == []


def test_append_accumulates_and_replaces_same_code(tmp_path):
    c1, c2 = mint_code(), mint_code()
    append_pending(tmp_path, c1, ttl_s=60, mode="full", now=10.0)
    append_pending(tmp_path, c2, ttl_s=60, mode="preview", now=11.0)
    # Re-mint c1 with new ttl/mode — replaces, does not duplicate
    append_pending(tmp_path, c1, ttl_s=120, mode="preview", now=12.0)
    pending = drain_pending(tmp_path)
    codes = {p["code"]: p for p in pending}
    assert set(codes) == {normalize_code(c1), normalize_code(c2)}
    assert codes[normalize_code(c1)]["ttl_s"] == 120
    assert codes[normalize_code(c1)]["mode"] == "preview"
    assert codes[normalize_code(c2)]["mode"] == "preview"


def test_take_pending_one_code_leaves_the_rest(tmp_path):
    c1, c2 = mint_code(), mint_code()
    append_pending(tmp_path, c1, ttl_s=60, mode="full", now=10.0)
    append_pending(tmp_path, c2, ttl_s=60, mode="preview", now=11.0)
    got = take_pending(tmp_path, c1, now=12.0)
    assert got is not None
    assert got["code"] == normalize_code(c1)
    assert got["mode"] == "full"
    assert take_pending(tmp_path, c1, now=13.0) is None
    leftover = drain_pending(tmp_path)
    assert [p["code"] for p in leftover] == [normalize_code(c2)]


def test_take_pending_expired_is_gone(tmp_path):
    code = mint_code()
    append_pending(tmp_path, code, ttl_s=10, mode="full", now=1.0)
    assert take_pending(tmp_path, code, now=12.0) is None
    assert drain_pending(tmp_path) == []


def test_missing_file_drains_empty(tmp_path):
    assert drain_pending(tmp_path) == []
    assert not spool_path(tmp_path).exists()


def test_corrupt_json_degrades_to_empty(tmp_path):
    path = spool_path(tmp_path)
    path.write_text("{not json", encoding="utf-8")
    assert drain_pending(tmp_path) == []
    # Drain rewrites a clean empty spool
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"version": 1, "pending": []}


def test_junk_entries_dropped_on_read(tmp_path):
    path = spool_path(tmp_path)
    good = normalize_code(mint_code())
    path.write_text(
        json.dumps({
            "version": 1,
            "pending": [
                {"code": good, "minted_at": 1.0, "ttl_s": 60, "mode": "full"},
                {"code": "!!!!!!!!", "minted_at": 1.0, "ttl_s": 60, "mode": "full"},
                "not-a-dict",
                {"code": good[:4], "minted_at": 1.0, "ttl_s": 60, "mode": "full"},
            ],
        }),
        encoding="utf-8",
    )
    pending = drain_pending(tmp_path)
    assert pending == [{
        "code": good,
        "minted_at": 1.0,
        "ttl_s": 60,
        "mode": "full",
    }]


def test_append_rejects_bad_inputs(tmp_path):
    with pytest.raises(ValueError):
        append_pending(tmp_path, mint_code(), ttl_s=60, mode="write", now=0.0)
    with pytest.raises(ValueError):
        append_pending(tmp_path, "SHORT", ttl_s=60, mode="full", now=0.0)
    with pytest.raises(ValueError):
        append_pending(tmp_path, mint_code(), ttl_s=0, mode="full", now=0.0)


def test_atomic_write_leaves_no_partial_on_crash(tmp_path, monkeypatch):
    """If the atomic writer raises mid-flight, an existing spool stays intact."""
    code = mint_code()
    append_pending(tmp_path, code, ttl_s=60, mode="full", now=1.0)
    path = spool_path(tmp_path)
    before = path.read_text(encoding="utf-8")

    import xlii.serve_spool as spool_mod

    def boom(*_a, **_k):
        raise OSError("simulated crash")

    monkeypatch.setattr(spool_mod, "write_text_atomic", boom)
    with pytest.raises(OSError):
        append_pending(tmp_path, mint_code(), ttl_s=60, mode="preview", now=2.0)
    assert path.read_text(encoding="utf-8") == before
    # No orphaned *.tmp siblings left behind by a failed replace (atomicio cleans up)
    leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_mode_stays_0600_after_rewrite(tmp_path):
    append_pending(tmp_path, mint_code(), ttl_s=60, mode="full", now=1.0)
    path = spool_path(tmp_path)
    # Deliberately loosen, then rewrite — atomicio must re-apply 0600
    os.chmod(path, 0o644)
    append_pending(tmp_path, mint_code(), ttl_s=60, mode="full", now=2.0)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_rmw_cycles_hold_an_exclusive_flock(tmp_path):
    """append/drain serialize their read-modify-write via flock on a .lock
    sibling — the daemon, the CLI minter, and serve's drain all write this file,
    and an unserialized RMW interleave can resurrect drained (possibly already
    paired) codes back into the spool."""
    import fcntl
    from xlii import serve_spool

    seen = {}
    orig_read = serve_spool._read

    def read_probe(path):
        lock = path.with_name(path.name + ".lock")
        fd = os.open(lock, os.O_RDWR)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                seen["held"] = True
            else:  # pragma: no cover - the pin failing
                fcntl.flock(fd, fcntl.LOCK_UN)
                seen["held"] = False
        finally:
            os.close(fd)
        return orig_read(path)

    monkey = pytest.MonkeyPatch()
    monkey.setattr(serve_spool, "_read", read_probe)
    try:
        append_pending(tmp_path, mint_code(), ttl_s=300, mode="full", now=0.0)
        held_during_append = seen.pop("held")
        assert held_during_append is True
        drain_pending(tmp_path)
        held_during_drain = seen.pop("held")
        assert held_during_drain is True
    finally:
        monkey.undo()


# --------------------------------------------------------------------------- #
#  Shared-seam extensions: default_state_dir + the revoke queue
# --------------------------------------------------------------------------- #


def test_default_state_dir_honors_env(monkeypatch, tmp_path):
    from xlii.serve_spool import default_state_dir
    monkeypatch.setenv("XLII_STATE_DIR", str(tmp_path))
    assert default_state_dir() == tmp_path


def test_revoke_queue_append_drain_dedupe(tmp_path):
    from xlii.serve_spool import append_revoke, drain_revokes, revokes_path

    append_revoke(tmp_path, "sid-1")
    append_revoke(tmp_path, "sid-2")
    append_revoke(tmp_path, "sid-1")          # dedupe
    append_revoke(tmp_path, "all")            # sentinel is a plain entry
    assert stat.S_IMODE(revokes_path(tmp_path).stat().st_mode) == 0o600

    assert drain_revokes(tmp_path) == ["sid-1", "sid-2", "all"]
    assert drain_revokes(tmp_path) == []      # cleared

    data = json.loads(revokes_path(tmp_path).read_text(encoding="utf-8"))
    assert data == {"version": 1, "pending": []}


def test_revoke_queue_rejects_junk_and_degrades(tmp_path):
    from xlii.serve_spool import append_revoke, drain_revokes, revokes_path

    with pytest.raises(ValueError):
        append_revoke(tmp_path, "")
    with pytest.raises(ValueError):
        append_revoke(tmp_path, "x" * 300)    # unbounded targets stay out of file+audit

    revokes_path(tmp_path).write_text("{not json", encoding="utf-8")
    assert drain_revokes(tmp_path) == []      # corrupt → empty, never a crash

    revokes_path(tmp_path).write_text(
        json.dumps({"version": 1, "pending": ["ok", 7, "", "y" * 300]}),
        encoding="utf-8",
    )
    assert drain_revokes(tmp_path) == ["ok"]  # junk entries silently dropped
