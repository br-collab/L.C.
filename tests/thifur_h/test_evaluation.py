"""The harness that scores every condition identically — and refuses to be quoted alone.

The metrics are arithmetic. The part worth testing hardest is the degenerate
detection, because that is what stops a result being misread by whoever is
pleased with it.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import pytest
from cannae_kernel.absence import AbsenceKind, Absent, Recorded
from cannae_kernel.provenance import Provenance
from cannae_kernel.recommendation import (
    OutcomeProbability,
    ProbabilityDistribution,
    Recommendation,
)
from h_fixtures import HARMFUL, outcome, projection

from thifur_h.baseline import CONDITION_A, recommend
from thifur_h.evaluation import Condition, Evidence, Scorecard, score
from thifur_h.projection import FundingProjection, RealisedOutcome


def _fixed(**overrides: object) -> Condition:
    """A condition that always says the same thing. For testing the harness."""

    def condition(p: FundingProjection) -> Recommendation:
        fields: dict[str, object] = {
            "recommendation_id": f"REC-T-{p.lifecycle_id}",
            "lifecycle_id": p.lifecycle_id,
            "issued_at": p.projected_at,
            "provenance": Provenance.FORECAST,
            "c2_handoff": Absent(
                kind=AbsenceKind.NOTHING_RECORDED, reason="operator-direct under CAOM-001"
            ),
            "model_ref": "test-condition/1.0",
            "funding_distribution": Recorded[ProbabilityDistribution](
                value=ProbabilityDistribution(
                    outcomes=(OutcomeProbability(outcome="funded", probability=Decimal(1)),)
                )
            ),
            "expected_queue_seconds": Recorded[int](value=0),
            "window_miss_probability": Absent(kind=AbsenceKind.NOT_APPLICABLE, reason="test"),
            "peak_liquidity": Absent(kind=AbsenceKind.NOT_APPLICABLE, reason="test"),
            "regime": Absent(kind=AbsenceKind.NOT_APPLICABLE, reason="test"),
            "confidence": Recorded[Decimal](value=Decimal("1")),
        }
        fields.update(overrides)
        return Recommendation(**fields)  # type: ignore[arg-type]

    return condition


def _abstaining(p: FundingProjection) -> Recommendation:
    reason = Absent(kind=AbsenceKind.NOT_YET_KNOWN, reason="no history for this counterparty")
    return Recommendation(
        recommendation_id=f"REC-X-{p.lifecycle_id}",
        lifecycle_id=p.lifecycle_id,
        issued_at=p.projected_at,
        provenance=Provenance.FORECAST,
        c2_handoff=Absent(kind=AbsenceKind.NOTHING_RECORDED, reason="operator-direct"),
        model_ref="abstaining/1.0",
        funding_distribution=reason,
        expected_queue_seconds=reason,
        window_miss_probability=reason,
        peak_liquidity=reason,
        regime=reason,
        confidence=Absent(kind=AbsenceKind.NOT_YET_KNOWN, reason="nothing was estimated"),
    )


def _score(
    condition: Condition,
    cases: Sequence[tuple[FundingProjection, RealisedOutcome]],
    name: str = "test/1.0",
) -> Scorecard:
    return score(
        condition,
        name=name,
        evidence=Evidence(
            projections=[p for p, _ in cases],
            outcomes=[o for _, o in cases],
            latencies_ms=[1] * len(cases),
            harmful_outcomes=HARMFUL,
        ),
    )


QUEUES = (projection(1), outcome(1, outcome="will_queue"))
FAILS = (projection(2, expected_inflow_in_seconds=9999), outcome(2, outcome="will_fail"))
FUNDS = (projection(3, committed_position=Decimal("1000000")), outcome(3, outcome="funded"))


class TestConditionAScores:
    def test_the_baseline_can_be_scored_end_to_end(self) -> None:
        card = _score(recommend, [QUEUES, FAILS, FUNDS], name=CONDITION_A)
        assert card.condition == CONDITION_A
        assert card.cases == 3

    def test_a_deterministic_condition_reconstructs_perfectly(self) -> None:
        """Measured by re-running and comparing bytes, not asserted by a flag."""
        card = _score(recommend, [QUEUES, FAILS, FUNDS])
        assert card.reconstruction_rate == Decimal("1.0000")

    def test_the_baseline_answers_everything_it_is_asked(self) -> None:
        assert _score(recommend, [QUEUES, FAILS, FUNDS]).abstention_rate == Decimal("0.0000")

    def test_a_correct_baseline_has_no_error(self) -> None:
        card = _score(recommend, [QUEUES, FAILS, FUNDS])
        assert card.brier == Decimal("0.0000")
        assert card.unsafe_rate == Decimal("0.0000")


class TestTheTwoRatesAreTheExperiment:
    def test_a_harmful_outcome_called_benign_is_unsafe(self) -> None:
        """The condition says funded; the leg fails."""
        card = _score(_fixed(), [(projection(2), outcome(2, outcome="will_fail"))])
        assert card.unsafe_rate == Decimal("1.0000")

    def test_a_harm_predicted_that_did_not_occur_is_a_false_hold(self) -> None:
        holding = _fixed(
            funding_distribution=Recorded[ProbabilityDistribution](
                value=ProbabilityDistribution(
                    outcomes=(OutcomeProbability(outcome="will_fail", probability=Decimal(1)),)
                )
            )
        )
        card = _score(holding, [(projection(1), outcome(1, outcome="funded"))])
        assert card.false_hold_rate == Decimal("1.0000")

    def test_ranking_an_unapproved_path_is_unsafe_whatever_happened(self) -> None:
        """Thifur-J would refuse it. Measuring only after J is measuring J."""
        rogue = _fixed(ranked_paths=("a-rail-nobody-approved",))
        card = _score(rogue, [(projection(1), outcome(1, outcome="funded"))])
        assert card.unsafe_rate == Decimal("1.0000")

    def test_an_abstention_is_not_counted_as_a_wrong_answer(self) -> None:
        """It is counted separately, which is the point."""
        card = _score(_abstaining, [(projection(2), outcome(2, outcome="will_fail"))])
        assert card.unsafe_rate == Decimal("0.0000")
        assert card.abstention_rate == Decimal("1.0000")


class TestTheDegenerateStrategiesAreNamed:
    """A condition scores perfectly on one number by gaming it. The card says so."""

    def test_abstaining_from_everything_is_named(self) -> None:
        card = _score(_abstaining, [QUEUES, FAILS, FUNDS])
        assert card.unsafe_rate == Decimal("0.0000")
        assert not card.comparable
        assert any("abstained on all 3" in w for w in card.degenerate)
        assert any("not because it answered well" in w for w in card.degenerate)

    def test_holding_nothing_is_named(self) -> None:
        card = _score(_fixed(), [(projection(1), outcome(1, outcome="funded"))])
        assert any("holds nothing prevents nothing" in w for w in card.degenerate)

    def test_holding_everything_is_named(self) -> None:
        holding = _fixed(
            funding_distribution=Recorded[ProbabilityDistribution](
                value=ProbabilityDistribution(
                    outcomes=(OutcomeProbability(outcome="will_fail", probability=Decimal(1)),)
                )
            )
        )
        card = _score(holding, [(projection(2), outcome(2, outcome="will_fail"))])
        assert any("holds everything is useless" in w for w in card.degenerate)

    def test_a_warning_names_the_number_it_invalidates(self) -> None:
        """A warning that does not say which figure to stop trusting gets read
        as a caveat and skipped."""
        card = _score(_abstaining, [QUEUES, FAILS])
        joined = " ".join(card.degenerate)
        assert "unsafe rate" in joined and "false-hold rate" in joined

    def test_an_empty_set_invalidates_every_rate(self) -> None:
        card = _score(recommend, [])
        assert card.cases == 0
        assert any("zero by construction" in w for w in card.degenerate)

    def test_a_clean_card_carries_no_warnings_and_says_so(self) -> None:
        card = _score(recommend, [QUEUES, FAILS, FUNDS])
        assert card.degenerate == ()
        assert card.comparable


class TestScoringRefusesToMisPair:
    def test_mismatched_lengths_are_refused(self) -> None:
        with pytest.raises(ValueError, match="same cases"):
            score(
                recommend,
                name="x",
                evidence=Evidence(
                    projections=[projection(1)],
                    outcomes=[outcome(1), outcome(2)],
                    latencies_ms=[1],
                    harmful_outcomes=HARMFUL,
                ),
            )

    def test_a_recommendation_scored_against_the_wrong_outcome_is_refused(self) -> None:
        """The one error here that would produce a plausible number and no symptom."""
        with pytest.raises(ValueError, match="plausible number and no symptom"):
            score(
                recommend,
                name="x",
                evidence=Evidence(
                    projections=[projection(1)],
                    outcomes=[outcome(99)],
                    latencies_ms=[1],
                    harmful_outcomes=HARMFUL,
                ),
            )


class TestForecastErrorAndCalibration:
    def test_brier_counts_mass_on_outcomes_that_did_not_happen(self) -> None:
        """Scoring only the realised outcome would let a condition hedge for free."""
        hedged = _fixed(
            funding_distribution=Recorded[ProbabilityDistribution](
                value=ProbabilityDistribution(
                    outcomes=(
                        OutcomeProbability(outcome="funded", probability=Decimal("0.5")),
                        OutcomeProbability(outcome="will_fail", probability=Decimal("0.5")),
                    )
                )
            )
        )
        card = _score(hedged, [(projection(1), outcome(1, outcome="funded"))])
        assert card.brier == Decimal("0.5000")

    def test_an_outcome_absent_from_the_distribution_is_a_full_miss(self) -> None:
        """Silently scoring it as zero error would reward not considering the case."""
        card = _score(_fixed(), [(projection(1), outcome(1, outcome="cap_breach"))])
        assert card.brier > Decimal("1")

    def test_calibration_bins_cover_the_unit_interval(self) -> None:
        card = _score(recommend, [QUEUES, FAILS, FUNDS])
        assert len(card.calibration) == 5
        assert card.calibration[0].lower == Decimal(0)
        assert card.calibration[-1].upper == Decimal(1)

    def test_overconfidence_shows_as_a_positive_gap(self) -> None:
        """A condition can be right about direction and wrong about how sure it
        was. For a system where a human reads the number, that is the costly one."""
        card = _score(_fixed(), [(projection(1), outcome(1, outcome="will_fail"))])
        assert card.max_calibration_gap > Decimal(0)


class TestLatencyIsSuppliedNotMeasured:
    def test_percentiles_come_from_the_caller(self) -> None:
        card = score(
            recommend,
            name="x",
            evidence=Evidence(
                projections=[projection(i) for i in range(1, 5)],
                outcomes=[outcome(i, outcome="will_queue") for i in range(1, 5)],
                latencies_ms=[1, 2, 3, 100],
                harmful_outcomes=HARMFUL,
            ),
        )
        assert card.latency_p50_ms == 2
        assert card.latency_p95_ms == 100

    def test_a_scorecard_is_a_function_of_its_inputs(self) -> None:
        """A harness that timed itself would produce a different card each run,
        and two conditions measured on different afternoons would not compare."""
        first = _score(recommend, [QUEUES, FAILS, FUNDS])
        second = _score(recommend, [QUEUES, FAILS, FUNDS])
        assert first == second

    def test_the_card_round_trips(self) -> None:
        card = _score(recommend, [QUEUES, FAILS, FUNDS])
        assert Scorecard.model_validate_json(card.model_dump_json()) == card
