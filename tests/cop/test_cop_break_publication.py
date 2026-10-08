"""COP-GAP-1 WP-3 acceptance tests for exact Atreides break bytes."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cannae_kernel.actor import ActorKind, ActorRef
from cannae_kernel.canonical import canonical_bytes, digest_bytes
from cannae_kernel.disposition import Disposition
from cannae_kernel.ids import ActorId
from cop_fakes import Rig, login, section

from cop.breaks import (
    AtreidesBreakRecord,
    BreaksPublication,
    HttpxBreaksClient,
    parse_publication,
)
from cop.exceptions import PublishedBreakExceptionSource, age, publication_to_register
from cop.observation import SourceMalformedError

URL = "https://example.invalid/breaks.json"
EVENT_AT = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
TAKEN_AT = datetime(2026, 10, 8, 16, 0, tzinfo=UTC)


def _owner() -> dict[str, object]:
    owner = ActorRef(
        actor_id=ActorId("act_01M2P20SY00000000000000001"),
        actor_kind=ActorKind.HUMAN,
        role="settlement operations",
        entitlement_refs=("synthetic:cockpit",),
        authenticated=True,
    )
    return owner.model_dump(mode="json")


def _record(*, owner: bool) -> dict[str, object]:
    return {
        "schema_version": "atreides.break/0.1-experimental",
        "enforcement_status": "ADVISORY_ONLY",
        "experimental": True,
        "synthetic": True,
        "break_id": "BRK-OWNED" if owner else "BRK-UNOWNED",
        "operation_id": "00000000-0000-0000-0000-000000000111",
        "regime": "CCP",
        "leg": "FUNDING",
        "symptom": "synthetic funding mismatch",
        "sources": ["synthetic instruction", "synthetic readback"],
        "difference": "expected 10, actual 9",
        "originating_event_at": EVENT_AT.isoformat().replace("+00:00", "Z"),
        "originating_event_ref": "synthetic:readback:funding",
        "cause_class": "SYNTHETIC_DATA_MISMATCH",
        "owner": _owner() if owner else None,
        "owner_absence_reason": None if owner else "no owner recorded",
        "sla_target": (EVENT_AT + timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
        "actions": [],
        "resolution_evidence": None,
        "state": "INVESTIGATING" if owner else "OPEN",
        "dsor_record_id": "00000000-0000-0000-0000-000000000222",
    }


def _wire() -> bytes:
    records = [_record(owner=True), _record(owner=False)]
    models = tuple(
        AtreidesBreakRecord.model_validate_json(json.dumps(record)) for record in records
    )
    digest = digest_bytes(
        b"".join(
            model.canonical_bytes() for model in sorted(models, key=lambda item: item.break_id)
        )
    )
    return canonical_bytes(
        BreaksPublication(
            schema_version=1,
            synthetic=True,
            enforcement_status="ADVISORY_ONLY",
            claim_label="EXPERIMENTAL",
            complete=True,
            taken_at=TAKEN_AT,
            input_digest=digest,
            records=models,
        )
    )


def test_http_source_names_the_real_url_and_maps_exact_bytes() -> None:
    client = HttpxBreaksClient(
        URL,
        http=httpx.Client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=_wire()))
        ),
    )
    publication = client.publication()
    register = publication_to_register(publication)

    assert client.source_label == URL
    assert len(register.records) == 2
    assert register.synthetic is True


def test_missing_owner_is_block_and_age_uses_originating_time() -> None:
    register = publication_to_register(parse_publication(_wire()))
    unowned = next(record for record in register.records if record.owner is None)

    assert unowned.effective_disposition is Disposition.BLOCK
    assert unowned.status_text == "no owner recorded"
    assert age(unowned, EVENT_AT + timedelta(hours=3)) == timedelta(hours=3)
    assert unowned.sla_target == timedelta(hours=2)


def test_owned_break_remains_a_hold_not_a_clean_state() -> None:
    register = publication_to_register(parse_publication(_wire()))
    owned = next(record for record in register.records if record.owner is not None)

    assert owned.effective_disposition is Disposition.HOLD


def test_tampered_record_fails_closed() -> None:
    document = json.loads(_wire())
    document["records"][0]["difference"] = "tampered"

    with pytest.raises(SourceMalformedError, match="shape"):
        parse_publication(json.dumps(document).encode())


def test_missing_sla_target_fails_closed_instead_of_defaulting() -> None:
    document = json.loads(_wire())
    del document["records"][0]["sla_target"]

    with pytest.raises(SourceMalformedError, match="shape"):
        parse_publication(json.dumps(document).encode())


def test_published_source_drives_the_read_only_board_with_unowned_first() -> None:
    http = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=_wire()))
    )
    source = PublishedBreakExceptionSource(HttpxBreaksClient(URL, http=http))
    rig = Rig()
    rig.clock.now = datetime(2026, 10, 8, 17, 0, tzinfo=UTC)
    rig.refresher._exceptions = source
    snapshot = rig.refresher.refresh_once()

    client = rig.app_client()
    login(client)
    body = section(client.get("/section/exceptions").get_data(as_text=True), "exceptions")

    assert body.index("BRK-UNOWNED") < body.index("BRK-OWNED")
    assert "Absent — no owner recorded" in body
    assert "settlement operations" in body
    assert "SYNTHETIC, EXPERIMENTAL, ADVISORY_ONLY" in body
    assert "SYNTHETIC_DATA_MISMATCH" in body
    assert "Open" in body and "Investigating" in body
    assert "5 h" in body
    assert snapshot.exceptions.register.source_url == URL
    assert "Assign owner" not in body
