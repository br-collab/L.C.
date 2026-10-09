"""A received execution is the kernel event, and it does not allocate."""

from __future__ import annotations

from datetime import UTC, date, datetime

from cannae_kernel.clocks import EventTimes
from cannae_kernel.disposition import Disposition
from cannae_kernel.envelopes import ExecutionEvent
from cannae_kernel.ids import EventId, IntentId, LifecycleId
from cannae_kernel.provenance import Provenance
from cannae_kernel.session import BusinessDate, MarketSession, SessionContext

from lc.events import LifecycleState
from lc.execution_received import ReceivedExecution, read_execution, receive_execution

AT = datetime(2026, 9, 21, 14, 30, tzinfo=UTC)
DIGEST = "sha256:" + "ab" * 32
_FORBIDDEN = ("compliant", "approved", "cleared", "certified")


def _event(**overrides: object) -> ExecutionEvent:
    fields: dict[str, object] = {
        "event_id": EventId("evt_01M2P20SY00000000000000001"),
        "lifecycle_id": LifecycleId("lif_01M2P20SY00000000000000001"),
        "intent_id": IntentId("int_01M2P20SY00000000000000001"),
        "intent_digest": DIGEST,
        "times": EventTimes(event_time=AT, observation_time=AT, processing_time=AT),
        "session": SessionContext(
            session=MarketSession.REGULAR,
            business_date=BusinessDate(
                value=date(2026, 9, 21),
                calendar="XNYS",
                established_by="the session calendar",
            ),
        ),
        "provenance": Provenance.FACT_SYNTHETIC,
        "payload_digest": "sha256:" + "cd" * 32,
    }
    fields.update(overrides)
    return ExecutionEvent.model_validate(fields)


def _no_claim(reason: str) -> None:
    folded = reason.casefold()
    for word in _FORBIDDEN:
        assert word not in folded


def test_a_received_execution_keeps_the_kernel_event_and_does_not_allocate() -> None:
    event = _event()
    received = receive_execution(event)
    assert isinstance(received.event, ExecutionEvent)
    assert received.event.event_id == event.event_id
    assert received.event.provenance is Provenance.FACT_SYNTHETIC
    assert received.event is event
    assert received.disposition is Disposition.PASS
    assert not hasattr(received, "allocation")
    assert LifecycleState.ALLOCATED.value not in received.reason
    _no_claim(received.reason)


def test_an_external_fact_is_preserved_and_a_forecast_is_not_an_execution() -> None:
    payload = _event(provenance=Provenance.FACT_EXTERNAL).model_dump(mode="python")
    external = read_execution(payload)
    assert isinstance(external, ReceivedExecution)
    assert external.event.provenance is Provenance.FACT_EXTERNAL
    forecast = _event().model_dump(mode="json")
    forecast["provenance"] = "FORECAST"
    unread = read_execution(forecast)
    assert unread.disposition is Disposition.INDETERMINATE
    assert not hasattr(unread, "event")
    _no_claim(unread.reason)


def test_an_unreadable_payload_is_indeterminate() -> None:
    for payload in (None, 12, "{", {"event_id": "not-an-event"}):
        unread = read_execution(payload)
        assert unread.disposition is Disposition.INDETERMINATE
        _no_claim(unread.reason)
