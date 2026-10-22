"""OMS-1 order terms, lifecycle controls, allocation and firm halt evidence."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from random import Random
from typing import Any

import pytest
from cannae_kernel.absence import Recorded
from cannae_kernel.canonical import digest
from cannae_kernel.ids import AllocationId, EventId, IntentId, LifecycleId, OrderId
from test_lifecycle import NOW, _actor, _event, _id, _payload
from test_market_access import capacity, limits, locates
from test_policy_and_venue import _child_register, _evidence, _policy, _venue_order

from emulators.venue import VenueEmulator, VenueOutcome
from lc.events import (
    LifecycleState,
    OrderEvent,
    OrderEventPayload,
    OrderTerms,
    OrderType,
    TimeInForce,
    seal_order_event,
)
from lc.lifecycle import LifecycleRegister, replay, transition
from lc.market_access import ChildRelease, FirmHalt, HaltScope, MarketAccessGate
from lc.oms import (
    FirmControlJournal,
    cancel_order,
    engage_halt,
    expire_day_orders,
    release_halt,
    replace_order,
)
from lc.policy import ExecutionReport, apply_execution
from lc.trade import (
    AllocationPlan,
    AllocationRefusalError,
    AllocationRule,
    AllocationTarget,
    allocate,
    capture,
)


def _terms(**changes: object) -> OrderTerms:
    values: dict[str, object] = {
        "account_id": "A",
        "strategy_id": "S1",
        "issue": "ACME",
        "side": "BUY",
        "order_type": OrderType.LIMIT,
        "time_in_force": TimeInForce.DAY,
        "control_price": Decimal(100),
        "limit_price": Decimal(100),
    }
    values.update(changes)
    return OrderTerms.model_validate(values)


def _register(
    *, quantity: Decimal = Decimal(100), terms: OrderTerms | None = None
) -> LifecycleRegister:
    event = seal_order_event(
        event_id=EventId(_id("evt_", 80)),
        lifecycle_id=LifecycleId(_id("lif_", 80)),
        parent_ids=(),
        rule_version="oms1/1",
        times=_event(80).times,
        actor=_actor(),
        idempotency_key="oms1-80",
        payload=OrderEventPayload(
            order_id=OrderId(_id("ord_", 80)),
            parent_order_id=None,
            intent_id=IntentId(_id("int_", 80)),
            state=LifecycleState.PARENT_ACTIVE,
            reason="sealed OMS order",
            quantity=Recorded(value=quantity),
            terms=terms or _terms(),
        ),
        prior_event_digest=None,
    )
    return LifecycleRegister(lifecycle_id=event.lifecycle_id).append(event)


def _append_order(
    register: LifecycleRegister, *, number: int, quantity: Decimal, terms: OrderTerms | None
) -> tuple[LifecycleRegister, OrderEvent]:
    event = seal_order_event(
        event_id=EventId(_id("evt_", number)),
        lifecycle_id=register.lifecycle_id,
        parent_ids=(register.events[-1].event_id,),
        rule_version="oms1/1",
        times=_event(number).times,
        actor=_actor(),
        idempotency_key=f"oms1-{number}",
        payload=OrderEventPayload(
            order_id=OrderId(_id("ord_", number)),
            parent_order_id=None,
            intent_id=IntentId(_id("int_", number)),
            state=LifecycleState.PARENT_ACTIVE,
            reason="sealed OMS order",
            quantity=Recorded(value=quantity),
            terms=terms,
        ),
        prior_event_digest=register.events[-1].envelope_digest,
    )
    return register.append(event), event


@pytest.mark.parametrize(
    ("order_type", "limit_price", "stop_price"),
    [
        (OrderType.MARKET, None, None),
        (OrderType.LIMIT, Decimal(100), None),
        (OrderType.STOP, None, Decimal(99)),
        (OrderType.STOP_LIMIT, Decimal(100), Decimal(99)),
    ],
)
def test_order_types_and_time_in_force_are_sealed(
    order_type: Any, limit_price: Any, stop_price: Any
) -> None:
    terms = _terms(
        order_type=order_type,
        limit_price=limit_price,
        stop_price=stop_price,
        control_price=limit_price or Decimal(100),
        time_in_force=TimeInForce.GOOD_TILL_CANCELLED,
    )
    register = _register(terms=terms)
    assert replay(register.events).orders[OrderId(_id("ord_", 80))].terms == terms


def test_invalid_price_terms_refuse_construction() -> None:
    with pytest.raises(ValueError, match="limit price"):
        _terms(order_type=OrderType.MARKET, limit_price=Decimal(100))
    with pytest.raises(ValueError, match="stop price"):
        _terms(order_type=OrderType.STOP, limit_price=None, stop_price=None)
    with pytest.raises(ValueError, match="control price"):
        _terms(control_price=Decimal(99))


def test_cancel_preserves_partial_fill_and_late_fill_opens_exception() -> None:
    venue = VenueEmulator(seed=7).execute(
        _venue_order(), outcome=VenueOutcome.PARTIAL_FILL, event_time=NOW
    )
    assert isinstance(venue.event, Recorded) and isinstance(venue.report, Recorded)
    report = ExecutionReport.model_validate(venue.report.value.model_dump(mode="python"))
    applied = apply_execution(
        _child_register(), venue.event.value, report, actor=_actor(), event=_event(81)
    )
    cancelled = cancel_order(
        applied.journal,
        order_id=report.order_id,
        actor=_actor(),
        event=_event(82),
        reason="customer cancelled remainder",
    )
    snapshot = replay(cancelled.events).orders[report.order_id]
    assert snapshot.executed_quantity == report.quantity
    assert snapshot.state is LifecycleState.CANCELLED
    late = apply_execution(cancelled, venue.event.value, report, actor=_actor(), event=_event(83))
    assert (
        replay(late.journal.events).orders[report.order_id].state is LifecycleState.EXCEPTION_OPEN
    )
    assert "late fill" in late.reason


class _Calendar:
    def __init__(self, open_day: bool) -> None:
        self.open_day = open_day

    def is_business_day(self, value: date) -> bool:
        del value
        return self.open_day


def test_day_expiry_uses_shared_calendar() -> None:
    register = _register()
    order_id = OrderId(_id("ord_", 80))
    expired = expire_day_orders(
        register,
        session_date=date(2026, 10, 22),
        calendar=_Calendar(True),
        actor=_actor(),
        events={order_id: _event(81)},
    )
    assert replay(expired.events).orders[order_id].state is LifecycleState.EXPIRED
    with pytest.raises(ValueError, match="business day"):
        expire_day_orders(
            register,
            session_date=date(2026, 10, 24),
            calendar=_Calendar(False),
            actor=_actor(),
            events={order_id: _event(81)},
        )
    gtc = _register(terms=_terms(time_in_force=TimeInForce.GOOD_TILL_CANCELLED))
    assert (
        expire_day_orders(
            gtc,
            session_date=date(2026, 10, 22),
            calendar=_Calendar(True),
            actor=_actor(),
            events={},
        )
        == gtc
    )


def test_open_order_notional_is_replayed_and_changes_after_cancel() -> None:
    register = _register(quantity=Decimal(90), terms=_terms())
    register, second_event = _append_order(
        register, number=81, quantity=Decimal(20), terms=_terms()
    )
    release = ChildRelease(
        order_event=second_event,
        lifecycle_register=register,
        reference_price=Decimal(100),
        submitted_at=NOW,
    )
    refused = MarketAccessGate().evaluate(
        release, limits=limits(), capacity_input=capacity(), locates_input=locates()
    )
    assert next(check for check in refused.checks if check.code == "ACCOUNT_CREDIT").passed is False
    register = cancel_order(
        register,
        order_id=OrderId(_id("ord_", 80)),
        actor=_actor(),
        event=_event(82),
        reason="cancel resting order",
    )
    passed = MarketAccessGate().evaluate(
        release.model_copy(update={"lifecycle_register": register}),
        limits=limits(),
        capacity_input=capacity(),
        locates_input=locates(),
    )
    assert next(check for check in passed.checks if check.code == "ACCOUNT_CREDIT").passed


def test_market_access_refuses_unsealed_or_unregistered_terms() -> None:
    register = _register()
    event = register.events[-1]
    without_terms = event.model_copy(
        update={"payload": event.payload.model_copy(update={"terms": None})}
    )
    with pytest.raises(ValueError, match="terms sealed"):
        MarketAccessGate().evaluate(
            ChildRelease(
                order_event=without_terms,
                lifecycle_register=register,
                reference_price=Decimal(100),
                submitted_at=NOW,
            ),
            limits=limits(),
            capacity_input=capacity(),
            locates_input=locates(),
        )
    _, unregistered_event = _append_order(register, number=81, quantity=Decimal(1), terms=_terms())
    other = _register(terms=_terms(account_id="B"))
    with pytest.raises(ValueError, match="not in the lifecycle"):
        MarketAccessGate().evaluate(
            ChildRelease(
                order_event=unregistered_event,
                lifecycle_register=other,
                reference_price=Decimal(100),
                submitted_at=NOW,
            ),
            limits=limits(),
            capacity_input=capacity(),
            locates_input=locates(),
        )


def test_firm_notional_includes_other_accounts_without_charging_account_limit() -> None:
    register = _register(quantity=Decimal(90), terms=_terms(account_id="B"))
    register, second_event = _append_order(
        register, number=81, quantity=Decimal(20), terms=_terms(account_id="A")
    )
    decision = MarketAccessGate().evaluate(
        ChildRelease(
            order_event=second_event,
            lifecycle_register=register,
            reference_price=Decimal(100),
            submitted_at=NOW,
        ),
        limits=limits(firm_aggregate_threshold=Decimal(10000)),
        capacity_input=capacity(),
        locates_input=locates(),
    )
    assert next(check for check in decision.checks if check.code == "ACCOUNT_CREDIT").passed
    assert (
        next(check for check in decision.checks if check.code == "FIRM_AGGREGATE").passed is False
    )


def test_live_order_without_terms_refuses_credit_and_names_order() -> None:
    register = _register(quantity=Decimal(1), terms=_terms(account_id="B"))
    register, _ = _append_order(register, number=81, quantity=Decimal(1), terms=None)
    register, candidate = _append_order(
        register, number=82, quantity=Decimal(1), terms=_terms(account_id="A")
    )
    decision = MarketAccessGate().evaluate(
        ChildRelease(
            order_event=candidate,
            lifecycle_register=register,
            reference_price=Decimal(100),
            submitted_at=NOW,
        ),
        limits=limits(),
        capacity_input=capacity(),
        locates_input=locates(),
    )
    check = next(item for item in decision.checks if item.code == "OPEN_ORDER_TERMS")
    assert check.passed is False
    assert str(OrderId(_id("ord_", 81))) in check.reason
    assert decision.disposition.value == "HOLD"


def test_targeted_allocation_and_two_fill_average_price_conserve() -> None:
    journal = _child_register()
    report = ExecutionReport(
        execution_id="E1",
        order_id=OrderId(_id("ord_", 11)),
        intent_id=_payload().intent_id,
        quantity=Decimal(100),
        price=Decimal(100),
        venue="VENUE-SYNTHETIC",
        outcome="FILL",
    )
    # The allocation engine consumes capture records, so the lifecycle needs only the
    # TRADE_CAPTURED state; the two executions remain separate records in the allocation.
    journal = transition(
        journal,
        order_id=report.order_id,
        target=LifecycleState.EXECUTED,
        reason="two fills complete",
        actor=_actor(),
        event=_event(84),
        quantity=Decimal(100),
    )
    journal, first = capture(journal, report, _payload(), actor=_actor(), event=_event(85))
    fills = (
        first.model_copy(update={"trade_id": "T1", "quantity": Decimal(40), "price": Decimal(100)}),
        first.model_copy(update={"trade_id": "T2", "quantity": Decimal(30), "price": Decimal(107)}),
    )
    plan = AllocationPlan(
        targets=(
            AllocationTarget(account="A", target_quantity=Decimal(50)),
            AllocationTarget(account="B", target_quantity=Decimal(30)),
            AllocationTarget(account="C", target_quantity=Decimal(20)),
        ),
        rule=AllocationRule.PRO_RATA,
        rounding_method="LARGEST_REMAINDER",
    )
    _, allocation = allocate(
        journal,
        fills,
        _payload(),
        allocation_id=AllocationId(_id("alc_", 86)),
        actor=_actor(),
        event=_event(86),
        plan=plan,
    )
    assert [entry.quantity for entry in allocation.entries] == [35, 21, 14]
    assert len({entry.average_price for entry in allocation.entries}) == 1
    assert sum((entry.quantity for entry in allocation.entries), Decimal(0)) == 70
    assert allocation.average_price == 103
    assert sum((entry.consideration for entry in allocation.entries), Decimal(0)) == 7210


def test_residual_and_full_target_allocation_are_declared() -> None:
    plan = AllocationPlan(
        targets=(
            AllocationTarget(account="A", target_quantity=Decimal(50)),
            AllocationTarget(account="B", target_quantity=Decimal(30)),
            AllocationTarget(account="C", target_quantity=Decimal(20)),
        ),
        rule=AllocationRule.PRO_RATA,
        rounding_method="LARGEST_REMAINDER",
    )
    # Directly exercise the same engine with a captured order prepared above.
    journal = _child_register()
    order_id = OrderId(_id("ord_", 11))
    journal = transition(
        journal,
        order_id=order_id,
        target=LifecycleState.EXECUTED,
        reason="complete",
        actor=_actor(),
        event=_event(87),
        quantity=Decimal(100),
    )
    report = ExecutionReport(
        execution_id="E2",
        order_id=order_id,
        intent_id=_payload().intent_id,
        quantity=Decimal(100),
        price=Decimal(100),
        venue="VENUE-SYNTHETIC",
        outcome="FILL",
    )
    journal, captured = capture(journal, report, _payload(), actor=_actor(), event=_event(88))
    _, full = allocate(
        journal,
        captured,
        _payload(),
        allocation_id=AllocationId(_id("alc_", 89)),
        actor=_actor(),
        event=_event(89),
        plan=plan,
    )
    assert [entry.quantity for entry in full.entries] == [50, 30, 20]
    assert full.residual_assignments == ()


def test_allocation_plan_and_rounding_refusal_paths_are_explicit() -> None:
    target = AllocationTarget(account="A", target_quantity=Decimal(1))
    with pytest.raises(ValueError, match="unique accounts"):
        AllocationPlan(
            targets=(target, target),
            rule=AllocationRule.PRO_RATA,
            rounding_method="LARGEST_REMAINDER",
        )
    with pytest.raises(ValueError, match="requires"):
        AllocationPlan(
            targets=(target,), rule=AllocationRule.ROTATION, rounding_method="LARGEST_REMAINDER"
        )
    with pytest.raises(ValueError, match="ROUND_HALF_EVEN"):
        AllocationPlan(
            targets=(target,),
            rule=AllocationRule.PRO_RATA,
            rounding_method="LARGEST_REMAINDER",
            average_price_rounding="ROUND_DOWN",
        )
    plan = AllocationPlan(
        targets=(
            AllocationTarget(account="A", target_quantity=Decimal(2)),
            AllocationTarget(account="B", target_quantity=Decimal(1)),
        ),
        rule=AllocationRule.ROTATION,
        rounding_method="TARGET_ORDER",
    )
    journal = _child_register()
    order_id = OrderId(_id("ord_", 11))
    journal = transition(
        journal,
        order_id=order_id,
        target=LifecycleState.EXECUTED,
        reason="complete",
        actor=_actor(),
        event=_event(91),
        quantity=Decimal(100),
    )
    report = ExecutionReport(
        execution_id="E3",
        order_id=order_id,
        intent_id=_payload().intent_id,
        quantity=Decimal(100),
        price=Decimal(100),
        venue="VENUE-SYNTHETIC",
        outcome="FILL",
    )
    with pytest.raises(ValueError, match="EXECUTED"):
        capture(_child_register(), report, _payload(), actor=_actor(), event=_event(90))
    journal, captured = capture(journal, report, _payload(), actor=_actor(), event=_event(92))
    sealed = transition(
        _child_register(),
        order_id=order_id,
        target=LifecycleState.EXECUTED,
        reason="complete with allocation plan",
        actor=_actor(),
        event=_event(98),
        quantity=Decimal(100),
        terms=_terms(allocation_plan_digest=digest(plan)),
    )
    sealed, sealed_capture = capture(sealed, report, _payload(), actor=_actor(), event=_event(99))
    with pytest.raises(ValueError, match="plan sealed"):
        allocate(
            sealed,
            sealed_capture,
            _payload(),
            allocation_id=AllocationId(_id("alc_", 99)),
            actor=_actor(),
            event=_event(100),
            plan=plan.model_copy(update={"rounding_method": "OTHER"}),
        )
    with pytest.raises(ValueError, match="at least one"):
        allocate(
            journal,
            (),
            _payload(),
            allocation_id=AllocationId(_id("alc_", 92)),
            actor=_actor(),
            event=_event(93),
            plan=plan,
        )
    with pytest.raises(ValueError, match="exceeds declared"):
        allocate(
            journal,
            captured,
            _payload(),
            allocation_id=AllocationId(_id("alc_", 93)),
            actor=_actor(),
            event=_event(94),
            plan=plan,
        )
    small = captured.model_copy(update={"quantity": Decimal(2), "price": Decimal(100)})
    _, rotated = allocate(
        journal,
        small,
        _payload(),
        allocation_id=AllocationId(_id("alc_", 94)),
        actor=_actor(),
        event=_event(95),
        plan=plan,
    )
    assert [entry.quantity for entry in rotated.entries] == [2]
    assert rotated.residual_assignments == ("A",)
    fractional = small.model_copy(update={"quantity": Decimal("1.5")})
    with pytest.raises(ValueError, match="whole-share"):
        allocate(
            journal,
            fractional,
            _payload(),
            allocation_id=AllocationId(_id("alc_", 95)),
            actor=_actor(),
            event=_event(96),
            plan=plan,
        )
    repeating = small.model_copy(update={"quantity": Decimal(1), "price": Decimal(100)})
    other = small.model_copy(
        update={"trade_id": "OTHER", "quantity": Decimal(2), "price": Decimal("100.50")}
    )
    fractional_plan = AllocationPlan(
        targets=(
            AllocationTarget(account="A", target_quantity=Decimal(1)),
            AllocationTarget(account="B", target_quantity=Decimal(1)),
            AllocationTarget(account="C", target_quantity=Decimal(1)),
        ),
        rule=AllocationRule.PRO_RATA,
        rounding_method="LARGEST_REMAINDER",
        fractional_enabled=True,
    )
    _, repeating_allocation = allocate(
        journal,
        (repeating, other),
        _payload(),
        allocation_id=AllocationId(_id("alc_", 96)),
        actor=_actor(),
        event=_event(97),
        plan=fractional_plan,
    )
    assert repeating_allocation.average_price == Decimal("100.333333")
    assert sum(
        (entry.consideration for entry in repeating_allocation.entries), Decimal(0)
    ) == Decimal("301.00")
    assert repeating_allocation.money_residuals
    with pytest.raises(AllocationRefusalError, match="declared plan"):
        allocate(
            journal,
            repeating,
            _payload(),
            allocation_id=AllocationId(_id("alc_", 97)),
            actor=_actor(),
            event=_event(101),
        )


def test_allocation_conserves_quantity_and_money_for_200_random_examples() -> None:
    rng = Random(20261022)
    journal = _child_register()
    order_id = OrderId(_id("ord_", 11))
    journal = transition(
        journal,
        order_id=order_id,
        target=LifecycleState.EXECUTED,
        reason="property allocation setup",
        actor=_actor(),
        event=_event(101),
        quantity=Decimal(100),
    )
    base_report = ExecutionReport(
        execution_id="PROPERTY",
        order_id=order_id,
        intent_id=_payload().intent_id,
        quantity=Decimal(100),
        price=Decimal(100),
        venue="VENUE-SYNTHETIC",
        outcome="FILL",
    )
    journal, captured = capture(journal, base_report, _payload(), actor=_actor(), event=_event(102))
    for example in range(200):
        total = rng.randint(3, 100)
        first_fill = rng.randint(1, total - 1)
        quantities = (Decimal(first_fill), Decimal(total - first_fill))
        prices = (
            Decimal(rng.randint(1, 50000)) / 100,
            Decimal(rng.randint(1, 50000)) / 100,
        )
        target_a = rng.randint(1, total - 2)
        target_b = rng.randint(1, total - target_a - 1)
        target_c = total - target_a - target_b
        plan = AllocationPlan(
            targets=(
                AllocationTarget(account="A", target_quantity=Decimal(target_a)),
                AllocationTarget(account="B", target_quantity=Decimal(target_b)),
                AllocationTarget(account="C", target_quantity=Decimal(target_c)),
            ),
            rule=AllocationRule.PRO_RATA,
            rounding_method="LARGEST_REMAINDER",
        )
        fills = tuple(
            captured.model_copy(
                update={"trade_id": f"P{example}-{index}", "quantity": quantity, "price": price}
            )
            for index, (quantity, price) in enumerate(zip(quantities, prices, strict=True))
        )
        _, allocation = allocate(
            journal,
            fills,
            _payload(),
            allocation_id=AllocationId(_id("alc_", 103)),
            actor=_actor(),
            event=_event(103),
            plan=plan,
        )
        expected_money = sum(
            (quantity * price for quantity, price in zip(quantities, prices, strict=True)),
            Decimal(0),
        ).quantize(Decimal("0.01"))
        assert sum((entry.quantity for entry in allocation.entries), Decimal(0)) == total
        assert (
            sum((entry.consideration for entry in allocation.entries), Decimal(0)) == expected_money
        )


def test_large_repeating_average_allocates_without_error() -> None:
    journal = _register(quantity=Decimal(300))
    order_id = OrderId(_id("ord_", 80))
    journal = transition(
        journal,
        order_id=order_id,
        target=LifecycleState.MARKET_ACCESS_PASSED,
        reason="large repeating-average controls passed",
        actor=_actor(),
        event=_event(107),
    )
    journal = transition(
        journal,
        order_id=order_id,
        target=LifecycleState.EXECUTED,
        reason="large repeating-average setup",
        actor=_actor(),
        event=_event(108),
        quantity=Decimal(300),
    )
    report = ExecutionReport(
        execution_id="REPEAT-LARGE",
        order_id=order_id,
        intent_id=_payload().intent_id,
        quantity=Decimal(300),
        price=Decimal("100.01"),
        venue="VENUE-SYNTHETIC",
        outcome="FILL",
    )
    journal, captured = capture(journal, report, _payload(), actor=_actor(), event=_event(109))
    fills = (
        captured.model_copy(
            update={"trade_id": "L1", "quantity": Decimal(100), "price": Decimal("100.01")}
        ),
        captured.model_copy(
            update={"trade_id": "L2", "quantity": Decimal(200), "price": Decimal("100.02")}
        ),
    )
    plan = AllocationPlan(
        targets=(
            AllocationTarget(account="A", target_quantity=Decimal(200)),
            AllocationTarget(account="B", target_quantity=Decimal(100)),
        ),
        rule=AllocationRule.PRO_RATA,
        rounding_method="LARGEST_REMAINDER",
    )
    _, allocation = allocate(
        journal,
        fills,
        _payload(),
        allocation_id=AllocationId(_id("alc_", 110)),
        actor=_actor(),
        event=_event(110),
        plan=plan,
    )
    assert allocation.average_price == Decimal("100.016667")
    assert sum((entry.consideration for entry in allocation.entries), Decimal(0)) == Decimal(
        "30005.00"
    )


def test_ioc_cancels_remainder_and_fok_refuses_partial_fill() -> None:
    result = VenueEmulator(seed=7).execute(
        _venue_order(), outcome=VenueOutcome.PARTIAL_FILL, event_time=NOW
    )
    assert isinstance(result.event, Recorded) and isinstance(result.report, Recorded)
    report = ExecutionReport.model_validate(result.report.value.model_dump(mode="python"))
    ioc = apply_execution(
        _child_register(terms=_terms(time_in_force=TimeInForce.IMMEDIATE_OR_CANCEL)),
        result.event.value,
        report,
        actor=_actor(),
        event=_event(104),
        remainder_event=_event(105),
    )
    ioc_order = replay(ioc.journal.events).orders[report.order_id]
    assert ioc.disposition.value == "PASS"
    assert ioc_order.state is LifecycleState.CANCELLED
    assert ioc_order.executed_quantity == report.quantity
    fok = apply_execution(
        _child_register(terms=_terms(time_in_force=TimeInForce.FILL_OR_KILL)),
        result.event.value,
        report,
        actor=_actor(),
        event=_event(106),
    )
    fok_order = replay(fok.journal.events).orders[report.order_id]
    assert fok.disposition.value == "HOLD"
    assert fok_order.state is LifecycleState.CANCELLED
    assert fok_order.executed_quantity == 0
    missing_cancel_event = apply_execution(
        _child_register(terms=_terms(time_in_force=TimeInForce.IMMEDIATE_OR_CANCEL)),
        result.event.value,
        report,
        actor=_actor(),
        event=_event(107),
    )
    assert missing_cancel_event.disposition.value == "HOLD"
    assert "distinct event" in missing_cancel_event.reason


def test_kill_switch_cancels_scope_refuses_new_order_and_release_replays() -> None:
    register = _register()
    order_id = OrderId(_id("ord_", 80))
    controls, cancelled = engage_halt(
        FirmControlJournal(),
        register,
        halt_id="HALT-1",
        scope=HaltScope.ACCOUNT,
        scope_id="A",
        authority=_actor(),
        recorded_at=NOW,
        cancellation_events={order_id: _event(81)},
    )
    assert replay(cancelled.events).orders[order_id].state is LifecycleState.CANCELLED
    new_register = _register()
    decision = MarketAccessGate().evaluate(
        ChildRelease(
            order_event=new_register.events[-1],
            lifecycle_register=new_register,
            reference_price=Decimal(100),
            submitted_at=NOW,
        ),
        limits=limits(),
        capacity_input=capacity(),
        locates_input=locates(),
        halts=controls.active_halts,
    )
    assert next(check for check in decision.checks if check.code == "FIRM_HALT").passed is False
    released = release_halt(controls, halt_id="HALT-1", authority=_actor(), released_at=NOW)
    assert released.active_halts == ()
    assert released.events[-1].applies(account_id="A", strategy_id="S1") is False
    assert FirmControlJournal.model_validate_json(released.model_dump_json()) == released
    with pytest.raises(ValueError, match="active halt"):
        release_halt(released, halt_id="HALT-1", authority=_actor(), released_at=NOW)
    _, untouched = engage_halt(
        FirmControlJournal(),
        register,
        halt_id="HALT-OTHER",
        scope=HaltScope.ACCOUNT,
        scope_id="OTHER",
        authority=_actor(),
        recorded_at=NOW,
        cancellation_events={},
    )
    assert untouched == register


def test_kill_switch_requires_authenticated_scope_bound_authority() -> None:
    with pytest.raises(ValueError, match="authenticated"):
        FirmHalt(
            halt_id="H",
            scope=HaltScope.FIRM,
            scope_id=None,
            authority=_actor().model_copy(update={"authenticated": False}),
            recorded_at=NOW,
        )
    with pytest.raises(ValueError, match="scope"):
        FirmHalt(
            halt_id="H", scope=HaltScope.ACCOUNT, scope_id=None, authority=_actor(), recorded_at=NOW
        )


def test_replace_reruns_strategy_and_market_access_on_new_terms() -> None:
    register = _register()
    outcome = replace_order(
        register,
        order_id=OrderId(_id("ord_", 80)),
        replacement_event=_event(81),
        decision_event=_event(82),
        replacement_terms=_terms(control_price=Decimal(120), limit_price=Decimal(120)),
        actor=_actor(),
        strategy_policy=_policy(),
        market_evidence=_evidence(price=Decimal(120)),
        market_gate=MarketAccessGate(),
        limits=limits(),
        capacity_input=capacity(),
        locates_input=locates(),
        reference_price=Decimal(100),
        processing_time=NOW,
    )
    assert outcome.strategy_decision.disposition.value == "HOLD"
    assert outcome.market_access_decision.disposition.value == "HOLD"
    assert replay(outcome.journal.events).orders[OrderId(_id("ord_", 80))].state is (
        LifecycleState.MARKET_ACCESS_REFUSED
    )
