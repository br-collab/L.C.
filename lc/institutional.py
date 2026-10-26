"""Institutional market, instrument, calendar and cash controls for INST-1."""

from __future__ import annotations

import re
from calendar import monthrange
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from itertools import pairwise
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "AssetClass",
    "InstrumentReference",
    "MarketProfile",
    "MarketSessionPermission",
    "QuantityUnit",
    "TickBucket",
    "TreasuryReference",
    "accrued_interest_actual_actual_icma",
    "coupon_schedule",
    "derive_cash",
    "derive_cash_from_fills",
    "minimum_price_increment",
    "validate_settlement_date",
]

CENT = Decimal("0.01")
WEEKDAY_COUNT = 5
RULE_612_AMENDED_COMPLIANCE_DATE = date(2027, 11, 1)
_CUSIP = re.compile(r"^[0-9A-Z*@#]{9}$", re.ASCII)
_CUSIP_VALUES = (
    {str(i): i for i in range(10)}
    | {chr(ord("A") + i): 10 + i for i in range(26)}
    | {"*": 36, "@": 37, "#": 38}
)


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class AssetClass(StrEnum):
    US_TREASURY = "US_TREASURY"
    US_EQUITY = "US_EQUITY"
    LISTED_OPTION = "LISTED_OPTION"


class QuantityUnit(StrEnum):
    FACE = "FACE"
    SHARES = "SHARES"
    CONTRACTS = "CONTRACTS"


class TickBucket(StrEnum):
    STANDARD = "STANDARD"
    NARROW_SPREAD = "NARROW_SPREAD"


class TreasuryReference(_Record):
    """Declared synthetic terms needed for Actual/Actual (ICMA) accrued interest."""

    annual_coupon_rate: Decimal = Field(ge=0, le=1)
    dated_date: date
    maturity_date: date
    payments_per_year: int = Field(gt=0, le=12)
    #: The first coupon date when it is not the first scheduled date after the dated date
    #: (a long first coupon). Absent means a regular or short first coupon.
    first_coupon_date: date | None = None

    @model_validator(mode="after")
    def _dates_are_ordered(self) -> Self:
        if self.dated_date >= self.maturity_date:
            raise ValueError("TREASURY_REFERENCE_DATES_INVALID")
        if 12 % self.payments_per_year:
            raise ValueError("TREASURY_PAYMENT_FREQUENCY_INVALID")
        return self


