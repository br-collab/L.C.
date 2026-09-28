"""Read-only publication of the newest clock in an L.C. lifecycle register."""

from __future__ import annotations

from typing import Literal

from cannae_kernel.clocks import EventTimes
from cannae_kernel.ids import EventId, LifecycleId
from cannae_kernel.provenance import Provenance
from pydantic import BaseModel, ConfigDict, Field

from lc.events import LifecycleState
from lc.lifecycle import LifecycleRegister

__all__ = ["LAYER_CLOCK_SCHEMA_VERSION", "LayerClockDocument", "publish_layer_clock"]

LAYER_CLOCK_SCHEMA_VERSION = 1


class LayerClockDocument(BaseModel):
    """The latest recorded L.C. event, projected without changing the register."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    layer: Literal["LC"] = "LC"
    lifecycle_id: LifecycleId
    event_id: EventId
    state: LifecycleState
    times: EventTimes
    provenance: Provenance
    event_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


def publish_layer_clock(register: LifecycleRegister) -> LayerClockDocument:
    """Project the register's newest event; refuse to make a clock from no event."""
    if not register.events:
        raise ValueError("cannot publish the L.C. layer clock from an empty register")
    event = register.events[-1]
    return LayerClockDocument(
        lifecycle_id=event.lifecycle_id,
        event_id=event.event_id,
        state=event.payload.state,
        times=event.times,
        provenance=event.provenance,
        event_digest=event.envelope_digest,
    )
