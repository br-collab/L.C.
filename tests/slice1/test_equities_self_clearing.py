"""EQ-1 real producer, real parser, decision run and publication."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from importlib import import_module
from pathlib import Path

from cannae_kernel.disposition import Disposition
from cannae_kernel.ids import OrderId

from lc.market_access import (
    ChildRelease,
    LimitsManifest,
    MarketAccessGate,
    SellMark,
    consume_producer_attestation,
)

AT = datetime(2026, 10, 22, 14, tzinfo=UTC)


def _order(number: str, **changes: object) -> ChildRelease:
    fields: dict[str, object] = {
        "order_id": OrderId("ord_0" + number * 25),
        "account_id": "CUSTOMER-1",
        "issue": "ACME",
        "side": "BUY",
        "quantity": Decimal(10),
        "limit_price": Decimal(100),
        "reference_price": Decimal(100),
        "submitted_at": AT,
    }
    fields.update(changes)
    return ChildRelease.model_validate(fields)


def _run() -> tuple[dict[str, bytes], bytes]:
    # Runtime import is deliberate: CI installs the pinned real producer and this test executes
    # it. L.C.'s static type boundary remains its own parser rather than Atreides internals.
    build_equities_publications = import_module(
        "atreides.equities.scenario"
    ).build_equities_publications
    publications = build_equities_publications(AT)
    capacity = consume_producer_attestation(
        publications["equities-account-capacity.json"], "ACCOUNT_CAPACITY"
    )
    locates = consume_producer_attestation(publications["equities-locates.json"], "LOCATES")
    limits = LimitsManifest(
        version="EQ1-1",
        authority="FIRM_RISK_DIRECT_EXCLUSIVE_CONTROL",
        account_credit_limits={"CUSTOMER-1": Decimal(5000)},
        firm_aggregate_threshold=Decimal(100000),
        price_collar_percent=Decimal("0.10"),
        maximum_quantity=Decimal(1000),
        maximum_notional=Decimal(50000),
        duplicate_window_seconds=30,
    )
    gate = MarketAccessGate()
    orders = (
        _order("1"),
        _order("2", quantity=Decimal(60)),
        _order("3", limit_price=Decimal(120)),
        _order("4", side="SELL", sell_mark=SellMark.SHORT),
        _order("5", side="SELL", sell_mark=SellMark.SHORT, locate_id="LOC-EQ1-1"),
    )
    decisions = tuple(
        gate.evaluate(
            order,
            limits=limits,
            capacity_input=capacity,
            locates_input=locates,
            firm_open_order_notional=Decimal(0),
            account_open_order_notional=(Decimal(0) if index != 1 else Decimal(1)),
        )
        for index, order in enumerate(orders)
    )
    document = {
        "schema_version": "lc.equities_market_access/1",
        "as_of": AT.isoformat(),
        "synthetic": True,
        "decisions": [decision.model_dump(mode="json") for decision in decisions],
    }
    raw = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    return publications, raw


def test_eq1_real_crossing_and_required_decisions_replay() -> None:
    publications, decisions = _run()
    assert _run() == (publications, decisions)
    parsed = json.loads(decisions)
    outcomes = parsed["decisions"]
    assert outcomes[0]["disposition"] == Disposition.PASS.value
    assert (
        next(c for c in outcomes[1]["checks"] if c["code"] == "ACCOUNT_CREDIT")["passed"] is False
    )
    assert (
        next(c for c in outcomes[2]["checks"] if c["code"] == "PRICE_COLLAR")["reason"]
        == "ERRONEOUS_PRICE"
    )
    assert (
        next(c for c in outcomes[3]["checks"] if c["code"] == "LOCATE")["reason"]
        == "LOCATE_MISSING"
    )
    assert outcomes[4]["disposition"] == Disposition.PASS.value


def test_eq1_publication_is_written_with_the_slice() -> None:
    destination = os.environ.get("LIFECYCLE_PUBLICATION_DIR")
    if destination is None:
        return
    publications, decisions = _run()
    publications["equities-market-access.json"] = decisions
    path = Path(destination)
    path.mkdir(parents=True, exist_ok=True)
    for name, payload in publications.items():
        (path / name).write_bytes(payload)
