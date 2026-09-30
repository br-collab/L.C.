"""Published, versioned view of a completed slice transcript."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Final, Literal

from cannae_kernel.absence import AbsenceKind, Absent, Recorded
from cannae_kernel.disposition import Disposition
from cannae_kernel.envelopes import (
    ApprovedIntentEnvelope,
    ClearingTransformation,
    ExecutionEvent,
    ObligationAcceptanceRecord,
    SettlementObligationEnvelope,
)
from cannae_kernel.ids import LifecycleId, ScenarioId
from cannae_kernel.provenance import Provenance
from pydantic import BaseModel, ConfigDict

from harness_c2.scenario import ScenarioRecord
from harness_c2.transcript import Crossing, CrossingArtifactEnvelope, CrossingTranscript

__all__ = [
    "LIFECYCLE_DOCUMENT_SCHEMA_VERSION",
    "LifecycleDocument",
    "LifecycleStage",
    "StageEvidence",
    "StageRow",
    "build_lifecycle_document",
]

LIFECYCLE_DOCUMENT_SCHEMA_VERSION: Final = "harness_c2.lifecycle_document/1"


class LifecycleStage(StrEnum):
    APPROVED_INTENT = "APPROVED_INTENT"
    EXECUTION = "EXECUTION"
    CLEARING = "CLEARING"
    SETTLEMENT_OBLIGATION = "SETTLEMENT_OBLIGATION"
    OBLIGATION_ACCEPTANCE = "OBLIGATION_ACCEPTANCE"
    SETTLED = "SETTLED"


class _DocumentModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class StageEvidence(_DocumentModel):
    disposition: Disposition
    reason: str
    stamped_at: datetime
    provenance: Provenance


class StageRow(_DocumentModel):
    stage: LifecycleStage
    evidence: Recorded[StageEvidence] | Absent


class LifecycleDocument(_DocumentModel):
    schema_version: Literal["harness_c2.lifecycle_document/1"] = LIFECYCLE_DOCUMENT_SCHEMA_VERSION
    taken_at: datetime
    scenario_id: ScenarioId
    lifecycle_id: LifecycleId
    synthetic: bool = True
    stages: tuple[StageRow, ...]


def _recorded(crossing: Crossing, *, reason: str | None = None) -> Recorded[StageEvidence]:
    return Recorded[StageEvidence](
        value=StageEvidence(
            disposition=crossing.disposition,
            reason=reason or crossing.reason,
            stamped_at=crossing.times.event_time,
            provenance=Provenance.FACT_SYNTHETIC,
        )
    )


def _not_reached(reason: str) -> Absent:
    return Absent(kind=AbsenceKind.NOTHING_RECORDED, reason=f"NOT_REACHED: {reason}")


def build_lifecycle_document(
    scenario: ScenarioRecord, transcript: CrossingTranscript, *, taken_at: datetime
) -> LifecycleDocument:
    """Project only facts recorded by ``transcript`` into the publication shape."""
    crossings = transcript.crossings

    def first(envelope_type: type[object]) -> Crossing | None:
        return next((c for c in crossings if isinstance(c.envelope, envelope_type)), None)

    intent = first(ApprovedIntentEnvelope)
    execution = first(ExecutionEvent)
    clearing = first(ClearingTransformation)
    obligation = first(SettlementObligationEnvelope)
    acceptance = first(ObligationAcceptanceRecord)
    cash_gate = next(
        (
            c
            for c in crossings
            if isinstance(c.envelope, CrossingArtifactEnvelope)
            and c.envelope.artifact_kind == "cash_gate_input"
        ),
        None,
    )
    settled = next(
        (
            c
            for c in crossings
            if isinstance(c.envelope, CrossingArtifactEnvelope)
            and c.envelope.artifact_kind == "reconciliation"
        ),
        None,
    )
    reached: dict[LifecycleStage, Crossing | None] = {
        LifecycleStage.APPROVED_INTENT: intent,
        LifecycleStage.EXECUTION: execution,
        LifecycleStage.CLEARING: clearing,
        LifecycleStage.SETTLEMENT_OBLIGATION: obligation,
        LifecycleStage.OBLIGATION_ACCEPTANCE: acceptance,
        LifecycleStage.SETTLED: settled,
    }
    rows: list[StageRow] = []
    stopped_reason = "the transcript contains no record for this stage"
    for stage in LifecycleStage:
        crossing = reached[stage]
        stage_reason: str | None = None
        if crossing is None:
            evidence: Recorded[StageEvidence] | Absent = _not_reached(stopped_reason)
        elif stage is LifecycleStage.OBLIGATION_ACCEPTANCE and cash_gate is not None:
            # The acceptance envelope records the decision; the preceding gate crossing
            # records its specific reason. The prefix names the recorded artifact kind
            # and disposition; it does not add a fact outside the transcript.
            stage_reason = f"CASH_GATE_{cash_gate.disposition.value}:{cash_gate.reason}"
            evidence = _recorded(crossing, reason=stage_reason)
        else:
            evidence = _recorded(crossing)
        rows.append(StageRow(stage=stage, evidence=evidence))
        if crossing is not None and crossing.disposition is not Disposition.PASS:
            stopped_reason = stage_reason or crossing.reason
    return LifecycleDocument(
        taken_at=taken_at,
        scenario_id=scenario.scenario_id,
        lifecycle_id=scenario.lifecycle_id,
        stages=tuple(rows),
    )
