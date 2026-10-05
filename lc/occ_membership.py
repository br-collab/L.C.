"""OCC Rule 301(b)(1): minimum net capital for a broker-dealer clearing member (ORDER SC-3, WP-6).

EXPERIMENTAL (charter section 18.6). Nothing here is production evidence, and
nothing here says whether OCC (the Options Clearing Corporation) would admit a
firm.

WHAT IS CHECKED, AND WHAT IS NOT
--------------------------------
OCC Rule 301 applies to "Each Clearing Member, including applicants for
clearing membership". Rule 301(b)(1) requires a fully-registered broker-dealer
to maintain minimum net capital equal to the greater of:

1. a fixed minimum;
2. for a firm on the basic standard, the net capital that keeps aggregate
   indebtedness within its stated ceiling as a percentage of net capital;
3. for a firm on the alternative standard, a stated percentage of aggregate
   debit items.

This module checks that one rule and nothing else: not Rule 301(b)(2) or
(b)(3) for other kinds of firm, and not any other condition of membership.
Every figure comes from the cited, hash-pinned OCC table (:mod:`lc.occ_rules`,
items :data:`MINIMUM_ITEM`, :data:`INDEBTEDNESS_CEILING_ITEM` and
:data:`DEBIT_ITEMS_ITEM`), never from a figure in code. The firm's figures come
from an entitled caller as typed evidence (:class:`NetCapitalEvidence`). This
module imports no settlement domain's code to compute them.

Rule 301 makes no distinction between an initial and an ongoing requirement,
so neither does this module (amendment A5 to ORDER SC-3, approved by Bill on
5 October 2026). OCC's "Becoming a Clearing Member" page still calls the fixed
minimum the "initial requirement"; the rule is what binds.

DISPOSITION
-----------
- INDETERMINATE: the evidence is missing, the firm is not a broker-dealer, the
  measure its standard needs is missing, the table lacks an item, or the
  currencies differ. Nothing can be said.
- HOLD: net capital is below the requirement. The result cites the leg that
  binds. It is not a claim that OCC has refused anyone.
- PASS: net capital meets the requirement. It says only that this one rule was
  met on the evidence supplied.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal
from enum import StrEnum
from typing import Final, Literal

from cannae_kernel.disposition import Disposition
from cannae_kernel.provenance import Provenance
from pydantic import BaseModel, ConfigDict, Field

from lc.occ_rules import OccRuleItem, OccRuleTable

__all__ = [
    "CLAIM",
    "DEBIT_ITEMS_ITEM",
    "INDEBTEDNESS_CEILING_ITEM",
    "MINIMUM_ITEM",
    "FirmRegistration",
    "MembershipCriterionCheck",
    "NetCapitalEvidence",
    "NetCapitalStandard",
    "check_net_capital",
]

_RULE: Final = "occ.rule301.broker_dealer"
#: The fixed minimum, in the table's currency.
MINIMUM_ITEM: Final = f"{_RULE}.minimum_net_capital"
#: The most aggregate indebtedness may be, as a percentage of net capital (basic standard).
INDEBTEDNESS_CEILING_ITEM: Final = f"{_RULE}.aggregate_indebtedness_ceiling"
#: The percentage of aggregate debit items net capital must reach (alternative standard).
DEBIT_ITEMS_ITEM: Final = f"{_RULE}.aggregate_debit_items_percentage"
#: The unit each item must state, so a figure is never read in the wrong sense.
_UNITS: Final = {
    INDEBTEDNESS_CEILING_ITEM: "percent of net capital",
    DEBIT_ITEMS_ITEM: "percent of aggregate debit items",
}
CLAIM: Final = (
    "OCC Rule 301(b)(1) minimum net capital for a broker-dealer; not OCC membership or approval"
)
_HUNDRED: Final = Decimal(100)
_CENT: Final = Decimal("0.01")


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class FirmRegistration(StrEnum):
    """Which paragraph of Rule 301(b) governs the firm. Only (b)(1) is checked here."""

    BROKER_DEALER = "BROKER_DEALER"
    FUTURES_COMMISSION_MERCHANT = "FUTURES_COMMISSION_MERCHANT"
    NON_US_SECURITIES_FIRM = "NON_US_SECURITIES_FIRM"


class NetCapitalStandard(StrEnum):
    #: Not electing the alternative standard: the aggregate indebtedness leg applies.
    BASIC = "BASIC"
    #: Electing the alternative standard: the aggregate debit items leg applies.
    ALTERNATIVE = "ALTERNATIVE"


class NetCapitalEvidence(_Record):
    """The firm's net capital position, as an entitled caller supplies it."""

    registration: FirmRegistration
    standard: NetCapitalStandard
    net_capital: Decimal = Field(ge=0)
    #: Needed on the basic standard.
    aggregate_indebtedness: Decimal | None = Field(default=None, ge=0)
    #: Needed on the alternative standard.
    aggregate_debit_items: Decimal | None = Field(default=None, ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    #: Who supplied it, for example the firm's net capital computation.
    source: str = Field(min_length=1)
    as_of_dtg: str = Field(pattern=r"^\d{12}$")
    provenance: Provenance


class MembershipCriterionCheck(_Record):
    disposition: Disposition
    reason: str
    claim: Literal[
        "OCC Rule 301(b)(1) minimum net capital for a broker-dealer; not OCC membership or approval"
    ] = CLAIM
    #: The greater-of requirement, rounded up to the cent for display. None when indeterminate.
    requirement: Decimal | None = None
    #: The items the requirement was read from.
    criteria: tuple[OccRuleItem, ...] = ()
    evidence: NetCapitalEvidence | None = None
    table_version: str
    table_items_digest: str


def _cite(item: OccRuleItem) -> str:
    return f'"{item.source.verbatim}" ({item.source.url})'


def check_net_capital(  # noqa: PLR0911 - one return per missing input, in checking order
    evidence: NetCapitalEvidence | None, table: OccRuleTable
) -> MembershipCriterionCheck:
    """Check ``evidence`` against Rule 301(b)(1) as the table states it. Pure."""

    def result(
        disposition: Disposition,
        reason: str,
        criteria: tuple[OccRuleItem, ...] = (),
        requirement: Decimal | None = None,
    ) -> MembershipCriterionCheck:
        return MembershipCriterionCheck(
            disposition=disposition,
            reason=reason,
            requirement=requirement,
            criteria=criteria,
            evidence=evidence,
            table_version=table.table_version,
            table_items_digest=table.items_digest,
        )

    if evidence is None:
        return result(Disposition.INDETERMINATE, "no net capital evidence was supplied")
    if evidence.registration is not FirmRegistration.BROKER_DEALER:
        return result(
            Disposition.INDETERMINATE,
            f"Rule 301(b)(1) covers broker-dealers; a {evidence.registration.value} "
            f"falls under another paragraph, which this check does not hold",
        )
    if evidence.standard is NetCapitalStandard.BASIC:
        leg_item, measure, measure_name = (
            INDEBTEDNESS_CEILING_ITEM,
            evidence.aggregate_indebtedness,
            "aggregate indebtedness",
        )
    else:
        leg_item, measure, measure_name = (
            DEBIT_ITEMS_ITEM,
            evidence.aggregate_debit_items,
            "aggregate debit items",
        )
    if measure is None:
        return result(
            Disposition.INDETERMINATE,
            f"the {evidence.standard.value} standard needs {measure_name}, which was not supplied",
        )
    minimum, leg = table.item(MINIMUM_ITEM), table.item(leg_item)
    for item_id, found in ((MINIMUM_ITEM, minimum), (leg_item, leg)):
        if found is None:
            return result(Disposition.INDETERMINATE, table.why_absent(item_id))
    assert minimum is not None and leg is not None
    if leg.unit != _UNITS[leg_item]:
        return result(
            Disposition.INDETERMINATE,
            f"table item {leg_item} is in {leg.unit!r}, not {_UNITS[leg_item]!r}",
        )
    if evidence.currency != minimum.unit:
        return result(
            Disposition.INDETERMINATE,
            f"the evidence is in {evidence.currency} and the minimum in {minimum.unit}",
        )

    criteria = (minimum, leg)
    # The leg's requirement, compared exactly: the basic leg divides by the ceiling,
    # which need not terminate in decimal, so both sides are multiplied out instead.
    if evidence.standard is NetCapitalStandard.BASIC:
        leg_met = evidence.net_capital * leg.decimal_value >= measure * _HUNDRED
        leg_requirement = measure * _HUNDRED / leg.decimal_value
    else:
        leg_met = evidence.net_capital * _HUNDRED >= measure * leg.decimal_value
        leg_requirement = measure * leg.decimal_value / _HUNDRED
    minimum_met = evidence.net_capital >= minimum.decimal_value
    requirement = max(minimum.decimal_value, leg_requirement).quantize(_CENT, ROUND_CEILING)
    stated = (
        f"net capital {evidence.net_capital} {evidence.currency} against a requirement of "
        f"{requirement} {minimum.unit}, the greater of {_cite(minimum)} and, on the "
        f"{evidence.standard.value} standard, {_cite(leg)}"
    )
    if not (minimum_met and leg_met):
        binding = [
            name
            for name, met in (("the fixed minimum", minimum_met), (measure_name, leg_met))
            if not met
        ]
        return result(
            Disposition.HOLD,
            f"{stated}. Below on {' and '.join(binding)}",
            criteria,
            requirement,
        )
    return result(Disposition.PASS, f"{stated}. This is {CLAIM}.", criteria, requirement)
