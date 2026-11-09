from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256

import pytest
from cannae_kernel.account import AccountStatus
from cannae_kernel.canonical import canonical_bytes, digest_bytes
from cannae_kernel.ids import OrderId
from test_lifecycle import (
    NOW,
    _accepted,
    _actor,
    _envelope,
    _event,
    _id,
    _payload,
    _wire_bytes,
)

from lc.events import OrderTerms, OrderType, TimeInForce
from lc.lifecycle import ChildOrder, IntentOutcome, accept_intent, split_parent
from lc.recommendation_profile import (
    PROFILE_COMPLETENESS_FIELDS,
    AccountType,
    AccountTypeStatusDocument,
    AccountTypeStatusEntry,
    AttestedAccountTypeStatusDocument,
    CustomerProfileEvidence,
    MappingRecommendationProfileResolver,
    RecommendationRefusedError,
)

PROFILE_ID = "SYN-PROFILE-RETAIL1"


def test_profile_completeness_contract_checksum() -> None:
    assert sha256(",".join(PROFILE_COMPLETENESS_FIELDS).encode()).hexdigest() == (
        "c51ad6bae415031695470dec96af86bbd6e54f8c2d9b52ec67e150083d580744"
    )


def _terms(*, recommended: bool, profile_id: str | None = PROFILE_ID) -> OrderTerms:
    return OrderTerms(
        account_id="SYN-ACCOUNT-RETAIL1",
        strategy_id="SYN-STRATEGY-RETAIL1",
        issue="SYNTHETIC-ISSUE-1",
        side="BUY",
        order_type=OrderType.LIMIT,
        time_in_force=TimeInForce.DAY,
        control_price=Decimal("100"),
        limit_price=Decimal("100"),
        recommended=recommended,
        customer_profile_record_id=profile_id,
    )


def _active_outcome() -> IntentOutcome:
    return _accepted()


def _child(terms: OrderTerms) -> ChildOrder:
    return ChildOrder(
        order_id=OrderId(_id("ord_", 13)),
        quantity=Decimal("100"),
        event=_event(13),
        terms=terms,
    )


def _resolver(
    *,
    complete: bool = True,
    verified_at: datetime = NOW,
    account_id: str = "SYN-ACCOUNT-RETAIL1",
) -> MappingRecommendationProfileResolver:
    return MappingRecommendationProfileResolver(
        {
            PROFILE_ID: CustomerProfileEvidence(
                record_id=PROFILE_ID,
                account_id=account_id,
                employment_status="SYNTHETIC_EMPLOYED" if complete else None,
                annual_income_band="SYNTHETIC_A",
                net_worth_band="SYNTHETIC_B",
                liquid_net_worth_band="SYNTHETIC_C",
                tax_status="SYNTHETIC_STATUS",
                investment_objectives=("SYNTHETIC_GROWTH",),
                risk_tolerance="SYNTHETIC_MODERATE",
                time_horizon="SYNTHETIC_LONG",
                liquidity_needs="SYNTHETIC_LOW",
                investment_experience=("SYNTHETIC_EQUITIES",),
                verified_at=verified_at,
                maximum_age_seconds=300,
                source="SYNTHETIC_ATREIDES_PROFILE",
            )
        }
    )


def _status(account_type: AccountType = AccountType.NATURAL_PERSON) -> bytes:
    payload = canonical_bytes(
        AccountTypeStatusDocument(
            schema_version="atreides.account_type_status/1",
            as_of=NOW,
            maximum_age_seconds=300,
            accounts=(
                AccountTypeStatusEntry(
                    account_id="SYN-ACCOUNT-RETAIL1",
                    account_type=account_type,
                    status=AccountStatus.ACTIVE,
                    last_event_id="SYN-ACCOUNT-EVENT-1",
                ),
            ),
        )
    )
    return (
        AttestedAccountTypeStatusDocument(
            schema_version="atreides.attested_account_type_status/1",
            payload_digest=digest_bytes(payload),
            payload=payload.decode(),
        )
        .model_dump_json()
        .encode()
    )


