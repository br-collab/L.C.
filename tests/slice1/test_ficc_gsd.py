"""R15: real Atreides FICC bytes consumed and published by L.C."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from atreides.clearing.access import (
    AccessArrangement,
    AccessDetermination,
    AccessPath,
    AccessStatus,
    AuthorityEvidence,
    NovationEvidence,
    PartyRef,
    PartyRole,
)
from atreides.clearing.ficc import (
    ClearingAgency,
    ClearingStatus,
    FiccGsdNettingRequest,
    NovatedObligation,
    produce_ficc_gsd_result,
)
from cannae_kernel.canonical import digest, digest_bytes
from cannae_kernel.delivery import ClearingMethod
from cannae_kernel.ids import LifecycleId, ScenarioId

from lc.clearing import AccessModel, consume_and_record_ficc
from lc.ficc import (
    FiccEvidenceStage,
    FiccFactStatus,
    FiccLifecycleDocument,
    consume_ficc_gsd_result,
    ficc_lifecycle_publication,
    ficc_lifecycle_publication_bytes,
)

AT = datetime(2026, 10, 21, 21, 0, tzinfo=UTC)
SCENARIO_ID = ScenarioId("scn_00000000000000000000000015")
LIFECYCLE_ID = LifecycleId("lif_03J0YY19FSPXWRSJ8ES4ZMMR15")
DIGEST = "sha256:" + "a" * 64


def _party(party_id: str, role: PartyRole) -> PartyRef:
    return PartyRef(party_id=party_id, role=role)


def _authority(party_id: str) -> AuthorityEvidence:
    return AuthorityEvidence(
        evidence_id=f"authority-evidence-{party_id}",
        principal_party_id=party_id,
        authority_id=f"authority-{party_id}",
        source="synthetic R15 membership evidence",
        digest=DIGEST,
    )


def _real_producer_bytes() -> tuple[bytes, bytes]:
    member = _party("member-a", PartyRole.DIRECT_NETTING_MEMBER)
    arrangement = AccessArrangement(
        path=AccessPath.FICC_DIRECT,
        acting_party=member,
        clearing_member=member,
        ficc=_party("FICC-GSD", PartyRole.CENTRAL_COUNTERPARTY),
        authority_evidence=(_authority(member.party_id),),
        novation_evidence=NovationEvidence(
            event_name="synthetic rule-defined novation",
            rule_reference="caller-supplied effective GSD Rulebook provision",
            evidence_id="arrangement-novation-1",
        ),
        rule_set_version="synthetic-r15-rule-set",
        processing_calendar_id="synthetic-r15-calendar",
    )
    result = produce_ficc_gsd_result(
        FiccGsdNettingRequest(
            clearing_agency=ClearingAgency.FICC_GSD,
            access=AccessDetermination(status=AccessStatus.ESTABLISHED, arrangement=arrangement),
            obligations=(
                NovatedObligation(
                    obligation_id="obligation-1",
                    trade_ids=("trade-1",),
                    member_id="member-a",
                    cusip="91282CJL6",
                    settlement_date=date(2026, 10, 22),
                    signed_quantity=Decimal("100"),
                    novation_evidence_id="novation-1",
                ),
                NovatedObligation(
                    obligation_id="obligation-2",
                    trade_ids=("trade-2",),
                    member_id="member-a",
                    cusip="91282CJL6",
                    settlement_date=date(2026, 10, 22),
                    signed_quantity=Decimal("-35"),
                    novation_evidence_id="novation-2",
                ),
            ),
            clearing_acceptance_evidence_id="acceptance-1",
            netting_evidence_id="netting-1",
            settlement_instruction_evidence_id="instruction-1",
            settlement_finality_evidence_id="finality-1",
        )
    )
    assert result.status is ClearingStatus.ESTABLISHED
    assert result.header is not None and result.payload is not None
    return result.header.model_dump_json().encode(), result.payload


def test_real_atreides_bytes_feed_lc_without_translation() -> None:
    header_bytes, payload = _real_producer_bytes()
    document = consume_ficc_gsd_result(header_bytes, payload)

    assert document.clearing_method == "FICC_GSD_NOVATED_NETTED"
    assert document.access_path == "FICC_DIRECT"
    assert document.net_settlement_obligations[0].net_quantity == Decimal("65")
    assert document.net_settlement_obligations[0].source_trade_ids == ("trade-1", "trade-2")

    transformation, recorded = consume_and_record_ficc(
        lifecycle_id=LIFECYCLE_ID,
        header_bytes=header_bytes,
        payload=payload,
    )
    assert recorded.method is ClearingMethod.NOVATED_NETTED
    assert recorded.method_label == "FICC_GSD_NOVATED_NETTED"
    assert recorded.access_model is AccessModel.FICC_DIRECT
    assert transformation.input_digests == (digest_bytes(payload),)
    assert transformation.output_digest == digest(recorded)


def test_receiver_recomputes_the_digest_before_parsing() -> None:
    header_bytes, payload = _real_producer_bytes()
    with pytest.raises(ValueError, match="attested digest"):
        consume_ficc_gsd_result(header_bytes, payload + b" ")


def test_five_facts_remain_separate_and_the_method_is_visible() -> None:
    header_bytes, payload = _real_producer_bytes()
    document = consume_ficc_gsd_result(header_bytes, payload)
    publication = ficc_lifecycle_publication(
        document=document,
        source_payload_digest=digest_bytes(payload),
        scenario_id=SCENARIO_ID,
        lifecycle_id=LIFECYCLE_ID,
        taken_at=AT,
    )
    restored = FiccLifecycleDocument.model_validate_json(
        ficc_lifecycle_publication_bytes(publication), strict=True
    )
    assert restored.clearing_method == "FICC_GSD_NOVATED_NETTED"
    assert tuple(fact.stage for fact in restored.facts) == tuple(FiccEvidenceStage)
    assert all(fact.status is FiccFactStatus.RECORDED for fact in restored.facts)
    assert len({fact.evidence_id for fact in restored.facts}) == 5


def test_ci_publication_writes_a_person_reachable_ficc_lifecycle() -> None:
    destination = os.environ.get("LIFECYCLE_PUBLICATION_DIR")
    if destination is None:
        return
    header_bytes, payload = _real_producer_bytes()
    document = consume_ficc_gsd_result(header_bytes, payload)
    publication = ficc_lifecycle_publication(
        document=document,
        source_payload_digest=digest_bytes(payload),
        scenario_id=SCENARIO_ID,
        lifecycle_id=LIFECYCLE_ID,
        taken_at=AT,
    )
    path = Path(destination)
    path.mkdir(parents=True, exist_ok=True)
    (path / "ficc-netted.json").write_bytes(ficc_lifecycle_publication_bytes(publication) + b"\n")
