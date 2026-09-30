"""Independent synthetic whole-lifecycle outcomes for BB1."""

from __future__ import annotations

from typing import Literal

from cannae_kernel.canonical import canonical_bytes_of, digest
from cannae_kernel.provenance import Provenance
from pydantic import BaseModel, ConfigDict, Field

__all__ = ["CounterfactualMarketInput", "emit_outcomes"]


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class CounterfactualMarketInput(_Record):
    schema_version: Literal["bb1.market_input/1"] = "bb1.market_input/1"
    scenario_id: str = Field(min_length=1)
    sealed_intent_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    candidate_ids: tuple[str, str]

    def canonical_bytes(self) -> bytes:
        return canonical_bytes_of(self)


def emit_outcomes(market: CounterfactualMarketInput) -> bytes:
    """Emit scenario facts without receiving or observing an execution policy."""
    candidates = (
        {
            "candidate_id": market.candidate_ids[0],
            "execution_price": "99.40",
            "funding_peak": "9950",
            "collateral_requirement": "750",
            "netting_result": "9950",
            "failure_probability": "0.08",
            "finality_delay_seconds": 900,
        },
        {
            "candidate_id": market.candidate_ids[1],
            "execution_price": "99.55",
            "funding_peak": "8000",
            "collateral_requirement": "500",
            "netting_result": "8000",
            "failure_probability": "0.02",
            "finality_delay_seconds": 120,
        },
    )
    document = {
        "schema_version": "bb1.outcomes/1",
        "scenario_digest": digest(market),
        "provenance": Provenance.FACT_SYNTHETIC,
        "candidates": candidates,
    }
    return canonical_bytes_of(document)
