"""Display-only reader for a settlement join.

The bytes are the producer's JSON. Field names are the producer's field names.
This module does not submit, acknowledge, or write a route. Unreadable evidence
is INDETERMINATE, and only a published asset-final or cash-final assertion is
shown as a clean state.
"""

from __future__ import annotations

from typing import Literal, Self

from cannae_kernel.disposition import Disposition
from cannae_kernel.finality import FinalityAssertion, FinalityType
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

__all__ = [
    "SettlementEvidenceReading",
    "read_settlement_evidence",
    "render_settlement_evidence",
]

_Stage = Literal["UNREAD", "INSTRUCTED", "MATCHED", "ACCEPTED", "REFUSED", "SETTLED", "FINAL"]
_VenueState = Literal["UNREAD", "MATCHED", "ACCEPTED", "REFUSED", "SETTLED", "FINAL"]
_Leg = Literal["asset", "cash"]
_UNREAD = "the settlement evidence could not be read, so it is not shown as a clean state"


class _JoinDocument(BaseModel):
    """The producer document, closed. A pass exists only beside the assertion."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    stage: _Stage
    disposition: Disposition
    reason: str = Field(min_length=1)
    leg: _Leg
    artifact_digest: str = Field(min_length=1)
    receipt_disposition: Disposition
    venue_state: _VenueState
    venue_disposition: Disposition
    assertion: FinalityAssertion | None = None

    @model_validator(mode="after")
    def _only_a_joined_final_assertion_passes(self) -> Self:
        final = self.stage == "FINAL"
        passed = self.disposition is Disposition.PASS
        has_assertion = self.assertion is not None
        if final != passed or final != has_assertion:
            raise ValueError("only a joined final assertion passes, and only a pass carries one")
        if self.assertion is None:
            return self
        expected = FinalityType.ASSET_FINAL if self.leg == "asset" else FinalityType.CASH_FINAL
        if self.assertion.finality_type is not expected:
            raise ValueError("the assertion type does not match the leg")
        if self.assertion.confidence is not None:
            raise ValueError("a finality fact from this join carries no confidence")
        return self


class SettlementEvidenceReading(BaseModel):
    """What the picture may show. A pass exists only beside published finality."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stage: str
    disposition: Disposition
    reason: str = Field(min_length=1)
    leg: str | None = None
    artifact_digest: str | None = None
    receipt_disposition: Disposition | None = None
    venue_state: str | None = None
    venue_disposition: Disposition | None = None
    assertion: FinalityAssertion | None = None

    @model_validator(mode="after")
    def _clean_only_when_final(self) -> Self:
        published = (
            self.stage == "FINAL"
            and self.assertion is not None
            and self.assertion.confidence is None
            and (
                (self.leg == "asset" and self.assertion.finality_type is FinalityType.ASSET_FINAL)
                or (self.leg == "cash" and self.assertion.finality_type is FinalityType.CASH_FINAL)
            )
        )
        if (self.disposition is Disposition.PASS) != published:
            raise ValueError("only published asset-final or cash-final evidence is a clean state")
        return self


def read_settlement_evidence(raw: bytes) -> SettlementEvidenceReading:
    """Read producer bytes. Unreadable, incomplete, or contradictory bytes do not pass."""
    try:
        document = _JoinDocument.model_validate_json(raw)
    except (ValidationError, ValueError, UnicodeError):
        return SettlementEvidenceReading(
            stage="UNREAD",
            disposition=Disposition.INDETERMINATE,
            reason=_UNREAD,
        )
    return SettlementEvidenceReading(
        stage=document.stage,
        disposition=document.disposition,
        reason=document.reason,
        leg=document.leg,
        artifact_digest=document.artifact_digest,
        receipt_disposition=document.receipt_disposition,
        venue_state=document.venue_state,
        venue_disposition=document.venue_disposition,
        assertion=document.assertion,
    )


def render_settlement_evidence(reading: SettlementEvidenceReading) -> str:
    """Plain text. Finality is named only when the reading published it."""
    if reading.disposition is Disposition.PASS and reading.assertion is not None:
        return (
            f"{reading.assertion.finality_type.value} on the {reading.leg} leg. "
            f"Provenance {reading.assertion.provenance.value}. {reading.reason}"
        )
    if reading.assertion is None:
        provenance = "no kernel finality assertion was published"
    else:
        provenance = reading.assertion.provenance.value
    return (
        f"{reading.stage}. Disposition {reading.disposition.value}. {reading.reason} "
        f"Provenance: {provenance}."
    )
