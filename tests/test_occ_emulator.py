"""SC-3 WP-2 acceptance: the synthetic OCC central counterparty.

Acceptance criteria, mapped:

- Conservation across open, close and novation:
  ``test_long_equals_short_after_every_step_of_a_mixed_sequence`` and the position cases.
- Duplicate trade is idempotent: ``test_a_duplicate_report_changes_nothing`` and
  ``test_a_resent_novated_pair_novates_once``.
- Rejects and unmatched trades: the named cases below.

The boundary to L.C. is in ``tests/test_occ_boundary.py``. Everything here is SYNTHETIC.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from cannae_kernel.canonical import canonical_bytes, digest, digest_bytes
from cannae_kernel.clocks import EventTimes
from cannae_kernel.envelopes import ExecutionEvent
from cannae_kernel.ids import IntentId, LifecycleId
from cannae_kernel.provenance import Provenance
from cannae_kernel.session import BusinessDate, MarketSession, SessionContext
from pydantic import ValidationError

import emulators.occ as occ_module
from emulators.occ import (
    CENTRAL_COUNTERPARTY,
    AccountType,
    OccEmulator,
    OccOutcome,
    OccTradeReport,
    OpenClose,
    Side,
)

NOW = datetime(2026, 10, 5, 15, 0, tzinfo=UTC)
SERIES = "SYN   261218C00150500"
OTHER_SERIES = "SYN   261218P00150500"


def _id(prefix: str, n: int) -> str:
    return prefix + f"{n:026d}"


def report(
    report_id: str,
    trade_id: str,
    side: Side,
    member: str = "CM-A",
    open_close: OpenClose = OpenClose.OPEN,
    **changes: Any,
) -> OccTradeReport:
    at = NOW + timedelta(seconds=len(report_id))
    base: dict[str, Any] = {
        "report_id": report_id,
        "trade_id": trade_id,
        "series_osi": SERIES,
        "side": side,
        "open_close": open_close,
        "contracts": 10,
        "premium": Decimal("5250"),
        "currency": "USD",
        "trade_date": date(2026, 10, 5),
        "clearing_member_id": member,
        "account_id": f"{member}-ACCT",
        "account_type": AccountType.CUSTOMER,
        "lifecycle_id": LifecycleId(_id("lif_", 1)),
        "intent_id": IntentId(_id("int_", 1)),
        "intent_digest": "sha256:" + "1" * 64,
        "session": SessionContext(
            session=MarketSession.REGULAR,
            business_date=BusinessDate(
                value=date(2026, 10, 5), calendar="SIFMA-US", established_by="scenario"
            ),
        ),
        "times": EventTimes(event_time=at, observation_time=at, processing_time=at),
    }
    return OccTradeReport(**(base | changes))


def trade(  # noqa: PLR0913 - each term of a trade a test varies stays explicit
    n: int,
    buyer: str,
    seller: str,
    *,
    buy: OpenClose = OpenClose.OPEN,
    sell: OpenClose = OpenClose.OPEN,
    contracts: int = 10,
    series: str = SERIES,
) -> tuple[OccTradeReport, OccTradeReport]:
    return (
        report(f"R{n}B", f"T{n}", Side.BUY, buyer, buy, contracts=contracts, series_osi=series),
        report(f"R{n}S", f"T{n}", Side.SELL, seller, sell, contracts=contracts, series_osi=series),
    )


def run(ccp: OccEmulator, reports: Iterable[OccTradeReport]) -> None:
    for each in reports:
        ccp.submit(each)


def holdings(ccp: OccEmulator) -> dict[tuple[str, str], tuple[int, int]]:
    return {
        (p.clearing_member_id, p.series_osi): (p.long_contracts, p.short_contracts)
        for p in ccp.positions()
    }


def assert_conserved(ccp: OccEmulator) -> None:
    for series in {p.series_osi for p in ccp.positions()}:
        longs = sum(p.long_contracts for p in ccp.positions() if p.series_osi == series)
        shorts = sum(p.short_contracts for p in ccp.positions() if p.series_osi == series)
        assert longs == shorts, series


# --- matching and novation --------------------------------------------------------------------


def test_one_side_waits_and_the_other_novates() -> None:
    ccp = OccEmulator()
    first, second = trade(1, "CM-A", "CM-B")
    waiting = ccp.submit(first)
    assert waiting.outcome is OccOutcome.UNMATCHED and ccp.unmatched() == (first,)
    novated = ccp.submit(second)
    assert novated.outcome is OccOutcome.NOVATED
    assert ccp.unmatched() == ()
    assert novated.novation is not None
    assert novated.novation.central_counterparty == CENTRAL_COUNTERPARTY
    assert novated.novation.buyer.clearing_member_id == "CM-A"
    assert novated.novation.seller.clearing_member_id == "CM-B"
    assert holdings(ccp) == {("CM-A", SERIES): (10, 0), ("CM-B", SERIES): (0, 10)}


def test_a_novation_emits_one_attesting_execution_event_per_side() -> None:
    ccp = OccEmulator()
    run(ccp, trade(1, "CM-A", "CM-B")[:1])
    result = ccp.submit(trade(1, "CM-A", "CM-B")[1])
    assert result.novation is not None and result.payload is not None
    assert result.payload == canonical_bytes(result.novation)
    assert len(result.events) == 2
    for event in result.events:
        assert isinstance(event, ExecutionEvent)
        assert event.provenance is Provenance.FACT_SYNTHETIC
        assert event.payload_digest == digest(result.novation) == digest_bytes(result.payload)
    assert result.events[0].event_id != result.events[1].event_id


def test_a_novation_is_deterministic() -> None:
    def novate() -> bytes:
        ccp = OccEmulator()
        run(ccp, trade(1, "CM-A", "CM-B")[:1])
        payload = ccp.submit(trade(1, "CM-A", "CM-B")[1]).payload
        assert payload is not None
        return payload

    assert novate() == novate()


@pytest.mark.parametrize(
    ("second", "reason"),
    [
        (report("R1X", "T1", Side.BUY, "CM-B"), "both reports are BUY"),
        (report("R1S", "T1", Side.SELL, "CM-B", contracts=11), "differ"),
        (report("R1S", "T1", Side.SELL, "CM-B", series_osi=OTHER_SERIES), "differ"),
        (report("R1S", "T1", Side.SELL, "CM-B", premium=Decimal("5251")), "differ"),
        (report("R1S", "T1", Side.SELL, "CM-B", trade_date=date(2026, 10, 6)), "differ"),
        (report("R1S", "T1", Side.SELL, "CM-B", currency="EUR"), "differ"),
    ],
    ids=["two-buys", "contracts", "series", "premium", "trade-date", "currency"],
)
def test_a_report_that_does_not_match_its_counterpart_is_rejected(
    second: OccTradeReport, reason: str
) -> None:
    ccp = OccEmulator()
    ccp.submit(report("R1B", "T1", Side.BUY))
    result = ccp.submit(second)
    assert result.outcome is OccOutcome.REJECTED and reason in result.reason
    assert ccp.positions() == ()
    assert [r.report_id for r in ccp.unmatched()] == ["R1B"]


def test_a_trade_already_novated_cannot_be_novated_again() -> None:
    ccp = OccEmulator()
    run(ccp, trade(1, "CM-A", "CM-B"))
    late = ccp.submit(report("R1Z", "T1", Side.SELL, "CM-C"))
    assert late.outcome is OccOutcome.REJECTED and "already novated" in late.reason


# --- positions and conservation ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("buyer", "buy", "seller", "sell", "expected"),
    [
        (
            "CM-A",
            OpenClose.OPEN,
            "CM-B",
            OpenClose.OPEN,
            {"CM-A": (13, 0), "CM-B": (0, 13), "CM-C": (3, 3), "CM-X": (3, 3)},
        ),
        (
            "CM-A",
            OpenClose.OPEN,
            "CM-C",
            OpenClose.CLOSE,
            {"CM-A": (13, 0), "CM-B": (0, 10), "CM-C": (0, 3), "CM-X": (3, 3)},
        ),
        (
            "CM-C",
            OpenClose.CLOSE,
            "CM-B",
            OpenClose.OPEN,
            {"CM-A": (10, 0), "CM-B": (0, 13), "CM-C": (3, 0), "CM-X": (3, 3)},
        ),
        (
            "CM-C",
            OpenClose.CLOSE,
            "CM-X",
            OpenClose.CLOSE,
            {"CM-A": (10, 0), "CM-B": (0, 10), "CM-C": (3, 0), "CM-X": (0, 3)},
        ),
    ],
    ids=["open-open", "open-sell-to-close", "buy-to-close-open", "close-close"],
)
def test_opening_adds_and_closing_reduces(
    buyer: str,
    buy: OpenClose,
    seller: str,
    sell: OpenClose,
    expected: dict[str, tuple[int, int]],
) -> None:
    """Before the trade under test: A long 10 and B short 10; C and X each long 3 and short 3.
    Expected positions are (long, short), written out by hand."""
    ccp = OccEmulator()
    run(ccp, trade(1, "CM-A", "CM-B"))
    run(ccp, trade(2, "CM-C", "CM-X", contracts=3))
    run(ccp, trade(3, "CM-X", "CM-C", contracts=3))
    result = ccp.submit(trade(4, buyer, seller, buy=buy, sell=sell, contracts=3)[0])
    assert result.outcome is OccOutcome.UNMATCHED
    assert ccp.submit(trade(4, buyer, seller, buy=buy, sell=sell, contracts=3)[1]).outcome is (
        OccOutcome.NOVATED
    )
    assert {m: holdings(ccp).get((m, SERIES), (0, 0)) for m in expected} == expected
    assert_conserved(ccp)


@pytest.mark.parametrize(
    ("closing", "reason"),
    [
        (trade(2, "CM-A", "CM-B", buy=OpenClose.CLOSE)[0], "buyer closes more short"),
        (trade(2, "CM-A", "CM-B", sell=OpenClose.CLOSE)[1], "seller closes more long"),
    ],
    ids=["buy-to-close-without-short", "sell-to-close-without-long"],
)
def test_closing_more_than_is_held_is_rejected(closing: OccTradeReport, reason: str) -> None:
    ccp = OccEmulator()
    counterpart = (
        trade(2, "CM-A", "CM-B")[1] if closing.side is Side.BUY else trade(2, "CM-A", "CM-B")[0]
    )
    ccp.submit(counterpart)
    result = ccp.submit(closing)
    assert result.outcome is OccOutcome.REJECTED and reason in result.reason
    assert ccp.positions() == ()


def test_long_equals_short_after_every_step_of_a_mixed_sequence() -> None:
    ccp = OccEmulator()
    members = ("CM-A", "CM-B", "CM-C", "CM-D")
    steps = []
    for n in range(1, 41):
        buyer, seller = members[n % 4], members[(n * 3 + 1) % 4]
        if buyer == seller:
            continue
        series = SERIES if n % 3 else OTHER_SERIES
        closes = n % 5 == 0
        steps.append(
            trade(
                n,
                buyer,
                seller,
                contracts=n % 7 + 1,
                series=series,
                buy=OpenClose.CLOSE if closes else OpenClose.OPEN,
            )
        )
    for buy_report, sell_report in steps:
        for each in (sell_report, buy_report):
            ccp.submit(each)
            assert_conserved(ccp)
    assert any(p.long_contracts for p in ccp.positions())


# --- idempotency ------------------------------------------------------------------------------


def test_a_duplicate_report_changes_nothing() -> None:
    ccp = OccEmulator()
    buy_report, sell_report = trade(1, "CM-A", "CM-B")
    first = ccp.submit(buy_report)
    assert ccp.submit(buy_report) == first
    assert ccp.unmatched() == (buy_report,)
    novated = ccp.submit(sell_report)
    before = ccp.positions()
    assert ccp.submit(sell_report) == novated
    assert ccp.submit(buy_report) == novated
    assert ccp.positions() == before


def test_a_resent_novated_pair_novates_once() -> None:
    ccp = OccEmulator()
    pair = trade(1, "CM-A", "CM-B")
    run(ccp, (*pair, *pair, *pair))
    assert holdings(ccp) == {("CM-A", SERIES): (10, 0), ("CM-B", SERIES): (0, 10)}


def test_a_reused_report_identifier_with_new_content_is_rejected() -> None:
    ccp = OccEmulator()
    ccp.submit(report("R1B", "T1", Side.BUY))
    changed = ccp.submit(report("R1B", "T1", Side.BUY, contracts=99))
    assert changed.outcome is OccOutcome.REJECTED and "different content" in changed.reason
    assert ccp.submit(report("R1B", "T1", Side.BUY)).outcome is OccOutcome.UNMATCHED


# --- the report itself ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "changes",
    [
        {"series_osi": "SYN 261218C00150500"},
        {"contracts": 0},
        {"premium": Decimal(0)},
        {"premium": 5250.0},
        {"currency": "usd"},
    ],
)
def test_a_malformed_report_is_refused(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        report("R1B", "T1", Side.BUY, **changes)


def test_the_emulator_states_its_limits() -> None:
    text = " ".join((occ_module.__doc__ or "").split())
    assert "EXPERIMENTAL" in text and "never imports L.C." in text
    assert "does not multiply a price by a contract size" in text
