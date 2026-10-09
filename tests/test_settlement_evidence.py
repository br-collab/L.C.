"""The settlement picture reads producer bytes and does not invent a cleaner state."""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from cannae_kernel.disposition import Disposition
from cannae_kernel.finality import FinalityAssertion, FinalityType

from cop.settlement_evidence import read_settlement_evidence, render_settlement_evidence

_FORBIDDEN = ("compliant", "approved", "cleared", "certified", "delivered", "acknowledged")


def test_unreadable_or_incomplete_bytes_are_not_a_clean_state() -> None:
    unread = read_settlement_evidence(b"not json")
    incomplete = read_settlement_evidence(
        b'{"stage":"FINAL","disposition":"PASS","reason":"joined","leg":"cash"}'
    )
    contradictory = read_settlement_evidence(
        json.dumps(
            {
                "stage": "SETTLED",
                "disposition": "PASS",
                "reason": "the venue said settled",
                "leg": "cash",
                "artifact_digest": "sha256:" + "ab" * 32,
                "receipt_disposition": "PASS",
                "venue_state": "SETTLED",
                "venue_disposition": "HOLD",
                "assertion": None,
            }
        ).encode()
    )
    for reading in (unread, incomplete, contradictory):
        assert reading.stage == "UNREAD"
        assert reading.disposition is Disposition.INDETERMINATE
        assert reading.assertion is None
        text = render_settlement_evidence(reading)
        assert "PASS" not in text
        assert "CASH_FINAL" not in text
        assert "ASSET_FINAL" not in text
        for word in _FORBIDDEN:
            assert word not in text.casefold()


def test_a_hold_is_shown_as_a_hold() -> None:
    raw = json.dumps(
        {
            "stage": "SETTLED",
            "disposition": "HOLD",
            "reason": "the venue said settled, and finality was not published",
            "leg": "asset",
            "artifact_digest": "sha256:" + "ab" * 32,
            "receipt_disposition": "PASS",
            "venue_state": "SETTLED",
            "venue_disposition": "HOLD",
            "assertion": None,
        }
    ).encode()
    reading = read_settlement_evidence(raw)
    text = render_settlement_evidence(reading)
    assert reading.stage == "SETTLED"
    assert reading.disposition is Disposition.HOLD
    assert reading.assertion is None
    assert "HOLD" in text
    assert "no kernel finality assertion was published" in text
    assert "ASSET_FINAL" not in text
    for word in _FORBIDDEN:
        assert word not in text.casefold()


def test_real_join_bytes_are_read_without_translation() -> None:
    if importlib.util.find_spec("atreides") is None:
        if os.environ.get("HANDOFF_EXTRA_REQUIRED") == "1":
            pytest.fail("the settlement join boundary is required but Atreides is not installed")
        pytest.skip(
            "Atreides is not installed, so the real settlement-join bytes are not exercised "
            "here. A skip is not a pass. The pinned handoff job exercises this seam."
        )
    raw = _real_join_bytes()
    payload = json.loads(raw)
    reading = read_settlement_evidence(raw)
    assert reading.stage == payload["stage"] == "FINAL"
    assert reading.disposition is Disposition.PASS
    assert reading.disposition.value == payload["disposition"]
    assert reading.reason == payload["reason"]
    assert reading.leg == payload["leg"] == "cash"
    assert reading.artifact_digest == payload["artifact_digest"]
    assert reading.receipt_disposition is not None
    assert reading.receipt_disposition.value == payload["receipt_disposition"]
    assert reading.venue_state == payload["venue_state"]
    assert reading.venue_disposition is not None
    assert reading.venue_disposition.value == payload["venue_disposition"]
    assert reading.assertion is not None
    assert json.loads(reading.assertion.model_dump_json()) == payload["assertion"]
    assert set(payload["assertion"]) == set(FinalityAssertion.model_fields)
    assert reading.assertion.finality_type is FinalityType.CASH_FINAL
    assert reading.assertion.confidence is None
    text = render_settlement_evidence(reading)
    assert "CASH_FINAL" in text
    assert "FACT_SYNTHETIC" in text
    for word in _FORBIDDEN:
        assert word not in text.casefold()


