"""COP-owned reader for Atreides synthetic customer protection advisories."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Annotated, Literal, Protocol

import httpx
from cannae_kernel.disposition import Disposition
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cop.http import get_bytes
from cop.observation import SourceFieldAbsentError, SourceMalformedError
from cop.settings import HTTP_TIMEOUT_SECONDS, PRODUCT_NAME

SUPPORTED_SCHEMA_VERSION = 1
SUPPORTED_ADVISORY_SCHEMA_VERSION = "0.1-draft"


class RuleVersion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    source_id: str = Field(min_length=1)
    citation: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    retrieval_dtg: str = Field(pattern=r"^\d{12}$")


class AdvisoryRow(BaseModel):
    """Every field emitted by ``CustomerProtectionAdvisory``, without translation."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal["0.1-draft"]
    claim_label: Literal["EXPERIMENTAL"]
    enforcement_status: Literal["ADVISORY_ONLY"]
    subject: Literal["reserve", "net_capital", "possession_or_control", "challenger"]
    as_of: date
    disposition: Disposition
    reasons: tuple[str, ...]
    missing_rules: tuple[str, ...]
    missing_inputs: tuple[str, ...]
    rule_versions: tuple[RuleVersion, ...]
    rule_table_version: str
    rule_table_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    input_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    result_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class AdvisoryScenario(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    scenario_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    advisories: Annotated[tuple[AdvisoryRow, ...], Field(min_length=1)]


class AdvisoryPublication(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1]
    synthetic: Literal[True]
    taken_at: datetime
    scenarios: Annotated[tuple[AdvisoryScenario, ...], Field(min_length=1)]


class AdvisorySource(Protocol):
    def publication(self) -> AdvisoryPublication: ...


def parse_publication(raw: bytes) -> AdvisoryPublication:
    """Parse a complete supported synthetic publication, or fail closed."""
    try:
        data = json.loads(raw)
    except ValueError:
        raise SourceMalformedError("Advisory publication is not valid JSON") from None
    if not isinstance(data, dict):
        raise SourceMalformedError("Advisory publication is not a JSON object")
    if "schema_version" not in data:
        raise SourceFieldAbsentError("Advisory publication has no schema_version")
    if data["schema_version"] != SUPPORTED_SCHEMA_VERSION:
        raise SourceMalformedError(
            f"Advisory publication is schema version {data['schema_version']!r}; "
            f"this reader understands only {SUPPORTED_SCHEMA_VERSION}"
        )
    try:
        return AdvisoryPublication.model_validate_json(raw)
    except ValidationError as exc:
        raise SourceMalformedError(
            f"Unexpected advisory publication shape ({exc.error_count()} problems)"
        ) from None


class HttpxAdvisoryClient:
    def __init__(self, url: str, http: httpx.Client | None = None) -> None:
        self._http = http or httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)
        self._url = url

    def publication(self) -> AdvisoryPublication:
        return parse_publication(
            get_bytes(
                self._http,
                self._url,
                headers={"Accept": "application/json", "User-Agent": PRODUCT_NAME},
            )
        )
