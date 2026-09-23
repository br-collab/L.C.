"""Deterministic execution-policy gate and execution-event consumer."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal, Self

from cannae_kernel.absence import AbsenceKind, Absent, Recorded
from cannae_kernel.actor import ActorKind, ActorRef
from cannae_kernel.canonical import digest
from cannae_kernel.disposition import Disposition
from cannae_kernel.envelopes import ExecutionEvent
from cannae_kernel.ids import IntentId, OrderId
from cannae_kernel.provenance import Provenance
from pydantic import BaseModel, ConfigDict, Field, model_validator

from lc.events import LifecycleState
from lc.lifecycle import EventInput, LifecycleRegister, replay, transition

__all__ = [
    "ExecutionApplication",
    "ExecutionPolicy",
    "ExecutionReport",
    "GateDecision",
    "MarketEvidenceSnapshot",
    "OperatorDecision",
    "apply_execution",
    "evaluate_policy",
]


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class ExecutionPolicy(_Record):
    policy_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    allowed_venues: tuple[str, ...]
    max_child_quantity: Decimal = Field(gt=0)
    minimum_price: Decimal = Field(gt=0)
    maximum_price: Decimal = Field(gt=0)
    expires_at: datetime
    max_evidence_age: timedelta

    @model_validator(mode="after")
    def _bounds_are_coherent(self) -> Self:
        if not self.allowed_venues or any(not venue for venue in self.allowed_venues):
            raise ValueError("policy must name at least one venue")
        if self.minimum_price > self.maximum_price:
            raise ValueError("minimum_price cannot exceed maximum_price")
        if self.expires_at.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")
        if self.max_evidence_age <= timedelta(0):
            raise ValueError("max_evidence_age must be positive")
        return self


class MarketEvidenceSnapshot(_Record):
    venue: str = Field(min_length=1)
    price: Decimal = Field(gt=0)
    observed_at: datetime
    provenance: Literal[Provenance.FACT_EXTERNAL, Provenance.FACT_SYNTHETIC]
    eligible: bool


class OperatorDecision(_Record):
    approved: bool
    decided_by: ActorRef
    provenance: Literal[Provenance.HUMAN_JUDGMENT]
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def _a_human_makes_the_human_decision(self) -> Self:
        if self.decided_by.actor_kind is not ActorKind.HUMAN:
            raise ValueError("an operator decision requires a HUMAN actor")
        return self


class GateDecision(_Record):
    disposition: Disposition
    policy: Recorded[ExecutionPolicy] | Absent
    reason: str = Field(min_length=1)
    provenance: Provenance


def evaluate_policy(
    policy: ExecutionPolicy,
    *,
    child_quantity: Decimal,
    evidence: MarketEvidenceSnapshot,
    processing_time: datetime,
    operator_decision: OperatorDecision | None = None,
) -> GateDecision:
    """Authorize policy data, never a hard-coded strategy branch."""
    failures = []
    if processing_time.tzinfo is None:
        failures.append("processing_time is not timezone-aware")
    elif processing_time > policy.expires_at:
        failures.append("execution policy expired")
    if processing_time - evidence.observed_at > policy.max_evidence_age:
        failures.append("market evidence is stale")
    if not evidence.eligible:
        failures.append("market evidence marks the venue ineligible")
    if evidence.venue not in policy.allowed_venues:
        failures.append("venue is outside the policy")

    outside_bounds = (
        child_quantity > policy.max_child_quantity
        or evidence.price < policy.minimum_price
        or evidence.price > policy.maximum_price
    )
    if outside_bounds and not (operator_decision and operator_decision.approved):
        failures.append("outside pre-authorized bounds without a CAOM-001 operator decision")

    if failures:
        reason = "; ".join(failures)
        return GateDecision(
            disposition=Disposition.HOLD,
            policy=Absent(kind=AbsenceKind.NOTHING_RECORDED, reason=reason),
            reason=reason,
            provenance=Provenance.POLICY_RESULT,
        )
    provenance = Provenance.HUMAN_JUDGMENT if outside_bounds else Provenance.POLICY_RESULT
    return GateDecision(
        disposition=Disposition.PASS,
        policy=Recorded[ExecutionPolicy](value=policy),
        reason=(
            operator_decision.reason
            if outside_bounds and operator_decision is not None
            else "evidence and child order are within the named policy bounds"
        ),
        provenance=provenance,
    )


class ExecutionReport(_Record):
    execution_id: str = Field(min_length=1)
    order_id: OrderId
    intent_id: IntentId
    venue: str = Field(min_length=1)
    quantity: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    outcome: Literal["FILL", "PARTIAL_FILL", "LATE_FILL", "OVERFILL"]


class ExecutionApplication(_Record):
    journal: LifecycleRegister
    disposition: Disposition
    reason: str = Field(min_length=1)


def apply_execution(
    register: LifecycleRegister,
    execution: ExecutionEvent,
    report: ExecutionReport,
    *,
    actor: ActorRef,
    event: EventInput,
) -> ExecutionApplication:
    """Receive a venue fact and refuse an overfill without absorbing it."""
    if execution.lifecycle_id != register.lifecycle_id:
        return ExecutionApplication(
            journal=register,
            disposition=Disposition.HOLD,
            reason="execution lifecycle does not match the register",
        )
    if execution.payload_digest != digest(report):
        return ExecutionApplication(
            journal=register,
            disposition=Disposition.HOLD,
            reason="execution payload digest mismatch",
        )
    snapshot = replay(register.events)
    order = snapshot.orders[report.order_id]
    remaining = order.quantity - order.executed_quantity
    if report.quantity > remaining:
        return ExecutionApplication(
            journal=register,
            disposition=Disposition.HOLD,
            reason=f"execution quantity {report.quantity} exceeds remaining quantity {remaining}",
        )
    target = (
        LifecycleState.EXECUTED
        if report.quantity == remaining
        else LifecycleState.PARTIALLY_EXECUTED
    )
    updated = transition(
        register,
        order_id=report.order_id,
        target=target,
        reason=f"venue execution {report.execution_id} recorded",
        actor=actor,
        event=event,
        quantity=report.quantity,
    )
    return ExecutionApplication(
        journal=updated,
        disposition=Disposition.PASS,
        reason="execution recorded without exceeding remaining quantity",
    )
