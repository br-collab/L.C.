"""M4 acceptance tests for policy authorization and independent venue facts."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from cannae_kernel.absence import Absent, Recorded
from cannae_kernel.actor import ActorKind, ActorRef
from cannae_kernel.canonical import digest
from cannae_kernel.disposition import Disposition
from cannae_kernel.ids import ActorId, EventId, LifecycleId, OrderId
from cannae_kernel.provenance import Provenance
from cannae_kernel.session import BusinessDate, MarketSession, SessionContext
from test_lifecycle import NOW, _accepted, _actor, _event, _id, _payload

from emulators.venue import VenueEmulator, VenueOrder, VenueOutcome
from lc.lifecycle import ChildOrder, EventInput, LifecycleRegister, split_parent
from lc.policy import (
    ExecutionPolicy,
    ExecutionReport,
    MarketEvidenceSnapshot,
    OperatorDecision,
    apply_execution,
    evaluate_policy,
)


def _policy() -> ExecutionPolicy:
    return ExecutionPolicy(
        policy_id="policy-us-treasury",
        version="1.0",
        allowed_venues=("VENUE-SYNTHETIC",),
        max_child_quantity=Decimal("100"),
        minimum_price=Decimal("90"),
        maximum_price=Decimal("110"),
        expires_at=NOW + timedelta(hours=1),
        max_evidence_age=timedelta(minutes=5),
    )


def _evidence(
    *, observed_at: datetime = NOW, price: Decimal = Decimal("100")
) -> MarketEvidenceSnapshot:
    return MarketEvidenceSnapshot(
        venue="VENUE-SYNTHETIC",
        price=price,
        observed_at=observed_at,
        provenance=Provenance.FACT_SYNTHETIC,
        eligible=True,
    )


def _child_register() -> LifecycleRegister:
    return split_parent(
        _accepted(),
        actor=_actor(),
        children=(
            ChildOrder(
                order_id=OrderId(_id("ord_", 11)),
                quantity=Decimal("100"),
                event=_event(13),
            ),
        ),
    ).journal


def _venue_order() -> VenueOrder:
    payload = _payload()
    return VenueOrder(
        lifecycle_id=LifecycleId(_id("lif_", 3)),
        order_id=OrderId(_id("ord_", 11)),
        intent_id=payload.intent_id,
        intent_digest=digest(payload),
        quantity=Decimal("100"),
        limit_price=Decimal("100"),
        policy_expires_at=NOW + timedelta(hours=1),
        session=SessionContext(
            session=MarketSession.REGULAR,
            business_date=BusinessDate(
                value=date(2026, 9, 23), calendar="SIFMA-US", established_by="scenario"
            ),
        ),
    )


def test_stale_market_evidence_holds_the_gate() -> None:
    decision = evaluate_policy(
        _policy(),
        child_quantity=Decimal("100"),
        evidence=_evidence(observed_at=NOW - timedelta(minutes=6)),
        processing_time=NOW,
    )
    assert decision.disposition is Disposition.HOLD
    assert isinstance(decision.policy, Absent)
    assert "stale" in decision.reason


def test_in_bounds_policy_is_a_deterministic_pass() -> None:
    decision = evaluate_policy(
        _policy(),
        child_quantity=Decimal("100"),
        evidence=_evidence(),
        processing_time=NOW,
    )
    assert decision.disposition is Disposition.PASS
    assert decision.provenance is Provenance.POLICY_RESULT


def test_out_of_bounds_requires_a_human_operator_decision() -> None:
    held = evaluate_policy(
        _policy(),
        child_quantity=Decimal("101"),
        evidence=_evidence(),
        processing_time=NOW,
    )
    operator = OperatorDecision(
        approved=True,
        decided_by=ActorRef(
            actor_id=ActorId(_id("act_", 9)),
            actor_kind=ActorKind.HUMAN,
            role="CAOM-001 operator",
            entitlement_refs=("EXECUTION_POLICY_OVERRIDE",),
            authenticated=True,
        ),
        provenance=Provenance.HUMAN_JUDGMENT,
        reason="operator approved the named bounded exception",
    )
    passed = evaluate_policy(
        _policy(),
        child_quantity=Decimal("101"),
        evidence=_evidence(),
        processing_time=NOW,
        operator_decision=operator,
    )
    assert held.disposition is Disposition.HOLD
    assert passed.disposition is Disposition.PASS
    assert passed.provenance is Provenance.HUMAN_JUDGMENT


def test_emulator_is_deterministic_and_can_report_every_unhappy_outcome() -> None:
    emulator = VenueEmulator(seed=7)
    order = _venue_order()
    for outcome in (
        VenueOutcome.PARTIAL_FILL,
        VenueOutcome.LATE_FILL,
        VenueOutcome.OVERFILL,
    ):
        first = emulator.execute(order, outcome=outcome, event_time=NOW)
        second = emulator.execute(order, outcome=outcome, event_time=NOW)
        assert first == second
        assert isinstance(first.event, Recorded)
        assert first.event.value.provenance is Provenance.FACT_SYNTHETIC


def test_no_fill_is_explicit_absence_not_a_convenient_execution_fact() -> None:
    result = VenueEmulator(seed=7).execute(
        _venue_order(), outcome=VenueOutcome.NO_FILL, event_time=NOW
    )
    assert isinstance(result.event, Absent)
    assert isinstance(result.report, Absent)
    assert "no execution event" in result.event.reason


def test_register_refuses_overfill_without_absorbing_it() -> None:
    register = _child_register()
    result = VenueEmulator(seed=7).execute(
        _venue_order(), outcome=VenueOutcome.OVERFILL, event_time=NOW
    )
    assert isinstance(result.event, Recorded) and isinstance(result.report, Recorded)
    report = ExecutionReport.model_validate(result.report.value.model_dump(mode="python"))
    before = register.canonical_bytes()
    applied = apply_execution(
        register,
        result.event.value,
        report,
        actor=_actor(),
        event=EventInput(
            event_id=EventId(_id("evt_", 20)),
            times=result.event.value.times,
            idempotency_key="execution-20",
        ),
    )
    assert applied.disposition is Disposition.HOLD
    assert "exceeds remaining" in applied.reason
    assert applied.journal.canonical_bytes() == before
