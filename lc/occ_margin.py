"""OCC margin and clearing fund, ingested; a firm-side estimate, labelled (ORDER SC-3, WP-5).

EXPERIMENTAL (charter section 18.6). Nothing here is production evidence, and
nothing here is OCC's (the Options Clearing Corporation's) margin.

OCC'S FIGURES ARE READ, NEVER COMPUTED
--------------------------------------
OCC margins with STANS (System for Theoretical Analysis and Numerical
Simulations), a proprietary methodology. This module does not reproduce it and
does not try. A margin or clearing fund requirement exists here only as a
report OCC (or a synthetic stand-in for OCC) issued: :class:`OccMarginReport`
and :class:`OccClearingFundReport`, each a reported fact with its source.

THE FIRM'S ESTIMATE IS A DIFFERENT THING
----------------------------------------
:class:`MarginApproximation` is the firm's own rough view, so that a report
that looks wrong can be noticed. It is a different type from a report, it is
labelled ``APPROXIMATION``, and its provenance is fixed as ``FORECAST``: a model
about something not observed. It cannot be passed where a report is expected,
and it does not claim to be OCC's or STANS output.

Its method is deliberately simple and stated in full: for every short
contract, each share component of the series' deliverable times that
security's price times a stress fraction the firm chooses. Its limitations are
carried on the record (:data:`LIMITATIONS`).

COMPARISON
----------
:func:`compare_margin` sets the estimate against the report and classifies the
variance against a tolerance the firm chooses, keeping both as evidence. The
disposition is advisory and never means OCC approval or compliance:
INDETERMINATE where the report is absent or is not the one for this account,
day and currency; HOLD where an input the estimate needs is missing, or the
variance is outside tolerance; PASS where it is within.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Final, Literal, Self

from cannae_kernel.disposition import Disposition
from cannae_kernel.provenance import Provenance
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lc.options import OptionSeries, SharesComponent

__all__ = [
    "LIMITATIONS",
    "ClearingFundIngestion",
    "MarginApproximation",
    "MarginComparison",
    "MarginPosition",
    "OccClearingFundReport",
    "OccMarginReport",
    "UnderlyingPrice",
    "VarianceClass",
    "approximate_margin",
    "compare_margin",
    "ingest_clearing_fund",
]

ZERO = Decimal(0)
_REPORTED: Final = frozenset({Provenance.FACT_EXTERNAL, Provenance.FACT_SYNTHETIC})

#: What the firm's estimate does not do. Carried on every estimate.
LIMITATIONS: Final[tuple[str, ...]] = (
    "does not reproduce STANS or any OCC methodology",
    "long positions contribute nothing: their premium is taken as already paid",
    "one stress fraction for every underlying, with no volatility, time or correlation",
    "cash components of an adjusted deliverable are not stressed",
    "no offsets between series, accounts or products",
)


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


def _reported(value: Provenance) -> Provenance:
    if value not in _REPORTED:
        raise ValueError(
            f"an OCC report is a reported fact: FACT_EXTERNAL or FACT_SYNTHETIC, not {value}"
        )
    return value


class OccMarginReport(_Record):
    """One account's margin requirement for one business day, as OCC reported it."""

    report_id: str = Field(min_length=1)
    clearing_member_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    business_date: date
    requirement: Decimal = Field(ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    provenance: Provenance
    #: Who issued it, for example ``"OCC"`` or ``"SYNTHETIC-OCC"``.
    source: str = Field(min_length=1)

    @field_validator("provenance")
    @classmethod
    def _a_fact(cls, value: Provenance) -> Provenance:
        return _reported(value)


class OccClearingFundReport(_Record):
    """One clearing member's clearing fund requirement, as OCC reported it."""

    report_id: str = Field(min_length=1)
    clearing_member_id: str = Field(min_length=1)
    business_date: date
    requirement: Decimal = Field(ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    provenance: Provenance
    source: str = Field(min_length=1)

    @field_validator("provenance")
    @classmethod
    def _a_fact(cls, value: Provenance) -> Provenance:
        return _reported(value)


class UnderlyingPrice(_Record):
    security_id: str = Field(min_length=1)
    price: Decimal = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class MarginPosition(_Record):
    series: OptionSeries
    long_contracts: int = Field(ge=0)
    short_contracts: int = Field(ge=0)


class MarginApproximation(_Record):
    """The firm's estimate. Not OCC's figure, and never presented as one."""

    label: Literal["APPROXIMATION"] = "APPROXIMATION"
    provenance: Literal[Provenance.FORECAST] = Provenance.FORECAST
    method: Literal["short-contracts-times-stressed-deliverable"] = (
        "short-contracts-times-stressed-deliverable"
    )
    clearing_member_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    business_date: date
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    #: The firm's chosen fractional move in each underlying's price.
    stress_fraction: Decimal = Field(gt=0, le=1)
    positions: tuple[MarginPosition, ...]
    prices: tuple[UnderlyingPrice, ...]
    #: ``None`` where an input it needs is missing.
    estimate: Decimal | None
    missing_inputs: tuple[str, ...] = ()
    limitations: tuple[str, ...] = LIMITATIONS

    @model_validator(mode="after")
    def _an_estimate_or_what_is_missing(self) -> Self:
        if (self.estimate is None) == (not self.missing_inputs):
            raise ValueError("an approximation has an estimate or names what is missing")
        if self.limitations != LIMITATIONS:
            raise ValueError("an approximation carries its stated limitations unchanged")
        return self


def approximate_margin(  # noqa: PLR0913 - every input to the estimate stays explicit
    *,
    clearing_member_id: str,
    account_id: str,
    business_date: date,
    currency: str,
    stress_fraction: Decimal,
    positions: tuple[MarginPosition, ...],
    prices: tuple[UnderlyingPrice, ...],
) -> MarginApproximation:
    """The firm's estimate for one account and day. Pure; refuses to guess a missing price."""
    by_security = {p.security_id: p for p in prices}
    total, missing = ZERO, []
    for position in positions:
        for component in position.series.deliverable.components:
            if not isinstance(component, SharesComponent) or not position.short_contracts:
                continue
            price = by_security.get(component.security_id)
            if price is None:
                missing.append(f"price[{component.security_id}]")
                continue
            if price.currency != currency:
                missing.append(f"price[{component.security_id}] in {currency}")
                continue
            total += position.short_contracts * component.quantity * price.price * stress_fraction
    return MarginApproximation(
        clearing_member_id=clearing_member_id,
        account_id=account_id,
        business_date=business_date,
        currency=currency,
        stress_fraction=stress_fraction,
        positions=positions,
        prices=prices,
        estimate=None if missing else total,
        missing_inputs=tuple(sorted(set(missing))),
    )


class VarianceClass(StrEnum):
    WITHIN_TOLERANCE = "within_tolerance"
    ESTIMATE_BELOW_REPORT = "estimate_below_report"
    ESTIMATE_ABOVE_REPORT = "estimate_above_report"


class MarginComparison(_Record):
    """The estimate against the report, classified, with both kept as evidence."""

    disposition: Disposition
    reason: str
    approximation: MarginApproximation
    report: OccMarginReport | None
    tolerance_fraction: Decimal
    variance: Decimal | None = None
    variance_class: VarianceClass | None = None


def compare_margin(
    approximation: MarginApproximation,
    report: OccMarginReport | None,
    *,
    tolerance_fraction: Decimal,
) -> MarginComparison:
    """Compare the firm's estimate with OCC's report. Advisory; never an approval."""
    if not ZERO <= tolerance_fraction <= 1:
        raise ValueError("the tolerance is a fraction of the reported requirement, 0 to 1")

    def result(disposition: Disposition, reason: str, **found: object) -> MarginComparison:
        return MarginComparison.model_validate(
            {
                "disposition": disposition,
                "reason": reason,
                "approximation": approximation,
                "report": report,
                "tolerance_fraction": tolerance_fraction,
                **found,
            }
        )

    if report is None:
        return result(Disposition.INDETERMINATE, "no OCC margin report for this account and day")
    if not isinstance(report, OccMarginReport):
        raise TypeError("only an OCC margin report is compared; an estimate is not a report")
    subject = (
        approximation.clearing_member_id,
        approximation.account_id,
        approximation.business_date,
        approximation.currency,
    )
    if subject != (
        report.clearing_member_id,
        report.account_id,
        report.business_date,
        report.currency,
    ):
        return result(
            Disposition.INDETERMINATE, "the report is for another member, account, day or currency"
        )
    if approximation.estimate is None:
        return result(
            Disposition.HOLD, f"the estimate is missing {', '.join(approximation.missing_inputs)}"
        )
    variance = approximation.estimate - report.requirement
    if abs(variance) <= tolerance_fraction * report.requirement:
        return result(
            Disposition.PASS,
            "the estimate is within tolerance of the report",
            variance=variance,
            variance_class=VarianceClass.WITHIN_TOLERANCE,
        )
    below = variance < 0
    return result(
        Disposition.HOLD,
        "the estimate is outside tolerance of the report; the variance needs review",
        variance=variance,
        variance_class=(
            VarianceClass.ESTIMATE_BELOW_REPORT if below else VarianceClass.ESTIMATE_ABOVE_REPORT
        ),
    )


class ClearingFundIngestion(_Record):
    """The clearing fund requirement read for one member and day, or why there is none."""

    disposition: Disposition
    reason: str
    report: OccClearingFundReport | None = None


def ingest_clearing_fund(
    reports: tuple[OccClearingFundReport, ...], *, clearing_member_id: str, business_date: date
) -> ClearingFundIngestion:
    """Read the one clearing fund report for a member and day. Never estimated."""
    matching = [
        r
        for r in reports
        if r.clearing_member_id == clearing_member_id and r.business_date == business_date
    ]
    if not matching:
        return ClearingFundIngestion(
            disposition=Disposition.INDETERMINATE,
            reason="no OCC clearing fund report for this member and day",
        )
    if len({(r.requirement, r.currency) for r in matching}) > 1:
        return ClearingFundIngestion(
            disposition=Disposition.INDETERMINATE,
            reason="OCC clearing fund reports for this day disagree",
        )
    return ClearingFundIngestion(
        disposition=Disposition.PASS,
        reason="read from the OCC report; not estimated",
        report=matching[0],
    )
