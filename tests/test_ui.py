"""Coverage for the shared confirm() prompt.

confirm() is the single home for destructive y/N confirmations. These lock the
acceptance contract that previously diverged across command files — some sites
accepted only 'y' and silently rejected 'yes'.

confirm() reads through the `xlii.tools._confirm` indirection (input() by
default; a Textual modal while the TUI runs), so these patch that seam rather
than builtins.input — a raw input() under the TUI deadlocks the worker thread."""

import pytest

from xlii.tui import confirm


@pytest.mark.parametrize("answer", ["y", "Y", "yes", "YES", " yes ", "Yes"])
def test_confirm_accepts_yes_variants(monkeypatch, answer):
    monkeypatch.setattr("xlii.tools._confirm", lambda prompt="": answer)
    assert confirm("delete? [y/N] ") is True


@pytest.mark.parametrize("answer", ["n", "no", "", "nope", "yeah", "ok", "1"])
def test_confirm_rejects_everything_else(monkeypatch, answer):
    monkeypatch.setattr("xlii.tools._confirm", lambda prompt="": answer)
    assert confirm("delete? [y/N] ") is False


def test_confirm_assume_yes_skips_prompt(monkeypatch):
    def boom(prompt=""):
        raise AssertionError("must not prompt when assume_yes=True")

    monkeypatch.setattr("xlii.tools._confirm", boom)
    assert confirm("delete? [y/N] ", assume_yes=True) is True


@pytest.mark.parametrize("exc", [EOFError, KeyboardInterrupt])
def test_confirm_eof_and_interrupt_count_as_no(monkeypatch, exc):
    def raise_it(prompt=""):
        raise exc

    monkeypatch.setattr("xlii.tools._confirm", raise_it)
    assert confirm("delete? [y/N] ") is False
