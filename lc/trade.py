"""Trade capture, allocation, matching, exception opening and affirmation."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
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
    "AllocationMoneyResidual",
    "AllocationPlan",
    "AllocationRecord",
    "AllocationRefusalError",
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


class CapturedExecution(_Record):
    execution_id: str = Field(min_length=1)
    quantity: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    execution_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class CaptureRecord(_Record):
    trade_id: str = Field(min_length=1)
    order_id: OrderId
    execution_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    instrument_id: str = Field(min_length=1)
    quantity: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    executions: tuple[CapturedExecution, ...]
    enrichment_folded_into_capture: bool = True
    provenance: Provenance = Provenance.POLICY_RESULT


class AllocationEntry(_Record):
    account: str = Field(min_length=1)
    quantity: Decimal = Field(gt=0)
    average_price: Decimal = Field(gt=0)
    consideration: Decimal = Field(gt=0)


class AllocationMoneyResidual(_Record):
    account: str = Field(min_length=1)
    amount: Decimal
    reason: str = Field(min_length=1)


class AllocationRefusalError(ValueError):
    """The declared allocation cannot conserve quantity or money."""


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
    average_price_decimal_places: int = Field(default=6, ge=0, le=12)
    average_price_rounding: str = "ROUND_HALF_EVEN"

    @model_validator(mode="after")
    def _unique_accounts(self) -> Self:
        if not self.targets or len({item.account for item in self.targets}) != len(self.targets):
            raise ValueError("allocation targets must name unique accounts")
        expected = "LARGEST_REMAINDER" if self.rule is AllocationRule.PRO_RATA else "TARGET_ORDER"
        if self.rounding_method != expected:
            raise ValueError(f"{self.rule.value} allocation requires {expected}")
        if self.average_price_rounding != "ROUND_HALF_EVEN":
            raise ValueError("average price rounding must be ROUND_HALF_EVEN")
        return self


class AllocationRecord(_Record):
    allocation_id: AllocationId
    trade_ids: tuple[str, ...]
    entries: tuple[AllocationEntry, ...]
    executed_quantity: Decimal = Field(gt=0)
    executed_consideration: Decimal = Field(gt=0)
    average_price: Decimal = Field(gt=0)
    average_price_decimal_places: int = Field(ge=0, le=12)
    average_price_rounding: str
    consideration_rounding: str
    rule: AllocationRule
    rounding_method: str
    residual_assignments: tuple[str, ...]
    money_residuals: tuple[AllocationMoneyResidual, ...]
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
    report: ExecutionReport | tuple[ExecutionReport, ...],
    intent: ApprovedIntentPayload,
    *,
    actor: ActorRef,
    event: EventInput,
) -> tuple[LifecycleRegister, CaptureRecord]:
    reports = report if isinstance(report, tuple) else (report,)
    if not reports:
        raise ValueError("trade capture requires execution reports")
    order_ids = {item.order_id for item in reports}
    if len(order_ids) != 1:
        raise ValueError("all captured reports must belong to one order")
    snapshot = replay(register.events)
    order = snapshot.orders[reports[0].order_id]
    if order.state is not LifecycleState.EXECUTED:
        raise ValueError("trade capture requires an EXECUTED order")
    total_quantity = sum((item.quantity for item in reports), Decimal(0))
    if total_quantity != order.executed_quantity:
        raise ValueError("execution reports do not sum to the executed quantity")
    total_money = sum((item.quantity * item.price for item in reports), Decimal(0))
    executions = tuple(
        CapturedExecution(
            execution_id=item.execution_id, quantity=item.quantity,
            price=item.price, execution_digest=digest(item),
        )
        for item in reports
    )
    record = CaptureRecord(
        trade_id=f"trade-{reports[0].execution_id}",
        order_id=reports[0].order_id,
        execution_digest=executions[0].execution_digest,
        instrument_id=intent.instrument_id,
        quantity=total_quantity,
        price=total_money / total_quantity,
        executions=executions,
    )
    journal = transition(
        register,
        order_id=reports[0].order_id,
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
        raise AllocationRefusalError("allocation requires at least one execution capture")
    if plan is None:
        raise AllocationRefusalError("allocation requires a declared plan before the order")
    total_quantity = sum((item.quantity for item in captures), Decimal(0))
    total_money = sum((item.quantity * item.price for item in captures), Decimal(0))
    price_quantum = Decimal(1).scaleb(-plan.average_price_decimal_places)
    average_price = (total_money / total_quantity).quantize(price_quantum, rounding=ROUND_HALF_EVEN)
    order = replay(register.events).orders[captures[0].order_id]
    if (
        order.terms is not None
        and order.terms.allocation_plan_digest is not None
        and order.terms.allocation_plan_digest != digest(plan)
    ):
        raise AllocationRefusalError(
            "allocation plan does not match the plan sealed before the order"
        )
    target_total = sum((item.target_quantity for item in plan.targets), Decimal(0))
    if total_quantity > target_total:
        raise AllocationRefusalError("executed quantity exceeds declared allocation targets")
    exact = [total_quantity * item.target_quantity / target_total for item in plan.targets]
    quantities = list(exact)
    residual_accounts: list[str] = []
    if not plan.fractional_enabled:
        if total_quantity != total_quantity.to_integral_value():
            raise AllocationRefusalError(
                "whole-share allocation cannot consume a fractional execution"
            )
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
    active = [
        (target, quantity, exact_quantity)
        for target, quantity, exact_quantity in zip(plan.targets, quantities, exact, strict=True)
        if quantity > 0
    ]
    total = sum((quantity for _, quantity, _ in active), Decimal(0))
    if total != total_quantity:  # pragma: no cover - guarded by allocation arithmetic
        raise AllocationRefusalError("declared allocation cannot conserve execution quantity")
    cent = Decimal("0.01")
    executed_consideration = total_money.quantize(cent, rounding=ROUND_HALF_EVEN)
    considerations = [
        (quantity * average_price).quantize(cent, rounding=ROUND_HALF_EVEN)
        for _, quantity, _ in active
    ]
    money_difference = executed_consideration - sum(considerations, Decimal(0))
    money_residuals: tuple[AllocationMoneyResidual, ...] = ()
    if money_difference:
        if not active:  # pragma: no cover - positive captures and targets guarantee a recipient
            raise AllocationRefusalError("monetary residual has no eligible allocation target")
        money_order = (
            sorted(
                range(len(active)),
                key=lambda index: (active[index][2] - active[index][1], -index),
                reverse=True,
            )
            if plan.rule is AllocationRule.PRO_RATA
            else list(range(len(active)))
        )
        recipient = money_order[0]
        considerations[recipient] += money_difference
        money_residuals = (
            AllocationMoneyResidual(
                account=active[recipient][0].account,
                amount=money_difference,
                reason=f"{plan.rule.value}:{plan.rounding_method}",
            ),
        )
    entries = tuple(
        AllocationEntry(
            account=target.account,
            quantity=quantity,
            average_price=average_price,
            consideration=consideration,
        )
        for (target, quantity, _), consideration in zip(active, considerations, strict=True)
    )
    if (  # pragma: no cover - residual assignment above makes this a defensive invariant
        sum((entry.consideration for entry in entries), Decimal(0)) != executed_consideration
    ):
        raise AllocationRefusalError("declared allocation cannot conserve execution consideration")
    allocation = AllocationRecord(
        allocation_id=allocation_id,
        trade_ids=tuple(item.trade_id for item in captures),
        entries=entries,
        executed_quantity=total_quantity,
        executed_consideration=executed_consideration,
        average_price=average_price,
        average_price_decimal_places=plan.average_price_decimal_places,
        average_price_rounding=plan.average_price_rounding,
        consideration_rounding="ROUND_HALF_EVEN_TO_CENT",
        rule=plan.rule,
        rounding_method=plan.rounding_method,
        residual_assignments=tuple(residual_accounts),
        money_residuals=money_residuals,
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
