"""Fabric node→center pull (F3, SFTP MVP) — the pure transport core.

Pins the ingest naming (idempotent, node-namespaced, chronological) and the
mirror-down behavior against a fake remote-fs connection. The sftp wire and the
Collection drain are exercised elsewhere / reused; here we lock the logic that
decides what lands on the throne and under what name.
"""

from __future__ import annotations

from types import SimpleNamespace

from xlii.fabric import (
    ingest_name,
    last_pull_epoch,
    maybe_auto_pull,
    pull_due,
    pull_interval_s,
    pull_node_turns,
    pull_roster,
    recall_label,
    remote_turns_dir,
    source_from_name,
    stamp_pull,
    via_tag,
)


# --------------------------------------------------------------------------- #
#  ingest naming
# --------------------------------------------------------------------------- #

def test_ingest_name_rekeys_sequence_under_node_preserving_timestamp():
    # The sortable timestamp stem is kept (chronological interleave with the
    # throne's own turns); the node-local -NNNN is re-keyed under the node so it
    # can't collide with the throne's sequence. Provenance is in the name.
    assert ingest_name("20260429T123456Z-0042.md", "node1") == "20260429T123456Z-node1-0042.md"


def test_ingest_name_is_pure_function_of_name_and_node():
    # Same source → same target (this is what makes a re-pull idempotent).
    a = ingest_name("20260429T123456Z-0001.md", "throne")
    b = ingest_name("20260429T123456Z-0001.md", "throne")
    assert a == b == "20260429T123456Z-throne-0001.md"


def test_ingest_name_defensively_prefixes_a_nonstandard_name():
    # A name that isn't a turn file still lands uniquely under the node rather
    # than being dropped or colliding.
    assert ingest_name("notes.md", "node1") == "node1-notes.md"


def test_remote_turns_dir_default_and_custom():
    assert remote_turns_dir("", "ixaac") == ".xlii/chat/ixaac/turns"
    assert remote_turns_dir("/srv/xlii/chat/", "ixaac") == "srv/xlii/chat/ixaac/turns"


# --------------------------------------------------------------------------- #
#  the pull (against a fake remote-fs connection)
# --------------------------------------------------------------------------- #

class FakeConn:
    """The slice of RemoteFsConnection the pull uses: listdir + read."""

    def __init__(self, files, *, listdir_error=None, read_errors=None):
        self.files = dict(files)             # {basename: bytes}
        self.listdir_error = listdir_error
        self.read_errors = set(read_errors or ())
        self.reads = []

    def listdir(self, path=""):
        if self.listdir_error is not None:
            raise self.listdir_error
        return [(name, False, len(data)) for name, data in self.files.items()]

    def read(self, path):
        self.reads.append(path)
        name = path.rsplit("/", 1)[-1]
        if name in self.read_errors:
            raise OSError("transfer failed")
        return self.files[name]


def _turns(*names):
    return {n: f"# turn {n}".encode() for n in names}


def test_pull_writes_new_turns_with_node_namespaced_names(tmp_path):
    conn = FakeConn(_turns("20260429T120000Z-0001.md", "20260429T120500Z-0002.md"))
    res = pull_node_turns(conn, ".xlii/chat/ixaac/turns", tmp_path, "node1")

    assert (res.pulled, res.skipped, res.errors) == (2, 0, [])
    landed = sorted(p.name for p in tmp_path.glob("*.md"))
    assert landed == ["20260429T120000Z-node1-0001.md", "20260429T120500Z-node1-0002.md"]
    # Bytes are copied verbatim.
    assert (tmp_path / "20260429T120000Z-node1-0001.md").read_bytes() == b"# turn 20260429T120000Z-0001.md"


def test_pull_is_idempotent(tmp_path):
    conn = FakeConn(_turns("20260429T120000Z-0001.md"))
    first = pull_node_turns(conn, "d", tmp_path, "node1")
    second = pull_node_turns(conn, "d", tmp_path, "node1")
    assert first.pulled == 1 and first.skipped == 0
    assert second.pulled == 0 and second.skipped == 1     # already present → skipped
    assert len(list(tmp_path.glob("*.md"))) == 1          # no duplicate


