"""Condition A: the deterministic baseline every later condition is measured against."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from cannae_kernel.absence import Absent, Recorded
from cannae_kernel.canonical import canonical_bytes
from cannae_kernel.provenance import Provenance
from cannae_kernel.recommendation import Recommendation
from h_fixtures import AT, projection
from pydantic import ValidationError

from thifur_h.baseline import CONDITION_A, FUNDED, WILL_FAIL, WILL_QUEUE, recommend


def _outcome(rec: Recommendation) -> str:
    assert isinstance(rec.funding_distribution, Recorded)
    return str(rec.funding_distribution.value.outcomes[0].outcome)


class TestTheThreeCases:
    def test_a_covered_position_is_funded(self) -> None:
        rec = recommend(projection(committed_position=Decimal("1000000")))
        assert _outcome(rec) == FUNDED
        assert isinstance(rec.expected_queue_seconds, Recorded)
        assert rec.expected_queue_seconds.value == 0

    def test_a_short_position_with_inflow_in_time_queues(self) -> None:
        rec = recommend(projection())
        assert _outcome(rec) == WILL_QUEUE
        assert isinstance(rec.expected_queue_seconds, Recorded)
        assert rec.expected_queue_seconds.value == 5400

    def test_an_inflow_that_arrives_late_fails(self) -> None:
        assert _outcome(recommend(projection(expected_inflow_in_seconds=9999))) == WILL_FAIL

    def test_an_inflow_too_small_fails_even_if_it_is_early(self) -> None:
        """Both halves matter. Treating either as sufficient is how a will_fail
        gets reported as a will_queue."""
        rec = recommend(
            projection(expected_inflow_amount=Decimal("1"), expected_inflow_in_seconds=1)
        )
        assert _outcome(rec) == WILL_FAIL

    def test_a_short_position_on_a_net_rail_does_not_queue(self) -> None:
        """Queuing is a gross-final property. A net rail that is short fails."""
        assert _outcome(recommend(projection(gross_final=False))) == WILL_FAIL

    def test_a_failing_leg_has_no_queue_duration(self) -> None:
        """A duration for something that never settles is a category error, and
        reporting one invites an operator to wait for it."""
        rec = recommend(projection(expected_inflow_in_seconds=9999))
        assert isinstance(rec.expected_queue_seconds, Absent)
        assert "no queue duration" in rec.expected_queue_seconds.reason


class TestItIsDeterministic:
    def test_the_same_projection_produces_the_same_bytes(self) -> None:
        p = projection()
        assert canonical_bytes(recommend(p)) == canonical_bytes(recommend(p))

    def test_it_reads_no_clock(self) -> None:
        """Stamped from the projection, so a replay reproduces it exactly."""
        rec = recommend(projection())
        assert rec.issued_at == AT

    def test_a_different_projection_time_changes_only_the_stamp(self) -> None:
        later = projection(projected_at=AT + timedelta(hours=1))
        assert recommend(later).issued_at == AT + timedelta(hours=1)
        assert _outcome(recommend(later)) == _outcome(recommend(projection()))

    def test_no_state_is_carried_between_calls(self) -> None:
        first = recommend(projection(committed_position=Decimal("1000000")))
        recommend(projection(gross_final=False))
        assert canonical_bytes(recommend(projection(committed_position=Decimal("1000000")))) == (
            canonical_bytes(first)
        )


class TestWhatItAbstainsFrom:
    """It answers what the projection answers and says so about the rest."""

    @pytest.mark.parametrize("field", ["window_miss_probability", "peak_liquidity", "regime"])
    def test_it_abstains_with_a_reason(self, field: str) -> None:
        value = getattr(recommend(projection()), field)
        assert isinstance(value, Absent)
        assert len(value.reason) > 20

    def test_it_does_not_emit_a_number_it_does_not_have(self) -> None:
        """It could emit 0.5 everywhere and score middling on calibration.
        Abstention makes the gap part of the score instead of hiding it."""
        rec = recommend(projection())
        assert isinstance(rec.window_miss_probability, Absent)


class TestItObeysTheContract:
    def test_it_is_a_forecast(self) -> None:
        assert recommend(projection()).provenance is Provenance.FORECAST

    def test_it_states_its_confidence(self) -> None:
        rec = recommend(projection())
        assert isinstance(rec.confidence, Recorded)
        assert rec.confidence.value == Decimal("1")

    def test_it_ranks_only_approved_paths(self) -> None:
        rec = recommend(projection(approved_paths=("fedwire",)))
        assert rec.ranked_paths == ("fedwire",)

    def test_it_does_not_reorder_what_it_has_no_opinion_about(self) -> None:
        """Inventing a preference it cannot justify is the unearned confidence
        this experiment exists to measure."""
        paths = ("chips", "fedwire", "ach")
        assert recommend(projection(approved_paths=paths)).ranked_paths == paths

    def test_it_names_which_condition_produced_it(self) -> None:
        assert recommend(projection()).model_ref == CONDITION_A

    def test_its_handoff_basis_is_absent_with_a_reason(self) -> None:
        basis = recommend(projection()).c2_handoff
        assert isinstance(basis, Absent)
        assert basis.reason == "operator-direct under CAOM-001"

    def test_a_projection_needs_at_least_one_approved_path(self) -> None:
        with pytest.raises(ValidationError):
            projection(approved_paths=())
