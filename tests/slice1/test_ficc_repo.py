"""R16 exact Atreides repo crossing and publication evidence."""

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
    FiccGsdNettingRequest,
    NovatedObligation,
    produce_ficc_gsd_result,
)
from atreides.clearing.repo import (
    RepoLeg,
    RepoResult,
    RepoService,
    RepoTerm,
    RepoTrade,
    dvp_legs_for_netting,
    produce_repo_result,
)
from cannae_kernel.ids import LifecycleId, ScenarioId

from lc.ficc import (
    consume_ficc_gsd_result,
    ficc_lifecycle_publication,
    ficc_lifecycle_publication_bytes,
)
from lc.repo import (
    consume_repo_result,
    repo_lifecycle_publication,
    repo_lifecycle_publication_bytes,
)

AT = datetime(2026, 10, 21, 23, 30, tzinfo=UTC)
SID = ScenarioId("scn_00000000000000000000000016")
LID = LifecycleId("lif_03J0YY19FSPXWRSJ8ES4ZMMR16")


def access() -> AccessDetermination:
    member = PartyRef(party_id="member-a", role=PartyRole.DIRECT_NETTING_MEMBER)
    arr = AccessArrangement(
        path=AccessPath.FICC_DIRECT,
        acting_party=member,
        clearing_member=member,
        ficc=PartyRef(party_id="FICC", role=PartyRole.CENTRAL_COUNTERPARTY),
        authority_evidence=(
            AuthorityEvidence(
                evidence_id="auth",
                principal_party_id="member-a",
                authority_id="a",
                source="synthetic",
                digest="sha256:" + "a" * 64,
            ),
        ),
        novation_evidence=NovationEvidence(
            event_name="comparison", rule_reference="synthetic", evidence_id="nov"
        ),
        rule_set_version="synthetic-r16",
        processing_calendar_id="synthetic",
    )
    return AccessDetermination(status=AccessStatus.ESTABLISHED, arrangement=arr)


def repo(service: RepoService) -> RepoResult:
    trade = RepoTrade(
        trade_id=f"repo-{service.value}",
        member_id="member-a",
        service=service,
        term=RepoTerm.OVERNIGHT,
        start_date=date(2026, 10, 22),
        close_date=date(2026, 10, 23),
        collateral_par=Decimal("100"),
        start_money=Decimal("99"),
        repo_rate_percent=Decimal("5"),
        haircut_percent=Decimal("1"),
        member_margin_approximation=Decimal("0.25"),
        cusip="91282CJL6" if service is RepoService.DVP else None,
        collateral_class="US_TREASURY_GENERAL" if service is RepoService.GCF else None,
        clearing_bank="synthetic-bank" if service is RepoService.GCF else None,
    )
    return produce_repo_result(trade=trade, access=access(), settled_legs=(RepoLeg.START,))


def test_real_producer_bytes_cross_without_translation_and_tamper_fails() -> None:
    # FIX-10 E requires the published rounding rule and each unrounded leg
    # amount to cross intact so the counterparty can check the rounded cash.
    result = repo(RepoService.DVP)
    assert result.payload_digest is not None and result.payload is not None
    doc = consume_repo_result(str(result.payload_digest), result.payload)
    assert (
        doc.observation_seat == "FICC_MEMBER"
        and doc.legs[0].leg == "START"
        and doc.legs[1].leg == "CLOSE"
    )
    assert doc.cash_rounding_convention == "ROUND_HALF_UP_TO_CENT"
    assert doc.legs[0].unrounded_signed_money == Decimal("-99")
    assert doc.legs[1].unrounded_signed_money == Decimal("99.01375")
    with pytest.raises(ValueError, match="attested digest"):
        consume_repo_result(str(result.payload_digest), result.payload + b" ")


def test_ci_publishes_dvp_gcf_and_mixed_cash_repo_documents() -> None:
    destination = os.environ.get("LIFECYCLE_PUBLICATION_DIR")
    if destination is None:
        return
    path = Path(destination)
    path.mkdir(parents=True, exist_ok=True)
    for service, name in (
        (RepoService.DVP, "ficc-repo-dvp.json"),
        (RepoService.GCF, "ficc-repo-gcf.json"),
    ):
        result = repo(service)
        assert result.payload_digest is not None and result.payload is not None
        repo_doc = consume_repo_result(str(result.payload_digest), result.payload)
        repo_pub = repo_lifecycle_publication(
            document=repo_doc,
            source_payload_digest=str(result.payload_digest),
            scenario_id=SID,
            lifecycle_id=LID,
            taken_at=AT,
        )
        assert repo_pub.cato_evidence is not None
        assert repo_pub.cato_evidence.source == "OFR Short-term Funding Monitor"
        assert repo_pub.cato_evidence.tool == "get_cleared_repo_segments"
        assert {series_id.split("_")[0] for series_id in repo_pub.cato_evidence.series_ids} == {
            "REPO-DVP",
            "REPO-GCF",
            "REPO-TRI",
        }
        (path / name).write_bytes(repo_lifecycle_publication_bytes(repo_pub) + b"\n")
    dvp = repo(RepoService.DVP)
    cash = NovatedObligation(
        obligation_id="cash",
        trade_ids=("cash-trade",),
        member_id="member-a",
        cusip="91282CJL6",
        settlement_date=date(2026, 10, 22),
        signed_quantity=Decimal("-40"),
        signed_settlement_amount=Decimal("39.5"),
        settlement_currency="USD",
        novation_evidence_id="cash-nov",
    )
    mixed = produce_ficc_gsd_result(
        FiccGsdNettingRequest(
            clearing_agency=ClearingAgency.FICC_GSD,
            access=access(),
            obligations=(cash, dvp_legs_for_netting(dvp)[0]),
            clearing_acceptance_evidence_id="accept",
            netting_evidence_id="net",
        )
    )
    assert mixed.header is not None and mixed.payload is not None
    ficc_doc = consume_ficc_gsd_result(mixed.header.model_dump_json().encode(), mixed.payload)
    ficc_pub = ficc_lifecycle_publication(
        document=ficc_doc,
        source_payload_digest=str(mixed.header.payload_digest),
        scenario_id=SID,
        lifecycle_id=LID,
        taken_at=AT,
    )
    (path / "ficc-cash-repo-netted.json").write_bytes(
        ficc_lifecycle_publication_bytes(ficc_pub) + b"\n"
    )
