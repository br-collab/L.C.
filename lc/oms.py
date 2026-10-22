"""Order-management controls built on the sealed L.C. lifecycle register."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from cannae_kernel.actor import ActorRef
from cannae_kernel.disposition import Disposition
from cannae_kernel.ids import OrderId
from pydantic import BaseModel, ConfigDict

from lc.events import LifecycleState, OrderEvent, OrderTerms, TimeInForce
from lc.lifecycle import EventInput, LifecycleRegister, replay, transition
from lc.market_access import (
    AttestedInput,
    ChildRelease,
    FirmHalt,
    HaltScope,
    LimitsManifest,
    MarketAccessDecision,
    MarketAccessGate,
)
from lc.policy import ExecutionPolicy, GateDecision, MarketEvidenceSnapshot, evaluate_policy


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class BusinessDayCalendar(Protocol):
    def is_business_day(self, value: date) -> bool: ...


class FirmControlJournal(_Record):
    events: tuple[FirmHalt, ...] = ()

    @property
    def active_halts(self) -> tuple[FirmHalt, ...]:
        latest = {event.halt_id: event for event in self.events}
        return tuple(event for event in latest.values() if event.released_at is None)


class ReplacementOutcome(_Record):
    journal: LifecycleRegister
    strategy_decision: GateDecision
    market_access_decision: MarketAccessDecision


def cancel_order(
    register: LifecycleRegister,
    *,
    order_id: OrderId,
    actor: ActorRef,
    event: EventInput,
    reason: str,
) -> LifecycleRegister:
    """Cancel only the unfilled remainder; replay preserves executed quantity."""
    return transition(
        register,
        order_id=order_id,
        target=LifecycleState.CANCELLED,
        reason=reason,
        actor=actor,
        event=event,
    )


def expire_day_orders(
    register: LifecycleRegister,
    *,
    session_date: date,
    calendar: BusinessDayCalendar,
    actor: ActorRef,
    events: dict[OrderId, EventInput],
) -> LifecycleRegister:
    if not calendar.is_business_day(session_date):
        raise ValueError("session end must be a business day in the shared calendar")
    result = register
    for order_id, order in replay(register.events).orders.items():
        if (
            order.terms is not None
            and order.terms.time_in_force is TimeInForce.DAY
            and order.state
            in {
                LifecycleState.PARENT_ACTIVE,
                LifecycleState.MARKET_ACCESS_PASSED,
                LifecycleState.PARTIALLY_EXECUTED,
            }
        ):
            result = transition(
                result,
                order_id=order_id,
                target=LifecycleState.EXPIRED,
                reason=f"DAY order expired at session end {session_date.isoformat()}",
                actor=actor,
                event=events[order_id],
            )
    return result


def engage_halt(  # noqa: PLR0913 - halt authority and cancellation events are evidence
    controls: FirmControlJournal,
    register: LifecycleRegister,
    *,
    halt_id: str,
    scope: HaltScope,
    scope_id: str | None,
    authority: ActorRef,
    recorded_at: datetime,
    cancellation_events: dict[OrderId, EventInput],
) -> tuple[FirmControlJournal, LifecycleRegister]:
    halt = FirmHalt(
        halt_id=halt_id,
        scope=scope,
        scope_id=scope_id,
        authority=authority,
        recorded_at=recorded_at,
    )
    updated = register
    for order_id, order in replay(register.events).orders.items():
        if (
            order.terms is not None
            and halt.applies(account_id=order.terms.account_id, strategy_id=order.terms.strategy_id)
            and order.state
            in {
                LifecycleState.PARENT_ACTIVE,
                LifecycleState.MARKET_ACCESS_PASSED,
                LifecycleState.PARTIALLY_EXECUTED,
            }
        ):
            updated = cancel_order(
                updated,
                order_id=order_id,
                actor=authority,
                event=cancellation_events[order_id],
                reason=f"cancelled by authority-bound firm halt {halt_id}",
            )
    return controls.model_copy(update={"events": (*controls.events, halt)}), updated


def release_halt(
    controls: FirmControlJournal,
    *,
    halt_id: str,
    authority: ActorRef,
    released_at: datetime,
) -> FirmControlJournal:
    current = next((event for event in reversed(controls.events) if event.halt_id == halt_id), None)
    if current is None or current.released_at is not None:
        raise ValueError("active halt not found")
    released = current.model_copy(update={"authority": authority, "released_at": released_at})
    return controls.model_copy(update={"events": (*controls.events, released)})


def replace_order(  # noqa: PLR0913 - every control input remains explicit evidence
    register: LifecycleRegister,
    *,
    order_id: OrderId,
    replacement_event: EventInput,
    decision_event: EventInput,
    replacement_terms: OrderTerms,
    actor: ActorRef,
    strategy_policy: ExecutionPolicy,
    market_evidence: MarketEvidenceSnapshot,
    market_gate: MarketAccessGate,
    limits: LimitsManifest,
    capacity_input: AttestedInput | None,
    locates_input: AttestedInput | None,
    reference_price: Decimal,
    processing_time: datetime,
    halts: tuple[FirmHalt, ...] = (),
) -> ReplacementOutcome:
    replaced = transition(
        register,
        order_id=order_id,
        target=LifecycleState.REPLACED,
        reason="cancel/replace terms sealed; controls must rerun",
        actor=actor,
        event=replacement_event,
        terms=replacement_terms,
    )
    order_event: OrderEvent = replaced.events[-1]
    strategy = evaluate_policy(
        strategy_policy,
        child_quantity=replay(replaced.events).orders[order_id].quantity,
        evidence=market_evidence.model_copy(update={"price": replacement_terms.control_price}),
        processing_time=processing_time,
    )
    market = market_gate.evaluate(
        ChildRelease(
            order_event=order_event,
            lifecycle_register=replaced,
            reference_price=reference_price,
            submitted_at=processing_time,
        ),
        limits=limits,
        capacity_input=capacity_input,
        locates_input=locates_input,
        halts=halts,
    )
    disposition = (
        Disposition.PASS
        if strategy.disposition is Disposition.PASS and market.disposition is Disposition.PASS
        else Disposition.HOLD
    )
    final = transition(
        replaced,
        order_id=order_id,
        target=(
            LifecycleState.MARKET_ACCESS_PASSED
            if disposition is Disposition.PASS
            else LifecycleState.MARKET_ACCESS_REFUSED
        ),
        reason=(
            f"replacement strategy={strategy.disposition.value}; market={market.disposition.value}"
        ),
        actor=actor,
        event=decision_event,
    )
    return ReplacementOutcome(
        journal=final, strategy_decision=strategy, market_access_decision=market
    )
