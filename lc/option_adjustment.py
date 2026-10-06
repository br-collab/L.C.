"""Evidence-backed OCC option adjustments linked to corporate actions.

EXPERIMENTAL. Adjustments are ingested exactly as stated in an OCC notice.
This module never calculates an adjustment from a corporate action.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lc.options import AdjustmentEvidence, Deliverable, DeliverableKind, OptionSeries

__all__ = [
    "AdjustmentDecision",
    "AdjustmentOutcome",
    "OccContractAdjustment",
    "SeriesTerms",
    "apply_adjustment",
]


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class AdjustmentOutcome(StrEnum):
    APPLIED = "APPLIED"
    NOT_YET_EFFECTIVE = "NOT_YET_EFFECTIVE"
    UNMATCHED_EVENT = "UNMATCHED_EVENT"
    INDETERMINATE = "INDETERMINATE"


class OccContractAdjustment(_Record):
    """One OCC notice, transcribed as data rather than computed."""

    adjustment_id: str = Field(min_length=1)
    series_osi: str = Field(min_length=1)
    effective_date: date
    corporate_action_event_id: str = Field(min_length=1)
    corporate_action_source_id: str = Field(min_length=1)
    deliverable: Deliverable
    evidence: AdjustmentEvidence

    @model_validator(mode="after")
    def _is_evidenced_adjusted_deliverable(self) -> Self:
        if self.deliverable.kind is not DeliverableKind.ADJUSTED:
            raise ValueError("an OCC adjustment states an adjusted deliverable")
        if self.deliverable.evidence != self.evidence:
            raise ValueError("the deliverable and adjustment must carry the same OCC evidence")
        return self


class SeriesTerms(_Record):
    """The active series and the terms retained before an applied adjustment."""

    active: OptionSeries
    prior: tuple[OptionSeries, ...] = ()


class AdjustmentDecision(_Record):
    outcome: AdjustmentOutcome
    terms: SeriesTerms
    reason: str
    adjustment: OccContractAdjustment


def apply_adjustment(
    terms: SeriesTerms,
    adjustment: OccContractAdjustment,
    *,
    as_of: date,
    known_corporate_actions: set[tuple[str, str]],
) -> AdjustmentDecision:
    """Apply a stated adjustment when its opaque corporate-action reference matches."""
    if adjustment.series_osi != terms.active.osi_identifier:
        raise ValueError("the adjustment names another option series")
    reference = (adjustment.corporate_action_event_id, adjustment.corporate_action_source_id)
    if reference not in known_corporate_actions:
        return AdjustmentDecision(
            outcome=AdjustmentOutcome.UNMATCHED_EVENT,
            terms=terms,
            reason="no matching corporate action event and reporting authority were supplied",
            adjustment=adjustment,
        )
    if as_of < adjustment.effective_date:
        return AdjustmentDecision(
            outcome=AdjustmentOutcome.NOT_YET_EFFECTIVE,
            terms=terms,
            reason="the OCC notice is not effective as of the requested date",
            adjustment=adjustment,
        )
    active = terms.active.model_copy(update={"deliverable": adjustment.deliverable})
    return AdjustmentDecision(
        outcome=AdjustmentOutcome.APPLIED,
        terms=SeriesTerms(active=active, prior=(*terms.prior, terms.active)),
        reason="the deliverable stated in the evidenced OCC notice is effective",
        adjustment=adjustment,
    )

