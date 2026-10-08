"""COP-owned reader for Atreides synthetic break publications.

The wire models mirror the producer's closed schema without importing Atreides.
The Common Operating Picture remains display only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal, Protocol, Self

import httpx
from cannae_kernel.actor import ActorRef
from cannae_kernel.canonical import Digest, canonical_bytes, digest_bytes
from cannae_kernel.disposition import Disposition
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from cop.http import get_bytes
from cop.observation import SourceFieldAbsentError, SourceMalformedError
from cop.settings import HTTP_TIMEOUT_SECONDS, PRODUCT_NAME

SUPPORTED_SCHEMA_VERSION = 1
BREAKS_SOURCE_LABEL = "Atreides synthetic break register"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class BreakState(StrEnum):
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    RESOLVED = "RESOLVED"
    WRITTEN_OFF = "WRITTEN_OFF"


class BreakAction(_Frozen):
    occurred_at: AwareDatetime
    actor: ActorRef
    action: str = Field(min_length=1)


class ResolutionEvidence(_Frozen):
    recorded_at: AwareDatetime
    recorded_by: ActorRef
    evidence_ref: str = Field(min_length=1)
    detail: str = Field(min_length=1)


@dataclass(frozen=True)
class BreakRecord:
    """Existing cross-layer panel row, retained until WP-4 revises the panel."""

    object_id: str
    left_layer: str
    left_claim: str
    left_stamped_at: datetime
    right_layer: str
    right_claim: str
    right_stamped_at: datetime
    disposition: Disposition


class AtreidesBreakRecord(_Frozen):
    """Every field emitted by Atreides, in producer declaration order."""

    schema_version: Literal["atreides.break/0.1-experimental"]
    enforcement_status: Literal["ADVISORY_ONLY"]
    experimental: Literal[True]
    synthetic: Literal[True]
    break_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    regime: str = Field(min_length=1)
    leg: str = Field(min_length=1)
    symptom: str = Field(min_length=1)
    sources: Annotated[tuple[str, ...], Field(min_length=1)]
    difference: str = Field(min_length=1)
    originating_event_at: AwareDatetime
    originating_event_ref: str = Field(min_length=1)
    cause_class: str = Field(min_length=1)
    owner: ActorRef | None
    owner_absence_reason: str | None
    sla_target: AwareDatetime
    actions: tuple[BreakAction, ...]
    resolution_evidence: ResolutionEvidence | None
    state: BreakState
    dsor_record_id: str | None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.owner is None and not self.owner_absence_reason:
            raise ValueError("an absent owner must carry an explicit absence reason")
        if self.owner is not None and self.owner_absence_reason is not None:
            raise ValueError("owner_absence_reason is set only when owner is absent")
        if self.state is BreakState.RESOLVED and self.resolution_evidence is None:
            raise ValueError("a resolved break must carry resolution evidence")
        if self.sla_target < self.originating_event_at:
            raise ValueError("sla_target precedes the originating event")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self)


def records_digest(records: tuple[AtreidesBreakRecord, ...]) -> Digest:
    ordered = sorted(records, key=lambda record: record.break_id)
    return digest_bytes(b"".join(record.canonical_bytes() for record in ordered))


Records = Annotated[tuple[AtreidesBreakRecord, ...], Field(min_length=1)]


class BreaksPublication(_Frozen):
    schema_version: Literal[1]
    synthetic: Literal[True]
    enforcement_status: Literal["ADVISORY_ONLY"]
    claim_label: Literal["EXPERIMENTAL"]
    complete: Literal[True]
    taken_at: AwareDatetime
    input_digest: Digest
    records: Records

    @field_validator("taken_at")
    @classmethod
    def _taken_at_is_utc(cls, value: AwareDatetime) -> AwareDatetime:
        if value.utcoffset() != timedelta(0):
            raise ValueError("taken_at must be UTC")
        return value

    @model_validator(mode="after")
    def _unique_and_intact(self) -> Self:
        ids = tuple(record.break_id for record in self.records)
        if len(ids) != len(set(ids)):
            raise ValueError("break_id values must be unique")
        if self.input_digest != records_digest(self.records):
            raise ValueError("input_digest does not match the published break records")
        return self


class BreakSource(Protocol):
    def breaks(self) -> tuple[BreakRecord, ...]: ...


class BreakPublicationSource(Protocol):
    @property
    def source_label(self) -> str: ...

    def publication(self) -> BreaksPublication: ...


def parse_publication(raw: bytes) -> BreaksPublication:
    """Parse exact producer bytes and reject incomplete or tampered input."""
    try:
        data = json.loads(raw)
    except ValueError:
        raise SourceMalformedError("Break publication is not valid JSON") from None
    if not isinstance(data, dict):
        raise SourceMalformedError("Break publication is not a JSON object")
    if "schema_version" not in data:
        raise SourceFieldAbsentError("Break publication has no schema_version")
    if data["schema_version"] != SUPPORTED_SCHEMA_VERSION:
        raise SourceMalformedError(
            f"Break publication is schema version {data['schema_version']!r}; "
            f"this reader understands only {SUPPORTED_SCHEMA_VERSION}"
        )
    try:
        return BreaksPublication.model_validate_json(raw)
    except ValidationError as exc:
        raise SourceMalformedError(
            f"Unexpected break publication shape ({exc.error_count()} problems)"
        ) from None


class HttpxBreaksClient:
    def __init__(self, url: str, http: httpx.Client | None = None) -> None:
        self._http = http or httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)
        self._url = url

    @property
    def source_label(self) -> str:
        return self._url

    def publication(self) -> BreaksPublication:
        return parse_publication(
            get_bytes(
                self._http,
                self._url,
                headers={"Accept": "application/json", "User-Agent": PRODUCT_NAME},
            )
        )
