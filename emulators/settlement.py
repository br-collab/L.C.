"""Synthetic settlement member and rail for deterministic experiments.

The member is the only component in this programme that submits, and it is
synthetic.  Keeping submission here prevents Atreides from gaining either a
submission capability or a rail credential.  The rail reports facts from its
own deterministic state and imports no business domain.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from random import Random

from cannae_kernel.clocks import EventTimes
from cannae_kernel.disposition import Disposition
from cannae_kernel.ids import LifecycleId
from cannae_kernel.provenance import Provenance
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "RailOutcome",
    "RailResponse",
    "SettlementSubmission",
    "SyntheticEntitledMember",
    "SyntheticRail",
]


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class RailOutcome(StrEnum):
    SETTLED = "SETTLED"
    QUEUED = "QUEUED"
    REJECTED = "REJECTED"
    INDETERMINATE = "INDETERMINATE"


class SettlementSubmission(_Record):
    """The synthetic member's byte-preserving submission record."""

    submission_id: str = Field(min_length=1)
    lifecycle_id: LifecycleId
    instruction: bytes = Field(min_length=1)
    instruction_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    times: EventTimes
    provenance: Provenance = Provenance.FACT_SYNTHETIC


class RailResponse(_Record):
    """A synthetic rail fact; indeterminate is a terminal reported outcome."""

    response_id: str = Field(min_length=1)
    submission_id: str = Field(min_length=1)
    lifecycle_id: LifecycleId
    outcome: RailOutcome
    disposition: Disposition
    reason: str = Field(min_length=1)
    times: EventTimes
    provenance: Provenance = Provenance.FACT_SYNTHETIC


class SyntheticEntitledMember:
    """The sole synthetic submitter; it owns no real credential or endpoint."""

    def __init__(self, *, seed: int) -> None:
        self._seed = seed

    def submit(
        self, *, lifecycle_id: LifecycleId, instruction: bytes, times: EventTimes
    ) -> SettlementSubmission:
        digest = hashlib.sha256(instruction).hexdigest()
        return SettlementSubmission(
            submission_id=f"synthetic-member-{self._seed}-{digest[:12]}",
            lifecycle_id=lifecycle_id,
            instruction=instruction,
            instruction_digest=f"sha256:{digest}",
            times=times,
        )


class SyntheticRail:
    """A deterministic four-outcome rail selected entirely by its seed."""

    _OUTCOMES = tuple(RailOutcome)

    def __init__(self, *, seed: int) -> None:
        self._seed = seed

    def respond(self, submission: SettlementSubmission, *, times: EventTimes) -> RailResponse:
        outcome = self._OUTCOMES[Random(self._seed).randrange(len(self._OUTCOMES))]
        disposition = {
            RailOutcome.SETTLED: Disposition.PASS,
            RailOutcome.QUEUED: Disposition.HOLD,
            RailOutcome.REJECTED: Disposition.BLOCK,
            RailOutcome.INDETERMINATE: Disposition.INDETERMINATE,
        }[outcome]
        reason = {
            RailOutcome.SETTLED: "synthetic rail recorded settlement",
            RailOutcome.QUEUED: "synthetic rail queued the instruction",
            RailOutcome.REJECTED: "synthetic rail rejected the instruction",
            RailOutcome.INDETERMINATE: "synthetic rail could not determine the instruction state",
        }[outcome]
        return RailResponse(
            response_id=f"synthetic-rail-{self._seed}-{submission.submission_id}",
            submission_id=submission.submission_id,
            lifecycle_id=submission.lifecycle_id,
            outcome=outcome,
            disposition=disposition,
            reason=reason,
            times=times,
        )
