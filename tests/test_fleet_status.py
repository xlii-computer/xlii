"""xlii/fleet_status.py — the /status fleet section's kernel (offline).

The load-bearing contracts: fleet_rows is ZERO-SOCKET (specs + cache only),
roles classify node (fabric-referenced) / machine / storage by the owner's
rules, the probe is injected-transport testable with a hard deadline, and the
cache is global, atomic, corrupt-tolerant, and roster-pruned.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

from xlii import fleet_status as fs


def _mgr(specs: dict):
    """A fake manager: names()+spec() only; get() must NEVER be called."""
    def _get(name):
        raise AssertionError("zero-socket contract violated: manager.get() called")
    return SimpleNamespace(
        names=lambda: sorted(specs),
        spec=lambda n: specs.get(n),
        get=_get,
    )


# --------------------------------------------------------------------------- #
#  role classification
# --------------------------------------------------------------------------- #

def test_classify_role_node_machine_storage():
    fabric = {"box1": {"remote": "aws-vm", "persona": "ixaac"}}
    assert fs.classify_role("aws-vm", {"protocol": "sftp"}, fabric) == ("node", "box1")
    assert fs.classify_role("app-farm", {"protocol": "sftp"}, fabric) == ("machine", None)
    assert fs.classify_role("backups", {"protocol": "webdav"}, fabric) == ("storage", None)
    assert fs.classify_role("old-box", {"protocol": "ftps"}, fabric) == ("machine", None)


def test_classify_role_tolerates_malformed_fabric_entries():
    fabric = {"bad1": "not-a-dict", "bad2": {"no_remote_key": 1}, "ok": {"remote": "x"}}
    assert fs.classify_role("x", {"protocol": "sftp"}, fabric) == ("node", "ok")
    assert fs.classify_role("y", {"protocol": "sftp"}, fabric) == ("machine", None)
    assert fs.classify_role("y", {"protocol": "sftp"}, None) == ("machine", None)


# --------------------------------------------------------------------------- #
#  fleet_rows — the zero-socket contract
# --------------------------------------------------------------------------- #

def test_fleet_rows_is_zero_socket_and_merges_cache():
    mgr = _mgr({
        "aws-vm": {"protocol": "sftp", "host": "1.2.3.4"},
        "backups": {"protocol": "webdav", "base_url": "https://x"},
    })
    fabric = {"box1": {"remote": "aws-vm"}}
    cache = {"probes": {"aws-vm": {"ok": True, "ts": 1000.0, "error": None, "latency_ms": 80}}}
    rows = fs.fleet_rows(manager=mgr, fabric_nodes=fabric, cache=cache)
    by = {r.name: r for r in rows}
    assert by["aws-vm"].role == "node" and by["aws-vm"].presence == "unknown"
    assert by["aws-vm"].reach == "ok" and by["aws-vm"].reach_ts == 1000.0
    assert by["backups"].role == "storage" and by["backups"].presence == "—"
    assert by["backups"].reach is None                    # never probed


def test_fleet_rows_empty_roster():
    assert fs.fleet_rows(manager=_mgr({}), fabric_nodes={}, cache={}) == []


# --------------------------------------------------------------------------- #
#  the probe — injected connector, deadline, per-name results
# --------------------------------------------------------------------------- #

def test_probe_fleet_success_and_failure():
    def connector(name, timeout):
        if name == "bad":
            raise OSError("boom")
    out = fs.probe_fleet(["good", "bad"], connector=connector, timeout=1.0)
    assert out["good"].ok is True and out["good"].latency_ms is not None
    assert out["bad"].ok is False and "boom" in out["bad"].error


def test_probe_fleet_deadline_marks_stragglers_timeout():
    def connector(name, timeout):
        time.sleep(10)  # far past the cap — the deadline abandons it
    out = fs.probe_fleet(["hang"], connector=connector, timeout=0.2)
    assert out["hang"].ok is False and "timeout" in out["hang"].error


def test_probe_fleet_empty():
    assert fs.probe_fleet([]) == {}


# --------------------------------------------------------------------------- #
#  the cache — round-trip, corrupt-tolerant, roster-pruned
# --------------------------------------------------------------------------- #

def test_cache_round_trip_and_prune(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_STATE_DIR", str(tmp_path))
    fs.save_probe_cache({"a": fs.ProbeResult(ok=True, latency_ms=5),
                         "gone": fs.ProbeResult(ok=False, error="x")},
                        roster_names=["a"])          # 'gone' pruned on save
    cache = fs.load_probe_cache()
    assert set(cache["probes"]) == {"a"}
    assert cache["probes"]["a"]["ok"] is True


def test_cache_corrupt_file_loads_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_STATE_DIR", str(tmp_path))
    (tmp_path / fs.PROBE_CACHE_NAME).write_text("{not json")
    assert fs.load_probe_cache() == {}


# --------------------------------------------------------------------------- #
#  rendering — pure strings, dry and factual
# --------------------------------------------------------------------------- #

def test_format_age_bands():
    now = 10_000.0
    assert fs.format_age(now - 30, now) == "30s"
    assert fs.format_age(now - 240, now) == "4m"
    assert fs.format_age(now - 7200, now) == "2h"
    assert fs.format_age(None, now) == ""


def test_format_fleet_lines_shape():
    now = time.time()
    rows = [
        fs.FleetRow(name="aws-vm", protocol="sftp", scheme="sftp", role="node",
                    fabric_node="box1", presence="unknown",
                    reach="ok", reach_ts=now - 240),
        fs.FleetRow(name="backups", protocol="webdav", scheme="dav", role="storage",
                    fabric_node=None, presence="—"),
    ]
    lines = fs.format_fleet_lines(rows, now=now)
    assert lines[0].startswith("[bold]▼ fleet[/bold]")
    assert "2 remote(s)" in lines[0] and "1 node(s)" in lines[0]
    assert "●" in lines[1] and "ok (4m)" in lines[1] and "unknown" in lines[1]
    assert "▪" in lines[2] and "— (never probed)" in lines[2]


def test_format_fleet_lines_stale_marker():
    now = time.time()
    rows = [fs.FleetRow(name="x", protocol="sftp", scheme="sftp", role="machine",
                        fabric_node=None, presence="—",
                        reach="ok", reach_ts=now - 7200)]
    lines = fs.format_fleet_lines(rows, now=now)
    assert "stale" in lines[1]
