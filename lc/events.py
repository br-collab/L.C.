"""Protocol-neutral event vocabulary for the L.C. order lifecycle.

The envelope is the kernel's hash-chained event type. L.C. owns only the order
payload and its lifecycle vocabulary; venue executions remain the frozen kernel
contract and are constructed only by an independent producer.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self

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
    "OrderTerms",
    "OrderType",
    "TimeInForce",
    "seal_order_event",
]


class LifecycleState(StrEnum):
    """Recorded states from intent acceptance through the Atreides handoff."""

    INTENT_ACCEPTED = "INTENT_ACCEPTED"
    REFUSED = "REFUSED"
    STRATEGY_AUTHORIZED = "STRATEGY_AUTHORIZED"
    PARENT_ACTIVE = "PARENT_ACTIVE"
    MARKET_ACCESS_PASSED = "MARKET_ACCESS_PASSED"
    MARKET_ACCESS_REFUSED = "MARKET_ACCESS_REFUSED"
    CANCELLED = "CANCELLED"
    REPLACED = "REPLACED"
    EXPIRED = "EXPIRED"
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


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class TimeInForce(StrEnum):
    DAY = "DAY"
    GOOD_TILL_CANCELLED = "GOOD_TILL_CANCELLED"
    IMMEDIATE_OR_CANCEL = "IMMEDIATE_OR_CANCEL"
    FILL_OR_KILL = "FILL_OR_KILL"


class OrderTerms(BaseModel):
    """Terms sealed into the order event that every control evaluates."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    account_id: str = Field(min_length=1)
    strategy_id: str = Field(min_length=1)
    issue: str = Field(min_length=1)
    side: Literal["BUY", "SELL"]
    order_type: OrderType
    time_in_force: TimeInForce
    control_price: Decimal = Field(gt=0)
    limit_price: Decimal | None = Field(default=None, gt=0)
    stop_price: Decimal | None = Field(default=None, gt=0)
    fractional_enabled: bool = False
    allocation_plan_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    approved_intent_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    quantity_basis: Literal["QUANTITY", "NOTIONAL"] = "QUANTITY"
    approved_notional: Decimal | None = Field(default=None, gt=0)
    policy_expires_at: datetime | None = None
    minimum_price_increment: Decimal = Field(default=Decimal("0.01"), gt=0)
    session_activity_permitted: bool = True

    @model_validator(mode="after")
    def _prices_match_order_type(self) -> Self:
        needs_limit = self.order_type in {OrderType.LIMIT, OrderType.STOP_LIMIT}
        needs_stop = self.order_type in {OrderType.STOP, OrderType.STOP_LIMIT}
        if needs_limit != (self.limit_price is not None):
            raise ValueError("limit price must be present exactly for limit and stop-limit orders")
        if needs_stop != (self.stop_price is not None):
            raise ValueError("stop price must be present exactly for stop and stop-limit orders")
        if self.limit_price is not None and self.control_price != self.limit_price:
            raise ValueError("limit orders must use their sealed limit price as control price")
        if (self.quantity_basis == "NOTIONAL") is not (self.approved_notional is not None):
            raise ValueError("notional-denominated orders require one approved notional")
        if self.policy_expires_at is not None and self.policy_expires_at.tzinfo is None:
            raise ValueError("policy_expires_at must be timezone-aware")
        for price in (self.control_price, self.limit_price, self.stop_price):
            if price is not None and price >= 1 and price % self.minimum_price_increment:
                raise ValueError("PRICE_INCREMENT_INVALID: 17 CFR 242.612")
        return self


class OrderEventPayload(BaseModel):
    """The domain payload sealed inside a kernel event envelope."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    order_id: OrderId
    parent_order_id: OrderId | None
    intent_id: IntentId
    state: LifecycleState
    reason: str = Field(min_length=1)
    quantity: Recorded[Decimal] | Absent
    consideration: Decimal = Field(default=Decimal(0), ge=0)
    terms: OrderTerms | None = None

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
