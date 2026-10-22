"""Trade capture, allocation, matching, exception opening and affirmation."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Self

from cannae_kernel.actor import ActorRef
from cannae_kernel.canonical import digest
from cannae_kernel.disposition import Disposition
from cannae_kernel.ids import AllocationId, OrderId
from cannae_kernel.provenance import Provenance
from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    "AllocationPlan",
    "AllocationRecord",
    "AllocationRule",
    "AllocationTarget",
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
    average_price: Decimal = Field(gt=0)
    consideration: Decimal = Field(gt=0)


class AllocationRule(StrEnum):
    PRO_RATA = "PRO_RATA"
    ROTATION = "ROTATION"


class AllocationTarget(_Record):
    account: str = Field(min_length=1)
    target_quantity: Decimal = Field(gt=0)


class AllocationPlan(_Record):
    targets: tuple[AllocationTarget, ...]
    rule: AllocationRule
    rounding_method: str = Field(min_length=1)
    fractional_enabled: bool = False

    @model_validator(mode="after")
    def _unique_accounts(self) -> Self:
        if not self.targets or len({item.account for item in self.targets}) != len(self.targets):
            raise ValueError("allocation targets must name unique accounts")
        expected = "LARGEST_REMAINDER" if self.rule is AllocationRule.PRO_RATA else "TARGET_ORDER"
        if self.rounding_method != expected:
            raise ValueError(f"{self.rule.value} allocation requires {expected}")
        return self


class AllocationRecord(_Record):
    allocation_id: AllocationId
    trade_ids: tuple[str, ...]
    entries: tuple[AllocationEntry, ...]
    executed_quantity: Decimal = Field(gt=0)
    executed_consideration: Decimal = Field(gt=0)
    average_price: Decimal = Field(gt=0)
    rule: AllocationRule
    rounding_method: str
    residual_assignments: tuple[str, ...]
    allocation_plan_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


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
    capture_record: CaptureRecord | tuple[CaptureRecord, ...],
    intent: ApprovedIntentPayload,
    *,
    allocation_id: AllocationId,
    actor: ActorRef,
    event: EventInput,
    plan: AllocationPlan | None = None,
) -> tuple[LifecycleRegister, AllocationRecord]:
    captures = capture_record if isinstance(capture_record, tuple) else (capture_record,)
    if not captures:
        raise ValueError("allocation requires at least one execution capture")
    total_quantity = sum((item.quantity for item in captures), Decimal(0))
    total_money = sum((item.quantity * item.price for item in captures), Decimal(0))
    average_price = total_money / total_quantity
    plan = plan or AllocationPlan(
        targets=tuple(
            AllocationTarget(
                account=account,
                target_quantity=total_quantity / Decimal(len(intent.allocation_accounts)),
            )
            for account in intent.allocation_accounts
        ),
        rule=AllocationRule.PRO_RATA,
        rounding_method="LARGEST_REMAINDER",
        fractional_enabled=True,
    )
    order = replay(register.events).orders[captures[0].order_id]
    if (
        order.terms is not None
        and order.terms.allocation_plan_digest is not None
        and order.terms.allocation_plan_digest != digest(plan)
    ):
        raise ValueError("allocation plan does not match the plan sealed before the order")
    target_total = sum((item.target_quantity for item in plan.targets), Decimal(0))
    if total_quantity > target_total:
        raise ValueError("executed quantity exceeds declared allocation targets")
    exact = [total_quantity * item.target_quantity / target_total for item in plan.targets]
    quantities = list(exact)
    residual_accounts: list[str] = []
    if not plan.fractional_enabled:
        if total_quantity != total_quantity.to_integral_value():
            raise ValueError("whole-share allocation cannot consume a fractional execution")
        quantities = [value.to_integral_value(rounding="ROUND_FLOOR") for value in exact]
        residual = int(total_quantity - sum(quantities, Decimal(0)))
        rounding_order = (
            sorted(
                range(len(exact)),
                key=lambda index: (exact[index] - quantities[index], -index),
                reverse=True,
            )
            if plan.rule is AllocationRule.PRO_RATA
            else list(range(len(exact)))
        )
        for index in rounding_order[:residual]:
            quantities[index] += 1
            residual_accounts.append(plan.targets[index].account)
    entries = tuple(
        AllocationEntry(
            account=target.account,
            quantity=quantity,
            average_price=average_price,
            consideration=quantity * average_price,
        )
        for target, quantity in zip(plan.targets, quantities, strict=True)
        if quantity > 0
    )
    total = sum((entry.quantity for entry in entries), Decimal(0))
    if sum((entry.consideration for entry in entries), Decimal(0)) != total_money:
        raise AssertionError("allocated consideration must equal execution consideration")
    allocation = AllocationRecord(
        allocation_id=allocation_id,
        trade_ids=tuple(item.trade_id for item in captures),
        entries=entries,
        executed_quantity=total_quantity,
        executed_consideration=total_money,
        average_price=average_price,
        rule=plan.rule,
        rounding_method=plan.rounding_method,
        residual_assignments=tuple(residual_accounts),
        allocation_plan_digest=digest(plan),
    )
    journal = transition(
        register,
        order_id=captures[0].order_id,
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
