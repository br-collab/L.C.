"""Independent reader for L.C.'s published, read-only layer-clock document."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal, Protocol

import httpx
from cannae_kernel.clocks import EventTimes
from cannae_kernel.ids import EventId, LifecycleId
from cannae_kernel.provenance import Provenance
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cop.http import get_bytes
from cop.observation import SourceFieldAbsentError, SourceMalformedError
from cop.settings import HTTP_TIMEOUT_SECONDS, PRODUCT_NAME

SUPPORTED_SCHEMA_VERSION = 1


class LayerClock(BaseModel):
    """The COP's own reading of the producer document; never imports :mod:`lc`."""

    model_config = ConfigDict(frozen=True, extra="ignore", strict=True)

    schema_version: Literal[1]
    layer: Literal["LC"]
    lifecycle_id: LifecycleId
    event_id: EventId
    state: str = Field(min_length=1)
    times: EventTimes
    provenance: Provenance
    event_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @property
    def source_time(self) -> datetime:
        return self.times.processing_time


class LayerClockSource(Protocol):
    def clock(self) -> LayerClock: ...


def parse_layer_clock(raw: bytes) -> LayerClock:
    try:
        data = json.loads(raw)
    except ValueError:
        raise SourceMalformedError("L.C. layer clock is not valid JSON") from None
    if not isinstance(data, dict):
        raise SourceMalformedError("L.C. layer clock is not a JSON object")
    version = data.get("schema_version")
    if version is None:
        raise SourceFieldAbsentError("L.C. layer clock has no schema_version")
    if version != SUPPORTED_SCHEMA_VERSION:
        raise SourceMalformedError(
            f"L.C. layer clock is schema version {version!r}; this reader understands "
            f"only {SUPPORTED_SCHEMA_VERSION}"
        )
    try:
        return LayerClock.model_validate_json(raw)
    except ValidationError as exc:
        raise SourceMalformedError(
            f"Unexpected L.C. layer-clock shape ({exc.error_count()} problems)"
        ) from None


class HttpxLayerClockClient:
    def __init__(self, url: str, http: httpx.Client | None = None) -> None:
        self._http = http or httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)
        self._url = url

    def clock(self) -> LayerClock:
        return parse_layer_clock(
            get_bytes(
                self._http,
                self._url,
                headers={"Accept": "application/json", "User-Agent": PRODUCT_NAME},
            )
        )
