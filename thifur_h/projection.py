"""The input a condition scores against, and the outcome it is scored on.

Value types, handed in. This package does not import Atreides (see
:mod:`thifur_h`), so the projection is described here in terms the kernel and
this package can validate, and whoever builds one from a domain object owns that
translation.

Two types, kept apart deliberately
-----------------------------------
:class:`FundingProjection` is **what was known before**. :class:`RealisedOutcome`
is **what actually happened**. A condition sees only the first; the harness sees
both, and never the second before the first has been scored.

Keeping them in separate types is what prevents the most expensive mistake
available in this experiment: a model that has seen the answer. The kernel's four
clocks exist for the same reason at the event level (Research Charter § 17.9);
this is that rule at the experiment level.
"""

from __future__ import annotations

from typing import Self

from cannae_kernel._model import KernelDecimal, KernelModel, NonEmptyStr, UtcDatetime
from cannae_kernel.ids import LifecycleId
from pydantic import Field, model_validator

__all__ = ["FundingProjection", "RealisedOutcome"]


class FundingProjection(KernelModel):
    """What was known about a cash leg before it settled.

    Everything a condition is allowed to see. Deliberately small: a baseline
    that needs more than this is not a baseline, and a later condition that
    needs more has to say so by changing this type, in public, rather than by
    reaching for something.
    """

    lifecycle_id: LifecycleId
    projected_at: UtcDatetime
    """When the projection was made. Every condition's output is timestamped
    from this, never from the wall clock, so a replay produces the same bytes."""

    currency: NonEmptyStr
    obligation_amount: KernelDecimal
    committed_position: KernelDecimal
    """What is committed against the obligation. Short when it is less."""

    window_closes_in_seconds: int = Field(ge=0)
    """How long until the settlement window closes."""

    expected_inflow_amount: KernelDecimal
    expected_inflow_in_seconds: int = Field(ge=0)
    """The inflow the domain expects, and when. `0` with a zero amount means
    none is expected — which is different from an inflow of zero arriving now."""

    gross_final: bool
    """True on a gross-final rail, where a queued instruction is not a failure
    and re-issuing it creates a duplicate payment that cannot be reversed."""

    approved_paths: tuple[NonEmptyStr, ...] = Field(min_length=1)
    """The paths a condition may rank. **Ranking one that is not here is an
    unsafe recommendation**, and the harness counts it as such."""

    @property
    def shortfall(self) -> KernelDecimal:
        """How short the committed position is. Zero or negative means covered."""
        return self.obligation_amount - self.committed_position

    @property
    def inflow_arrives_in_window(self) -> bool:
        """Whether the expected inflow lands before the window closes."""
        return self.expected_inflow_in_seconds <= self.window_closes_in_seconds

    @property
    def inflow_covers_shortfall(self) -> bool:
        """Whether the expected inflow clears the shortfall, and in time.

        Both halves matter. An inflow large enough but late does not clear the
        window, and an inflow in time but too small does not clear the
        obligation. Treating either as sufficient is how a `will_fail` gets
        reported as a `will_queue`.
        """
        return self.inflow_arrives_in_window and self.expected_inflow_amount >= self.shortfall


class RealisedOutcome(KernelModel):
    """What actually happened. The harness sees this; a condition never does."""

    lifecycle_id: LifecycleId
    outcome: NonEmptyStr
    """The domain's own name for what happened — Atreides' `FundingDisposition`
    value. Carried as a string for the reason the kernel carries it as one: this
    package cannot tell `will_queue` from a typo either."""

    settled_within_window: bool
    path_taken: NonEmptyStr

    @model_validator(mode="after")
    def _an_outcome_names_itself(self) -> Self:
        if not self.outcome.strip():
            raise ValueError("a realised outcome must name what happened")
        return self
