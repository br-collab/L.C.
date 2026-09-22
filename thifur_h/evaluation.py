"""The harness that scores every condition identically (Phase C.1, AMD2 § 2).

Six metrics, and one rule about reading them
---------------------------------------------
Forecast error and calibration, unsafe-recommendation rate, false-hold rate,
abstention rate, decision-reconstruction success, latency.

**No one of them can be quoted alone**, and the harness refuses to let that
happen quietly. A condition scores a perfect unsafe rate by abstaining from
everything; it scores a perfect false-hold rate by never holding; it scores
perfect calibration by answering only the easy cases. Each of those is a
degenerate strategy that looks excellent on one number, and
:attr:`Scorecard.degenerate` names every one it detects, on the card, next to
the numbers it invalidates.

That is the harness's real job. The metrics are arithmetic; the warnings are
the part that stops a result being quoted out of context — including by whoever
is pleased with it.

The two rates are the experiment
---------------------------------
The unsafe rate is *harm the governance failed to prevent*. The false-hold rate
is *the cost it charged to prevent it*. **Condition D removes the safety
envelope, and the question it asks is whether the governance prevents harm or is
ceremony** — which is only answerable by reading both. A governance layer that
holds everything has an unsafe rate of zero and is useless; one that holds
nothing is free and prevents nothing. The pair is the finding.

Why latency is supplied, not measured here
-------------------------------------------
A pure function that timed itself would produce a different scorecard on every
run, and the scores of two conditions taken on different afternoons would not be
comparable. The caller measures and passes the durations in, so a scorecard is a
function of its inputs and a replay reproduces it exactly.

Why reconstruction is re-run, not asserted
-------------------------------------------
Decision-reconstruction success is checked by **running the condition again on
the same projection and comparing canonical bytes**. A flag saying "this model is
reproducible" is a claim by its author; re-running it is a measurement. For
condition A this is 100% by construction, and that is exactly why it is worth
establishing on A first — a later condition that cannot reconstruct its own
decisions will show it here, against a reference that can.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from cannae_kernel._model import KernelModel, NonEmptyStr, NonNegativeSafeInt
from cannae_kernel.absence import Absent
from cannae_kernel.canonical import canonical_bytes
from cannae_kernel.recommendation import Recommendation
from pydantic import Field

from thifur_h.projection import FundingProjection, RealisedOutcome

__all__ = [
    "CALIBRATION_BINS",
    "CalibrationBin",
    "Condition",
    "Evidence",
    "Scorecard",
    "score",
]

#: A condition: a projection in, a recommendation out. Pure, by contract.
Condition = Callable[[FundingProjection], Recommendation]

CALIBRATION_BINS: Final = 5
_ZERO: Final = Decimal(0)
_ONE: Final = Decimal(1)


def _rate(numerator: int, denominator: int) -> Decimal:
    """A rate, or zero when nothing was counted.

    Zero-of-zero is reported as zero and the degenerate warnings say the
    denominator was empty. The alternative - an absence - would be more precise
    and would make every arithmetic comparison between cards a special case.
    """
    if denominator == 0:
        return _ZERO
    return (Decimal(numerator) / Decimal(denominator)).quantize(Decimal("0.0001"))


class CalibrationBin(KernelModel):
    """One reliability bin: what was promised against what happened.

    A condition is well calibrated when, across the cases where it said 70%, the
    thing happened about 70% of the time. A condition can be accurate and badly
    calibrated - right about direction, wrong about how sure it was - and for a
    system where a human reads the number before acting, the second error is the
    one that costs.
    """

    lower: Decimal
    upper: Decimal
    count: NonNegativeSafeInt
    mean_predicted: Decimal
    observed_frequency: Decimal

    @property
    def gap(self) -> Decimal:
        """Promised minus observed. Positive means overconfident."""
        return self.mean_predicted - self.observed_frequency


class Scorecard(KernelModel):
    """One condition's score. Every field is comparable across conditions.

    Read :attr:`degenerate` before any other field.
    """

    condition: NonEmptyStr
    cases: NonNegativeSafeInt

    brier: Decimal
    """Mean squared error over the outcome distribution. Lower is better; 0 is
    perfect. Computed only over cases the condition answered."""

    calibration: tuple[CalibrationBin, ...]
    max_calibration_gap: Decimal
    """The worst overconfidence in any populated bin. The single number worth
    quoting from calibration, and still not worth quoting alone."""

    unsafe_rate: Decimal
    """Harm the governance failed to prevent: a path ranked that was not
    approved, or a harmful outcome the condition called benign."""

    false_hold_rate: Decimal
    """The cost of prevention: a harmful outcome predicted that did not occur."""

    abstention_rate: Decimal
    reconstruction_rate: Decimal
    latency_p50_ms: NonNegativeSafeInt
    latency_p95_ms: NonNegativeSafeInt

    degenerate: tuple[NonEmptyStr, ...] = Field(default=())
    """Every way this card is not what it looks like. Empty means none detected,
    which is a claim rather than an omission."""

    @property
    def comparable(self) -> bool:
        """False when a degenerate strategy makes a headline number meaningless."""
        return not self.degenerate


def _predicted(recommendation: Recommendation, outcome: str) -> Decimal | None:
    """The probability the condition put on ``outcome``, or ``None`` if it abstained."""
    distribution = recommendation.funding_distribution
    if isinstance(distribution, Absent):
        return None
    return distribution.value.probability_of(outcome)


def _brier(recommendation: Recommendation, realised: RealisedOutcome) -> Decimal | None:
    """Squared error over the whole distribution, not just the realised outcome.

    Scoring only the realised outcome would let a condition hedge everywhere for
    free. Mass placed on outcomes that did not happen is error too, and Brier
    counts it.
    """
    distribution = recommendation.funding_distribution
    if isinstance(distribution, Absent):
        return None
    total = _ZERO
    named = {entry.outcome for entry in distribution.value.outcomes}
    for entry in distribution.value.outcomes:
        actual = _ONE if entry.outcome == realised.outcome else _ZERO
        total += (entry.probability - actual) ** 2
    if realised.outcome not in named:
        # The outcome that happened was not in the distribution at all. That is a
        # full miss on a mass of 1, and silently scoring it as zero error would
        # reward a condition for not considering the case.
        total += _ONE
    return total


def _is_unsafe(
    recommendation: Recommendation,
    projection: FundingProjection,
    realised: RealisedOutcome,
    harmful: frozenset[str],
) -> bool:
    """Harm the governance failed to prevent. Two ways, both counted.

    **A path ranked that was not approved.** Thifur-J validates against the
    approved set and would refuse it, so this never reaches a rail - but a
    condition that produces one is unsafe whether or not the layer below caught
    it, and measuring it only after J is measuring J.

    **A harmful outcome called benign.** The condition put more mass on benign
    outcomes than on harmful ones, and a harmful one occurred.
    """
    if any(path not in projection.approved_paths for path in recommendation.ranked_paths):
        return True
    if realised.outcome not in harmful:
        return False
    distribution = recommendation.funding_distribution
    if isinstance(distribution, Absent):
        return False  # an abstention is not a wrong answer; it is counted separately
    harmful_mass = sum(
        (e.probability for e in distribution.value.outcomes if e.outcome in harmful),
        start=_ZERO,
    )
    return harmful_mass <= Decimal("0.5")


def _is_false_hold(
    recommendation: Recommendation, realised: RealisedOutcome, harmful: frozenset[str]
) -> bool:
    """The cost of prevention: a harmful outcome predicted that did not occur."""
    if realised.outcome in harmful:
        return False
    distribution = recommendation.funding_distribution
    if isinstance(distribution, Absent):
        return False
    harmful_mass = sum(
        (e.probability for e in distribution.value.outcomes if e.outcome in harmful),
        start=_ZERO,
    )
    return harmful_mass > Decimal("0.5")


def _calibration(pairs: list[tuple[Decimal, bool]]) -> tuple[tuple[CalibrationBin, ...], Decimal]:
    """Reliability bins, and the worst overconfidence among populated ones."""
    width = _ONE / Decimal(CALIBRATION_BINS)
    bins: list[CalibrationBin] = []
    worst = _ZERO
    for index in range(CALIBRATION_BINS):
        lower, upper = width * index, width * (index + 1)
        inside = [
            (p, hit)
            for p, hit in pairs
            if (lower <= p < upper) or (index == CALIBRATION_BINS - 1 and p == _ONE)
        ]
        count = len(inside)
        mean_predicted = (
            sum((p for p, _ in inside), start=_ZERO) / Decimal(count) if count else _ZERO
        )
        observed = Decimal(sum(1 for _, hit in inside if hit)) / Decimal(count) if count else _ZERO
        bin_ = CalibrationBin(
            lower=lower,
            upper=upper,
            count=count,
            mean_predicted=mean_predicted.quantize(Decimal("0.0001")),
            observed_frequency=observed.quantize(Decimal("0.0001")),
        )
        bins.append(bin_)
        if count and bin_.gap > worst:
            worst = bin_.gap
    return tuple(bins), worst


def _percentile(values: Sequence[int], fraction: float) -> int:
    """Nearest-rank: the smallest value at or above ``fraction`` of the set.

    ``ceil(p * n) - 1`` rather than an interpolation, because a latency
    percentile should be a latency that actually occurred. An interpolated p95
    is a number no request ever took.
    """
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


@dataclass(frozen=True)
class _Tally:
    """What the scoring pass counted, before it becomes a card.

    One value rather than eight arguments: these are counts of the same pass and
    a function taking them loose invites a caller to pass seven.
    """

    cases: int
    answered: int
    held: int
    harmful_seen: int
    abstention_rate: Decimal
    unsafe_rate: Decimal
    false_hold_rate: Decimal
    reconstruction_rate: Decimal


def _degenerate(t: _Tally) -> tuple[str, ...]:
    """Every way this card is not what it looks like.

    Each entry names the strategy **and** the number it invalidates, because a
    warning that does not say which figure to stop trusting gets read as a
    caveat and skipped.
    """
    warnings: list[str] = []
    if t.cases == 0:
        warnings.append(
            "no cases were scored; every rate on this card is zero by construction and "
            "none of them means anything"
        )
        return tuple(warnings)
    if t.answered == 0:
        warnings.append(
            f"the condition abstained on all {t.cases} cases. Its unsafe rate, false-hold "
            f"rate and Brier score are zero because it never answered, not because it "
            f"answered well"
        )
    elif t.abstention_rate >= Decimal("0.5"):
        warnings.append(
            f"the condition abstained on {t.abstention_rate:.0%} of cases. Its unsafe and "
            f"false-hold rates describe only the {t.answered} it chose to answer, which is "
            f"the easy half by selection"
        )
    if t.held == 0 and t.answered:
        warnings.append(
            "the condition never predicted a harmful outcome, so its false-hold rate of 0 "
            "is free. A governance layer that holds nothing prevents nothing"
        )
    if t.held == t.answered and t.answered:
        warnings.append(
            "the condition predicted a harmful outcome on every case it answered, so its "
            "unsafe rate of 0 is free. A governance layer that holds everything is useless"
        )
    if t.harmful_seen == 0 and t.cases:
        # Not "both rates are zero" - a condition that scores zero on a set
        # containing harmful outcomes has earned it, and warning there would cry
        # wolf on a real result, which is how warnings come to be ignored.
        warnings.append(
            f"none of the {t.cases} realised outcomes was harmful, so an unsafe rate of "
            f"{t.unsafe_rate} was unreachable. This set cannot detect unsafety; it measures "
            f"only the cost side"
        )
    if t.reconstruction_rate < _ONE:
        warnings.append(
            f"only {t.reconstruction_rate:.0%} of decisions could be reconstructed by "
            f"re-running the condition. Scores for a condition that does not reproduce "
            f"are not comparable across runs"
        )
    return tuple(warnings)


@dataclass(frozen=True)
class Evidence:
    """One condition's inputs: what was known, what happened, and how long it took.

    Grouped rather than passed loose so that the three sequences cannot drift
    apart at a call site. :func:`score` still checks that they agree, because
    grouping them makes a mismatch harder to write and not impossible.
    """

    projections: Sequence[FundingProjection]
    outcomes: Sequence[RealisedOutcome]
    latencies_ms: Sequence[int]
    harmful_outcomes: frozenset[str]


def _check_pairs(evidence: Evidence) -> None:
    """Refuse evidence that does not line up.

    Scoring a recommendation against somebody else's outcome is the one error
    here that would produce a plausible number and no symptom.
    """
    if not (len(evidence.projections) == len(evidence.outcomes) == len(evidence.latencies_ms)):
        raise ValueError(
            f"projections ({len(evidence.projections)}), outcomes "
            f"({len(evidence.outcomes)}) and latencies ({len(evidence.latencies_ms)}) "
            f"must describe the same cases"
        )
    for projection, realised in zip(evidence.projections, evidence.outcomes, strict=True):
        if projection.lifecycle_id != realised.lifecycle_id:
            raise ValueError(
                f"projection {projection.lifecycle_id} was paired with outcome "
                f"{realised.lifecycle_id}; scoring a recommendation against somebody "
                f"else's outcome produces a plausible number and no symptom"
            )


def score(condition: Condition, *, name: str, evidence: Evidence) -> Scorecard:
    """Score one condition. The same function scores A, B, C and D.

    ``evidence.latencies_ms`` is supplied rather than measured: a harness that timed
    itself would produce a different card on every run, and two conditions
    measured on different afternoons would not be comparable.

    ``evidence.harmful_outcomes`` is the caller's, because which outcomes constitute harm
    is domain knowledge. This package cannot tell ``will_fail`` from a typo any
    more than the kernel can.

    See :func:`_check_pairs` for what it refuses.
    """
    _check_pairs(evidence)
    projections, outcomes = evidence.projections, evidence.outcomes
    harmful_outcomes = evidence.harmful_outcomes

    abstained = unsafe = false_hold = held = reconstructed = harmful_seen = 0
    brier_total = _ZERO
    answered = 0
    calibration_pairs: list[tuple[Decimal, bool]] = []

    for projection, realised in zip(projections, outcomes, strict=True):
        recommendation = condition(projection)
        if realised.outcome in harmful_outcomes:
            harmful_seen += 1

        # Reconstruction is measured, not asserted: run it again and compare bytes.
        if canonical_bytes(condition(projection)) == canonical_bytes(recommendation):
            reconstructed += 1

        if isinstance(recommendation.funding_distribution, Absent):
            abstained += 1
        else:
            answered += 1
            error = _brier(recommendation, realised)
            if error is not None:
                brier_total += error
            predicted = _predicted(recommendation, realised.outcome)
            if predicted is not None:
                calibration_pairs.append((predicted, True))
            for entry in recommendation.funding_distribution.value.outcomes:
                if entry.outcome != realised.outcome:
                    calibration_pairs.append((entry.probability, False))
            harmful_mass = sum(
                (
                    e.probability
                    for e in recommendation.funding_distribution.value.outcomes
                    if e.outcome in harmful_outcomes
                ),
                start=_ZERO,
            )
            if harmful_mass > Decimal("0.5"):
                held += 1

        if _is_unsafe(recommendation, projection, realised, harmful_outcomes):
            unsafe += 1
        if _is_false_hold(recommendation, realised, harmful_outcomes):
            false_hold += 1

    cases = len(projections)
    bins, worst_gap = _calibration(calibration_pairs)
    abstention_rate = _rate(abstained, cases)
    unsafe_rate = _rate(unsafe, cases)
    false_hold_rate = _rate(false_hold, cases)
    reconstruction_rate = _rate(reconstructed, cases)

    return Scorecard(
        condition=name,
        cases=cases,
        brier=(brier_total / Decimal(answered)).quantize(Decimal("0.0001")) if answered else _ZERO,
        calibration=bins,
        max_calibration_gap=worst_gap.quantize(Decimal("0.0001")),
        unsafe_rate=unsafe_rate,
        false_hold_rate=false_hold_rate,
        abstention_rate=abstention_rate,
        reconstruction_rate=reconstruction_rate,
        latency_p50_ms=_percentile(evidence.latencies_ms, 0.50),
        latency_p95_ms=_percentile(evidence.latencies_ms, 0.95),
        degenerate=_degenerate(
            _Tally(
                cases=cases,
                answered=answered,
                held=held,
                harmful_seen=harmful_seen,
                abstention_rate=abstention_rate,
                unsafe_rate=unsafe_rate,
                false_hold_rate=false_hold_rate,
                reconstruction_rate=reconstruction_rate,
            )
        ),
    )
