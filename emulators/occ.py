"""Synthetic OCC-style central counterparty for listed options (ORDER SC-3, WP-2).

EXPERIMENTAL (charter section 18.6). It stands in for OCC (the Options Clearing
Corporation) in deterministic experiments, reports facts from its own state,
and never imports L.C.

WHAT IT DOES
------------
Each clearing member reports its side of an exchange-matched trade. Two
reports with the same trade identifier, one buy and one sell, on the same
series, quantity, premium, currency and trade date, are matched and
**novated**: the central counterparty becomes the buyer to the seller and the
seller to the buyer, and each side's position moves. A report whose
counterpart has not arrived is held as unmatched. Anything else is rejected,
with the reason.

Positions are carried per clearing member, account and series, as long and
short contracts. Opening a long or short adds to it; closing reduces the
opposite side, and closing more than is held is rejected. Across every series,
long contracts equal short contracts after every novation, because every
novation moves the same quantity on both sides.

WHAT IT DOES NOT ASSUME
-----------------------
The premium is the total amount the reporting member states. The emulator
does not multiply a price by a contract size, because contract size is one of
the OCC conventions the order reserves to Bill's verification.

WHAT CROSSES TO L.C.
--------------------
For each novation, the emulator's own typed payload
(:class:`NovatedOptionTrade`) as canonical bytes, and one kernel
:class:`~cannae_kernel.envelopes.ExecutionEvent` per side attesting the
digest of exactly those bytes, with ``FACT_SYNTHETIC`` provenance. An
independent caller carries both to L.C.; neither package imports the other.

Resubmitting a report that was already processed changes nothing and returns
the original result.
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from enum import StrEnum

from cannae_kernel.canonical import canonical_bytes, digest
from cannae_kernel.clocks import EventTimes
from cannae_kernel.envelopes import ExecutionEvent
from cannae_kernel.ids import EventId, IntentId, LifecycleId, encode_ulid
from cannae_kernel.provenance import Provenance
from cannae_kernel.session import SessionContext
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "CENTRAL_COUNTERPARTY",
    "AccountType",
    "NovatedOptionTrade",
    "NovationParty",
    "OccEmulator",
    "OccOutcome",
    "OccResult",
    "OccTradeReport",
    "OpenClose",
    "Position",
    "Side",
]

CENTRAL_COUNTERPARTY = "SYNTHETIC-OCC"
_OSI = r"^[A-Z0-9 ]{6}\d{6}[CP]\d{8}$"


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OpenClose(StrEnum):
    OPEN = "OPEN"
    CLOSE = "CLOSE"


class AccountType(StrEnum):
    CUSTOMER = "CUSTOMER"
    FIRM = "FIRM"
    MARKET_MAKER = "MARKET_MAKER"


class OccTradeReport(_Record):
    """One clearing member's side of an exchange-matched option trade."""

    report_id: str = Field(min_length=1)
    #: The exchange's trade identifier, which both sides share.
    trade_id: str = Field(min_length=1)
    series_osi: str = Field(pattern=_OSI)
    side: Side
    open_close: OpenClose
    contracts: int = Field(gt=0)
    #: The total premium for the trade, as the member states it.
    premium: Decimal = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    trade_date: date
    clearing_member_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    account_type: AccountType
    #: The kernel identities the member's execution belongs to.
    lifecycle_id: LifecycleId
    intent_id: IntentId
    intent_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    session: SessionContext
    times: EventTimes


class NovationParty(_Record):
    """One side of a novated trade, now facing the central counterparty."""

    report_id: str
    clearing_member_id: str
    account_id: str
    account_type: AccountType
    open_close: OpenClose


class NovatedOptionTrade(_Record):
    """The emulator's own record of a novation: the payload that crosses to L.C."""

    novation_id: str
    trade_id: str
    central_counterparty: str
    series_osi: str
    contracts: int
    premium: Decimal
    currency: str
    trade_date: date
    buyer: NovationParty
    seller: NovationParty


class OccOutcome(StrEnum):
    NOVATED = "NOVATED"
    UNMATCHED = "UNMATCHED"
    REJECTED = "REJECTED"


class OccResult(_Record):
    """What happened to one report."""

    report_id: str
    outcome: OccOutcome
    reason: str
    novation: NovatedOptionTrade | None = None
    #: ``canonical_bytes(novation)``: the exact bytes the events attest.
    payload: bytes | None = None
    events: tuple[ExecutionEvent, ...] = ()


class Position(_Record):
    clearing_member_id: str
    account_id: str
    account_type: AccountType
    series_osi: str
    long_contracts: int = Field(ge=0)
    short_contracts: int = Field(ge=0)


_Key = tuple[str, str, AccountType, str]


def _key(report: OccTradeReport) -> _Key:
    return (report.clearing_member_id, report.account_id, report.account_type, report.series_osi)


def _event_id(report: OccTradeReport) -> EventId:
    """A deterministic event identifier: the event's own time, and a hash of the report."""
    milliseconds = int(report.times.event_time.timestamp() * 1000)
    randomness = hashlib.sha256(f"occ:{report.report_id}".encode()).digest()[:10]
    return EventId(EventId.prefix + encode_ulid(milliseconds, randomness))


def _terms(report: OccTradeReport) -> tuple[object, ...]:
    return (report.series_osi, report.contracts, report.premium, report.currency, report.trade_date)


