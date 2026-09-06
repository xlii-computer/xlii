"""CLAIM_INPUT — the pane outcome that makes popups unnecessary (godzilla-mothra V0c).

The headless half: the ask is pure data (:class:`~xlii.panes.InputClaim`), the Dock routes
it to the installed input-line owner, and ANY body can answer it its own way (the XMPP X3
ask-shape) — proven here with a fake fabric-style sink, no TUI involved. The TUI fulfilment
(morph/relabel/Esc) is covered in ``test_tui_claim_input.py``.
"""

from __future__ import annotations

import dataclasses

import pytest

from xlii.panes import CLAIM_INPUT, PREFILL, Action, InputClaim, Outcome
from xlii.panes.dock import Dock


class _PrefillOnlySink:
    """The pre-claim world: a valid InputSink with no claim() — asks must raise."""

    def __init__(self):
        self.prefilled = []

    def prefill(self, text):
        self.prefilled.append(text)


class _AnsweringSink(_PrefillOnlySink):
    """A claim-capable sink that answers the ask its own way — the headless-body proof
    (what a fabric body does over chat, a TUI does by morphing its input line)."""

    def __init__(self, answer="bob", grant=True):
        super().__init__()
        self.answer = answer
        self.grant = grant
        self.asks = []

    def claim(self, claim):
        self.asks.append(claim)
        if not self.grant:
            return False  # refused — and per the ClaimSink contract, NO callbacks from here
        claim.on_submit(self.answer)
        return True


def _ask(submits, cancels=None, prompt="name:", initial=""):
    return InputClaim(
        prompt=prompt,
        on_submit=submits.append,
        initial=initial,
        on_cancel=None if cancels is None else (lambda: cancels.append(True)),
    )


# --- the ask-shape is pure, frozen data ---------------------------------------


def test_input_claim_is_frozen():
    claim = _ask([])
    with pytest.raises(dataclasses.FrozenInstanceError):
        claim.prompt = "other:"


def test_outcome_claim_defaults_none_backcompat():
    # Every pre-claim Outcome construction stays valid and unchanged.
    out = Outcome(PREFILL, "tasks://x", text="/tasks run x")
    assert out.claim is None
    assert out == Outcome(PREFILL, "tasks://x", text="/tasks run x")


# --- dispatch routing: the explicit seam ---------------------------------------


def test_dispatch_claim_without_sink_raises():
    d = Dock()
    with pytest.raises(NotImplementedError):
        d.dispatch(Outcome(CLAIM_INPUT, claim=_ask([])), from_slot="A")


def test_dispatch_claim_without_payload_raises():
    d = Dock()
    d.set_input_sink(_AnsweringSink())
    with pytest.raises(ValueError):
        d.dispatch(Outcome(CLAIM_INPUT, "tasks://x"), from_slot="A")


def test_dispatch_claim_on_prefill_only_sink_raises():
    d = Dock()
    d.set_input_sink(_PrefillOnlySink())
    with pytest.raises(NotImplementedError):
        d.dispatch(Outcome(CLAIM_INPUT, claim=_ask([])), from_slot="A")


def test_prefill_still_routes_beside_claim():
    d = Dock()
    sink = _AnsweringSink()
    d.set_input_sink(sink)
    d.dispatch(Outcome(PREFILL, text="/tasks run nightly"), from_slot="A")
    assert sink.prefilled == ["/tasks run nightly"]


# --- a headless body answers the ask its own way -------------------------------


def test_headless_body_answers_the_ask():
    d = Dock()
    d.set_input_sink(_AnsweringSink(answer="approve a3f2"))
    submits, cancels = [], []
    assert d.dispatch(Outcome(CLAIM_INPUT, claim=_ask(submits, cancels)), from_slot="A") is None
    assert submits == ["approve a3f2"]
    assert cancels == []  # answered asks never cancel


def test_refused_claim_reports_on_cancel():
    d = Dock()
    d.set_input_sink(_AnsweringSink(grant=False))
    submits, cancels = [], []
    d.dispatch(Outcome(CLAIM_INPUT, claim=_ask(submits, cancels)), from_slot="A")
    assert submits == []
    assert cancels == [True]  # exactly once — the dispatcher owns refusal notification


def test_refused_claim_without_on_cancel_is_quiet():
    d = Dock()
    d.set_input_sink(_AnsweringSink(grant=False))
    d.dispatch(Outcome(CLAIM_INPUT, claim=InputClaim("name:", lambda t: None)), from_slot="A")


# --- the throwaway consumer: a pane item's action carries the ask --------------


def test_consumer_pane_action_drives_the_loop():
    """The whole seam, headless: a pane offers "rename…" as an Action whose outcome
    carries the ask; dispatch hands it to the input-line owner; the answer lands back
    in the consumer's callback. claim → (answer) → callback → done."""
    d = Dock()
    sink = _AnsweringSink(answer="new-name")
    d.set_input_sink(sink)
    renamed = []
    action = Action(
        "rename",
        "Rename…",
        Outcome(
            CLAIM_INPUT,
            "tasks://old-name",
            claim=InputClaim(prompt="rename to:", on_submit=renamed.append, initial="old-name"),
        ),
    )
    assert d.dispatch(action.outcome, from_slot="A") is None
    assert renamed == ["new-name"]
    # the sink saw the full ask-shape, untouched
    (seen,) = sink.asks
    assert (seen.prompt, seen.initial) == ("rename to:", "old-name")
