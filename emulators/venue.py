"""Deterministic independent venue emulator.

The emulator reports synthetic facts from its own state. It never imports L.C.
and never turns a no-fill outcome into an execution event.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from random import Random
from typing import Literal, cast

from cannae_kernel.absence import AbsenceKind, Absent, Recorded
from cannae_kernel.canonical import digest
from cannae_kernel.clocks import EventTimes
from cannae_kernel.envelopes import ExecutionEvent
from cannae_kernel.ids import EventId, IntentId, LifecycleId, OrderId
from cannae_kernel.provenance import Provenance
from cannae_kernel.session import SessionContext
from pydantic import BaseModel, ConfigDict, Field

__all__ = ["VenueEmulator", "VenueOrder", "VenueOutcome", "VenueResult"]


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class VenueOutcome(StrEnum):
    FILL = "FILL"
    PARTIAL_FILL = "PARTIAL_FILL"
    NO_FILL = "NO_FILL"
    LATE_FILL = "LATE_FILL"
    OVERFILL = "OVERFILL"


class VenueOrder(_Record):
    lifecycle_id: LifecycleId
    order_id: OrderId
    intent_id: IntentId
    intent_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    quantity: Decimal = Field(gt=0)
    limit_price: Decimal = Field(gt=0)
    policy_expires_at: datetime
    session: SessionContext


class VenueExecutionReport(_Record):
    execution_id: str = Field(min_length=1)
    order_id: OrderId
    intent_id: IntentId
    venue: str = Field(min_length=1)
    quantity: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    outcome: Literal["FILL", "PARTIAL_FILL", "LATE_FILL", "OVERFILL"]


class VenueResult(_Record):
    event: Recorded[ExecutionEvent] | Absent
    report: Recorded[VenueExecutionReport] | Absent


def _kernel_id(prefix: str, seed: str) -> str:
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    raw = hashlib.sha256(seed.encode()).digest()
    value = int.from_bytes(raw[:16], "big") & ((1 << 128) - 1)
    chars = []
    for _ in range(26):
        value, index = divmod(value, 32)
        chars.append(alphabet[index])
    chars[-1] = alphabet[alphabet.index(chars[-1]) % 8]
    return prefix + "".join(reversed(chars))


class VenueEmulator:
    """One deterministic venue whose outcome is selected explicitly for testing."""

    def __init__(self, *, seed: int, venue: str = "VENUE-SYNTHETIC") -> None:
        self._seed = seed
        self._venue = venue

    def execute(
        self,
        order: VenueOrder,
        *,
        outcome: VenueOutcome,
        event_time: datetime,
    ) -> VenueResult:
        if outcome is VenueOutcome.NO_FILL:
            absent = Absent(
                kind=AbsenceKind.NOTHING_RECORDED,
                reason="venue reported no fill; no execution event exists",
            )
            return VenueResult(event=absent, report=absent)

        rng = Random(self._seed)
        if outcome is VenueOutcome.PARTIAL_FILL:
            quantity = order.quantity / Decimal(2)
        elif outcome is VenueOutcome.OVERFILL:
            quantity = order.quantity + Decimal(1)
        else:
            quantity = order.quantity
        observed_at = event_time
        if outcome is VenueOutcome.LATE_FILL and event_time <= order.policy_expires_at:
            observed_at = order.policy_expires_at
        processing_time = observed_at
        execution_id = f"synthetic-{self._seed}-{rng.randrange(1_000_000):06d}"
        report = VenueExecutionReport(
            execution_id=execution_id,
            order_id=order.order_id,
            intent_id=order.intent_id,
            venue=self._venue,
            quantity=quantity,
            price=order.limit_price,
            outcome=cast(Literal["FILL", "PARTIAL_FILL", "LATE_FILL", "OVERFILL"], outcome.value),
        )
        event = ExecutionEvent(
            event_id=EventId(_kernel_id("evt_", execution_id)),
            lifecycle_id=order.lifecycle_id,
            intent_id=order.intent_id,
            intent_digest=order.intent_digest,
            times=EventTimes(
                event_time=event_time,
                observation_time=observed_at,
                processing_time=processing_time,
                decision_time=None,
            ),
            session=order.session,
            provenance=Provenance.FACT_SYNTHETIC,
            payload_digest=digest(report),
        )
        return VenueResult(
            event=Recorded[ExecutionEvent](value=event),
            report=Recorded[VenueExecutionReport](value=report),
        )
