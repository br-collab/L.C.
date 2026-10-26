"""INST-1 WP1 institutional controls and adversarial acceptance."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from random import Random

import pytest
from pydantic import ValidationError

from lc.asset_profile import BILATERAL_TREASURY
from lc.institutional import (
    AssetClass,
    InstrumentReference,
    MarketProfile,
    QuantityUnit,
    TickBucket,
    TreasuryReference,
    accrued_interest_actual_actual_icma,
    derive_cash,
    minimum_price_increment,
    validate_settlement_date,
)


def _treasury() -> InstrumentReference:
    return InstrumentReference(
        cusip=" 91282cjl6 ",
        asset_class=AssetClass.US_TREASURY,
        quantity_unit=QuantityUnit.FACE,
        treasury=TreasuryReference(
            annual_coupon_rate=Decimal("0.04"),
            dated_date=date(2026, 1, 15),
            maturity_date=date(2036, 1, 15),
            payments_per_year=2,
        ),
    )


def test_face_quantity_is_per_100_and_accrual_uses_declared_reference() -> None:
    principal, accrued, total = derive_cash(
        quantity=Decimal(100),
        price=Decimal("99.5"),
        settlement_date=date(2026, 4, 15),
        instrument=_treasury(),
    )
    assert principal == Decimal("99.50")
    assert accrued == Decimal("0.99")
    assert total == Decimal("100.49")


def test_derived_cash_conserves_to_the_cent_for_200_generated_examples() -> None:
    """Rule 29 seed 20261026: derived total equals principal plus accrued to the cent."""
    rng = Random(20261026)
    instrument = _treasury()
    for _ in range(200):
        face = Decimal(rng.randint(1, 10_000_000))
        price = Decimal(rng.randint(1, 200_000)) / Decimal(1000)
        principal, accrued, total = derive_cash(
            quantity=face,
            price=price,
            settlement_date=date(2026, 4, 15),
            instrument=instrument,
        )
        assert total == principal + accrued
        assert total == total.quantize(Decimal("0.01"))


def test_equity_cash_has_no_accrued_interest() -> None:
    equity = InstrumentReference(
        cusip="037833100",
        asset_class=AssetClass.US_EQUITY,
        quantity_unit=QuantityUnit.SHARES,
    )
    assert derive_cash(
        quantity=Decimal(3),
        price=Decimal("100.01"),
        settlement_date=date(2026, 10, 26),
        instrument=equity,
    ) == (Decimal("300.03"), Decimal(0), Decimal("300.03"))
    with pytest.raises(ValidationError, match="ACCRUED_INTEREST_NOT_PERMITTED"):
        InstrumentReference(
            cusip="037833100",
            asset_class=AssetClass.US_EQUITY,
            quantity_unit=QuantityUnit.SHARES,
            treasury=_treasury().treasury,
        )


@pytest.mark.parametrize(
    "cusip",
    ["91282CJL5", "91282CJL" + "\uff16", "<script>1", "A" * 400],
)
def test_cusip_refuses_bad_check_digit_unicode_markup_and_overlength(cusip: str) -> None:
    with pytest.raises(ValidationError):
        InstrumentReference(
            cusip=cusip,
            asset_class=AssetClass.US_TREASURY,
            quantity_unit=QuantityUnit.FACE,
            treasury=_treasury().treasury,
        )


def test_market_profile_numbers_are_strict_and_positive() -> None:
    base = {
        "profile_id": "nms-t1",
        "market": "US_EQUITY",
        "asset_class": AssetClass.US_EQUITY,
        "settlement_cycle": 1,
        "calendar": "SIFMA-US",
        "deadline_seconds": 0,
    }
    for bad in (
        base | {"settlement_cycle": 0},
        base | {"settlement_cycle": True},
        base | {"deadline_seconds": -1},
    ):
        with pytest.raises(ValidationError):
            MarketProfile.model_validate(bad)


def test_calendar_enforces_declared_t_plus_one_across_weekend_and_holiday() -> None:
    profile = MarketProfile(
        profile_id="nms-t1",
        market="US_EQUITY",
        asset_class=AssetClass.US_EQUITY,
        settlement_cycle=1,
        calendar="SIFMA-US",
        holidays=frozenset({date(2026, 12, 25)}),
        deadline_seconds=0,
    )
    validate_settlement_date(
        trade_date=date(2026, 12, 24), value_date=date(2026, 12, 28), profile=profile
    )
    for wrong in (date(2026, 12, 24), date(2026, 12, 25), date(2026, 12, 29)):
        with pytest.raises(ValueError, match="SETTLEMENT_CYCLE_MISMATCH"):
            validate_settlement_date(
                trade_date=date(2026, 12, 24), value_date=wrong, profile=profile
            )


def test_rule_612_increment_uses_bucket_only_after_current_relief_expires() -> None:
    pinned = (Path(__file__).parents[1] / "lc/rules/sec-rule-612.txt").read_text()
    assert "legacy minimum pricing increment is $0.01" in pinned
    assert "first business day of November 2027" in pinned
    assert minimum_price_increment(
        price=Decimal(100), business_date=date(2026, 10, 26), bucket=TickBucket.NARROW_SPREAD
    ) == Decimal("0.01")
    assert minimum_price_increment(
        price=Decimal(100), business_date=date(2027, 11, 1), bucket=TickBucket.NARROW_SPREAD
    ) == Decimal("0.005")
    assert minimum_price_increment(
        price=Decimal(100), business_date=date(2027, 11, 1), bucket=TickBucket.STANDARD
    ) == Decimal("0.01")


def test_asset_profile_names_market_asset_cycle_and_calendar() -> None:
    pinned = (Path(__file__).parents[1] / "lc/rules/sec-rule-15c6-1.txt").read_text()
    assert "later than the first business day after the date of the contract" in pinned
    assert "government security" in pinned
    assert BILATERAL_TREASURY.asset_class is AssetClass.US_TREASURY
    assert BILATERAL_TREASURY.settlement_cycle == 1
    assert BILATERAL_TREASURY.calendar == "Fedwire Securities"


# INST-1 Amendment 3, V3: the coupon schedule is built backward from maturity with the
# end-of-month rule. Expected values are hand arithmetic (coupon x accrued days / period days),
# stated in each case. The convention is labelled convention: U.S. Treasury Actual/Actual
# (ICMA), not a quoted rule text.


def _ref(
    rate: str, dated: date, maturity: date, first_coupon: date | None = None
) -> TreasuryReference:
    return TreasuryReference(
        annual_coupon_rate=Decimal(rate),
        dated_date=dated,
        maturity_date=maturity,
        payments_per_year=2,
        first_coupon_date=first_coupon,
    )


def _accrued(settle: date, reference: TreasuryReference) -> Decimal:
    return accrued_interest_actual_actual_icma(
        face=Decimal(1_000_000), settlement_date=settle, reference=reference
    )


def test_v3_month_end_coupons_stay_at_month_end() -> None:
    """28 Feb 2027 to 31 Aug 2027 is 184 days; 183 accrued: 20,000 x 183 / 184.

    Before WP1b the schedule rolled forward and clamped 31 Aug to 28 Feb to 28 Aug, and
    this case returned 217.39.
    """
    reference = _ref("0.04", date(2026, 8, 31), date(2028, 8, 31))
    assert _accrued(date(2027, 8, 30), reference) == Decimal("19891.30")


def test_v3_february_maturity_pays_on_the_last_day_of_august() -> None:
    """28 Feb 2027 to 31 Aug 2027 is 184 days; 107 accrued: 10,000 x 107 / 184."""
    reference = _ref("0.02", date(2026, 2, 28), date(2029, 2, 28))
    assert _accrued(date(2027, 6, 15), reference) == Decimal("5815.22")


def test_v3_short_first_coupon_accrues_from_the_dated_date() -> None:
    """Dated 15 Oct, first coupon 15 Nov 2026; quasi period 15 May to 15 Nov is 184 days.

    17 days accrued (15 Oct to 1 Nov): 20,000 x 17 / 184.
    """
    reference = _ref("0.04", date(2026, 10, 15), date(2031, 11, 15))
    assert _accrued(date(2026, 11, 1), reference) == Decimal("1847.83")


def test_v3_long_first_coupon_spans_two_quasi_periods() -> None:
    """First coupon 15 May 2027: 31 of 184 days, then 61 of 181 days.

    20,000 x (31 / 184 + 61 / 181) = 10,109.90.
    """
    reference = _ref("0.04", date(2026, 10, 15), date(2031, 11, 15), date(2027, 5, 15))
    assert _accrued(date(2027, 1, 15), reference) == Decimal("10109.90")


def test_v3_a_first_coupon_off_the_schedule_is_refused() -> None:
    reference = _ref("0.04", date(2026, 10, 15), date(2031, 11, 15), date(2027, 5, 20))
    with pytest.raises(ValueError, match="FIRST_COUPON_NOT_ON_SCHEDULE"):
        _accrued(date(2027, 1, 15), reference)
