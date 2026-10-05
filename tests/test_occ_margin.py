"""SC-3 WP-5 acceptance: OCC margin and clearing fund ingestion.

Acceptance criteria, mapped:

- The estimate cannot be represented as OCC or STANS output:
  ``test_an_estimate_is_not_a_report``, ``test_an_estimate_is_a_forecast_with_its_limitations``
  and ``test_an_estimate_cannot_be_compared_as_if_it_were_a_report``.
- Variance is classified and its evidence retained:
  ``test_variance_is_classified_and_both_records_are_kept`` (every class) and the boundary cases.
- Absent report returns INDETERMINATE: ``test_an_absent_report_is_indeterminate`` and the
  clearing fund cases.

Every member, price and report below is SYNTHETIC.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from cannae_kernel.disposition import Disposition
from cannae_kernel.provenance import Provenance
from pydantic import ValidationError

import lc.occ_margin as margin_module
from lc.occ_margin import (
    LIMITATIONS,
    MarginApproximation,
    MarginPosition,
    OccClearingFundReport,
    OccMarginReport,
    UnderlyingPrice,
    VarianceClass,
    approximate_margin,
    compare_margin,
    ingest_clearing_fund,
)
from lc.options import (
    AdjustmentEvidence,
    CashComponent,
    Deliverable,
    DeliverableKind,
    ExerciseStyle,
    OptionRight,
    OptionSeries,
    SharesComponent,
)

D = Decimal
DAY = date(2026, 10, 5)
SYN = "SYNTHETIC-XYZ"


def series(deliverable: Deliverable | None = None, root: str = "SYN") -> OptionSeries:
    return OptionSeries(
        root=root,
        underlying_security_id=SYN,
        expiry=date(2026, 12, 18),
        right=OptionRight.CALL,
        strike=D(150),
        style=ExerciseStyle.AMERICAN,
        deliverable=deliverable
        or Deliverable(
            kind=DeliverableKind.STANDARD,
            components=(SharesComponent(security_id=SYN, quantity=D(100)),),
        ),
    )


def estimate(
    positions: tuple[MarginPosition, ...] | None = None,
    prices: tuple[UnderlyingPrice, ...] | None = None,
    **changes: Any,
) -> MarginApproximation:
    base: dict[str, Any] = {
        "clearing_member_id": "CM-A",
        "account_id": "CM-A-ACCT",
        "business_date": DAY,
        "currency": "USD",
        "stress_fraction": D("0.15"),
        "positions": positions
        if positions is not None
        else (MarginPosition(series=series(), long_contracts=4, short_contracts=10),),
        "prices": prices
        if prices is not None
        else (UnderlyingPrice(security_id=SYN, price=D(160), currency="USD"),),
    }
    return approximate_margin(**(base | changes))


def report(requirement: str = "24000", **changes: Any) -> OccMarginReport:
    base: dict[str, Any] = {
        "report_id": "SYN-MARGIN-1",
        "clearing_member_id": "CM-A",
        "account_id": "CM-A-ACCT",
        "business_date": DAY,
        "requirement": D(requirement),
        "currency": "USD",
        "provenance": Provenance.FACT_SYNTHETIC,
        "source": "SYNTHETIC-OCC",
    }
    return OccMarginReport(**(base | changes))


# --- the estimate ------------------------------------------------------------------------------


def test_the_estimate_follows_its_stated_method() -> None:
    """10 short contracts x 100 shares x 160 x 0.15 = 24,000. The 4 long contracts add nothing."""
    assert estimate().estimate == D(24000)


def test_an_adjusted_deliverable_stresses_every_share_component_and_no_cash() -> None:
    adjusted = Deliverable(
        kind=DeliverableKind.ADJUSTED,
        evidence=AdjustmentEvidence(
            notice_id="SYNTHETIC-MEMO",
            url="https://example.invalid/m",
            retrieved_dtg="202610051200",
            sha256="ab" * 32,
        ),
        components=(
            SharesComponent(security_id=SYN, quantity=D(50)),
            SharesComponent(security_id="SYNTHETIC-ACQ", quantity=D(20)),
            CashComponent(amount=D(500), currency="USD"),
        ),
    )
    result = estimate(
        positions=(
            MarginPosition(series=series(adjusted, "SYN1"), long_contracts=0, short_contracts=2),
        ),
        prices=(
            UnderlyingPrice(security_id=SYN, price=D(100), currency="USD"),
            UnderlyingPrice(security_id="SYNTHETIC-ACQ", price=D(40), currency="USD"),
        ),
    )
    assert result.estimate == 2 * (D(50) * 100 + D(20) * 40) * D("0.15")


@pytest.mark.parametrize(
    ("prices", "missing"),
    [
        ((), "price[SYNTHETIC-XYZ]"),
        (
            (UnderlyingPrice(security_id=SYN, price=D(160), currency="EUR"),),
            "price[SYNTHETIC-XYZ] in USD",
        ),
    ],
    ids=["no-price", "price-in-another-currency"],
)
def test_a_missing_input_is_named_not_guessed(
    prices: tuple[UnderlyingPrice, ...], missing: str
) -> None:
    result = estimate(prices=prices)
    assert result.estimate is None and result.missing_inputs == (missing,)
    comparison = compare_margin(result, report(), tolerance_fraction=D("0.1"))
    assert comparison.disposition is Disposition.HOLD and missing in comparison.reason


def test_an_estimate_is_a_forecast_with_its_limitations() -> None:
    result = estimate()
    assert result.label == "APPROXIMATION"
    assert result.provenance is Provenance.FORECAST
    assert "does not reproduce STANS or any OCC methodology" in result.limitations
    for changes in (
        {"label": "OCC"},
        {"provenance": Provenance.FACT_EXTERNAL},
        {"limitations": ("reproduces STANS",)},
    ):
        with pytest.raises(ValidationError):
            MarginApproximation.model_validate(result.model_dump() | changes)


def test_an_estimate_is_not_a_report() -> None:
    """An estimate's fields cannot be read as an OCC report, in either direction."""
    with pytest.raises(ValidationError):
        OccMarginReport.model_validate(estimate().model_dump())
    for provenance in (Provenance.FORECAST, Provenance.POLICY_RESULT, Provenance.RECOMMENDATION):
        with pytest.raises(ValidationError, match="reported fact"):
            report(provenance=provenance)


