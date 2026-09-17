"""Aureon ``/api/snapshot`` source for the Live Aureon panel.

Fields read, as served by aureon ``server.py`` ``api_snapshot()``: ``deploy_sha`` (a
string, ``"unset"`` outside Railway), ``stack`` (``initializing``, ``running`` or
``ready``), ``positions`` (a count), ``pending`` (a count of pending decisions) and
``market_open`` (a boolean). A field that is absent or null is kept as ``None`` and shown
as INDETERMINATE on its own; a field with the wrong type makes the whole response
malformed, because a source that changed shape cannot be trusted field by field.
"""

from __future__ import annotations

from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from cop.http import get_json
from cop.observation import SourceMalformedError
from cop.settings import AUREON_SNAPSHOT_URL, HTTP_TIMEOUT_SECONDS, PRODUCT_NAME

DEPLOY_SHA_UNSET = "unset"


class AureonSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", strict=True)

    deploy_sha: str | None = None
    stack: str | None = None
    positions: int | None = None
    pending: int | None = None
    market_open: bool | None = None


class AureonSource(Protocol):
    def snapshot(self) -> AureonSnapshot: ...


def parse_snapshot(data: object) -> AureonSnapshot:
    if not isinstance(data, dict):
        raise SourceMalformedError("Snapshot is not a JSON object")
    try:
        return AureonSnapshot.model_validate(data)
    except ValidationError as exc:
        raise SourceMalformedError(
            f"Unexpected snapshot shape ({exc.error_count()} problems)"
        ) from None


class HttpxAureonClient:
    def __init__(self, http: httpx.Client | None = None, url: str = AUREON_SNAPSHOT_URL) -> None:
        self._http = http or httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)
        self._url = url

    def snapshot(self) -> AureonSnapshot:
        data = get_json(
            self._http,
            self._url,
            headers={"Accept": "application/json", "User-Agent": PRODUCT_NAME},
        )
        return parse_snapshot(data)
