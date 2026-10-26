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
