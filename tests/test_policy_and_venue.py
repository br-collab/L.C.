"""M4 acceptance tests for policy authorization and independent venue facts."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from cannae_kernel.absence import Absent, Recorded
from cannae_kernel.actor import ActorKind, ActorRef
from cannae_kernel.canonical import digest
from cannae_kernel.disposition import Disposition
from cannae_kernel.ids import ActorId, EventId, LifecycleId, OrderId
from cannae_kernel.provenance import Provenance
from cannae_kernel.session import BusinessDate, MarketSession, SessionContext
from test_lifecycle import NOW, _accepted, _actor, _envelope, _event, _id, _payload

from emulators.venue import VenueEmulator, VenueOrder, VenueOutcome
from lc.events import LifecycleState, OrderTerms, OrderType, TimeInForce
from lc.lifecycle import (
    ApprovedIntentPayload,
    ChildOrder,
    EventInput,
    LifecycleRegister,
    split_parent,
    transition,
)
from lc.policy import (
    ExecutionApplication,
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


#: The approved-intent digest the default child order is sealed under. Obligation tests name
#: the same digest in their source manifest (INST-1 Amendment 3: formation reads it from here).
#: The digest of the default sealed intent; the default child is sealed under it.
INTENT_DIGEST = digest(_envelope())


def default_terms(**changes: object) -> OrderTerms:
    """Complete sealed terms for the default child: INST-1 Amendment 3 refuses a fill without."""
    values: dict[str, object] = {
        "account_id": "ACCOUNT-1",
        "strategy_id": "STRATEGY-1",
        "issue": "91282CJL6",
        "side": "BUY",
        "order_type": OrderType.LIMIT,
        "time_in_force": TimeInForce.DAY,
        "control_price": Decimal("100"),
        "limit_price": Decimal("100"),
        "approved_intent_digest": INTENT_DIGEST,
        "policy_expires_at": NOW + timedelta(hours=1),
    }
    values.update(changes)
    return OrderTerms.model_validate(values)


def _child_register(
    *,
    terms: OrderTerms | None = None,
    quantity: Decimal = Decimal("100"),
    unsealed: bool = False,
    intent_payload: ApprovedIntentPayload | None = None,
) -> LifecycleRegister:
    """The default child at MARKET_ACCESS_PASSED; ``unsealed`` records it with no terms."""
    terms = None if unsealed else (terms if terms is not None else default_terms())
    register = split_parent(
        _accepted(intent_payload),
        actor=_actor(),
        children=(
            ChildOrder(
                order_id=OrderId(_id("ord_", 11)),
                quantity=quantity,
                event=_event(13),
                terms=terms,
            ),
        ),
    ).journal
    return transition(
        register,
        order_id=OrderId(_id("ord_", 11)),
        target=LifecycleState.MARKET_ACCESS_PASSED,
        reason="pre-trade controls passed",
        actor=_actor(),
        event=_event(14),
    )


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


def test_execution_cannot_bypass_market_access() -> None:
    register = split_parent(
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
    result = VenueEmulator(seed=7).execute(
        _venue_order(), outcome=VenueOutcome.FILL, event_time=NOW
    )
    assert isinstance(result.event, Recorded) and isinstance(result.report, Recorded)
    report = ExecutionReport.model_validate(result.report.value.model_dump(mode="python"))
    before = register.canonical_bytes()
    applied = apply_execution(
        register,
        result.event.value,
        report,
        actor=_actor(),
        event=_event(20),
    )
    assert applied.disposition is Disposition.HOLD
    assert applied.reason == "execution refused because market access has not passed"
    assert applied.journal.canonical_bytes() == before


def _terms(**changes: object) -> OrderTerms:
    values: dict[str, object] = {
        "account_id": "SYN-CUSTOMER-1",
        "strategy_id": "INST-1",
        "issue": "91282CJL6",
        "side": "BUY",
        "order_type": OrderType.LIMIT,
        "time_in_force": TimeInForce.DAY,
        "control_price": Decimal(100),
        "limit_price": Decimal(100),
        "approved_intent_digest": "sha256:" + "a" * 64,
        "quantity_basis": "QUANTITY",
        "policy_expires_at": NOW + timedelta(hours=1),
    }
    values.update(changes)
    return OrderTerms.model_validate(values)


def _application(
    *, terms: OrderTerms, price: Decimal, event_time: datetime = NOW
) -> ExecutionApplication:
    result = VenueEmulator(seed=7).execute(
        _venue_order(), outcome=VenueOutcome.FILL, event_time=event_time
    )
    assert isinstance(result.event, Recorded) and isinstance(result.report, Recorded)
    report = ExecutionReport.model_validate(
        result.report.value.model_dump(mode="python")
    ).model_copy(update={"price": price})
    execution = result.event.value.model_copy(update={"payload_digest": digest(report)})
    return apply_execution(
        _child_register(terms=terms), execution, report, actor=_actor(), event=_event(20)
    )


@pytest.mark.parametrize(
    ("side", "limit", "fill"),
    [("BUY", Decimal(100), Decimal("100.01")), ("SELL", Decimal(100), Decimal("99.99"))],
)
def test_fill_outside_sealed_limit_is_refused(side: str, limit: Decimal, fill: Decimal) -> None:
    outcome = _application(
        terms=_terms(side=side, control_price=limit, limit_price=limit), price=fill
    )
    assert outcome.disposition is Disposition.HOLD
    assert outcome.reason.startswith("FILL_OUTSIDE_LIMIT")


def test_fill_at_limit_still_passes() -> None:
    assert _application(terms=_terms(), price=Decimal(100)).disposition is Disposition.PASS


def test_fill_after_authority_expiry_is_refused() -> None:
    outcome = _application(
        terms=_terms(policy_expires_at=NOW - timedelta(seconds=1)),
        price=Decimal(100),
        event_time=NOW,
    )
    assert outcome.reason.startswith("POLICY_EXPIRED")


# INST-1 Amendment 3, V6: execution controls fail closed. Before WP1b, the limit, expiry,
# notional and session checks ran only when terms were present, so these fills passed.


def _fill(register: LifecycleRegister, price: Decimal) -> ExecutionApplication:
    result = VenueEmulator(seed=7).execute(
        _venue_order(), outcome=VenueOutcome.FILL, event_time=NOW
    )
    assert isinstance(result.event, Recorded) and isinstance(result.report, Recorded)
    report = ExecutionReport.model_validate(
        result.report.value.model_dump(mode="python")
    ).model_copy(update={"price": price})
    execution = result.event.value.model_copy(update={"payload_digest": digest(report)})
    return apply_execution(register, execution, report, actor=_actor(), event=_event(20))


def test_v6_an_order_without_sealed_terms_holds_at_any_price() -> None:
    for price in (Decimal(100), Decimal(1_000_000)):
        outcome = _fill(_child_register(unsealed=True), price)
        assert outcome.disposition is Disposition.HOLD
        assert outcome.reason.startswith("TERMS_ABSENT")


def test_v6_terms_without_a_policy_expiry_hold() -> None:
    outcome = _fill(_child_register(terms=_terms(policy_expires_at=None)), Decimal(100))
    assert outcome.disposition is Disposition.HOLD
    assert outcome.reason.startswith("POLICY_EXPIRY_ABSENT")


def test_v6_the_unsealed_hold_records_no_fill() -> None:
    register = _child_register(unsealed=True)
    outcome = _fill(register, Decimal(100))
    assert outcome.journal == register


def test_notional_basis_is_binding_but_quantity_estimate_is_not() -> None:
    held = _application(
        terms=_terms(quantity_basis="NOTIONAL", approved_notional=Decimal("9999")),
        price=Decimal(100),
    )
    passed = _application(
        terms=_terms(quantity_basis="QUANTITY", approved_notional=None),
        price=Decimal(100),
    )
    assert held.reason.startswith("APPROVED_NOTIONAL_EXCEEDED")
    assert passed.disposition is Disposition.PASS


def test_closed_session_refuses_execution() -> None:
    outcome = _application(terms=_terms(session_activity_permitted=False), price=Decimal(100))
    assert outcome.reason.startswith("SESSION_CLOSED")
