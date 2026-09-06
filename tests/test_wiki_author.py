"""wiki_author — the AI distillation + skeptical-editor verify, driven by a fake completer.

No live model: every test injects a ``complete(messages) -> str`` so the write→refute→promote
logic is exercised deterministically. The one behaviour that must never regress is the
anti-confabulation guard — a page with no provenance is never auto-verified."""

from __future__ import annotations

import pytest

from xlii import wiki as W
from xlii import wiki_author


def test_distill_writes_unverified_with_recorded_sources(tmp_path):
    src = tmp_path / "notes.txt"
    src.write_text("The kernel owns the turn lifecycle.")
    seen = {}

    def fake(messages):
        seen["messages"] = messages
        return "# Kernel\n\nThe kernel owns the turn lifecycle."

    recorded = wiki_author.distill(tmp_path, "kernel", [f"file://{src}"], fake)

    page = W.read_page(tmp_path, "kernel")
    assert page.verified is False                       # born unverified
    assert recorded == [f"file://{src}"] and page.sources == recorded
    assert "turn lifecycle" in page.body
    # the source's *text* was actually fed to the model (real provenance, not just an address)
    assert any("owns the turn lifecycle" in m["content"] for m in seen["messages"])


def test_distill_drops_unreadable_sources_from_provenance(tmp_path):
    good = tmp_path / "g.txt"
    good.write_text("real fact")
    recorded = wiki_author.distill(
        tmp_path, "p", [f"file://{good}", "file:///no/such/path"], lambda m: "# P\n\nbody"
    )
    assert recorded == [f"file://{good}"]               # only the readable source is recorded


def test_distill_empty_reply_raises(tmp_path):
    with pytest.raises(wiki_author.WikiAuthorError):
        wiki_author.distill(tmp_path, "p", [], lambda m: "   ")


def test_verify_promotes_on_clean_verdict(tmp_path):
    src = tmp_path / "s.txt"
    src.write_text("fact")
    W.write_page(tmp_path, "k", "# K\n\nfact", sources=[f"file://{src}"])
    verdict = wiki_author.verify(tmp_path, "k", lambda m: "VERIFIED\n(all claims trace to source)")
    assert verdict.promoted is True
    assert W.read_page(tmp_path, "k").verified is True


def test_verify_refuted_keeps_page_unverified(tmp_path):
    src = tmp_path / "s.txt"
    src.write_text("fact")
    W.write_page(tmp_path, "k", "# K\n\nwild claim", sources=[f"file://{src}"], verified=False)
    verdict = wiki_author.verify(tmp_path, "k", lambda m: "REFUTED\n- 'wild claim' is unsupported")
    assert verdict.promoted is False
    assert W.read_page(tmp_path, "k").verified is False


def test_verify_never_trusts_a_page_with_no_sources(tmp_path):
    # the anti-confabulation guard: a completer that always says VERIFIED must NOT promote an
    # unprovenanced page — there's nothing to check it against.
    W.write_page(tmp_path, "rumor", "# Rumor\n\nunsourced")
    verdict = wiki_author.verify(tmp_path, "rumor", lambda m: "VERIFIED")
    assert verdict.promoted is False
    assert "no sources" in verdict.reasons
    assert W.read_page(tmp_path, "rumor").verified is False


def test_session_completer_none_without_pool_or_cfg():
    from types import SimpleNamespace

    assert wiki_author.session_completer(SimpleNamespace(pool=None, cfg=None)) is None
