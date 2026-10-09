"""Listed option series and position accounts (ORDER SC-3, WP-1).

EXPERIMENTAL (charter section 18.6). Nothing here is production evidence, and
nothing here clears, exercises or settles anything.

A SERIES IS NAMED BY ITS TERMS
------------------------------
An option series is its root, expiry, right (call or put) and strike. Its
identifier is derived from those terms in the OSI (Options Symbology
Initiative) layout: the root padded to six characters, the expiry as YYMMDD,
``C`` or ``P``, and the strike in thousandths as eight digits. The identifier
is never stored apart from the terms, so the two cannot disagree, and parsing
an identifier rebuilds the same terms (:meth:`OptionSeries.from_osi`).

WHAT ONE CONTRACT DELIVERS IS DATA
----------------------------------
The deliverable is stated on the series, component by component, and nothing
here assumes a contract size. A standard deliverable is a single quantity of
the underlying security. Anything else (several securities, cash in lieu, a
quantity changed by a corporate action) is an adjusted deliverable, and an
adjusted deliverable cannot be constructed without the evidence for the
adjustment: the OCC (Options Clearing Corporation) notice it comes from, with
its URL, retrieval DTG (date-time group) and SHA-256. Adjustments are
ingested, never computed.

SETTLEMENT PATHS ARE PROFILES
-----------------------------
A premium settles as a payment, and an exercise or assignment settles as
delivery against payment of the strike. They are different paths to finality,
so they are different asset profiles, admitted to the existing registry as
data (:data:`LISTED_OPTION_ASSET_PROFILES`). An adjusted deliverable can add
two more: a further security delivered free of payment, and a cash component
such as cash in lieu paid on its own.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self

from cannae_kernel.delivery import DeliveryPattern
from pydantic import BaseModel, ConfigDict, Field, model_validator

from lc.asset_profile import DEFAULT_ASSET_PROFILES, AssetProfile, AssetProfileRegistry

__all__ = [
    "LISTED_OPTION_ASSET_PROFILES",
    "LISTED_OPTION_EXERCISE",
    "LISTED_OPTION_EXERCISE_CASH",
    "LISTED_OPTION_EXERCISE_FREE_DELIVERY",
    "LISTED_OPTION_PREMIUM",
    "AdjustmentEvidence",
    "CashComponent",
    "Deliverable",
    "DeliverableKind",
    "ExerciseStyle",
    "OptionRight",
    "OptionSeries",
    "PositionAccount",
    "PositionAccountType",
    "PositionCapability",
    "PositionCapabilityRegistry",
    "PositionProduct",
    "SharesComponent",
    "UnsupportedPositionError",
]

_ROOT = r"^[A-Z0-9]{1,6}$"
_DTG = r"^\d{12}$"
_SHA256 = r"^[0-9a-f]{64}$"
#: The OSI layout: 21 characters, a two-digit expiry year, and a strike field of eight
#: digits of thousandths.
_OSI_LENGTH = 21
_OSI_YEARS = range(2000, 2100)
_MAX_STRIKE_THOUSANDTHS = 99_999_999


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class OptionRight(StrEnum):
    CALL = "C"
    PUT = "P"


class ExerciseStyle(StrEnum):
    #: Exercisable on any business day up to expiry.
    AMERICAN = "AMERICAN"
    #: Exercisable only at expiry.
    EUROPEAN = "EUROPEAN"


class DeliverableKind(StrEnum):
    STANDARD = "STANDARD"
    ADJUSTED = "ADJUSTED"


class AdjustmentEvidence(_Record):
    """The OCC notice an adjusted deliverable comes from."""

    #: The notice's own identifier, for example an information memo number.
    notice_id: str = Field(min_length=1)
    url: str = Field(pattern=r"^https://")
    retrieved_dtg: str = Field(pattern=_DTG)
    sha256: str = Field(pattern=_SHA256)


class SharesComponent(_Record):
    """A quantity of one security delivered per contract."""

    component_type: Literal["shares"] = "shares"
    security_id: str = Field(min_length=1)
    quantity: Decimal = Field(gt=0)


class CashComponent(_Record):
    """A cash amount delivered per contract, such as cash in lieu."""

    component_type: Literal["cash"] = "cash"
    amount: Decimal = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class Deliverable(_Record):
    """What one contract delivers on exercise, as stated, never assumed."""

    kind: DeliverableKind
    components: tuple[SharesComponent | CashComponent, ...] = Field(min_length=1)
    evidence: AdjustmentEvidence | None = None

    @model_validator(mode="after")
    def _evidence_matches_kind(self) -> Self:
        if self.kind is DeliverableKind.ADJUSTED and self.evidence is None:
            raise ValueError("an adjusted deliverable requires the evidence for its adjustment")
        if self.kind is DeliverableKind.STANDARD:
            if self.evidence is not None:
                raise ValueError("a standard deliverable carries no adjustment evidence")
            if len(self.components) != 1 or not isinstance(self.components[0], SharesComponent):
                raise ValueError(
                    "a standard deliverable is one quantity of the underlying; "
                    "anything else is adjusted"
                )
        return self


class OptionSeries(_Record):
    """One listed option series. Its identity is its terms."""

    root: str = Field(pattern=_ROOT)
    #: The underlying security a standard deliverable is a quantity of.
    underlying_security_id: str = Field(min_length=1)
    expiry: date
    right: OptionRight
    strike: Decimal = Field(gt=0)
    style: ExerciseStyle
    deliverable: Deliverable

    @model_validator(mode="after")
    def _expressible_in_osi(self) -> Self:
        if self.expiry.year not in _OSI_YEARS:
            raise ValueError("an OSI expiry carries a two-digit year, 2000 to 2099")
        thousandths = self.strike * 1000
        if thousandths != thousandths.to_integral_value():
            raise ValueError("an OSI strike is stated to at most three decimal places")
        if thousandths > _MAX_STRIKE_THOUSANDTHS:
            raise ValueError("an OSI strike has at most five whole digits")
        if self.deliverable.kind is DeliverableKind.STANDARD:
            (component,) = self.deliverable.components
            assert isinstance(component, SharesComponent)
            if component.security_id != self.underlying_security_id:
                raise ValueError("a standard deliverable is the series' own underlying")
        return self

    @property
    def osi_identifier(self) -> str:
        """The 21-character OSI identifier, derived from the terms."""
        thousandths = int(self.strike * 1000)
        return f"{self.root:<6}{self.expiry:%y%m%d}{self.right.value}{thousandths:08d}"

    @classmethod
    def from_osi(
        cls,
        identifier: str,
        *,
        underlying_security_id: str,
        style: ExerciseStyle,
        deliverable: Deliverable,
    ) -> OptionSeries:
        """Rebuild a series from its identifier and the facts the identifier does not carry.

        Refuses an identifier that is not exactly what the parsed terms produce, so
        two spellings of one series cannot both be accepted.
        """
        if len(identifier) != _OSI_LENGTH:
            raise ValueError("an OSI identifier is 21 characters")
        root, expiry, right, strike = (
            identifier[:6].rstrip(" "),
            identifier[6:12],
            identifier[12],
            identifier[13:],
        )
        if not strike.isdigit() or not expiry.isdigit():
            raise ValueError(f"{identifier!r} is not an OSI identifier")
        series = cls(
            root=root,
            underlying_security_id=underlying_security_id,
            # Two OSI digits are a year in 2000 to 2099. strptime's %y would read 69 to 99
            # as the twentieth century.
            expiry=date(_OSI_YEARS.start + int(expiry[:2]), int(expiry[2:4]), int(expiry[4:])),
            right=OptionRight(right),
            strike=Decimal(int(strike)) / 1000,
            style=style,
            deliverable=deliverable,
        )
        if series.osi_identifier != identifier:
            raise ValueError(f"{identifier!r} is not the canonical spelling of its own terms")
        return series


class PositionAccountType(StrEnum):
    """The account a clearing member holds an option position in."""

    CUSTOMER = "CUSTOMER"
    FIRM = "FIRM"
    MARKET_MAKER = "MARKET_MAKER"


class PositionProduct(StrEnum):
    """Products for which L.C. currently carries typed position accounts."""

    LISTED_OPTION = "LISTED_OPTION"


class PositionAccount(_Record):
    clearing_member_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    account_type: PositionAccountType


class PositionCapability(_Record):
    """The products one identified position account is allowed to carry."""

    account: PositionAccount
    products: frozenset[PositionProduct]


class UnsupportedPositionError(ValueError):
    """An account is absent from the registry or cannot carry the requested product."""


class PositionCapabilityRegistry(_Record):
    """The sole account-to-product capability source for L.C. position boundaries."""

    entries: tuple[PositionCapability, ...]

    @model_validator(mode="after")
    def _accounts_are_unique(self) -> Self:
        accounts = tuple(entry.account for entry in self.entries)
        if len(set(accounts)) != len(accounts):
            raise ValueError("a position account appears more than once in the capability registry")
        return self

    def require(self, account: PositionAccount, product: PositionProduct) -> None:
        for entry in self.entries:
            if entry.account == account:
                if product in entry.products:
                    return
                raise UnsupportedPositionError(
                    f"account {account.account_id} cannot carry {product.value} positions"
                )
        raise UnsupportedPositionError(
            f"account {account.account_id} has no recorded position capability"
        )


LISTED_OPTION_PREMIUM = AssetProfile(
    profile_id="equity-option.listed.premium",
    settlement_pattern=DeliveryPattern.PAYMENT_ONLY,
    cash_representation="commercial-bank money through each clearing member's settlement bank",
    custody_path="option positions carried at the central counterparty",
    conditional_execution="premium paid against the novated trade, with no securities leg",
    securities_finality_evidence="not applicable: a premium moves no securities",
    cash_finality_evidence="central counterparty premium settlement confirmation",
)

LISTED_OPTION_EXERCISE = AssetProfile(
    profile_id="equity-option.listed.exercise",
    settlement_pattern=DeliveryPattern.DVP,
    cash_representation="commercial-bank money for the strike consideration",
    custody_path="underlying securities through the equity settlement system",
    conditional_execution="deliver the deliverable against payment of the strike",
    securities_finality_evidence="underlying securities delivery finality",
    cash_finality_evidence="strike consideration payment finality",
)

LISTED_OPTION_EXERCISE_FREE_DELIVERY = AssetProfile(
    profile_id="equity-option.listed.exercise.free-delivery",
    settlement_pattern=DeliveryPattern.FOP,
    cash_representation="not applicable: the strike settles against the first security",
    custody_path="underlying securities through the equity settlement system",
    conditional_execution="deliver a further security of an adjusted deliverable, free of payment",
    securities_finality_evidence="underlying securities delivery finality",
    cash_finality_evidence="not applicable: no cash moves",
)

LISTED_OPTION_EXERCISE_CASH = AssetProfile(
    profile_id="equity-option.listed.exercise.cash",
    settlement_pattern=DeliveryPattern.PAYMENT_ONLY,
    cash_representation="commercial-bank money through each clearing member's settlement bank",
    custody_path="not applicable: no securities move",
    conditional_execution="pay a cash component of an adjusted deliverable, or a strike alone",
    securities_finality_evidence="not applicable: no securities move",
    cash_finality_evidence="cash component payment finality",
)

#: The default registry with every listed option path admitted, as data.
LISTED_OPTION_ASSET_PROFILES: AssetProfileRegistry = (
    DEFAULT_ASSET_PROFILES.register(LISTED_OPTION_PREMIUM)
    .register(LISTED_OPTION_EXERCISE)
    .register(LISTED_OPTION_EXERCISE_FREE_DELIVERY)
    .register(LISTED_OPTION_EXERCISE_CASH)
)
