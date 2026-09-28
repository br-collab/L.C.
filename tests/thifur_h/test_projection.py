"""The projection keeps forecast inputs separate from realised outcomes."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from h_fixtures import AT, outcome, projection
from pydantic import ValidationError

from thifur_h.projection import FundingProjection, RealisedOutcome


class TestFundingProjectionArithmetic:
    def test_shortfall_is_the_uncovered_amount(self) -> None:
        assert projection().shortfall == Decimal("750000")

    def test_an_overfunded_position_has_a_negative_shortfall(self) -> None:
        assert projection(committed_position=Decimal("1100000")).shortfall == Decimal("-100000")

    def test_an_inflow_at_the_window_boundary_is_in_time(self) -> None:
        value = projection(expected_inflow_in_seconds=7200)
        assert value.inflow_arrives_in_window is True

    def test_an_inflow_one_second_late_is_not_in_time(self) -> None:
        value = projection(expected_inflow_in_seconds=7201)
        assert value.inflow_arrives_in_window is False

    def test_an_exact_inflow_in_time_covers_the_shortfall(self) -> None:
        assert projection(expected_inflow_amount=Decimal("750000")).inflow_covers_shortfall is True

    @pytest.mark.parametrize(
        ("amount", "seconds"),
        [(Decimal("749999.99"), 1), (Decimal("750000"), 7201)],
    )
    def test_amount_and_time_are_both_required(self, amount: Decimal, seconds: int) -> None:
        value = projection(expected_inflow_amount=amount, expected_inflow_in_seconds=seconds)
        assert value.inflow_covers_shortfall is False


class TestTheTwoSidesStaySeparate:
    def test_projection_carries_no_realised_answer(self) -> None:
        assert set(FundingProjection.model_fields).isdisjoint(
            {"outcome", "settled_within_window", "path_taken"}
        )

    def test_realised_outcome_carries_no_forecast_inputs(self) -> None:
        assert set(RealisedOutcome.model_fields).isdisjoint(
            {"projected_at", "expected_inflow_amount", "expected_inflow_in_seconds"}
        )

    def test_the_types_share_only_the_lifecycle_identifier(self) -> None:
        assert set(FundingProjection.model_fields) & set(RealisedOutcome.model_fields) == {
            "lifecycle_id"
        }

    def test_realised_outcome_rejects_projection_fields(self) -> None:
        fields = outcome().model_dump()
        fields["projected_at"] = AT
        with pytest.raises(ValidationError, match="projected_at"):
            RealisedOutcome(**fields)


class TestValidation:
    @pytest.mark.parametrize("field", ["window_closes_in_seconds", "expected_inflow_in_seconds"])
    def test_durations_cannot_be_negative(self, field: str) -> None:
        with pytest.raises(ValidationError):
            projection(**{field: -1})

    def test_projection_requires_an_approved_path(self) -> None:
        with pytest.raises(ValidationError):
            projection(approved_paths=())

    def test_projection_time_must_be_timezone_aware(self) -> None:
        with pytest.raises(ValidationError):
            projection(projected_at=(AT + timedelta(hours=1)).replace(tzinfo=None))

    def test_realised_outcome_cannot_be_blank(self) -> None:
        with pytest.raises(ValidationError):
            outcome(outcome=" ")

    def test_realised_path_cannot_be_empty(self) -> None:
        with pytest.raises(ValidationError):
            outcome(path_taken="")