def test_pull_missing_remote_dir_is_an_empty_pull_not_an_error(tmp_path):
    conn = FakeConn({}, listdir_error=FileNotFoundError("no such dir"))
    res = pull_node_turns(conn, "d", tmp_path, "node1")
    assert (res.pulled, res.skipped, res.errors) == (0, 0, [])


def test_pull_collects_per_file_errors_without_aborting(tmp_path):
    conn = FakeConn(_turns("20260429T120000Z-0001.md", "20260429T120500Z-0002.md"),
                    read_errors={"20260429T120000Z-0001.md"})
    res = pull_node_turns(conn, "d", tmp_path, "node1")
    assert res.pulled == 1                                # the good one still landed
    assert len(res.errors) == 1 and "0001" in res.errors[0]


def test_pull_dry_run_counts_but_writes_nothing(tmp_path):
    conn = FakeConn(_turns("20260429T120000Z-0001.md"))
    res = pull_node_turns(conn, "d", tmp_path, "node1", dry_run=True)
    assert res.pulled == 1
    assert list(tmp_path.glob("*.md")) == []              # nothing written
    assert conn.reads == []                               # and nothing fetched


def test_pull_ignores_directories_and_non_md_entries(tmp_path):
    conn = FakeConn({})
    conn.files = {}  # craft a mixed listing directly
    conn.listdir = lambda path="": [
        ("20260429T120000Z-0001.md", False, 10),
        ("subdir", True, None),
        ("scratch.txt", False, 5),
    ]
    conn.read = lambda path: b"# turn"
    res = pull_node_turns(conn, "d", tmp_path, "node1")
    assert res.pulled == 1
    assert [p.name for p in tmp_path.glob("*")] == ["20260429T120000Z-node1-0001.md"]


def test_pull_rejects_unsafe_ingest_names(tmp_path):
    """A node name or remote basename that would escape local_dir is skipped."""
    conn = FakeConn({"notes.md": b"# turn"})
    res = pull_node_turns(conn, "d", tmp_path, "../evil")
    assert res.pulled == 0 and res.skipped == 0 and len(res.errors) == 1
    assert "unsafe ingest name" in res.errors[0]
    assert list(tmp_path.glob("*.md")) == []

    conn2 = FakeConn({"foo/bar.md": b"# turn"})
    res2 = pull_node_turns(conn2, "d", tmp_path, "node1")
    assert res2.pulled == 0 and res2.skipped == 0 and len(res2.errors) == 1
    assert "unsafe ingest name" in res2.errors[0]


# --------------------------------------------------------------------------- #
#  body tags (filename → recall)
# --------------------------------------------------------------------------- #

def test_source_from_name_reads_ingest_tag():
    assert source_from_name("20260429T123456Z-node1-0042.md") == "node1"
    assert source_from_name("20260429T123456Z-usb-stick-0001.md") == "usb-stick"
    assert source_from_name("/tmp/turns/20260429T123456Z-vm-0007.md") == "vm"


def test_source_from_name_local_write_is_untagged():
    assert source_from_name("20260429T123456Z-0042.md") is None
    assert source_from_name("notes.md") is None
    assert source_from_name("node1-notes.md") is None


def test_source_from_name_round_trips_ingest_name():
    landed = ingest_name("20260429T123456Z-0042.md", "node1")
    assert landed == "20260429T123456Z-node1-0042.md"
    assert source_from_name(landed) == "node1"


def test_recall_label_and_via_tag():
    assert via_tag("node1") == "[via node1]"
    assert via_tag(None) == ""
    assert via_tag("") == ""
    assert recall_label("turns/20260429T123456Z-node1-0042.md") == (
        "turns/20260429T123456Z-node1-0042.md  (via node1)"
    )
    assert recall_label("turns/20260429T123456Z-0042.md") == "turns/20260429T123456Z-0042.md"
    assert recall_label("wiki/kernel.md") == "wiki/kernel.md"