def test_recommended_retail_order_with_missing_profile_is_refused_by_name() -> None:
    with pytest.raises(RecommendationRefusedError, match="RECOMMENDATION_PROFILE_MISSING"):
        split_parent(
            _active_outcome(),
            actor=_actor(),
            children=(_child(_terms(recommended=True, profile_id=None)),),
            recommendation_profile_resolver=_resolver(),
            evaluated_at=NOW,
            account_status_input=_status(),
        )


def test_recommended_retail_order_with_stale_profile_is_refused_by_name() -> None:
    with pytest.raises(RecommendationRefusedError, match="RECOMMENDATION_PROFILE_STALE"):
        split_parent(
            _active_outcome(),
            actor=_actor(),
            children=(_child(_terms(recommended=True)),),
            recommendation_profile_resolver=_resolver(verified_at=NOW - timedelta(seconds=301)),
            evaluated_at=NOW,
            account_status_input=_status(),
        )


def test_recommended_retail_order_with_current_complete_profile_is_admitted() -> None:
    outcome = split_parent(
        _active_outcome(),
        actor=_actor(),
        children=(_child(_terms(recommended=True)),),
        recommendation_profile_resolver=_resolver(),
        evaluated_at=NOW,
        account_status_input=_status(),
    )
    assert outcome.journal.events[-1].payload.terms == _terms(recommended=True)


@pytest.mark.parametrize(
    ("complete", "account_id", "refusal"),
    [
        (False, "SYN-ACCOUNT-RETAIL1", "RECOMMENDATION_PROFILE_INCOMPLETE"),
        (True, "SYN-ACCOUNT-OTHER", "RECOMMENDATION_PROFILE_ACCOUNT_MISMATCH"),
    ],
)
def test_profile_content_and_account_binding_are_enforced(
    complete: bool, account_id: str, refusal: str
) -> None:
    with pytest.raises(RecommendationRefusedError, match=refusal):
        split_parent(
            _active_outcome(),
            actor=_actor(),
            children=(_child(_terms(recommended=True)),),
            recommendation_profile_resolver=_resolver(complete=complete, account_id=account_id),
            evaluated_at=NOW,
            account_status_input=_status(),
        )


def test_account_type_comes_from_atreides_status_document() -> None:
    outcome = split_parent(
        _active_outcome(),
        actor=_actor(),
        children=(_child(_terms(recommended=True, profile_id=None)),),
        evaluated_at=NOW,
        account_status_input=_status(AccountType.INSTITUTIONAL),
    )
    assert outcome.journal.events[-1].payload.terms is not None


def test_termless_child_is_refused_by_name() -> None:
    with pytest.raises(RecommendationRefusedError, match="RECOMMENDATION_ORDER_TERMS_MISSING"):
        split_parent(
            _active_outcome(),
            actor=_actor(),
            children=(
                ChildOrder(
                    order_id=OrderId(_id("ord_", 14)),
                    quantity=Decimal(100),
                    event=_event(14),
                ),
            ),
        )


def test_intent_acceptance_runs_recommendation_gate() -> None:
    payload = _payload()
    outcome = accept_intent(
        _envelope(payload),
        _wire_bytes(payload),
        parent_order_id=OrderId(_id("ord_", 15)),
        actor=_actor(),
        event=_event(15),
        terms=_terms(recommended=True),
        recommendation_profile_resolver=_resolver(),
        account_status_input=_status(),
        evaluated_at=NOW,
    )
    assert outcome.journal.events[-1].payload.terms == _terms(recommended=True)


def test_non_recommended_retail_order_is_not_blocked_by_profile_gate() -> None:
    outcome = split_parent(
        _active_outcome(),
        actor=_actor(),
        children=(_child(_terms(recommended=False, profile_id=None)),),
    )
    assert outcome.journal.events[-1].payload.terms == _terms(recommended=False, profile_id=None)
