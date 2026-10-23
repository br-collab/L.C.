"""Exact consumer and person-readable publication for Atreides FICC repo bytes."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from cannae_kernel.canonical import canonical_bytes, digest_bytes
from cannae_kernel.ids import LifecycleId, ScenarioId
from pydantic import BaseModel, ConfigDict, Field


class _Wire(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class RepoFact(_Wire):
    stage: Literal[
        "CLEARING_ACCEPTANCE",
        "NOVATION",
        "NETTING",
        "SETTLEMENT_INSTRUCTION",
        "SETTLEMENT_FINALITY",
    ]
    status: Literal["RECORDED", "NOT_RECORDED"]
    evidence_id: str | None = None
    reason: str | None = None


class RepoLeg(_Wire):
    obligation_id: str
    trade_id: str
    leg: Literal["START", "CLOSE"]
    settlement_date: date | None
    signed_par: Decimal
    signed_money: Decimal | None
    # FIX-10 E: "keep the unrounded amount on the leg" so the receiving
    # participant can reproduce the published cash rounding.
    unrounded_signed_money: Decimal | None
    currency: str
    cusip: str | None
    collateral_class: str | None
    settlement_path: Literal["DVP_NETTING", "GCF_TRIPARTY"]
    facts: tuple[RepoFact, ...]


class GcfAllocation(_Wire):
    trade_id: str
    collateral_class: str
    clearing_bank: str
    allocated_par: Decimal
    returned_par: Decimal | None


class RepoDocument(_Wire):
    schema_version: Literal["atreides.ficc_repo/1"]
    clearing_agency: Literal["FICC_GSD"]
    clearing_method: Literal["FICC_GSD_DVP_REPO", "FICC_GCF_REPO"]
    observation_seat: Literal["FICC_MEMBER"]
    observed_member_id: str
    access_path: Literal["FICC_DIRECT", "FICC_SPONSORED", "FICC_AGENT"]
    sponsored_execution: Literal["DONE_WITH", "DONE_AWAY"] | None
    repo_rate_percent: Decimal
    day_count_basis: int
    term: Literal["OVERNIGHT", "TERM", "OPEN"]
    haircut_percent: Decimal
    member_margin_approximation: Decimal
    member_margin_method: Literal["SYNTHETIC_APPROXIMATION_NOT_FICC_VAR"]
    # FIX-10 E: "record the rounding rule in the published document so a
    # counterparty can check it."
    cash_rounding_convention: Literal["ROUND_HALF_UP_TO_CENT"]
    legs: tuple[RepoLeg, ...]
    gcf_allocation: GcfAllocation | None


class CatoRepoEvidence(_Wire):
    """Stable citation to the CATO-D1 cleared-repo context, not a price observation."""

    source_repository: Literal["br-collab/Cato-FICC-MCP"] = "br-collab/Cato-FICC-MCP"
    source_commit: Literal["fa9d709384fe2d1a49f93e4ef24cb2b782037067"] = (
        "fa9d709384fe2d1a49f93e4ef24cb2b782037067"
    )
    tool: Literal["get_cleared_repo_segments"] = "get_cleared_repo_segments"
    source: Literal["OFR Short-term Funding Monitor"] = "OFR Short-term Funding Monitor"
    context_only: Literal[True] = True
    series_ids: tuple[str, ...] = (
        "REPO-DVP_AR_OO-P",
        "REPO-DVP_TV_OO-P",
        "REPO-DVP_AR_TOT-P",
        "REPO-DVP_TV_TOT-P",
        "REPO-GCF_AR_OO-P",
        "REPO-GCF_TV_OO-P",
        "REPO-GCF_AR_TOT-P",
        "REPO-GCF_TV_TOT-P",
        "REPO-TRI_AR_OO-P",
        "REPO-TRI_TV_OO-P",
        "REPO-TRI_AR_TOT-P",
        "REPO-TRI_TV_TOT-P",
    )


class RepoLifecycleDocument(_Wire):
    schema_version: Literal["harness_c2.ficc_repo_lifecycle/1"] = "harness_c2.ficc_repo_lifecycle/1"
    taken_at: datetime
    scenario_id: ScenarioId
    lifecycle_id: LifecycleId
    synthetic: Literal[True] = True
    source_payload_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    cato_evidence: CatoRepoEvidence | None = None
    repo: RepoDocument


def consume_repo_result(payload_digest: str, payload: bytes) -> RepoDocument:
    if digest_bytes(payload) != payload_digest:
        raise ValueError("FICC repo payload does not match its attested digest")
    return RepoDocument.model_validate_json(payload, strict=True)


def repo_lifecycle_publication(
    *,
    document: RepoDocument,
    source_payload_digest: str,
    scenario_id: ScenarioId,
    lifecycle_id: LifecycleId,
    taken_at: datetime,
) -> RepoLifecycleDocument:
    return RepoLifecycleDocument(
        taken_at=taken_at,
        scenario_id=scenario_id,
        lifecycle_id=lifecycle_id,
        source_payload_digest=source_payload_digest,
        cato_evidence=CatoRepoEvidence(),
        repo=document,
    )


def repo_lifecycle_publication_bytes(document: RepoLifecycleDocument) -> bytes:
    return canonical_bytes(document)
