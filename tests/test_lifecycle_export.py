"""PS-3D Part 7 (gate G3 finding H3): L.C. writes the lifecycle export surveillance reads.

Every row is an executed order event joined to exactly one venue report, with the venue
event's provenance. Anything that cannot be joined is refused by name.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cannae_kernel.absence import Recorded
from cannae_kernel.disposition import Disposition
from cannae_kernel.envelopes import ExecutionEvent
from cannae_kernel.ids import EventId
from test_lifecycle import NOW, _actor, _id
from test_policy_and_venue import _child_register, _venue_order

from emulators.venue import VenueEmulator, VenueOutcome
from lc.lifecycle import EventInput, LifecycleRegister
from lc.lifecycle_export import LIFECYCLE_EXPORT_SCHEMA, export_executions, write_lifecycle_export
from lc.policy import ExecutionReport, apply_execution

CONTROL = "lifecycle export join control"


def _venue(seed: int) -> tuple[ExecutionEvent, ExecutionReport]:
    venue = VenueEmulator(seed=seed).execute(
        _venue_order(), outcome=VenueOutcome.FILL, event_time=NOW
    )
    assert isinstance(venue.event, Recorded) and isinstance(venue.report, Recorded)
    report = ExecutionReport.model_validate(venue.report.value.model_dump(mode="python"))
    return venue.event.value, report


def _executed() -> tuple[LifecycleRegister, ExecutionEvent, ExecutionReport]:
    venue_event, report = _venue(8)
    applied = apply_execution(
        _child_register(),
        venue_event,
        report,
        actor=_actor(),
        event=EventInput(
            event_id=EventId(_id("evt_", 21)), times=venue_event.times, idempotency_key="x-21"
        ),
    )
    assert applied.disposition is Disposition.PASS
    return applied.journal, venue_event, report


def test_the_export_is_the_registers_fills_with_the_venues_provenance(tmp_path: Path) -> None:
    register, venue_event, report = _executed()
    path = write_lifecycle_export(register, ((venue_event, report),), tmp_path / "export.json")
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["schema"] == LIFECYCLE_EXPORT_SCHEMA
    (row,) = document["executions"]
    assert row["order_id"] == str(report.order_id)
    assert row["execution_id"] == report.execution_id
    assert row["quantity"] == str(report.quantity) and row["price"] == str(report.price)
    assert row["provenance"] == venue_event.provenance.value
    assert row["symbol"] and row["side"] in {"BUY", "SELL"} and row["account_id"]
    for absent in ("close_price", "session_volume_percent", "beneficial_owner_id"):
        assert absent not in row, "not an L.C. fact"


def test_a_tampered_venue_report_is_refused() -> None:
    register, venue_event, report = _executed()
    tampered = report.model_copy(update={"price": report.price + 1})
    with pytest.raises(ValueError, match="LIFECYCLE_EXPORT_DIGEST_MISMATCH"):
        export_executions(register, ((venue_event, tampered),))


def test_a_fill_without_its_venue_report_is_refused() -> None:
    register, _, _ = _executed()
    with pytest.raises(ValueError, match="LIFECYCLE_EXPORT_UNREPORTED_FILL"):
        export_executions(register, ())


def test_a_venue_report_the_register_never_recorded_is_refused() -> None:
    register, venue_event, report = _executed()
    other_event, other_report = _venue(9)
    assert other_report.execution_id != report.execution_id
    with pytest.raises(ValueError, match="LIFECYCLE_EXPORT_UNRECORDED_EXECUTION"):
        export_executions(register, ((venue_event, report), (other_event, other_report)))