class OccEmulator:
    """A deterministic central counterparty. State is its own; results are facts."""

    def __init__(self) -> None:
        self._results: dict[str, tuple[OccTradeReport, OccResult]] = {}
        self._pending: dict[str, OccTradeReport] = {}
        self._novated: set[str] = set()
        self._long: dict[_Key, int] = {}
        self._short: dict[_Key, int] = {}

    def submit(self, report: OccTradeReport) -> OccResult:
        """Hold, novate or reject ``report``. A resubmission returns the original result."""
        prior = self._results.get(report.report_id)
        if prior is not None:
            if prior[0] == report:
                return prior[1]
            return self._reject(
                report, "report identifier reused with different content", record=False
            )
        if report.trade_id in self._novated:
            return self._reject(report, "trade already novated")
        counterpart = self._pending.get(report.trade_id)
        if counterpart is None:
            self._pending[report.trade_id] = report
            result = OccResult(
                report_id=report.report_id,
                outcome=OccOutcome.UNMATCHED,
                reason="awaiting the counterpart report",
            )
            self._results[report.report_id] = (report, result)
            return result
        return self._match(report, counterpart)

    def _match(self, report: OccTradeReport, counterpart: OccTradeReport) -> OccResult:
        if counterpart.side is report.side:
            return self._reject(report, f"both reports are {report.side.value}")
        if _terms(counterpart) != _terms(report):
            return self._reject(report, "series, contracts, premium, currency or trade date differ")
        buyer, seller = (report, counterpart) if report.side is Side.BUY else (counterpart, report)
        refusal = self._close_refusal(buyer, seller)
        if refusal is not None:
            return self._reject(report, refusal)
        return self._novate(report, buyer, seller)

    def _close_refusal(self, buyer: OccTradeReport, seller: OccTradeReport) -> str | None:
        # A buy to close reduces a short; a sell to close reduces a long.
        buyer_short, seller_long = self._short.get(_key(buyer), 0), self._long.get(_key(seller), 0)
        if buyer.open_close is OpenClose.CLOSE and buyer_short < buyer.contracts:
            return "the buyer closes more short contracts than its account holds"
        if seller.open_close is OpenClose.CLOSE and seller_long < seller.contracts:
            return "the seller closes more long contracts than its account holds"
        return None

    def _reject(self, report: OccTradeReport, reason: str, *, record: bool = True) -> OccResult:
        result = OccResult(report_id=report.report_id, outcome=OccOutcome.REJECTED, reason=reason)
        if record:
            self._results[report.report_id] = (report, result)
        return result

    def _novate(
        self, report: OccTradeReport, buyer: OccTradeReport, seller: OccTradeReport
    ) -> OccResult:
        quantity = buyer.contracts
        if buyer.open_close is OpenClose.OPEN:
            self._long[_key(buyer)] = self._long.get(_key(buyer), 0) + quantity
        else:
            self._short[_key(buyer)] -= quantity
        if seller.open_close is OpenClose.OPEN:
            self._short[_key(seller)] = self._short.get(_key(seller), 0) + quantity
        else:
            self._long[_key(seller)] -= quantity
        del self._pending[report.trade_id]
        self._novated.add(report.trade_id)

        def party(side: OccTradeReport) -> NovationParty:
            return NovationParty(
                report_id=side.report_id,
                clearing_member_id=side.clearing_member_id,
                account_id=side.account_id,
                account_type=side.account_type,
                open_close=side.open_close,
            )

        novation = NovatedOptionTrade(
            novation_id=f"NOV-{report.trade_id}",
            trade_id=report.trade_id,
            central_counterparty=CENTRAL_COUNTERPARTY,
            series_osi=buyer.series_osi,
            contracts=quantity,
            premium=buyer.premium,
            currency=buyer.currency,
            trade_date=buyer.trade_date,
            buyer=party(buyer),
            seller=party(seller),
        )
        payload_digest = digest(novation)
        events = tuple(
            ExecutionEvent(
                event_id=_event_id(side),
                lifecycle_id=side.lifecycle_id,
                intent_id=side.intent_id,
                intent_digest=side.intent_digest,
                times=side.times,
                session=side.session,
                provenance=Provenance.FACT_SYNTHETIC,
                payload_digest=payload_digest,
            )
            for side in (buyer, seller)
        )
        result = OccResult(
            report_id=report.report_id,
            outcome=OccOutcome.NOVATED,
            reason="matched and novated",
            novation=novation,
            payload=canonical_bytes(novation),
            events=events,
        )
        for side in (buyer, seller):
            self._results[side.report_id] = (side, result)
        return result

    def unmatched(self) -> tuple[OccTradeReport, ...]:
        """Reports still waiting for their counterpart, in trade-identifier order."""
        return tuple(self._pending[t] for t in sorted(self._pending))

    def positions(self) -> tuple[Position, ...]:
        """Every account's position in every series it has traded, in a fixed order."""
        keys = sorted(set(self._long) | set(self._short))
        return tuple(
            Position(
                clearing_member_id=k[0],
                account_id=k[1],
                account_type=k[2],
                series_osi=k[3],
                long_contracts=self._long.get(k, 0),
                short_contracts=self._short.get(k, 0),
            )
            for k in keys
        )
