"""Fail-closed retail recommendation profile input gate.

This gate models only the presence and currency of a profile input. It does not
judge best interest or the substance of a recommendation.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from lc.events import OrderTerms


class RecommendationRefusal(StrEnum):
    PROFILE_MISSING = "RECOMMENDATION_PROFILE_MISSING"
    PROFILE_INCOMPLETE = "RECOMMENDATION_PROFILE_INCOMPLETE"
    PROFILE_STALE = "RECOMMENDATION_PROFILE_STALE"
    PROFILE_ACCOUNT_MISMATCH = "RECOMMENDATION_PROFILE_ACCOUNT_MISMATCH"


class RecommendationRefusedError(ValueError):
    def __init__(self, reason: RecommendationRefusal) -> None:
        self.reason = reason
        super().__init__(reason.value)


class CustomerProfileEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    record_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    complete: bool
    verified_at: datetime
    maximum_age_seconds: int = Field(gt=0)
    source: str = Field(pattern=r"^SYNTHETIC_[A-Z0-9_]+$")


class RecommendationProfileResolver(Protocol):
    def resolve_profile(self, record_id: str) -> CustomerProfileEvidence: ...


class MappingRecommendationProfileResolver:
    def __init__(self, records: Mapping[str, CustomerProfileEvidence]) -> None:
        self._records = records

    def resolve_profile(self, record_id: str) -> CustomerProfileEvidence:
        record = self._records.get(record_id)
        if record is None:
            raise RecommendationRefusedError(RecommendationRefusal.PROFILE_MISSING)
        return record


def enforce_recommendation_profile(
    terms: OrderTerms,
    *,
    resolver: RecommendationProfileResolver | None,
    evaluated_at: datetime | None,
) -> None:
    if not terms.recommended or terms.account_type != "RETAIL":
        return
    if resolver is None or evaluated_at is None or terms.customer_profile_record_id is None:
        raise RecommendationRefusedError(RecommendationRefusal.PROFILE_MISSING)
    profile = resolver.resolve_profile(terms.customer_profile_record_id)
    if profile.account_id != terms.account_id:
        raise RecommendationRefusedError(RecommendationRefusal.PROFILE_ACCOUNT_MISMATCH)
    if not profile.complete:
        raise RecommendationRefusedError(RecommendationRefusal.PROFILE_INCOMPLETE)
    if (evaluated_at - profile.verified_at).total_seconds() > profile.maximum_age_seconds:
        raise RecommendationRefusedError(RecommendationRefusal.PROFILE_STALE)
