"""COP-owned reader for Atreides synthetic requirement traceability evidence."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Annotated, Literal, Protocol, Self

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

from cop.http import get_bytes
from cop.observation import SourceFieldAbsentError, SourceMalformedError
from cop.settings import HTTP_TIMEOUT_SECONDS, PRODUCT_NAME

SUPPORTED_SCHEMA_VERSION = 1


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class CoverageStatus(StrEnum):
    COVERED = "COVERED"
    UNTESTED = "UNTESTED"
    FAILING = "FAILING"
    NOT_RUN = "NOT_RUN"


class RequirementCoverage(_Frozen):
    requirement_id: str = Field(min_length=1)
    status: CoverageStatus
    test_node_ids: tuple[str, ...]


class TraceabilityPublication(_Frozen):
    schema_version: Literal[1]
    synthetic: Literal[True]
    enforcement_status: Literal["ADVISORY_ONLY"]
    claim_label: Literal["EXPERIMENTAL"]
    run_scope: Literal["FULL"]
    run_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    run_timestamp: AwareDatetime
    requirements: Annotated[tuple[RequirementCoverage, ...], Field(min_length=1)]
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _unique_requirement_ids(self) -> Self:
        ids = tuple(row.requirement_id for row in self.requirements)
        if len(ids) != len(set(ids)):
            raise ValueError("requirement_id values must be unique")
        return self


class TraceabilitySource(Protocol):
    def publication(self) -> TraceabilityPublication: ...


def parse_publication(raw: bytes) -> TraceabilityPublication:
    """Parse a complete supported publication, or fail closed."""
    try:
        data = json.loads(raw)
    except ValueError:
        raise SourceMalformedError("Traceability publication is not valid JSON") from None
    if not isinstance(data, dict):
        raise SourceMalformedError("Traceability publication is not a JSON object")
    if "schema_version" not in data:
        raise SourceFieldAbsentError("Traceability publication has no schema_version")
    if data["schema_version"] != SUPPORTED_SCHEMA_VERSION:
        raise SourceMalformedError(
            f"Traceability publication is schema version {data['schema_version']!r}; "
            f"this reader understands only {SUPPORTED_SCHEMA_VERSION}"
        )
    try:
        return TraceabilityPublication.model_validate_json(raw)
    except ValidationError as exc:
        raise SourceMalformedError(
            f"Unexpected traceability publication shape ({exc.error_count()} problems)"
        ) from None


class HttpxTraceabilityClient:
    def __init__(self, url: str, http: httpx.Client | None = None) -> None:
        self._http = http or httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)
        self._url = url

    def publication(self) -> TraceabilityPublication:
        return parse_publication(
            get_bytes(
                self._http,
                self._url,
                headers={"Accept": "application/json", "User-Agent": PRODUCT_NAME},
            )
        )
