"""Consume exact attested FICC GSD result bytes without importing Atreides."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self

from cannae_kernel.canonical import canonical_bytes, digest_bytes
from cannae_kernel.ids import LifecycleId, ScenarioId
from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "FiccEvidenceFact",
    "FiccEvidenceStage",
    "FiccFactStatus",
    "FiccGsdDocument",
    "FiccLifecycleDocument",
    "FiccNetObligation",
    "FiccNovatedObligation",
    "consume_ficc_gsd_result",
    "ficc_lifecycle_publication",
    "ficc_lifecycle_publication_bytes",
]


class _WireModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class FiccEvidenceStage(StrEnum):
    CLEARING_ACCEPTANCE = "CLEARING_ACCEPTANCE"
    NOVATION = "NOVATION"
    NETTING = "NETTING"
    SETTLEMENT_INSTRUCTION = "SETTLEMENT_INSTRUCTION"
    SETTLEMENT_FINALITY = "SETTLEMENT_FINALITY"


class FiccFactStatus(StrEnum):
    RECORDED = "RECORDED"
    NOT_RECORDED = "NOT_RECORDED"


class FiccEvidenceFact(_WireModel):
    stage: FiccEvidenceStage
    status: FiccFactStatus
    evidence_id: str | None = None
    reason: str | None = None


class FiccNovatedObligation(_WireModel):
    obligation_id: str
    trade_ids: tuple[str, ...]
    member_id: str
    cusip: str
    settlement_date: date
    signed_quantity: Decimal
    novation_evidence_id: str


class FiccNetObligation(_WireModel):
    member_id: str
    cusip: str
    settlement_date: date
    net_quantity: Decimal
    source_obligation_ids: tuple[str, ...]
    source_trade_ids: tuple[str, ...]


class FiccGsdDocument(_WireModel):
    schema_version: Literal["atreides.ficc_gsd_netting/1"]
    clearing_agency: Literal["FICC_GSD"]
    clearing_method: Literal["FICC_GSD_NOVATED_NETTED"]
    access_path: Literal["FICC_DIRECT", "FICC_SPONSORED", "FICC_AGENT"]
    rule_set_version: str
    processing_calendar_id: str
    facts: tuple[FiccEvidenceFact, ...]
    novated_obligations: tuple[FiccNovatedObligation, ...]
    net_settlement_obligations: tuple[FiccNetObligation, ...]

    @model_validator(mode="after")
    def _facts_are_complete_unique_and_ordered(self) -> Self:
        expected = tuple(FiccEvidenceStage)
        if tuple(fact.stage for fact in self.facts) != expected:
            raise ValueError("FICC facts are absent, duplicated, or unordered")
        return self


class _FiccHeader(_WireModel):
    schema_version: Literal["atreides.ficc_gsd_netting_header/1"]
    payload_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    clearing_agency: Literal["FICC_GSD"]
    clearing_method: Literal["FICC_GSD_NOVATED_NETTED"]


class FiccLifecycleDocument(_WireModel):
    """Person-readable publication of the five independent FICC lifecycle facts."""

    schema_version: Literal["harness_c2.ficc_lifecycle_document/1"] = (
        "harness_c2.ficc_lifecycle_document/1"
    )
    taken_at: datetime
    scenario_id: ScenarioId
    lifecycle_id: LifecycleId
    synthetic: Literal[True] = True
    source_payload_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    clearing_agency: Literal["FICC_GSD"] = "FICC_GSD"
    clearing_method: Literal["FICC_GSD_NOVATED_NETTED"] = "FICC_GSD_NOVATED_NETTED"
    access_path: Literal["FICC_DIRECT", "FICC_SPONSORED", "FICC_AGENT"]
    rule_set_version: str
    processing_calendar_id: str
    facts: tuple[FiccEvidenceFact, ...]
    net_settlement_obligations: tuple[FiccNetObligation, ...]


def consume_ficc_gsd_result(header_bytes: bytes, payload: bytes) -> FiccGsdDocument:
    """Verify the producer's digest, then parse those same bytes with no translation."""

    header = _FiccHeader.model_validate_json(header_bytes, strict=True)
    if digest_bytes(payload) != header.payload_digest:
        raise ValueError("FICC result payload does not match its attested digest")
    document = FiccGsdDocument.model_validate_json(payload, strict=True)
    if document.clearing_agency != header.clearing_agency:
        raise ValueError("FICC clearing agency does not match its attested header")
    if document.clearing_method != header.clearing_method:
        raise ValueError("FICC clearing method does not match its attested header")
    return document


def ficc_lifecycle_publication(
    *,
    document: FiccGsdDocument,
    source_payload_digest: str,
    scenario_id: ScenarioId,
    lifecycle_id: LifecycleId,
    taken_at: datetime,
) -> FiccLifecycleDocument:
    """Project the consumed result into a labelled, independently checkable view."""

    return FiccLifecycleDocument(
        taken_at=taken_at,
        scenario_id=scenario_id,
        lifecycle_id=lifecycle_id,
        source_payload_digest=source_payload_digest,
        access_path=document.access_path,
        rule_set_version=document.rule_set_version,
        processing_calendar_id=document.processing_calendar_id,
        facts=document.facts,
        net_settlement_obligations=document.net_settlement_obligations,
    )


def ficc_lifecycle_publication_bytes(document: FiccLifecycleDocument) -> bytes:
    return canonical_bytes(document)
