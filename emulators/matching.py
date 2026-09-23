"""Independent deterministic matching and affirmation emulator."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from cannae_kernel.provenance import Provenance
from pydantic import BaseModel, ConfigDict, Field

__all__ = ["MatchRequest", "MatchResponse", "MatchingEmulator", "MatchingOutcome"]


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class MatchingOutcome(StrEnum):
    MATCHED_AND_AFFIRMED = "MATCHED_AND_AFFIRMED"
    MISMATCH = "MISMATCH"
    NEVER_AFFIRMED = "NEVER_AFFIRMED"


class MatchRequest(_Record):
    trade_id: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    quantity: str = Field(min_length=1)
    allocation_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class MatchResponse(_Record):
    response_id: str = Field(min_length=1)
    trade_id: str = Field(min_length=1)
    outcome: MatchingOutcome
    matched: bool
    affirmed: bool
    reason: str = Field(min_length=1)
    provenance: Literal[Provenance.FACT_SYNTHETIC] = Provenance.FACT_SYNTHETIC


class MatchingEmulator:
    def __init__(self, *, seed: int) -> None:
        self._seed = seed

    def respond(self, request: MatchRequest, *, outcome: MatchingOutcome) -> MatchResponse:
        matched = outcome is not MatchingOutcome.MISMATCH
        affirmed = outcome is MatchingOutcome.MATCHED_AND_AFFIRMED
        reasons = {
            MatchingOutcome.MATCHED_AND_AFFIRMED: "counterparty matched and affirmed",
            MatchingOutcome.MISMATCH: "counterparty quantity does not match",
            MatchingOutcome.NEVER_AFFIRMED: "counterparty matched but supplied no affirmation",
        }
        return MatchResponse(
            response_id=f"match-{self._seed}-{request.trade_id}",
            trade_id=request.trade_id,
            outcome=outcome,
            matched=matched,
            affirmed=affirmed,
            reason=reasons[outcome],
        )
