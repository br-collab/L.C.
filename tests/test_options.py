"""SC-3 WP-1 acceptance: listed option series and position accounts.

Acceptance criteria, mapped:

- Series identity is canonical: ``test_the_identifier_is_derived_from_the_terms``,
  ``test_every_series_on_the_grid_round_trips_through_its_identifier``,
  ``test_each_term_changes_the_identity`` and ``test_a_non_canonical_identifier_is_refused``.
- Adjusted deliverable requires adjustment evidence:
  ``test_an_adjusted_deliverable_requires_its_evidence`` and the deliverable cases beside it.

Every series, account and notice below is SYNTHETIC.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from itertools import product
from pathlib import Path
from typing import Any

import pytest
from cannae_kernel.delivery import DeliveryPattern
from pydantic import ValidationError

import lc.options as options_module
from lc.asset_profile import DEFAULT_ASSET_PROFILES
from lc.options import (
    LISTED_OPTION_ASSET_PROFILES,
    AdjustmentEvidence,
    CashComponent,
    Deliverable,
    DeliverableKind,
    ExerciseStyle,
    OptionRight,
    OptionSeries,
    PositionAccount,
    PositionAccountType,
    SharesComponent,
)

D = Decimal
UNDERLYING = "SYNTHETIC-XYZ"
CONTRACT_SHARES = D(100)  # SYNTHETIC data for these tests; the module assumes no size
STANDARD = Deliverable(
    kind=DeliverableKind.STANDARD,
    components=(SharesComponent(security_id=UNDERLYING, quantity=CONTRACT_SHARES),),
)
NOTICE = AdjustmentEvidence(
    notice_id="SYNTHETIC-MEMO-1",
    url="https://example.invalid/synthetic-memo-1",
    retrieved_dtg="202610051200",
    sha256="ab" * 32,
)


def series(**changes: Any) -> OptionSeries:
    base: dict[str, Any] = {
        "root": "SYN",
        "underlying_security_id": UNDERLYING,
        "expiry": date(2026, 12, 18),
        "right": OptionRight.CALL,
        "strike": D("150.5"),
        "style": ExerciseStyle.AMERICAN,
        "deliverable": STANDARD,
    }
    return OptionSeries(**(base | changes))


# --- identity ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "identifier"),
    [
        ({}, "SYN   261218C00150500"),
        ({"right": OptionRight.PUT, "strike": D("0.5")}, "SYN   261218P00000500"),
        ({"root": "SYNTHX", "strike": D("99999.999")}, "SYNTHX261218C99999999"),
        ({"root": "S1", "expiry": date(2030, 1, 4), "strike": D(25)}, "S1    300104C00025000"),
    ],
)
def test_the_identifier_is_derived_from_the_terms(changes: dict[str, Any], identifier: str) -> None:
    built = series(**changes)
    assert built.osi_identifier == identifier
    assert len(identifier) == 21


_ROOTS = ("A", "SYN", "SYNTHX", "S2")
_EXPIRIES = (date(2000, 1, 1), date(2026, 12, 18), date(2099, 12, 31))
_STRIKES = (D("0.001"), D("1"), D("12.5"), D("150.125"), D("99999.999"))


@pytest.mark.parametrize(
    ("root", "expiry", "right", "strike"),
    list(product(_ROOTS, _EXPIRIES, OptionRight, _STRIKES)),
)
def test_every_series_on_the_grid_round_trips_through_its_identifier(
    root: str, expiry: date, right: OptionRight, strike: Decimal
) -> None:
    built = series(root=root, expiry=expiry, right=right, strike=strike)
    rebuilt = OptionSeries.from_osi(
        built.osi_identifier,
        underlying_security_id=UNDERLYING,
        style=ExerciseStyle.AMERICAN,
        deliverable=STANDARD,
    )
    assert rebuilt == built
    assert rebuilt.osi_identifier == built.osi_identifier


@pytest.mark.parametrize(
    "changes",
    [
        {"root": "SYM"},
        {"expiry": date(2026, 12, 19)},
        {"right": OptionRight.PUT},
        {"strike": D("150.501")},
    ],
)
def test_each_term_changes_the_identity(changes: dict[str, Any]) -> None:
    assert series(**changes).osi_identifier != series().osi_identifier


def test_equal_terms_give_one_identity_however_the_strike_is_written() -> None:
    assert series(strike=D("150.500")).osi_identifier == series(strike=D("150.5")).osi_identifier


@pytest.mark.parametrize(
    ("identifier", "match"),
    [
        ("SYN  261218C00150500", "21 characters"),
        ("SYN   261218C0015050X", "not an OSI identifier"),
        ("SYN   2612X8C00150500", "not an OSI identifier"),
        ("SYN   261218X00150500", "valid OptionRight"),
        ("SYN   261332C00150500", "month must be in 1..12"),
        ("   SYN261218C00150500", "String should match pattern"),
        ("syn   261218C00150500", "String should match pattern"),
    ],
)
def test_a_non_canonical_identifier_is_refused(identifier: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        OptionSeries.from_osi(
            identifier,
            underlying_security_id=UNDERLYING,
            style=ExerciseStyle.AMERICAN,
            deliverable=STANDARD,
        )


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"strike": D("150.0005")}, "three decimal places"),
        ({"strike": D("100000")}, "five whole digits"),
        ({"expiry": date(2100, 1, 1)}, "two-digit year"),
        ({"root": "TOOLONG"}, "String should match pattern"),
        ({"strike": 150.5}, "Decimal"),
    ],
)
def test_a_series_must_be_expressible_in_osi(changes: dict[str, Any], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        series(**changes)


# --- the deliverable --------------------------------------------------------------------------


def test_an_adjusted_deliverable_requires_its_evidence() -> None:
    components = (SharesComponent(security_id=UNDERLYING, quantity=D(150)),)
    with pytest.raises(ValidationError, match="requires the evidence"):
        Deliverable(kind=DeliverableKind.ADJUSTED, components=components)
    adjusted = Deliverable(kind=DeliverableKind.ADJUSTED, components=components, evidence=NOTICE)
    assert series(root="SYN1", deliverable=adjusted).deliverable.evidence == NOTICE


def test_an_adjusted_deliverable_may_combine_securities_and_cash_in_lieu() -> None:
    adjusted = Deliverable(
        kind=DeliverableKind.ADJUSTED,
        evidence=NOTICE,
        components=(
            SharesComponent(security_id="SYNTHETIC-ACQUIRER", quantity=D(52)),
            CashComponent(amount=D("1234.56"), currency="USD"),
        ),
    )
    assert series(root="SYN2", deliverable=adjusted).deliverable.kind is DeliverableKind.ADJUSTED


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"components": STANDARD.components, "evidence": NOTICE}, "carries no adjustment"),
        (
            {"components": (CashComponent(amount=D(1), currency="USD"),)},
            "anything else is adjusted",
        ),
        (
            {
                "components": (
                    *STANDARD.components,
                    SharesComponent(security_id="SYNTHETIC-B", quantity=D(1)),
                )
            },
            "anything else is adjusted",
        ),
        ({"components": ()}, "at least 1 item"),
    ],
)
def test_a_standard_deliverable_is_one_quantity_of_the_underlying(
    kwargs: dict[str, Any], match: str
) -> None:
    with pytest.raises(ValidationError, match=match):
        Deliverable(kind=DeliverableKind.STANDARD, **kwargs)


def test_a_standard_deliverable_is_the_series_own_underlying() -> None:
    other = Deliverable(
        kind=DeliverableKind.STANDARD,
        components=(SharesComponent(security_id="SYNTHETIC-B", quantity=D(100)),),
    )
    with pytest.raises(ValidationError, match="series' own underlying"):
        series(deliverable=other)


@pytest.mark.parametrize(
    "changes",
    [{"url": "http://example.invalid"}, {"retrieved_dtg": "2026-10-05"}, {"sha256": "AB" * 32}],
)
def test_adjustment_evidence_is_pinned(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        AdjustmentEvidence(**(NOTICE.model_dump() | changes))


def test_no_contract_size_is_assumed() -> None:
    """A standard deliverable's quantity is stated on the series, never a constant here."""
    source = Path(options_module.__file__).read_text(encoding="utf-8")
    assert not re.search(r"(?<![\d_])100(?![\d_])", source)


# --- accounts and asset profiles --------------------------------------------------------------


@pytest.mark.parametrize("account_type", list(PositionAccountType))
def test_every_position_account_type(account_type: PositionAccountType) -> None:
    account = PositionAccount(
        clearing_member_id="SYNTHETIC-CM", account_id="A-1", account_type=account_type
    )
    assert account.account_type is account_type


def test_both_settlement_paths_are_admitted_as_data() -> None:
    premium = LISTED_OPTION_ASSET_PROFILES.resolve("equity-option.listed.premium")
    exercise = LISTED_OPTION_ASSET_PROFILES.resolve("equity-option.listed.exercise")
    assert premium.settlement_pattern is DeliveryPattern.PAYMENT_ONLY
    assert exercise.settlement_pattern is DeliveryPattern.DVP
    for profile in DEFAULT_ASSET_PROFILES.profiles:
        assert LISTED_OPTION_ASSET_PROFILES.resolve(profile.profile_id) == profile
    with pytest.raises(KeyError):
        DEFAULT_ASSET_PROFILES.resolve("equity-option.listed.premium")


def test_docstring_states_experimental() -> None:
    assert "EXPERIMENTAL" in (options_module.__doc__ or "")
