"""Reader for Aureon's published cash-leg demonstration document (COP-1 panel 11)."""

from __future__ import annotations

import json
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cop.http import get_bytes
from cop.observation import SourceFieldAbsentError, SourceMalformedError
from cop.settings import AUREON_CASH_LEG_URL, HTTP_TIMEOUT_SECONDS, PRODUCT_NAME


class CashLeg(BaseModel):
    """The claims panel 11 can actually read from the endpoint."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    scenario: str = Field(min_length=1)
    boundary: str = Field(min_length=1)
    funding_disposition: str = Field(min_length=1)
    funding_headline: str = Field(min_length=1)
    rail: str = Field(min_length=1)
    finality_class: str = Field(min_length=1)
    decision: str = Field(min_length=1)
    net_debit_cap_headroom: str = Field(min_length=1)


class CashLegSource(Protocol):
    def cash_leg(self) -> CashLeg: ...


def _required_mapping(stage: object, name: str) -> dict[str, Any]:
    if not isinstance(stage, dict):
        raise SourceMalformedError(f"Cash-leg {name} stage is not an object")
    detail = stage.get("detail")
    if not isinstance(detail, dict):
        raise SourceFieldAbsentError(f"Cash-leg {name} stage has no detail object")
    return detail


def parse_cash_leg(raw: bytes) -> CashLeg:
    """Parse only published claims; absent clocks and cutoff remain absent in the view."""
    try:
        data = json.loads(raw)
    except ValueError:
        raise SourceMalformedError("Cash-leg document is not valid JSON") from None
    if not isinstance(data, dict):
        raise SourceMalformedError("Cash-leg document is not a JSON object")
    if data.get("status") != "ok":
        raise SourceMalformedError(f"Cash-leg document status is {data.get('status')!r}, not 'ok'")
    stages = data.get("stages")
    if not isinstance(stages, list):
        raise SourceFieldAbsentError("Cash-leg document has no stages list")
    by_number: dict[str, dict[str, Any]] = {}
    for stage in stages:
        if isinstance(stage, dict) and isinstance(stage.get("stage"), str):
            by_number[stage["stage"].split(".", 1)[0]] = stage
    if "1" not in by_number or "2" not in by_number:
        raise SourceFieldAbsentError("Cash-leg document lacks funding or CATO-F stage")
    funding = _required_mapping(by_number["1"], "funding")
    routing = _required_mapping(by_number["2"], "CATO-F")
    try:
        return CashLeg.model_validate(
            {
                "scenario": data.get("scenario"),
                "boundary": data.get("boundary"),
                "funding_disposition": funding.get("disposition"),
                "funding_headline": by_number["1"].get("headline"),
                "rail": routing.get("recommended_rail"),
                "finality_class": routing.get("finality_class"),
                "decision": routing.get("decision"),
                "net_debit_cap_headroom": funding.get("net_debit_cap_headroom"),
            }
        )
    except ValidationError as exc:
        raise SourceMalformedError(
            f"Unexpected cash-leg document shape ({exc.error_count()} problems)"
        ) from None


class HttpxCashLegClient:
    def __init__(self, http: httpx.Client | None = None, url: str = AUREON_CASH_LEG_URL) -> None:
        self._http = http or httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)
        self._url = url

    def cash_leg(self) -> CashLeg:
        return parse_cash_leg(
            get_bytes(
                self._http,
                self._url,
                headers={"Accept": "application/json", "User-Agent": PRODUCT_NAME},
            )
        )
