"""Evidence that a kernel execution event was received.

Receiving an execution records the event. It does not allocate, capture, or
move any later lifecycle stage. The event identity and the provenance are the
kernel fields on the event, not copies invented here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from cannae_kernel.disposition import Disposition
from cannae_kernel.envelopes import ExecutionEvent
from pydantic import BaseModel, ConfigDict, Field, ValidationError

__all__ = [
    "ExecutionReceipt",
    "ReceivedExecution",
    "read_execution",
    "receive_execution",
]


class ReceivedExecution(BaseModel):
    """A received execution. The kernel event is the evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event: ExecutionEvent
    disposition: Literal[Disposition.PASS] = Disposition.PASS
    reason: str = Field(
        default="an execution event was received, and no later stage was inferred",
        min_length=1,
    )


class ExecutionReceipt(BaseModel):
    """A failed read. There is no event, and the grade is not a pass."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    disposition: Literal[Disposition.INDETERMINATE] = Disposition.INDETERMINATE
    reason: str = Field(min_length=1)


def receive_execution(event: ExecutionEvent) -> ReceivedExecution:
    """Record one already validated execution event.

    The caller passes the kernel type. This function does not allocate.
    """
    return ReceivedExecution(event=event)


def read_execution(payload: object) -> ReceivedExecution | ExecutionReceipt:
    """Read one execution event, or return INDETERMINATE when it is not one."""
    try:
        if isinstance(payload, ExecutionEvent):
            event = payload
        elif isinstance(payload, str | bytes):
            event = ExecutionEvent.model_validate_json(payload)
        elif isinstance(payload, Mapping):
            event = ExecutionEvent.model_validate(dict(payload))
        else:
            return ExecutionReceipt(reason="the payload is not a readable execution event")
    except ValidationError:
        return ExecutionReceipt(reason="the payload is not a readable execution event")
    return receive_execution(event)
