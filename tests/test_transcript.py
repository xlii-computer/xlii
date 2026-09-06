"""Round-trip coverage for per-turn transcript persistence.

The module had no direct tests; these lock the write -> read contract (and the
marks/timestamp parsing the refactor consolidated into shared helpers) so the
five former inline copies can't silently diverge again."""

from xlii import transcript as tx


def test_write_load_roundtrip(tmp_path):
    turns = tmp_path / "turns"
    tx.write_turn(turns, "  what is the auth flow?  ", "  it uses OMEMO device trust  ")

    loaded = tx.load_recent_turns(turns, limit=10)
    assert len(loaded) == 1
    t = loaded[0]
    # write_turn strips, to_markdown/_BLOCK_RX preserve the stripped body
    assert t.user == "what is the auth flow?"
    assert t.assistant == "it uses OMEMO device trust"
    assert t.timestamp.endswith("UTC")  # recovered from the "# turn — <ts>" line
    assert t.marks == []


def test_load_recent_is_chronological_and_limited(tmp_path):
    turns = tmp_path / "turns"
    for i in range(5):
        tx.write_turn(turns, f"q{i}", f"a{i}")

    last_two = tx.load_recent_turns(turns, limit=2)
    assert [t.user for t in last_two] == ["q3", "q4"]

    all_turns = tx.load_recent_turns(turns, limit=0)  # limit<=0 means "all"
    assert [t.user for t in all_turns] == ["q0", "q1", "q2", "q3", "q4"]


def test_multiline_bodies_survive(tmp_path):
    turns = tmp_path / "turns"
    body = "line one\n\nline two\n- a bullet\n## not-a-header-mid-body"
    tx.write_turn(turns, "multi\nline\nquestion", body)

    t = tx.load_recent_turns(turns, 1)[0]
    assert t.user == "multi\nline\nquestion"
    assert t.assistant == body


def test_mark_and_recall_roundtrip(tmp_path):
    turns = tmp_path / "turns"
    tx.write_turn(turns, "older", "older reply")
    tx.write_turn(turns, "newer", "newer reply")

    assert tx.mark_last_turn(turns, "release") is True
    assert tx.mark_last_turn(turns, "release") is True  # idempotent: already marked

    got = tx.get_marked_turn(turns, "release")
    assert got is not None
    assert got.user == "newer"
    assert got.marks == ["release"]

    assert tx.get_marked_turn(turns, "nope") is None


def test_marks_preserved_through_reload(tmp_path):
    turns = tmp_path / "turns"
    tx.write_turn(turns, "q", "a", marks=["alpha", "beta"])

    t = tx.load_recent_turns(turns, 1)[0]
    assert t.marks == ["alpha", "beta"]


def test_list_marks_newest_first(tmp_path):
    turns = tmp_path / "turns"
    tx.write_turn(turns, "q1", "a1", marks=["one"])
    tx.write_turn(turns, "q2", "a2", marks=["two", "three"])

    listed = tx.list_marks(turns)
    names = [name for name, _ in listed]
    # newest turn's marks come first, in document order within a turn
    assert names == ["two", "three", "one"]
    assert all(ts for _, ts in listed)


def test_get_last_turn_content(tmp_path):
    turns = tmp_path / "turns"
    tx.write_turn(turns, "first", "first reply")
    tx.write_turn(turns, "latest", "latest reply")

    content = tx.get_last_turn_content(turns)
    assert content == {"user": "latest", "assistant": "latest reply"}


def test_unparsable_file_is_skipped_not_fatal(tmp_path):
    turns = tmp_path / "turns"
    turns.mkdir()
    # An old-dated garbage file sorts behind the real (current-dated) turn.
    (turns / "20200101T000000Z-0001.md").write_text("not a turn file at all")
    tx.write_turn(turns, "real", "real reply")

    loaded = tx.load_recent_turns(turns, 10)
    assert [t.user for t in loaded] == ["real"]  # garbage skipped, not fatal
    assert tx.get_last_turn_content(turns) == {"user": "real", "assistant": "real reply"}


def test_get_last_turn_content_none_when_newest_unparsable(tmp_path):
    turns = tmp_path / "turns"
    turns.mkdir()
    # Future-dated garbage sorts ahead of any real turn -> newest is unparsable.
    (turns / "29991231T235959Z-9999.md").write_text("## user\nno assistant block here")
    assert tx.get_last_turn_content(turns) is None


def test_missing_dir_is_graceful(tmp_path):
    missing = tmp_path / "nope"
    assert tx.load_recent_turns(missing, 5) == []
    assert tx.count_turns(missing) == 0
    assert tx.list_marks(missing) == []
    assert tx.get_marked_turn(missing, "x") is None
    assert tx.get_last_turn_content(missing) is None
    assert tx.mark_last_turn(missing, "x") is False


def test_concurrent_slot_collision_bumps_instead_of_overwriting(tmp_path, monkeypatch):
    """Two writers that count the same next-free slot in the same second must
    not overwrite each other (the ask --session double-send race): creation is
    exclusive, and a taken filename bumps to the next slot."""
    from datetime import datetime, timezone

    frozen = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)

    class _FrozenDatetime:
        @staticmethod
        def now(tz=None):
            return frozen

    monkeypatch.setattr(tx, "datetime", _FrozenDatetime)
    turns = tmp_path / "turns"
    first = tx.write_turn(turns, "q1", "a1")

    # Simulate the loser of the race: the slot write_turn will compute next
    # (count=1 → n=2) is already taken by a concurrent winner.
    stolen = turns / tx.turn_filename(frozen, 2)
    stolen.write_text((turns / first.name).read_text().replace("q1", "stolen"))

    second = tx.write_turn(turns, "q2", "a2")
    assert second != stolen
    assert "stolen" in stolen.read_text()      # winner untouched
    assert "q2" in second.read_text()          # loser landed in the next slot
    users = [t.user for t in tx.load_recent_turns(turns, limit=10)]
    assert users == ["q1", "stolen", "q2"]     # nothing lost, order intact


def test_pulled_turn_carries_body_into_history(tmp_path):
    """A turn landed by fabric pull is tagged in inline history; the file body
    stays clean so the transcript UI does not show the tag."""
    from xlii.fabric import ingest_name

    turns = tmp_path / "turns"
    turns.mkdir()
    tx.write_turn(turns, "said here", "local reply")
    assert tx.load_recent_turns(turns, 1)[0].source is None

    pulled = turns / ingest_name("20260429T120000Z-0001.md", "node1")
    pulled.write_text(
        "# turn — 2026-04-29 12:00:00 UTC\n\n"
        "## user\nsaid on the vm\n\n"
        "## assistant\nvm reply\n"
    )
    loaded = {t.user: t for t in tx.load_recent_turns(turns, limit=10)}
    remote = loaded["said on the vm"]
    assert remote.source == "node1"
    assert remote.user == "said on the vm"
    pair = remote.as_history_pair()
    assert pair[0]["content"] == "[via node1]\nsaid on the vm"
    assert pair[1]["content"] == "vm reply"
    # this-box turn stays untagged in history
    assert loaded["said here"].as_history_pair()[0]["content"] == "said here"
