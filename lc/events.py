"""Protocol-neutral event vocabulary for the L.C. order lifecycle.

The envelope is the kernel's hash-chained event type. L.C. owns only the order
payload and its lifecycle vocabulary; venue executions remain the frozen kernel
contract and are constructed only by an independent producer.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Self

from cannae_kernel.absence import Absent, Recorded
from cannae_kernel.actor import ActorRef
from cannae_kernel.clocks import EventTimes
from cannae_kernel.domains import Domain
from cannae_kernel.events import EventEnvelope, seal
from cannae_kernel.ids import EventId, IntentId, LifecycleId, OrderId
from cannae_kernel.provenance import Provenance
from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "LifecycleState",
    "OrderEvent",
    "OrderEventPayload",
    "seal_order_event",
]


class LifecycleState(StrEnum):
    """Recorded states from intent acceptance through the Atreides handoff."""

    INTENT_ACCEPTED = "INTENT_ACCEPTED"
    REFUSED = "REFUSED"
    STRATEGY_AUTHORIZED = "STRATEGY_AUTHORIZED"
    PARENT_ACTIVE = "PARENT_ACTIVE"
    PARTIALLY_EXECUTED = "PARTIALLY_EXECUTED"
    EXECUTED = "EXECUTED"
    TRADE_CAPTURED = "TRADE_CAPTURED"
    ALLOCATED = "ALLOCATED"
    MATCHED = "MATCHED"
    AFFIRMED = "AFFIRMED"
    EXCEPTION_OPEN = "EXCEPTION_OPEN"
    CLEARING_TRANSFORMED = "CLEARING_TRANSFORMED"
    OBLIGATION_READY = "OBLIGATION_READY"
    SETTLEMENT_CANDIDATE_READY = "SETTLEMENT_CANDIDATE_READY"
    HANDED_TO_ATREIDES = "HANDED_TO_ATREIDES"
    REFUSED_BY_ATREIDES = "REFUSED_BY_ATREIDES"
    NOT_REACHED = "NOT_REACHED"


class OrderEventPayload(BaseModel):
    """The domain payload sealed inside a kernel event envelope."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    order_id: OrderId
    parent_order_id: OrderId | None
    intent_id: IntentId
    state: LifecycleState
    reason: str = Field(min_length=1)
    quantity: Recorded[Decimal] | Absent

    @model_validator(mode="after")
    def _child_names_parent(self) -> Self:
        if self.parent_order_id == self.order_id:
            raise ValueError("an order cannot be its own parent")
        return self


class OrderEvent(EventEnvelope[OrderEventPayload]):
    """An L.C.-produced, canonical, hash-chained order lifecycle event."""

    @model_validator(mode="after")
    def _produced_by_lc_without_authority(self) -> Self:
        if self.producer_domain is not Domain.LC:
            raise ValueError("OrderEvent producer_domain must be LC")
        if self.provenance is not Provenance.POLICY_RESULT:
            raise ValueError("OrderEvent provenance must be POLICY_RESULT")
        return self


def seal_order_event(  # noqa: PLR0913 - the frozen envelope requires each field explicitly
    *,
    event_id: EventId,
    lifecycle_id: LifecycleId,
    parent_ids: tuple[EventId, ...],
    rule_version: str,
    times: EventTimes,
    actor: ActorRef,
    idempotency_key: str,
    payload: OrderEventPayload,
    prior_event_digest: str | None,
) -> OrderEvent:
    """Seal an L.C. order event without reading a clock or entropy source."""
    envelope = seal(
        event_id=event_id,
        lifecycle_id=lifecycle_id,
        parent_ids=parent_ids,
        producer_domain=Domain.LC,
        event_type=f"lc.order.{payload.state.value.lower()}",
        schema_version="lc.order-event/1.0",
        rule_version=rule_version,
        times=times,
        provenance=Provenance.POLICY_RESULT,
        actor=actor,
        idempotency_key=idempotency_key,
        payload=payload,
        prior_event_digest=prior_event_digest,
    )
    return OrderEvent.model_validate(envelope.model_dump(mode="python"))