def test_an_estimate_cannot_be_compared_as_if_it_were_a_report() -> None:
    with pytest.raises(TypeError, match="an estimate is not a report"):
        compare_margin(estimate(), estimate(), tolerance_fraction=D("0.1"))  # type: ignore[arg-type]


# --- comparison ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("requirement", "disposition", "variance_class"),
    [
        ("24000", Disposition.PASS, VarianceClass.WITHIN_TOLERANCE),
        ("26000", Disposition.PASS, VarianceClass.WITHIN_TOLERANCE),
        ("30000", Disposition.HOLD, VarianceClass.ESTIMATE_BELOW_REPORT),
        ("20000", Disposition.HOLD, VarianceClass.ESTIMATE_ABOVE_REPORT),
    ],
)
def test_variance_is_classified_and_both_records_are_kept(
    requirement: str, disposition: Disposition, variance_class: VarianceClass
) -> None:
    approximation, issued = estimate(), report(requirement)
    comparison = compare_margin(approximation, issued, tolerance_fraction=D("0.1"))
    assert comparison.disposition is disposition
    assert comparison.variance_class is variance_class
    assert comparison.variance == D(24000) - D(requirement)
    assert comparison.approximation == approximation and comparison.report == issued


def test_the_tolerance_boundary_is_inclusive() -> None:
    edge = compare_margin(estimate(), report("21818.18"), tolerance_fraction=D("0.1"))
    assert edge.variance_class is VarianceClass.ESTIMATE_ABOVE_REPORT
    on = compare_margin(estimate(), report("20000"), tolerance_fraction=D("0.2"))
    assert on.variance_class is VarianceClass.WITHIN_TOLERANCE


