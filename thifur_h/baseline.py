"""Condition A: the deterministic baseline (Phase C.1, AMD2 § 2).

The floor every later condition is measured against. It contains no model — it
is the existing domain projection, restated in the C.0 contract, so that B, C and
D are compared against *what the system already does* rather than against
nothing.

Why the baseline must exist before anything else
-------------------------------------------------
Every later claim in this experiment is **"better than A"**. If A is not built
and scored first, "better" is measured against a memory of how the old thing
behaved, and a memory is a thing people are generous to. A is deliberately dull
and deliberately first.

It is also the control for the question Condition D asks — whether the
governance prevents harm or is ceremony. D removes the safety envelope from the
adaptive model; A is the other end of that axis, a system with no model at all.
Neither is interesting alone; the span between them is the result.

What "deterministic" means here, precisely
-------------------------------------------
The same projection produces byte-identical bytes, on any machine, for ever. No
clock is read — the recommendation is stamped from
:attr:`~thifur_h.projection.FundingProjection.projected_at` — no randomness is
drawn, and no state is carried between calls. That is not a nicety: a baseline
that drifts is not a baseline, and a score compared against a drifting reference
tells you nothing about the thing you changed.

Where it abstains, and why that is the honest answer
-----------------------------------------------------
A answers the two questions the domain projection actually answers: which
funding outcome, and how long a queue. It abstains from the three that need a
model it does not have — window-miss probability, peak liquidity profile, regime
classification — each with the reason.

That is the point of abstention being a first-class value. The baseline could
emit 0.5 for everything and score middling on calibration; instead it says what
it does not know, and the abstention rate becomes part of its score rather than
hidden inside it. A later condition that answers those three has to beat A on
what A answered **and** justify the answers A declined to give.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

from cannae_kernel._model import NonEmptyStr
from cannae_kernel.absence import AbsenceKind, Absent, Recorded
from cannae_kernel.provenance import Provenance
from cannae_kernel.recommendation import (
    OutcomeProbability,
    ProbabilityDistribution,
    Recommendation,
)

from thifur_h.projection import FundingProjection

__all__ = [
    "CONDITION_A",
    "FUNDED",
    "WILL_FAIL",
    "WILL_QUEUE",
    "recommend",
]

#: Identifies which condition produced a recommendation, so a scorecard can
#: never be attributed to the wrong one.
CONDITION_A: Final = "condition-A/deterministic-baseline/1.0"

#: The domain's outcome names, as this package uses them. They are Atreides'
#: `FundingDisposition` values and this package cannot validate them — it
#: carries the strings, exactly as the kernel does, and the boundary that builds
#: a projection owns checking them against the real enum.
FUNDED: Final = "funded"
WILL_QUEUE: Final = "will_queue"
WILL_FAIL: Final = "will_fail"

_ONE: Final = Decimal("1")
_ZERO: Final = Decimal("0")

#: The baseline states its own certainty as 1: it is a restatement of a
#: deterministic projection, not an estimate. That is not a claim of being right
#: — the projection can be wrong — but of there being no model uncertainty to
#: report. The harness scores whether that certainty was earned, which is the
#: whole point of measuring calibration on a condition that never hedges.
_BASELINE_CONFIDENCE: Final = _ONE


def _absent(reason: str, kind: AbsenceKind = AbsenceKind.NOT_APPLICABLE) -> Absent:
    return Absent(kind=kind, reason=reason)


def _distribution(projection: FundingProjection) -> ProbabilityDistribution:
    """The projection's outcome, as a degenerate distribution.

    A places all mass on one outcome, because that is what a deterministic
    projection says — it has no second opinion. Emitting a hedged distribution
    would make A score better on Brier and would be a lie about what it knows.

    The three cases are the domain's own, and the middle one is the distinction
    the whole cash-leg argument rests on: **a queued gross-final instruction is
    not a failure**, and treating it as one is the expensive error, because
    re-issuing creates a duplicate payment that settlement cannot reverse.
    """
    if projection.shortfall <= _ZERO:
        outcome = FUNDED
    elif projection.gross_final and projection.inflow_covers_shortfall:
        outcome = WILL_QUEUE
    else:
        outcome = WILL_FAIL
    return ProbabilityDistribution(
        outcomes=(OutcomeProbability(outcome=outcome, probability=_ONE),)
    )


def _queue_seconds(projection: FundingProjection) -> Recorded[int] | Absent:
    """How long the leg queues: the inflow's arrival, or zero if it is covered.

    Absent when the projection says it will not settle at all. A duration for
    something that never settles is not a small number — it is a category error,
    and reporting one invites an operator to wait for it.
    """
    if projection.shortfall <= _ZERO:
        return Recorded[int](value=0)
    if projection.gross_final and projection.inflow_covers_shortfall:
        return Recorded[int](value=projection.expected_inflow_in_seconds)
    return _absent(
        "the projection does not have this leg settling, so there is no queue duration",
        AbsenceKind.NOT_APPLICABLE,
    )


def recommend(projection: FundingProjection) -> Recommendation:
    """Condition A. Pure: same projection in, byte-identical recommendation out.

    Reads no clock, draws no randomness and carries no state between calls.
    """
    ranked: tuple[NonEmptyStr, ...] = projection.approved_paths
    return Recommendation(
        recommendation_id=f"REC-A-{projection.lifecycle_id}",
        lifecycle_id=projection.lifecycle_id,
        issued_at=projection.projected_at,
        provenance=Provenance.FORECAST,
        c2_handoff=_absent("operator-direct under CAOM-001", AbsenceKind.NOTHING_RECORDED),
        model_ref=CONDITION_A,
        funding_distribution=Recorded[ProbabilityDistribution](value=_distribution(projection)),
        expected_queue_seconds=_queue_seconds(projection),
        window_miss_probability=_absent(
            "the baseline restates a deterministic projection; it estimates no probability "
            "of missing the window"
        ),
        peak_liquidity=_absent(
            "the baseline projects a single obligation, not an intraday liquidity profile"
        ),
        regime=_absent("regime classification requires a time series the baseline does not read"),
        # Ranked in the order the domain supplied them. A has no opinion about
        # order and says so by not reordering - inventing a preference it cannot
        # justify would be the kind of unearned confidence this experiment exists
        # to measure.
        ranked_paths=ranked,
        confidence=Recorded[Decimal](value=_BASELINE_CONFIDENCE),
    )