class InstrumentReference(_Record):
    cusip: str
    asset_class: AssetClass
    quantity_unit: QuantityUnit
    treasury: TreasuryReference | None = None

    @field_validator("cusip", mode="before")
    @classmethod
    def _normalise_cusip(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return value.strip().upper()

    @field_validator("cusip")
    @classmethod
    def _valid_cusip(cls, value: str) -> str:
        if not value.isascii() or not _CUSIP.fullmatch(value):
            raise ValueError("CUSIP_FORMAT_INVALID")
        total = 0
        for index, char in enumerate(value[:8]):
            number = _CUSIP_VALUES[char] * (2 if index % 2 else 1)
            total += number // 10 + number % 10
        if (10 - total % 10) % 10 != int(value[8]):
            raise ValueError("CUSIP_CHECK_DIGIT_INVALID")
        return value

    @model_validator(mode="after")
    def _terms_match_asset(self) -> Self:
        if self.asset_class is AssetClass.US_TREASURY:
            if self.quantity_unit is not QuantityUnit.FACE or self.treasury is None:
                raise ValueError("TREASURY_REFERENCE_REQUIRED")
        elif self.treasury is not None:
            raise ValueError("ACCRUED_INTEREST_NOT_PERMITTED")
        if (
            self.asset_class is AssetClass.US_EQUITY
            and self.quantity_unit is not QuantityUnit.SHARES
        ):
            raise ValueError("EQUITY_QUANTITY_UNIT_INVALID")
        return self


class MarketSessionPermission(_Record):
    session_name: str = Field(min_length=1)
    activity_permitted: bool


class MarketProfile(_Record):
    profile_id: str = Field(min_length=1)
    market: str = Field(min_length=1)
    asset_class: AssetClass
    settlement_cycle: int = Field(gt=0, le=5)
    calendar: str = Field(min_length=1)
    holidays: frozenset[date] = frozenset()
    deadline_seconds: int = Field(ge=0)
    tick_bucket: TickBucket | None = None
    sessions: tuple[MarketSessionPermission, ...] = ()

    def permits(self, session_name: str) -> bool:
        permission = next(
            (item for item in self.sessions if item.session_name == session_name), None
        )
        return permission is not None and permission.activity_permitted


def _shift_month(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def _last_day(value: date) -> int:
    return monthrange(value.year, value.month)[1]


def _back_from_maturity(maturity: date, months: int, *, end_of_month: bool) -> date:
    """A coupon date ``months`` before maturity, keeping maturity's day of month.

    End-of-month rule: when maturity falls on the last day of its month, every coupon
    falls on the last day of its month (31 Aug, 28 or 29 Feb, 31 Aug ...), so the schedule
    never drifts to the 28th after passing through February.
    """
    shifted = _shift_month(maturity, -months)
    if end_of_month:
        return shifted.replace(day=_last_day(shifted))
    return shifted


def coupon_schedule(reference: TreasuryReference) -> tuple[date, ...]:
    """Quasi-coupon dates from the one on or before the dated date through maturity.

    Generated backward from maturity, as U.S. Treasury notes and bonds pay on the
    maturity date's day of month (or the last day, under the end-of-month rule).
    """
    months = 12 // reference.payments_per_year
    end_of_month = reference.maturity_date.day == _last_day(reference.maturity_date)
    dates = [reference.maturity_date]
    step = 1
    while dates[-1] > reference.dated_date:
        dates.append(
            _back_from_maturity(reference.maturity_date, months * step, end_of_month=end_of_month)
        )
        step += 1
    return tuple(reversed(dates))


def accrued_interest_actual_actual_icma(
    *, face: Decimal, settlement_date: date, reference: TreasuryReference
) -> Decimal:
    """Actual/Actual (ICMA): actual days accrued over actual days in the coupon period.

    INST-1 Amendment 3 (V3). The schedule is built backward from maturity with the
    end-of-month rule (``coupon_schedule``). Before the first coupon, interest accrues
    from the dated date, measured against the quasi-coupon period(s) it falls in, so a
    short or long first coupon is valued correctly. The convention is the order's stated
    U.S. Treasury market convention; cent rounding is declared firm policy because the
    convention does not prescribe this simulator's cash-ledger rounding.
    """
    if not reference.dated_date <= settlement_date <= reference.maturity_date:
        raise ValueError("SETTLEMENT_OUTSIDE_TREASURY_TERM")
    quasi = coupon_schedule(reference)
    first_payment = reference.first_coupon_date or next(
        item for item in quasi if item > reference.dated_date
    )
    if first_payment not in quasi or first_payment <= reference.dated_date:
        raise ValueError("FIRST_COUPON_NOT_ON_SCHEDULE")
    payments = tuple(item for item in quasi if item >= first_payment)
    paid = [item for item in payments if item <= settlement_date]
    coupon = face * reference.annual_coupon_rate / reference.payments_per_year
    if paid:
        last = paid[-1]
        following = next((item for item in quasi if item > last), last)
        if following == last:
            return Decimal("0.00")
        fraction = Decimal((settlement_date - last).days) / Decimal((following - last).days)
    else:
        fraction = Decimal(0)
        for begin, finish in pairwise(quasi):
            low = max(begin, reference.dated_date)
            high = min(finish, settlement_date)
            if high > low:
                fraction += Decimal((high - low).days) / Decimal((finish - begin).days)
    return (coupon * fraction).quantize(CENT, rounding=ROUND_HALF_EVEN)


def derive_cash(
    *,
    quantity: Decimal,
    price: Decimal,
    settlement_date: date,
    instrument: InstrumentReference,
) -> tuple[Decimal, Decimal, Decimal]:
    """Derive principal, accrued and total; never trust a supplied cash total.

    INST-1 WP1.2: "US Treasuries: quantity is face (par) amount in USD;
    price is per 100 of par; principal = face x price / 100." Equities use
    ``shares x price``. ``ROUND_HALF_EVEN_TO_CENT`` is declared firm policy.
    """
    if not quantity.is_finite() or not price.is_finite() or quantity <= 0 or price <= 0:
        raise ValueError("NON_FINITE_OR_NON_POSITIVE_ECONOMICS")
    if instrument.asset_class is AssetClass.US_TREASURY:
        principal = quantity * price / Decimal(100)
        assert instrument.treasury is not None
        accrued = accrued_interest_actual_actual_icma(
            face=quantity, settlement_date=settlement_date, reference=instrument.treasury
        )
    else:
        principal = quantity * price
        accrued = Decimal(0)
    principal = principal.quantize(CENT, rounding=ROUND_HALF_EVEN)
    return principal, accrued, (principal + accrued).quantize(CENT, rounding=ROUND_HALF_EVEN)


def derive_cash_from_fills(
    *,
    quantity: Decimal,
    consideration: Decimal,
    settlement_date: date,
    instrument: InstrumentReference,
) -> tuple[Decimal, Decimal, Decimal]:
    """Principal, accrued and total from the order's sealed fills.

    INST-1 Amendment 3 (V1). ``consideration`` is the sum of quantity x price over the
    sealed fills (``OrderSnapshot.executed_consideration``), so an average price is never
    rounded first. Treasury prices are per 100 of par, so principal is consideration / 100.
    """
    if (
        not quantity.is_finite()
        or not consideration.is_finite()
        or quantity <= 0
        or consideration <= 0
    ):
        raise ValueError("NON_FINITE_OR_NON_POSITIVE_ECONOMICS")
    if instrument.asset_class is AssetClass.US_TREASURY:
        if instrument.treasury is None:
            raise ValueError("TREASURY_REFERENCE_REQUIRED")
        principal = consideration / Decimal(100)
        accrued = accrued_interest_actual_actual_icma(
            face=quantity, settlement_date=settlement_date, reference=instrument.treasury
        )
    else:
        principal = consideration
        accrued = Decimal(0)
    principal = principal.quantize(CENT, rounding=ROUND_HALF_EVEN)
    return principal, accrued, (principal + accrued).quantize(CENT, rounding=ROUND_HALF_EVEN)


def _business_days_after(start: date, count: int, holidays: frozenset[date]) -> date:
    current = start
    moved = 0
    while moved < count:
        current += timedelta(days=1)
        if current.weekday() < WEEKDAY_COUNT and current not in holidays:
            moved += 1
    return current


def validate_settlement_date(*, trade_date: date, value_date: date, profile: MarketProfile) -> None:
    """Validate the profile's declared cycle on its named market calendar.

    17 CFR 240.15c6-1(a), pinned in ``rules/sec-rule-15c6-1.txt``: a broker or
    dealer shall not contract for covered securities to settle "later than the
    first business day after the date of the contract" unless otherwise agreed.
    Government securities are excluded; their T+1 profile is market convention.
    """
    expected = _business_days_after(trade_date, profile.settlement_cycle, profile.holidays)
    if value_date != expected:
        raise ValueError(
            f"SETTLEMENT_CYCLE_MISMATCH: {profile.calendar} {profile.settlement_cycle=} "
            f"requires {expected.isoformat()}"
        )


def minimum_price_increment(
    *, price: Decimal, business_date: date, bucket: TickBucket | None
) -> Decimal:
    """17 CFR 242.612, including the amended rule's deferred compliance date.

    The pinned rule record states: "For an NMS stock priced at or above $1.00,
    the legacy minimum pricing increment is $0.01." It also records the amended
    $0.005 narrow-spread bucket and Release 34-105656's 2027-11-01 date.
    """
    if price < Decimal(1):
        return Decimal("0.0001")
    if business_date >= RULE_612_AMENDED_COMPLIANCE_DATE and bucket is TickBucket.NARROW_SPREAD:
        return Decimal("0.005")
    return Decimal("0.01")
