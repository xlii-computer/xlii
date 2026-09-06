"""Tool-output spill to disk (cursor-workflows.md A2 dynamic context)."""

from xlii.scratch import _KEEP, spill_dir, write_spill


def test_write_spill_roundtrip(tmp_path):
    rel = write_spill(tmp_path, "X" * 5000, seq=1)
    assert rel.startswith(".xlii/scratch/tool-output/")
    assert rel.endswith(".txt")
    assert (tmp_path / rel).read_text() == "X" * 5000  # full text preserved


def test_spill_seq_in_name(tmp_path):
    rel = write_spill(tmp_path, "hi", seq=7)
    assert (tmp_path / rel).name.startswith("007-")


def test_prune_caps_file_count(tmp_path):
    for i in range(_KEEP + 10):
        write_spill(tmp_path, "a", seq=i)
    files = list(spill_dir(tmp_path).glob("*.txt"))
    assert len(files) <= _KEEP
