"""Collusion / test-weakening detector (L2)."""

from __future__ import annotations

from xlii.loop_collusion import detect_weakening, fingerprint_test_file, is_test_path


def test_is_test_path():
    assert is_test_path("tests/foo.py")
    assert is_test_path("pkg/test_bar.py")
    assert not is_test_path("src/main.py")


def test_fingerprint_counts_asserts():
    fp = fingerprint_test_file("def t():\n    assert 1\n    assert 2\n")
    assert fp["asserts"] == 2


def test_detect_weakening_fewer_asserts():
    prior = {
        "1": {
            "tests/test_a.py": fingerprint_test_file(
                "def test_x():\n    assert 1\n    assert 2\n"
            )
        }
    }
    current = {"tests/test_a.py": "def test_x():\n    assert 1\n"}
    alarms = detect_weakening(prior, current)
    assert len(alarms) == 1
    assert "assert count weakened" in alarms[0]


def test_detect_weakening_no_regression():
    content = "def test_x():\n    assert 1\n    assert 2\n"
    prior = {"1": {"tests/test_a.py": fingerprint_test_file(content)}}
    current = {"tests/test_a.py": content}
    assert detect_weakening(prior, current) == []
