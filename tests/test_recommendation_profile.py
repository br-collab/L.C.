from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from cannae_kernel.ids import OrderId
from test_lifecycle import NOW, _accepted, _actor, _event, _id

from lc.events import OrderTerms, OrderType, TimeInForce
from lc.lifecycle import ChildOrder, IntentOutcome, split_parent
from lc.recommendation_profile import (
    CustomerProfileEvidence,
    MappingRecommendationProfileResolver,
    RecommendationRefusedError,
)

PROFILE_ID = "SYN-PROFILE-RETAIL1"


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
        account_type="RETAIL",
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
                complete=complete,
                verified_at=verified_at,
                maximum_age_seconds=300,
                source="SYNTHETIC_ATREIDES_PROFILE",
            )
        }
    )


def test_recommended_retail_order_with_missing_profile_is_refused_by_name() -> None:
    with pytest.raises(RecommendationRefusedError, match="RECOMMENDATION_PROFILE_MISSING"):
        split_parent(
            _active_outcome(),
            actor=_actor(),
            children=(_child(_terms(recommended=True, profile_id=None)),),
            recommendation_profile_resolver=_resolver(),
            evaluated_at=NOW,
        )


def test_recommended_retail_order_with_stale_profile_is_refused_by_name() -> None:
    with pytest.raises(RecommendationRefusedError, match="RECOMMENDATION_PROFILE_STALE"):
        split_parent(
            _active_outcome(),
            actor=_actor(),
            children=(_child(_terms(recommended=True)),),
            recommendation_profile_resolver=_resolver(verified_at=NOW - timedelta(seconds=301)),
            evaluated_at=NOW,
        )


def test_recommended_retail_order_with_current_complete_profile_is_admitted() -> None:
    outcome = split_parent(
        _active_outcome(),
        actor=_actor(),
        children=(_child(_terms(recommended=True)),),
        recommendation_profile_resolver=_resolver(),
        evaluated_at=NOW,
    )
    assert outcome.journal.events[-1].payload.terms == _terms(recommended=True)


def test_non_recommended_retail_order_is_not_blocked_by_profile_gate() -> None:
    outcome = split_parent(
        _active_outcome(),
        actor=_actor(),
        children=(_child(_terms(recommended=False, profile_id=None)),),
    )
    assert outcome.journal.events[-1].payload.terms == _terms(
        recommended=False, profile_id=None
    )
