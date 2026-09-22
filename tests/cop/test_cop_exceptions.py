"""COP-2 panels 13-14: exception register, health, filters and drawer."""

from __future__ import annotations

from datetime import timedelta

from cannae_kernel.disposition import Disposition
from cop_fakes import Rig, login, section

from cop.demo import DemoExceptions
from cop.exceptions import ExceptionStatus, age, health


def test_production_register_is_absent_not_an_empty_zero() -> None:
    rig = Rig()
    rig.refresher.refresh_once()
    client = rig.app_client()
    login(client)
    body = section(client.get("/section/exceptions").get_data(as_text=True), "exceptions")
    assert "Absent in production" in body
    assert "No published aggregate" in body
    assert "Exception health" not in body


def test_demo_register_has_all_kinds_and_missing_owner_forces_block() -> None:
    rig = Rig(exceptions_configured=True)
    register = DemoExceptions(rig.clock).register()
    assert {record.kind.value for record in register.records} == {
        "Break",
        "Escalation",
        "Hold",
        "Override",
    }
    ownerless = next(record for record in register.records if record.owner is None)
    assert ownerless.disposition is Disposition.HOLD
    assert ownerless.effective_disposition is Disposition.BLOCK


def test_sla_age_uses_first_layer_event_time_and_writeoffs_are_separate() -> None:
    rig = Rig(exceptions_configured=True)
    register = DemoExceptions(rig.clock).register()
    first = register.records[0]
    assert age(first, rig.clock()) == timedelta(minutes=190)
    summary = health(register, rig.clock())
    assert (summary.under_1h, summary.one_to_4h, summary.four_to_24h, summary.over_24h) == (
        3,
        1,
        1,
        2,
    )
    assert summary.written_off == 1
    assert not any(
        record.status is ExceptionStatus.RESOLVED
        for record in register.records
        if record.written_off
    )
    assert summary.trend == (9, 8, 8, 7)


def test_filters_and_read_only_detail_drawer_render() -> None:
    rig = Rig(exceptions_configured=True)
    rig.refresher.refresh_once()
    client = rig.app_client()
    login(client)

    no_owner = client.get("/section/exceptions?filter=no-owner").get_data(as_text=True)
    assert "E-104" in no_owner
    assert "E-101 · Break record mismatch" not in no_owner
    assert "BLOCK" in no_owner and "No owner" in no_owner

    drawer = client.get("/section/exceptions?selected=E-101").get_data(as_text=True)
    assert "Trail across layers" in drawer
    assert "What closes it" in drawer
    assert "Open where authority lives" in drawer
    assert "Read-only" in drawer
    assert "Assign owner" not in drawer


def test_written_off_keeps_its_disposition_and_status_separate() -> None:
    rig = Rig(exceptions_configured=True)
    rig.refresher.refresh_once()
    client = rig.app_client()
    login(client)
    body = client.get("/section/exceptions?selected=E-099").get_data(as_text=True)
    assert "PASS" in body
    assert "Written off" in body
    assert "written off · separate" in body