# --------------------------------------------------------------------------- #
#  auto-pull clock
# --------------------------------------------------------------------------- #

def test_pull_due_respects_interval_and_zero_off(tmp_path):
    stamp = tmp_path / "fabric-last-pull.json"
    assert pull_due(interval_s=900, now=1000.0, path=stamp) is True
    stamp_pull(now=1000.0, path=stamp)
    assert last_pull_epoch(stamp) == 1000.0
    assert pull_due(interval_s=900, now=1899.0, path=stamp) is False
    assert pull_due(interval_s=900, now=1900.0, path=stamp) is True
    assert pull_due(interval_s=0, now=10_000.0, path=stamp) is False


def test_pull_interval_s_coerces_and_falls_back():
    assert pull_interval_s(SimpleNamespace(fabric_pull_interval_s=60)) == 60
    assert pull_interval_s(SimpleNamespace(fabric_pull_interval_s="nope")) == 900
    assert pull_interval_s(SimpleNamespace()) == 900


def test_maybe_auto_pull_skips_without_roster(tmp_path):
    cfg = SimpleNamespace(fabric_nodes={}, fabric_pull_interval_s=1)
    assert maybe_auto_pull(cfg, state_path=tmp_path / "w.json") is None


def test_maybe_auto_pull_runs_once_then_skips(tmp_path):
    turns = tmp_path / "ixaac" / "turns"
    turns.mkdir(parents=True)
    persona = SimpleNamespace(name="ixaac", turns_dir=turns, project_root=tmp_path / "ixaac")
    conn = FakeConn(_turns("20260429T120000Z-0001.md"))
    cfg = SimpleNamespace(
        fabric_nodes={"node1": {"remote": "box", "persona": "ixaac"}},
        fabric_pull_interval_s=900,
    )
    stamp = tmp_path / "w.json"
    called = {"n": 0}

    def connect(_name):
        called["n"] += 1
        return conn

    first = maybe_auto_pull(
        cfg,
        now=1000.0,
        state_path=stamp,
        connect=connect,
        resolve_persona=lambda _n: persona,
        drain=lambda _p: ("skipped", True),
        require_remote=lambda _n: None,
    )
    assert first is not None
    assert first.pulled_turns() == 1
    assert called["n"] == 1
    assert (turns / "20260429T120000Z-node1-0001.md").is_file()

    second = maybe_auto_pull(
        cfg,
        now=1001.0,
        state_path=stamp,
        connect=connect,
        resolve_persona=lambda _n: persona,
        drain=lambda _p: ("skipped", True),
        require_remote=lambda _n: None,
    )
    assert second is None
    assert called["n"] == 1


def test_pull_roster_defaults_to_the_journal_not_ixaac():
    from xlii.persona import DEFAULT_PERSONA_ID

    seen: list[str] = []
    pull_roster(
        {"n": {"remote": "box"}},
        connect=lambda _n: FakeConn({}),
        resolve_persona=lambda n: seen.append(n) or None,
    )
    assert seen == [DEFAULT_PERSONA_ID]


def test_pull_roster_leftover_ixaac_spec_is_the_journal():
    from xlii.persona import DEFAULT_PERSONA_ID

    seen: list[str] = []
    pull_roster(
        {"n": {"remote": "box", "persona": "ixaac"}},
        connect=lambda _n: FakeConn({}),
        resolve_persona=lambda n: seen.append(n) or None,
    )
    assert seen == [DEFAULT_PERSONA_ID]


def test_pull_roster_unknown_node_is_an_error():
    batch = pull_roster(
        {"node1": {"remote": "box"}},
        only="nope",
        connect=lambda _n: FakeConn({}),
        resolve_persona=lambda _n: None,
    )
    assert batch.ok() is False
    assert any("no such node" in e for e in batch.errors)
