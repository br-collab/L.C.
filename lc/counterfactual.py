"""BB1 policy comparison over one sealed synthetic market scenario."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal, Self

from cannae_kernel.canonical import digest_bytes
from cannae_kernel.provenance import Provenance
from pydantic import BaseModel, ConfigDict, Field, model_validator

from lc.policy import CounterfactualExecutionPolicy

MINIMUM_CANDIDATES = 2

__all__ = [
    "CandidateOutcome",
    "CounterfactualExperimentResult",
    "PolicySelection",
    "compare_policies",
    "parse_outcomes",
]


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class CandidateOutcome(_Record):
    candidate_id: str = Field(min_length=1)
    execution_price: Decimal = Field(gt=0)
    funding_peak: Decimal = Field(ge=0)
    collateral_requirement: Decimal = Field(ge=0)
    netting_result: Decimal
    failure_probability: Decimal = Field(ge=0, le=1)
    finality_delay_seconds: int = Field(ge=0)


class _OutcomeDocument(_Record):
    schema_version: Literal["bb1.outcomes/1"]
    scenario_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    provenance: Literal[Provenance.FACT_SYNTHETIC]
    candidates: tuple[CandidateOutcome, ...]

    @model_validator(mode="after")
    def _candidate_ids_are_unique(self) -> Self:
        identifiers = tuple(candidate.candidate_id for candidate in self.candidates)
        if len(identifiers) < MINIMUM_CANDIDATES or len(set(identifiers)) != len(identifiers):
            raise ValueError("counterfactual comparison requires distinct candidates")
        return self


class PolicySelection(_Record):
    policy_id: str
    policy_version: str
    selected_candidate_id: str
    outcome: CandidateOutcome


class CounterfactualExperimentResult(_Record):
    source_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    scenario_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    price_only: PolicySelection
    settlement_aware: PolicySelection
    differing_variables: tuple[str, ...]


def parse_outcomes(value: bytes) -> _OutcomeDocument:
    return _OutcomeDocument.model_validate_json(value, strict=True)


def _select(
    policy: CounterfactualExecutionPolicy, candidates: tuple[CandidateOutcome, ...]
) -> CandidateOutcome:
    if policy.objective == "PRICE_ONLY":
        return min(candidates, key=lambda item: (item.execution_price, item.candidate_id))
    return min(
        candidates,
        key=lambda item: (
            item.failure_probability,
            item.funding_peak,
            item.collateral_requirement,
            item.finality_delay_seconds,
            item.execution_price,
            item.candidate_id,
        ),
    )


def compare_policies(
    value: bytes,
    *,
    expected_scenario_digest: str,
    price_only: CounterfactualExecutionPolicy,
    settlement_aware: CounterfactualExecutionPolicy,
) -> CounterfactualExperimentResult:
    document = parse_outcomes(value)
    if document.scenario_digest != expected_scenario_digest:
        raise ValueError("counterfactual outcomes belong to another scenario")
    if price_only.objective != "PRICE_ONLY" or settlement_aware.objective != "SETTLEMENT_AWARE":
        raise ValueError("BB1 requires one price-only and one settlement-aware policy")
    price_choice = _select(price_only, document.candidates)
    settlement_choice = _select(settlement_aware, document.candidates)
    fields = tuple(
        name
        for name in CandidateOutcome.model_fields
        if name != "candidate_id"
        and getattr(price_choice, name) != getattr(settlement_choice, name)
    )
    return CounterfactualExperimentResult(
        source_digest=digest_bytes(value),
        scenario_digest=document.scenario_digest,
        price_only=PolicySelection(
            policy_id=price_only.policy_id,
            policy_version=price_only.version,
            selected_candidate_id=price_choice.candidate_id,
            outcome=price_choice,
        ),
        settlement_aware=PolicySelection(
            policy_id=settlement_aware.policy_id,
            policy_version=settlement_aware.version,
            selected_candidate_id=settlement_choice.candidate_id,
            outcome=settlement_choice,
        ),
        differing_variables=fields,
    )
