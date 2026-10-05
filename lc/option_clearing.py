"""L.C. admits a central counterparty's novation of a listed option trade (ORDER SC-3, WP-2).

EXPERIMENTAL (charter section 18.6). Nothing here is production evidence.

WHAT ARRIVES
------------
A novation crosses into L.C. as two things, from a central counterparty L.C.
never imports: the exact bytes of the counterparty's novation record, and a
kernel :class:`~cannae_kernel.envelopes.ExecutionEvent` attesting their digest.

WHAT IS CHECKED, IN ORDER
-------------------------
1. The digest of the bytes as received equals the digest the event attests.
   This is checked before parsing, so nothing is parsed that was not attested.
2. The bytes parse as L.C.'s own typed record of a novation
   (:class:`NovatedTradeFact`), strictly.
3. The parsed record re-serialises to exactly the bytes received. The record
   L.C. holds is therefore the one the counterparty attested, with nothing
   dropped, defaulted or reshaped on the way: no translation.

A novation that fails any check is not admitted, and the reason is typed
(:class:`NovationRefusal`). The event's provenance is the kernel's to enforce:
an execution is ``FACT_EXTERNAL`` or ``FACT_SYNTHETIC``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from cannae_kernel.canonical import canonical_bytes, digest_bytes
from cannae_kernel.envelopes import ExecutionEvent
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lc.options import PositionAccountType

__all__ = [
    "AdmittedNovation",
    "NovatedTradeFact",
    "NovationNotAdmittedError",
    "NovationPartyFact",
    "NovationRefusal",
    "admit_novation",
]


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class NovationPartyFact(_Record):
    """One side of a novated trade, now facing the central counterparty."""

    report_id: str = Field(min_length=1)
    clearing_member_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    account_type: PositionAccountType
    open_close: Literal["OPEN", "CLOSE"]


class NovatedTradeFact(_Record):
    """A novated listed option trade, as the central counterparty reported it."""

    novation_id: str = Field(min_length=1)
    trade_id: str = Field(min_length=1)
    central_counterparty: str = Field(min_length=1)
    series_osi: str = Field(pattern=r"^[A-Z0-9 ]{6}\d{6}[CP]\d{8}$")
    contracts: int = Field(gt=0)
    premium: Decimal = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    trade_date: date
    buyer: NovationPartyFact
    seller: NovationPartyFact


class NovationRefusal(StrEnum):
    DIGEST_MISMATCH = "digest_mismatch"
    """The bytes received are not the bytes the event attests."""
    MALFORMED = "malformed"
    """The bytes do not parse as a novation record."""
    NOT_CANONICAL = "not_canonical"
    """The bytes parse, but are not the canonical form of what they say: something
    was added, reordered or reformatted after attestation."""


class NovationNotAdmittedError(ValueError):
    def __init__(self, refusal: NovationRefusal, detail: str) -> None:
        super().__init__(f"{refusal.value}: {detail}")
        self.refusal = refusal


class AdmittedNovation(_Record):
    """A novation L.C. has verified and holds, with the event that attested it."""

    event: ExecutionEvent
    trade: NovatedTradeFact


def admit_novation(event: ExecutionEvent, payload: bytes) -> AdmittedNovation:
    """Verify ``payload`` against ``event`` and parse it, or say why not."""
    received = digest_bytes(payload)
    if received != event.payload_digest:
        raise NovationNotAdmittedError(
            NovationRefusal.DIGEST_MISMATCH,
            f"received {received}, the event attests {event.payload_digest}",
        )
    try:
        trade = NovatedTradeFact.model_validate_json(payload)
    except ValidationError as error:
        raise NovationNotAdmittedError(
            NovationRefusal.MALFORMED, error.errors()[0]["msg"]
        ) from None
    if canonical_bytes(trade) != payload:
        raise NovationNotAdmittedError(
            NovationRefusal.NOT_CANONICAL,
            "the parsed record does not re-serialise to the bytes received",
        )
    return AdmittedNovation(event=event, trade=trade)