def _real_join_bytes() -> bytes:
    """One execution of the real emitter, receipt, readback, and join."""
    canonical = importlib.import_module("atreides.messaging.canonical")
    emit = importlib.import_module("atreides.messaging.emit")
    readback = importlib.import_module("atreides.messaging.readback")
    receipt_mod = importlib.import_module("atreides.messaging.receipt")
    join = importlib.import_module("atreides.messaging.settlement_join")
    actor = importlib.import_module("cannae_kernel.actor")
    ids = importlib.import_module("cannae_kernel.ids")
    provenance = importlib.import_module("cannae_kernel.provenance")
    finality = importlib.import_module("cannae_kernel.finality")

    prepared_at = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    observed = prepared_at + timedelta(hours=1)
    member = canonical.FinancialInstitution(bicfi="AAAAUS33XXX")
    instruction = canonical.CashLegInstruction(
        message_id="MSG-001",
        end_to_end_id="E2E-001",
        created_at=prepared_at,
        amount=Decimal("1000000.00"),
        currency="USD",
        debtor=member,
        creditor=canonical.FinancialInstitution(bicfi="BBBBUS33XXX"),
        settlement_method=canonical.SettlementMethod.CLEARING_SYSTEM,
        sender=member,
        receiver=canonical.FinancialInstitution(bicfi="BBBBUS33XXX"),
    )
    artifact = emit.emit_instruction_artifact(instruction)
    digest = emit.instruction_artifact_digest(artifact)
    action = receipt_mod.ExternalActionReceipt.model_validate(
        {
            "entitled_member_id": "member-1",
            "event_id": ids.EventId("evt_" + "4" * 26),
            "action": receipt_mod.ExternalAction.SETTLEMENT_INSTRUCTION,
            "artifact_digest": digest,
            "observation_time": observed,
            "provenance": provenance.Provenance.FACT_SYNTHETIC,
            "message_id": instruction.message_id,
            "end_to_end_id": instruction.end_to_end_id,
        }
    )
    document = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.002.001.16">\n'
        b"  <FIToFIPmtStsRpt>\n"
        b"    <GrpHdr><MsgId>STS-9001</MsgId><CreDtTm>2026-08-14T13:00:00Z</CreDtTm></GrpHdr>\n"
        b"    <OrgnlGrpInfAndSts>\n"
        b"      <OrgnlMsgId>MSG-001</OrgnlMsgId>\n"
        b"      <OrgnlMsgNmId>pacs.009.001.13</OrgnlMsgNmId>\n"
        b"    </OrgnlGrpInfAndSts>\n"
        b"    <TxInfAndSts>\n"
        b"      <OrgnlEndToEndId>E2E-001</OrgnlEndToEndId>\n"
        b"      <OrgnlTxId>TX-001</OrgnlTxId>\n"
        b"      <TxSts>ACSC</TxSts>\n"
        b"    </TxInfAndSts>\n"
        b"  </FIToFIPmtStsRpt>\n"
        b"</Document>\n"
    )
    match = readback.ingest_readback(document, (instruction,))
    entry = match.report.entries[0]
    joined = join.join_settlement_evidence(
        artifact,
        instruction,
        (action,),
        match,
        entry,
        leg="cash",
        governing_rule_set="synthetic-readback/0.1",
        authoritative_actor=actor.ActorRef(
            actor_id=ids.ActorId("act_" + "6" * 26),
            actor_kind=actor.ActorKind.EXTERNAL_EMULATOR,
            role="synthetic-venue",
            entitlement_refs=(),
            authenticated=True,
        ),
        authoritative_event_id=ids.EventId("evt_" + "6" * 26),
        effective_time=prepared_at,
        observation_time=observed,
        evidence_reference="STS-9001",
        conditionality_status=finality.ConditionalityStatus.UNCONDITIONAL,
        revocability_status=finality.RevocabilityStatus.IRREVOCABLE,
        provenance=provenance.Provenance.FACT_SYNTHETIC,
    )
    raw = joined.model_dump_json().encode()
    if not isinstance(raw, bytes):
        raise AssertionError("the producer did not return bytes")
    return raw
