"""One published OCC membership criterion: initial net capital (ORDER SC-3, WP-6).

EXPERIMENTAL (charter section 18.6). Nothing here is production evidence, and
nothing here says whether OCC (the Options Clearing Corporation) would admit a
firm.

WHAT IS CHECKED, AND WHAT IS NOT
--------------------------------
OCC publishes an initial minimum net capital criterion for clearing membership
applicants and directs readers to its Rules and By-Laws for the exact
requirements. This module checks that one published criterion and nothing
else: not the Rules, not continuing requirements, not any other condition of
membership.

The criterion comes from the cited, hash-pinned OCC table
(:mod:`lc.occ_rules`, item :data:`CRITERION_ITEM`), never from a figure in
code. The firm's net capital comes from an entitled caller as typed evidence
(:class:`InitialNetCapitalEvidence`). This module imports no settlement
domain's code to compute it.

DISPOSITION
-----------
- INDETERMINATE: the evidence is missing, the table has no admitted criterion,
  or the two are in different currencies. Nothing can be said.
- HOLD: the evidence is below the criterion. The result cites the criterion.
  It is not a claim that OCC has refused anyone.
- PASS: the evidence meets the criterion. It says only that this one published
  criterion was met.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final, Literal

from cannae_kernel.disposition import Disposition
from cannae_kernel.provenance import Provenance
from pydantic import BaseModel, ConfigDict, Field

from lc.occ_rules import OccRuleItem, OccRuleTable

__all__ = [
    "CLAIM",
    "CRITERION_ITEM",
    "InitialNetCapitalEvidence",
    "MembershipCriterionCheck",
    "check_initial_net_capital",
]

#: The table item holding OCC's published initial net capital criterion.
CRITERION_ITEM: Final = "occ.membership.initial_net_capital"
CLAIM: Final = "one published OCC application criterion; not OCC membership or approval"


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class InitialNetCapitalEvidence(_Record):
    """The firm's net capital, as an entitled caller supplies it."""

    amount: Decimal = Field(ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    #: Who supplied it, for example the firm's net capital computation.
    source: str = Field(min_length=1)
    as_of_dtg: str = Field(pattern=r"^\d{12}$")
    provenance: Provenance


class MembershipCriterionCheck(_Record):
    disposition: Disposition
    reason: str
    claim: Literal["one published OCC application criterion; not OCC membership or approval"] = (
        CLAIM
    )
    criterion: OccRuleItem | None = None
    evidence: InitialNetCapitalEvidence | None = None
    table_version: str
    table_items_digest: str


def check_initial_net_capital(
    evidence: InitialNetCapitalEvidence | None, table: OccRuleTable
) -> MembershipCriterionCheck:
    """Check ``evidence`` against the published criterion in ``table``. Pure."""
    criterion = table.item(CRITERION_ITEM)

    def result(disposition: Disposition, reason: str) -> MembershipCriterionCheck:
        return MembershipCriterionCheck(
            disposition=disposition,
            reason=reason,
            criterion=criterion,
            evidence=evidence,
            table_version=table.table_version,
            table_items_digest=table.items_digest,
        )

    if criterion is None:
        return result(Disposition.INDETERMINATE, table.why_absent(CRITERION_ITEM))
    if evidence is None:
        return result(Disposition.INDETERMINATE, "no initial net capital evidence was supplied")
    if evidence.currency != criterion.unit:
        return result(
            Disposition.INDETERMINATE,
            f"the evidence is in {evidence.currency} and the criterion in {criterion.unit}",
        )
    cited = f'"{criterion.source.verbatim}" ({criterion.source.url})'
    if evidence.amount < criterion.decimal_value:
        return result(
            Disposition.HOLD,
            f"net capital {evidence.amount} {evidence.currency} is below the published "
            f"criterion of {criterion.value} {criterion.unit}: {cited}",
        )
    return result(
        Disposition.PASS,
        f"net capital {evidence.amount} {evidence.currency} meets the published criterion "
        f"of {criterion.value} {criterion.unit}: {cited}. This is {CLAIM}.",
    )