def test_a_zero_requirement_matches_only_a_zero_estimate() -> None:
    flat = estimate(
        positions=(MarginPosition(series=series(), long_contracts=3, short_contracts=0),)
    )
    assert compare_margin(flat, report("0"), tolerance_fraction=D("0.5")).disposition is (
        Disposition.PASS
    )
    assert compare_margin(estimate(), report("0"), tolerance_fraction=D("0.5")).variance_class is (
        VarianceClass.ESTIMATE_ABOVE_REPORT
    )


def test_an_absent_report_is_indeterminate() -> None:
    comparison = compare_margin(estimate(), None, tolerance_fraction=D("0.1"))
    assert comparison.disposition is Disposition.INDETERMINATE
    assert comparison.variance is None and comparison.report is None


@pytest.mark.parametrize(
    "changes",
    [
        {"clearing_member_id": "CM-B"},
        {"account_id": "OTHER"},
        {"business_date": date(2026, 10, 6)},
        {"currency": "EUR"},
    ],
)
def test_a_report_for_something_else_is_indeterminate(changes: dict[str, Any]) -> None:
    comparison = compare_margin(estimate(), report(**changes), tolerance_fraction=D("0.1"))
    assert comparison.disposition is Disposition.INDETERMINATE


@pytest.mark.parametrize("tolerance", [D(-1), D(2)])
def test_a_tolerance_is_a_fraction(tolerance: Decimal) -> None:
    with pytest.raises(ValueError, match="fraction"):
        compare_margin(estimate(), report(), tolerance_fraction=tolerance)


# --- clearing fund ---------------------------------------------------------------------------


def fund(requirement: str = "5000000", **changes: Any) -> OccClearingFundReport:
    base: dict[str, Any] = {
        "report_id": "SYN-CF-1",
        "clearing_member_id": "CM-A",
        "business_date": DAY,
        "requirement": D(requirement),
        "currency": "USD",
        "provenance": Provenance.FACT_SYNTHETIC,
        "source": "SYNTHETIC-OCC",
    }
    return OccClearingFundReport(**(base | changes))


def test_the_clearing_fund_is_read_not_estimated() -> None:
    read = ingest_clearing_fund(
        (fund(), fund(clearing_member_id="CM-B")), clearing_member_id="CM-A", business_date=DAY
    )
    assert read.disposition is Disposition.PASS
    assert read.report == fund()
    assert "not estimated" in read.reason


@pytest.mark.parametrize(
    ("reports", "reason"),
    [
        ((), "no OCC clearing fund report"),
        ((fund(), fund("6000000", report_id="SYN-CF-2")), "disagree"),
    ],
    ids=["absent", "conflicting"],
)
def test_an_absent_or_conflicting_clearing_fund_is_indeterminate(
    reports: tuple[OccClearingFundReport, ...], reason: str
) -> None:
    read = ingest_clearing_fund(reports, clearing_member_id="CM-A", business_date=DAY)
    assert read.disposition is Disposition.INDETERMINATE and reason in read.reason


def test_a_clearing_fund_report_is_a_reported_fact() -> None:
    with pytest.raises(ValidationError, match="reported fact"):
        fund(provenance=Provenance.FORECAST)


def test_the_module_states_it_does_not_reproduce_stans() -> None:
    text = " ".join((margin_module.__doc__ or "").split())
    assert "EXPERIMENTAL" in text and "does not reproduce it" in text
    assert LIMITATIONS[0] == "does not reproduce STANS or any OCC methodology"


def test_an_approximation_has_an_estimate_or_names_what_is_missing() -> None:
    dumped = estimate().model_dump()
    with pytest.raises(ValidationError, match="names what is missing"):
        MarginApproximation.model_validate(dumped | {"estimate": None})
    with pytest.raises(ValidationError, match="names what is missing"):
        MarginApproximation.model_validate(dumped | {"missing_inputs": ("price[X]",)})
