"""Fail-closed retail recommendation profile input gate.

This gate models only the presence and currency of a profile input. It does not
judge best interest or the substance of a recommendation.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal, Protocol

from cannae_kernel.account import AccountStatus
from cannae_kernel.signed_attestation import (
    AttestationRefusal,
    AttestationVerificationError,
    verify_attestation,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lc.events import OrderTerms
from lc.trust_anchors import (
    TrustAnchorError,
    TrustAnchorRefusal,
    require_anchor_valid_at,
    resolve_pinned_anchor,
)

PROFILE_COMPLETENESS_FIELDS = (
    "employment_status",
    "annual_income_band",
    "net_worth_band",
    "liquid_net_worth_band",
    "tax_status",
    "investment_objectives",
    "risk_tolerance",
    "time_horizon",
    "liquidity_needs",
    "investment_experience",
)
ACCOUNT_STATUS_MAXIMUM_AGE = timedelta(seconds=300)
SYNTHETIC_SIGNATURE_SCOPE: Literal[
    "synthetic mode: signatures authenticate within one run only"
] = "synthetic mode: signatures authenticate within one run only"


class RecommendationRefusal(StrEnum):
    ACCOUNT_STATUS_MISSING = "RECOMMENDATION_ACCOUNT_STATUS_MISSING"
    ACCOUNT_STATUS_TAMPERED = "RECOMMENDATION_ACCOUNT_STATUS_TAMPERED"
    ACCOUNT_STATUS_UNREADABLE = "RECOMMENDATION_ACCOUNT_STATUS_UNREADABLE"
    ACCOUNT_STATUS_STALE = "RECOMMENDATION_ACCOUNT_STATUS_STALE"
    ACCOUNT_STATUS_IN_FUTURE = "RECOMMENDATION_ACCOUNT_STATUS_IN_FUTURE"
    ACCOUNT_STATUS_SIGNING_KEY_UNKNOWN = "RECOMMENDATION_ACCOUNT_STATUS_SIGNING_KEY_UNKNOWN"
    ACCOUNT_STATUS_SIGNING_KEY_REVOKED = "RECOMMENDATION_ACCOUNT_STATUS_SIGNING_KEY_REVOKED"
    ACCOUNT_STATUS_SIGNING_KEY_OUTSIDE_VALIDITY = (
        "RECOMMENDATION_ACCOUNT_STATUS_SIGNING_KEY_OUTSIDE_VALIDITY"
    )
    ACCOUNT_STATUS_PRODUCER_MISMATCH = "RECOMMENDATION_ACCOUNT_STATUS_PRODUCER_MISMATCH"
    ACCOUNT_STATUS_SIGNATURE_INVALID = "RECOMMENDATION_ACCOUNT_STATUS_SIGNATURE_INVALID"
    ACCOUNT_TYPE_UNKNOWN = "RECOMMENDATION_ACCOUNT_TYPE_UNKNOWN"
    ORDER_TERMS_MISSING = "RECOMMENDATION_ORDER_TERMS_MISSING"
    PROFILE_MISSING = "RECOMMENDATION_PROFILE_MISSING"
    PROFILE_INCOMPLETE = "RECOMMENDATION_PROFILE_INCOMPLETE"
    PROFILE_STALE = "RECOMMENDATION_PROFILE_STALE"
    PROFILE_ACCOUNT_MISMATCH = "RECOMMENDATION_PROFILE_ACCOUNT_MISMATCH"
    RECOMMENDATION_STATE_REQUIRED = "RECOMMENDATION_STATE_REQUIRED"


class RecommendationRefusedError(ValueError):
    def __init__(self, reason: RecommendationRefusal) -> None:
        self.reason = reason
        super().__init__(reason.value)


class CustomerProfileEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    record_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    employment_status: str | None
    annual_income_band: str | None
    net_worth_band: str | None
    liquid_net_worth_band: str | None
    tax_status: str | None
    investment_objectives: tuple[str, ...]
    risk_tolerance: str | None
    time_horizon: str | None
    liquidity_needs: str | None
    investment_experience: tuple[str, ...]
    verified_at: datetime
    maximum_age_seconds: int = Field(gt=0)
    source: str = Field(pattern=r"^SYNTHETIC_[A-Z0-9_]+$")

    @property
    def complete(self) -> bool:
        return all(getattr(self, name) for name in PROFILE_COMPLETENESS_FIELDS)


class AccountType(StrEnum):
    INSTITUTIONAL = "INSTITUTIONAL"
    NATURAL_PERSON = "NATURAL_PERSON"


class AccountTypeStatusEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    account_id: str
    account_type: AccountType
    status: AccountStatus
    last_event_id: str


class AccountTypeStatusDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    schema_version: Literal["atreides.account_type_status/1"]
    as_of: datetime
    maximum_age_seconds: int
    accounts: tuple[AccountTypeStatusEntry, ...]
    signature_scope: Literal["synthetic mode: signatures authenticate within one run only"] = (
        SYNTHETIC_SIGNATURE_SCOPE
    )


def read_account_status_document(
    raw: bytes | None,
    *,
    evaluated_at: datetime,
) -> AccountTypeStatusDocument:
    if raw is None:
        raise RecommendationRefusedError(RecommendationRefusal.ACCOUNT_STATUS_MISSING)
    try:
        anchor, registry = resolve_pinned_anchor(raw, expected_producer="atreides")
        payload = verify_attestation(raw, expected_producer="atreides", registry=registry)
        document = AccountTypeStatusDocument.model_validate_json(payload, strict=True)
        require_anchor_valid_at(anchor, evaluated_at)
    except TrustAnchorError as exc:
        trust_refusals = {
            TrustAnchorRefusal.UNKNOWN_KEY: (
                RecommendationRefusal.ACCOUNT_STATUS_SIGNING_KEY_UNKNOWN
            ),
            TrustAnchorRefusal.WRONG_PRODUCER: (
                RecommendationRefusal.ACCOUNT_STATUS_PRODUCER_MISMATCH
            ),
            TrustAnchorRefusal.REVOKED: (RecommendationRefusal.ACCOUNT_STATUS_SIGNING_KEY_REVOKED),
            TrustAnchorRefusal.OUTSIDE_VALIDITY: (
                RecommendationRefusal.ACCOUNT_STATUS_SIGNING_KEY_OUTSIDE_VALIDITY
            ),
            TrustAnchorRefusal.MALFORMED: RecommendationRefusal.ACCOUNT_STATUS_UNREADABLE,
        }
        raise RecommendationRefusedError(trust_refusals[exc.refusal]) from exc
    except AttestationVerificationError as exc:
        attestation_refusals = {
            AttestationRefusal.UNKNOWN_KEY: (
                RecommendationRefusal.ACCOUNT_STATUS_SIGNING_KEY_UNKNOWN
            ),
            AttestationRefusal.WRONG_PRODUCER: (
                RecommendationRefusal.ACCOUNT_STATUS_PRODUCER_MISMATCH
            ),
            AttestationRefusal.INVALID_SIGNATURE: (
                RecommendationRefusal.ACCOUNT_STATUS_SIGNATURE_INVALID
            ),
            AttestationRefusal.MALFORMED: RecommendationRefusal.ACCOUNT_STATUS_UNREADABLE,
        }
        raise RecommendationRefusedError(attestation_refusals[exc.refusal]) from exc
    except ValidationError as exc:
        raise RecommendationRefusedError(RecommendationRefusal.ACCOUNT_STATUS_UNREADABLE) from exc
    if document.as_of > evaluated_at:
        raise RecommendationRefusedError(RecommendationRefusal.ACCOUNT_STATUS_IN_FUTURE)
    if evaluated_at - document.as_of > ACCOUNT_STATUS_MAXIMUM_AGE:
        raise RecommendationRefusedError(RecommendationRefusal.ACCOUNT_STATUS_STALE)
    return document


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
    account_status_input: bytes | None,
) -> None:
    if terms.recommended is False:
        return
    if evaluated_at is None:
        raise RecommendationRefusedError(RecommendationRefusal.ACCOUNT_STATUS_MISSING)
    document = read_account_status_document(
        account_status_input,
        evaluated_at=evaluated_at,
    )
    account = next((row for row in document.accounts if row.account_id == terms.account_id), None)
    if account is None:
        raise RecommendationRefusedError(RecommendationRefusal.ACCOUNT_TYPE_UNKNOWN)
    if account.account_type is not AccountType.NATURAL_PERSON:
        return
    if terms.recommended is None:
        raise RecommendationRefusedError(RecommendationRefusal.RECOMMENDATION_STATE_REQUIRED)
    if resolver is None or evaluated_at is None or terms.customer_profile_record_id is None:
        raise RecommendationRefusedError(RecommendationRefusal.PROFILE_MISSING)
    profile = resolver.resolve_profile(terms.customer_profile_record_id)
    if profile.account_id != terms.account_id:
        raise RecommendationRefusedError(RecommendationRefusal.PROFILE_ACCOUNT_MISMATCH)
    if not profile.complete:
        raise RecommendationRefusedError(RecommendationRefusal.PROFILE_INCOMPLETE)
    if (evaluated_at - profile.verified_at).total_seconds() > profile.maximum_age_seconds:
        raise RecommendationRefusedError(RecommendationRefusal.PROFILE_STALE)
