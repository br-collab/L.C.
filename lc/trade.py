"""Trade capture, allocation, matching, exception opening and affirmation."""

from __future__ import annotations

from decimal import Decimal

from cannae_kernel.actor import ActorRef
from cannae_kernel.canonical import digest
from cannae_kernel.disposition import Disposition
from cannae_kernel.ids import AllocationId, OrderId
from cannae_kernel.provenance import Provenance
from pydantic import BaseModel, ConfigDict, Field

from lc.events import LifecycleState
from lc.lifecycle import (
    ApprovedIntentPayload,
    EventInput,
    LifecycleRegister,
    record_not_reached,
    replay,
    transition,
)
from lc.policy import ExecutionReport

__all__ = [
    "AllocationEntry",
    "AllocationRecord",
    "CaptureRecord",
    "MatchFact",
    "OpenException",
    "TradeOutcome",
    "affirm",
    "allocate",
    "capture",
    "record_match",
]


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class CaptureRecord(_Record):
    trade_id: str = Field(min_length=1)
    order_id: OrderId
    execution_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    instrument_id: str = Field(min_length=1)
    quantity: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    enrichment_folded_into_capture: bool = True
    provenance: Provenance = Provenance.POLICY_RESULT


class AllocationEntry(_Record):
    account: str = Field(min_length=1)
    quantity: Decimal = Field(gt=0)


class AllocationRecord(_Record):
    allocation_id: AllocationId
    trade_id: str = Field(min_length=1)
    entries: tuple[AllocationEntry, ...]
    executed_quantity: Decimal = Field(gt=0)


class MatchFact(_Record):
    response_id: str = Field(min_length=1)
    trade_id: str = Field(min_length=1)
    matched: bool
    affirmed: bool
    reason: str = Field(min_length=1)
    provenance: Provenance


class OpenException(_Record):
    exception_id: str = Field(min_length=1)
    order_id: OrderId
    reason: str = Field(min_length=1)
    source_response_id: str = Field(min_length=1)


class TradeOutcome(_Record):
    journal: LifecycleRegister
    disposition: Disposition
    exception: OpenException | None = None


def capture(
    register: LifecycleRegister,
    report: ExecutionReport,
    intent: ApprovedIntentPayload,
    *,
    actor: ActorRef,
    event: EventInput,
) -> tuple[LifecycleRegister, CaptureRecord]:
    snapshot = replay(register.events)
    order = snapshot.orders[report.order_id]
    if order.state is not LifecycleState.EXECUTED:
        raise ValueError("trade capture requires an EXECUTED order")
    record = CaptureRecord(
        trade_id=f"trade-{report.execution_id}",
        order_id=report.order_id,
        execution_digest=digest(report),
        instrument_id=intent.instrument_id,
        quantity=order.executed_quantity,
        price=report.price,
    )
    journal = transition(
        register,
        order_id=report.order_id,
        target=LifecycleState.TRADE_CAPTURED,
        reason="execution captured; ENRICHED is explicitly folded into TRADE_CAPTURED",
        actor=actor,
        event=event,
        quantity=record.quantity,
    )
    return journal, record


def allocate(  # noqa: PLR0913 - allocation evidence is explicit at the call site
    register: LifecycleRegister,
    capture_record: CaptureRecord,
    intent: ApprovedIntentPayload,
    *,
    allocation_id: AllocationId,
    actor: ActorRef,
    event: EventInput,
) -> tuple[LifecycleRegister, AllocationRecord]:
    accounts = intent.allocation_accounts
    each = capture_record.quantity / Decimal(len(accounts))
    entries = tuple(
        AllocationEntry(
            account=account,
            quantity=(
                capture_record.quantity - each * Decimal(len(accounts) - 1)
                if index == len(accounts) - 1
                else each
            ),
        )
        for index, account in enumerate(accounts)
    )
    total = sum((entry.quantity for entry in entries), Decimal(0))
    if total != capture_record.quantity:
        raise AssertionError("allocated quantity must equal executed quantity")
    allocation = AllocationRecord(
        allocation_id=allocation_id,
        trade_id=capture_record.trade_id,
        entries=entries,
        executed_quantity=capture_record.quantity,
    )
    journal = transition(
        register,
        order_id=capture_record.order_id,
        target=LifecycleState.ALLOCATED,
        reason=f"allocation intent applied; quantity conserved in {allocation_id}",
        actor=actor,
        event=event,
        quantity=total,
    )
    return journal, allocation


def record_match(  # noqa: PLR0913 - match evidence is explicit at the call site
    register: LifecycleRegister,
    *,
    order_id: OrderId,
    fact: MatchFact,
    exception_id: str,
    actor: ActorRef,
    event: EventInput,
) -> TradeOutcome:
    if not fact.matched:
        exception = OpenException(
            exception_id=exception_id,
            order_id=order_id,
            reason=fact.reason,
            source_response_id=fact.response_id,
        )
        journal = transition(
            register,
            order_id=order_id,
            target=LifecycleState.EXCEPTION_OPEN,
            reason=f"matching exception {exception_id}: {fact.reason}",
            actor=actor,
            event=event,
        )
        return TradeOutcome(
            journal=journal,
            disposition=Disposition.HOLD,
            exception=exception,
        )
    journal = transition(
        register,
        order_id=order_id,
        target=LifecycleState.MATCHED,
        reason=f"matching fact {fact.response_id} verified",
        actor=actor,
        event=event,
    )
    return TradeOutcome(journal=journal, disposition=Disposition.PASS)


def affirm(
    outcome: TradeOutcome,
    *,
    order_id: OrderId,
    fact: MatchFact,
    actor: ActorRef,
    event: EventInput,
) -> TradeOutcome:
    if outcome.exception is not None or not fact.affirmed:
        journal = record_not_reached(
            outcome.journal,
            order_id=order_id,
            requested=LifecycleState.AFFIRMED,
            reason=f"affirmation not received ({fact.reason})",
            actor=actor,
            event=event,
        )
        return TradeOutcome(
            journal=journal,
            disposition=Disposition.HOLD,
            exception=outcome.exception,
        )
    journal = transition(
        outcome.journal,
        order_id=order_id,
        target=LifecycleState.AFFIRMED,
        reason=f"affirmation fact {fact.response_id} verified",
        actor=actor,
        event=event,
    )
    return TradeOutcome(journal=journal, disposition=Disposition.PASS)
