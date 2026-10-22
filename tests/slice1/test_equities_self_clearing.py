"""EQ-1 real producer, real parser, decision run and publication."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from importlib import import_module
from pathlib import Path

from cannae_kernel.absence import Recorded
from cannae_kernel.actor import ActorKind, ActorRef
from cannae_kernel.clocks import EventTimes
from cannae_kernel.disposition import Disposition
from cannae_kernel.ids import ActorId, EventId, IntentId, LifecycleId, OrderId

from lc.events import (
    LifecycleState,
    OrderEventPayload,
    OrderTerms,
    OrderType,
    TimeInForce,
    seal_order_event,
)
from lc.lifecycle import LifecycleRegister
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
        "quantity": Decimal(1),
        "limit_price": Decimal(100),
        "reference_price": Decimal(100),
        "submitted_at": AT,
    }
    fields.update(changes)
    order_id = fields.pop("order_id")
    quantity = fields.pop("quantity")
    reference_price = fields.pop("reference_price")
    submitted_at = fields.pop("submitted_at")
    sell_mark = fields.pop("sell_mark", None)
    locate_id = fields.pop("locate_id", None)
    limit_price = fields.pop("limit_price")
    terms = OrderTerms.model_validate(
        {
            "account_id": fields.pop("account_id"),
            "strategy_id": "EQ1-STRATEGY",
            "issue": fields.pop("issue"),
            "side": fields.pop("side"),
            "order_type": OrderType.LIMIT,
            "time_in_force": TimeInForce.DAY,
            "control_price": limit_price,
            "limit_price": limit_price,
        }
    )
    actor = ActorRef(
        actor_id=ActorId("act_" + "7" * 26),
        actor_kind=ActorKind.DETERMINISTIC_SERVICE,
        role="eq1-market-access",
        entitlement_refs=(),
        authenticated=True,
    )
    times = EventTimes(event_time=AT, observation_time=AT, processing_time=AT, decision_time=AT)
    event = seal_order_event(
        event_id=EventId("evt_0" + number * 25),
        lifecycle_id=LifecycleId("lif_" + number * 26),
        parent_ids=(),
        rule_version="eq1b-market-access/2",
        times=times,
        actor=actor,
        idempotency_key=f"eq1-order-{number}",
        payload=OrderEventPayload.model_validate(
            {
                "order_id": order_id,
                "parent_order_id": None,
                "intent_id": IntentId("int_" + number * 26),
                "state": LifecycleState.PARENT_ACTIVE,
                "reason": "sealed EQ-1 order terms",
                "quantity": Recorded.model_validate({"value": quantity}),
                "terms": terms,
            }
        ),
        prior_event_digest=None,
    )
    return ChildRelease.model_validate(
        {
            "order_event": event,
            "lifecycle_register": LifecycleRegister(lifecycle_id=event.lifecycle_id).append(event),
            "reference_price": reference_price,
            "sell_mark": sell_mark,
            "locate_id": locate_id,
            "submitted_at": submitted_at,
        }
    )


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
        _order("2", quantity=Decimal(2)),
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
        )
        for order in orders
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
    capacity_envelope = json.loads(publications["equities-account-capacity.json"])
    capacity = json.loads(capacity_envelope["payload"])
    books = json.loads(publications["equities-books.json"])
    margin = json.loads(publications["equities-margin.json"])
    assert capacity["journal_digest"] == books["journal_digest"] == margin["journal_digest"]
    assert outcomes[0]["capacity_journal_digest"] == books["journal_digest"]
    assert (
        outcomes[0]["capacity_margin_result_digest"]
        == capacity["accounts"][0]["margin_result_digest"]
    )
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
