"""Append-only byte transcript of every cross-domain crossing."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from cannae_kernel.canonical import Digest, digest_bytes
from cannae_kernel.clocks import EventTimes
from cannae_kernel.disposition import Disposition
from cannae_kernel.envelopes import (
    ApprovedIntentEnvelope,
    ClearingTransformation,
    ExecutionEvent,
    ObligationAcceptanceRecord,
    SettlementObligationEnvelope,
)
from cannae_kernel.ids import LifecycleId
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from harness_c2.scenario import ScenarioRecord

__all__ = [
    "Crossing",
    "CrossingArtifactEnvelope",
    "CrossingTranscript",
    "Domain",
    "Envelope",
    "record_crossing",
]

Domain = Literal[
    "aureon",
    "lc",
    "atreides",
    "emulator",
    "synthetic_member",
    "synthetic_rail",
    "harness_c2",
]


class CrossingArtifactEnvelope(BaseModel):
    """Typed attestation header for a crossing that has no kernel domain envelope.

    Submission, external rail response and reconciliation are evidence artifacts,
    not economic domain envelopes.  The transcript still needs a frozen header that
    binds their exact bytes to the lifecycle without importing their producer's type.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal["harness_c2.crossing_artifact/0.1-draft"] = (
        "harness_c2.crossing_artifact/0.1-draft"
    )
    artifact_id: str = Field(min_length=1)
    lifecycle_id: LifecycleId
    artifact_kind: Literal[
        "cash_gate_input",
        "prepared_instruction",
        "member_submission",
        "rail_response",
        "reconciliation",
    ]
    payload_digest: Digest


Envelope = Annotated[
    ApprovedIntentEnvelope
    | ExecutionEvent
    | ClearingTransformation
    | SettlementObligationEnvelope
    | ObligationAcceptanceRecord
    | CrossingArtifactEnvelope,
    Field(discriminator="schema_version"),
]
_ENVELOPE: TypeAdapter[Envelope] = TypeAdapter(Envelope)


class _Record(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )


class Crossing(_Record):
    producer: Domain
    consumer: Domain
    lifecycle_id: LifecycleId
    scenario_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    envelope: Envelope
    payload_bytes: bytes = Field(min_length=1)
    producer_asserted_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    consumer_computed_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    times: EventTimes
    disposition: Disposition
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def _digest_mismatch_is_a_refusal(self) -> Self:
        mismatch = self.producer_asserted_digest != self.consumer_computed_digest
        if mismatch and self.disposition is not Disposition.BLOCK:
            raise ValueError("DIGEST_MISMATCH must be recorded as BLOCK")
        if mismatch and "DIGEST_MISMATCH" not in self.reason:
            raise ValueError("digest mismatch refusal must be named DIGEST_MISMATCH")
        return self


class CrossingTranscript(_Record):
    """An immutable transcript; append returns a new value and never edits history."""

    crossings: tuple[Crossing, ...] = ()

    def append(self, crossing: Crossing) -> CrossingTranscript:
        return self.model_copy(update={"crossings": (*self.crossings, crossing)}, deep=True)

    def to_bytes(self) -> bytes:
        return self.model_dump_json().encode("utf-8")

    @classmethod
    def from_bytes(cls, value: bytes) -> CrossingTranscript:
        return cls.model_validate_json(value, strict=True)


def record_crossing(  # noqa: PLR0913 - every crossing field is evidence, not configuration
    *,
    scenario: ScenarioRecord,
    producer: Domain,
    consumer: Domain,
    lifecycle_id: LifecycleId,
    envelope: Envelope,
    payload_bytes: bytes,
    producer_asserted_digest: str,
    times: EventTimes,
    accepted_disposition: Disposition,
    accepted_reason: str,
) -> Crossing:
    """Recompute the received bytes and retain both sides of the digest comparison."""
    scenario.require_lifecycle(lifecycle_id)
    asserted = producer_asserted_digest
    computed = digest_bytes(payload_bytes)
    if asserted != computed:
        disposition = Disposition.BLOCK
        reason = "DIGEST_MISMATCH: received payload bytes differ from producer assertion"
    else:
        disposition = accepted_disposition
        reason = accepted_reason
    return Crossing(
        producer=producer,
        consumer=consumer,
        lifecycle_id=lifecycle_id,
        scenario_digest=scenario.canonical_digest,
        envelope=_ENVELOPE.validate_python(envelope, strict=True),
        payload_bytes=payload_bytes,
        producer_asserted_digest=asserted,
        consumer_computed_digest=computed,
        times=times,
        disposition=disposition,
        reason=reason,
    )
