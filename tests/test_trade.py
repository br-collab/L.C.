"""M5 acceptance tests for capture, allocation, matching and affirmation."""

from __future__ import annotations

import ast
import random
from decimal import Decimal
from pathlib import Path

import pytest
from cannae_kernel.absence import Recorded
from cannae_kernel.canonical import digest
from cannae_kernel.disposition import Disposition
from cannae_kernel.ids import AllocationId, EventId, OrderId
from test_lifecycle import NOW, _actor, _event, _id, _payload
from test_policy_and_venue import _child_register, _venue_order

from emulators.matching import MatchingEmulator, MatchingOutcome, MatchRequest
from emulators.venue import VenueEmulator, VenueOutcome
from lc.events import LifecycleState
from lc.lifecycle import EventInput, LifecycleRegister, replay
from lc.policy import ExecutionReport, apply_execution
from lc.trade import (
    AllocationPlan,
    AllocationRecord,
    AllocationRule,
    AllocationTarget,
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
        plan=AllocationPlan(
            targets=tuple(
                AllocationTarget(account=account, target_quantity=captured.quantity)
                for account in _payload().allocation_accounts
            ),
            rule=AllocationRule.PRO_RATA,
            rounding_method="LARGEST_REMAINDER",
        ),
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


def test_capture_refuses_empty_mixed_order_and_incomplete_report_sets() -> None:
    journal, report = _executed()

    with pytest.raises(ValueError, match="requires execution reports"):
        capture(journal, (), _payload(), actor=_actor(), event=_event(220))

    other_order = report.model_copy(update={"order_id": OrderId(_id("ord_", 221))})
    with pytest.raises(ValueError, match="belong to one order"):
        capture(journal, (report, other_order), _payload(), actor=_actor(), event=_event(221))

    incomplete = report.model_copy(update={"quantity": report.quantity - Decimal(1)})
    with pytest.raises(ValueError, match="sum to the executed quantity"):
        capture(journal, incomplete, _payload(), actor=_actor(), event=_event(222))


def test_generated_multifill_capture_and_allocation_conserve_share_and_cent() -> None:
    """Rule 29 property test: 240 generated examples, fixed seed 20261022."""
    rng = random.Random(20261022)
    base = VenueEmulator(seed=8).execute(_venue_order(), outcome=VenueOutcome.FILL, event_time=NOW)
    assert isinstance(base.event, Recorded) and isinstance(base.report, Recorded)
    for example in range(240):
        first_qty = Decimal(rng.randrange(1, 100))
        second_qty = Decimal(100) - first_qty
        first_price = Decimal(rng.randrange(1, 100_000)) / Decimal(64)
        second_price = Decimal(rng.randrange(1, 100_000)) / Decimal(64)
        reports = (
            ExecutionReport.model_validate(base.report.value.model_dump(mode="python")).model_copy(
                update={
                    "execution_id": f"p-{example}-1",
                    "quantity": first_qty,
                    "price": first_price,
                    "outcome": "PARTIAL_FILL",
                }
            ),
            ExecutionReport.model_validate(base.report.value.model_dump(mode="python")).model_copy(
                update={
                    "execution_id": f"p-{example}-2",
                    "quantity": second_qty,
                    "price": second_price,
                    "outcome": "FILL",
                }
            ),
        )
        journal = _child_register()
        for index, report in enumerate(reports):
            execution = base.event.value.model_copy(update={"payload_digest": digest(report)})
            applied = apply_execution(
                journal, execution, report, actor=_actor(), event=_event(2000 + example * 2 + index)
            )
            assert applied.disposition is Disposition.PASS
            journal = applied.journal
        journal, captured = capture(
            journal, reports, _payload(), actor=_actor(), event=_event(3000 + example)
        )
        _, allocation = allocate(
            journal,
            captured,
            _payload(),
            allocation_id=AllocationId(_id("alc_", 4000 + example)),
            actor=_actor(),
            event=_event(4000 + example),
            plan=AllocationPlan(
                targets=(
                    AllocationTarget(account="A", target_quantity=Decimal(34)),
                    AllocationTarget(account="B", target_quantity=Decimal(66)),
                ),
                rule=AllocationRule.PRO_RATA,
                rounding_method="LARGEST_REMAINDER",
            ),
        )
        assert sum((entry.quantity for entry in allocation.entries), Decimal(0)) == Decimal(100)
        expected_cents = (first_qty * first_price + second_qty * second_price).quantize(
            Decimal("0.01")
        )
        assert (
            sum((entry.consideration for entry in allocation.entries), Decimal(0)) == expected_cents
        )


def test_grok_multifill_case_books_exact_money() -> None:
    reports = (
        ExecutionReport(
            execution_id="grok-1",
            order_id=_venue_order().order_id,
            intent_id=_payload().intent_id,
            venue="VENUE-SYNTHETIC",
            quantity=Decimal(60),
            price=Decimal("100.015625"),
            outcome="PARTIAL_FILL",
        ),
        ExecutionReport(
            execution_id="grok-2",
            order_id=_venue_order().order_id,
            intent_id=_payload().intent_id,
            venue="VENUE-SYNTHETIC",
            quantity=Decimal(40),
            price=Decimal("99.375"),
            outcome="FILL",
        ),
    )
    journal = _child_register()
    base = VenueEmulator(seed=8).execute(_venue_order(), outcome=VenueOutcome.FILL, event_time=NOW)
    assert isinstance(base.event, Recorded)
    for index, report in enumerate(reports):
        applied = apply_execution(
            journal,
            base.event.value.model_copy(update={"payload_digest": digest(report)}),
            report,
            actor=_actor(),
            event=_event(5000 + index),
        )
        journal = applied.journal
    _, captured = capture(journal, reports, _payload(), actor=_actor(), event=_event(5002))
    assert captured.quantity * captured.price == Decimal("9975.937500")


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
