"""M5 acceptance tests for capture, allocation, matching and affirmation."""

from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path

from cannae_kernel.absence import Recorded
from cannae_kernel.canonical import digest
from cannae_kernel.disposition import Disposition
from cannae_kernel.ids import AllocationId, EventId
from test_lifecycle import NOW, _actor, _event, _id, _payload
from test_policy_and_venue import _child_register, _venue_order

from emulators.matching import MatchingEmulator, MatchingOutcome, MatchRequest
from emulators.venue import VenueEmulator, VenueOutcome
from lc.events import LifecycleState
from lc.lifecycle import EventInput, LifecycleRegister, replay
from lc.policy import ExecutionReport, apply_execution
from lc.trade import (
    AllocationRecord,
    CaptureRecord,
    MatchFact,
    affirm,
    allocate,
    capture,
    record_match,
)

ROOT = Path(__file__).resolve().parents[1]


def _executed() -> tuple[LifecycleRegister, ExecutionReport]:
    venue = VenueEmulator(seed=8).execute(_venue_order(), outcome=VenueOutcome.FILL, event_time=NOW)
    assert isinstance(venue.event, Recorded) and isinstance(venue.report, Recorded)
    report = ExecutionReport.model_validate(venue.report.value.model_dump(mode="python"))
    applied = apply_execution(
        _child_register(),
        venue.event.value,
        report,
        actor=_actor(),
        event=EventInput(
            event_id=EventId(_id("evt_", 21)),
            times=venue.event.value.times,
            idempotency_key="execution-21",
        ),
    )
    assert applied.disposition is Disposition.PASS
    return applied.journal, report


def _allocated() -> tuple[LifecycleRegister, CaptureRecord, AllocationRecord]:
    journal, report = _executed()
    journal, captured = capture(journal, report, _payload(), actor=_actor(), event=_event(22))
    journal, allocation = allocate(
        journal,
        captured,
        _payload(),
        allocation_id=AllocationId(_id("alc_", 23)),
        actor=_actor(),
        event=_event(23),
    )
    return journal, captured, allocation


def _fact(outcome: MatchingOutcome) -> MatchFact:
    journal, captured, allocation = _allocated()
    del journal
    response = MatchingEmulator(seed=9).respond(
        MatchRequest(
            trade_id=captured.trade_id,
            instrument_id=captured.instrument_id,
            quantity=str(captured.quantity),
            allocation_digest=digest(allocation),
        ),
        outcome=outcome,
    )
    return MatchFact(
        response_id=response.response_id,
        trade_id=response.trade_id,
        matched=response.matched,
        affirmed=response.affirmed,
        reason=response.reason,
        provenance=response.provenance,
    )


def test_capture_explicitly_folds_in_enrichment_and_allocation_conserves() -> None:
    journal, captured, allocation = _allocated()
    assert captured.enrichment_folded_into_capture is True
    assert sum((entry.quantity for entry in allocation.entries), Decimal(0)) == captured.quantity
    assert allocation.executed_quantity == captured.quantity
    assert replay(journal.events).orders[captured.order_id].state is LifecycleState.ALLOCATED


def test_mismatch_opens_its_own_exception_and_cannot_close_it_here() -> None:
    journal, captured, _ = _allocated()
    outcome = record_match(
        journal,
        order_id=captured.order_id,
        fact=_fact(MatchingOutcome.MISMATCH),
        exception_id="exc-match-1",
        actor=_actor(),
        event=_event(24),
    )
    assert outcome.disposition is Disposition.HOLD
    assert outcome.exception is not None and outcome.exception.exception_id == "exc-match-1"
    tree = ast.parse((ROOT / "lc" / "trade.py").read_text(encoding="utf-8"))
    function_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert not {name for name in function_names if "close" in name.lower()}


def test_never_affirmed_trade_cannot_progress() -> None:
    journal, captured, _ = _allocated()
    fact = _fact(MatchingOutcome.NEVER_AFFIRMED)
    matched = record_match(
        journal,
        order_id=captured.order_id,
        fact=fact,
        exception_id="unused",
        actor=_actor(),
        event=_event(24),
    )
    result = affirm(
        matched,
        order_id=captured.order_id,
        fact=fact,
        actor=_actor(),
        event=_event(25),
    )
    assert result.disposition is Disposition.HOLD
    assert (
        replay(result.journal.events).orders[captured.order_id].state is LifecycleState.NOT_REACHED
    )


def test_matched_and_affirmed_trade_reaches_affirmed() -> None:
    journal, captured, _ = _allocated()
    fact = _fact(MatchingOutcome.MATCHED_AND_AFFIRMED)
    matched = record_match(
        journal,
        order_id=captured.order_id,
        fact=fact,
        exception_id="unused",
        actor=_actor(),
        event=_event(24),
    )
    result = affirm(
        matched,
        order_id=captured.order_id,
        fact=fact,
        actor=_actor(),
        event=_event(25),
    )
    assert result.disposition is Disposition.PASS
    assert replay(result.journal.events).orders[captured.order_id].state is LifecycleState.AFFIRMED
