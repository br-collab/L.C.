# mypy: disable-error-code=no-untyped-def

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from cannae_kernel.absence import Recorded
from cannae_kernel.canonical import canonical_bytes, digest_bytes
from cannae_kernel.disposition import Disposition
from cannae_kernel.ids import EventId, IntentId, LifecycleId, OrderId
from pydantic import BaseModel
from test_lifecycle import _actor, _event, _id

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
    AttestedInput,
    CapacityAccount,
    CapacityDocument,
    CapacityPosition,
    ChildRelease,
    LimitsManifest,
    Locate,
    LocateDocument,
    MarketAccessGate,
    SellMark,
    consume_producer_attestation,
    record_decision,
    record_limit_change,
)

NOW = datetime(2026, 10, 22, 14, tzinfo=UTC)
ORDER = OrderId("ord_" + "1" * 26)


def attested(model: BaseModel) -> AttestedInput:
    payload = canonical_bytes(model)
    return AttestedInput(payload=payload, payload_digest=digest_bytes(payload))


def capacity(as_of: datetime = NOW) -> AttestedInput:
    return attested(
        CapacityDocument(
            schema_version="atreides.equities_account_capacity/2",
            as_of=as_of,
            maximum_age_seconds=300,
            synthetic=True,
            journal_digest="sha256:" + "1" * 64,
            accounts=(
                CapacityAccount(
                    account_id="A",
                    buying_power=Decimal(10000),
                    margin_excess=Decimal(5000),
                    margin_result_digest="sha256:" + "2" * 64,
                    positions=(CapacityPosition(issue="ACME", quantity=Decimal(100)),),
                ),
            ),
        )
    )


def locates(*records: Locate) -> AttestedInput:
    return attested(
        LocateDocument(
            schema_version="atreides.equities_locates/1", as_of=NOW, synthetic=True, locates=records
        )
    )


def locate(**changes: object) -> Locate:
    fields: dict[str, object] = {
        "locate_id": "L1",
        "source": "SYNTHETIC_LENDER",
        "located_at": NOW,
        "expires_at": NOW + timedelta(hours=1),
        "issue": "ACME",
        "quantity": Decimal(100),
    }
    fields.update(changes)
    return Locate.model_validate(fields)


def limits(**changes: object) -> LimitsManifest:
    fields: dict[str, object] = {
        "version": "1",
        "authority": "FIRM_RISK",
        "account_credit_limits": {"A": Decimal(10000)},
        "firm_aggregate_threshold": Decimal(100000),
        "price_collar_percent": Decimal("0.10"),
        "maximum_quantity": Decimal(1000),
        "maximum_notional": Decimal(50000),
        "duplicate_window_seconds": 60,
    }
    fields.update(changes)
    return LimitsManifest.model_validate(fields)


def order(**changes: object) -> ChildRelease:
    fields: dict[str, object] = {
        "order_id": ORDER,
        "account_id": "A",
        "strategy_id": "STRATEGY-1",
        "issue": "ACME",
        "side": "BUY",
        "quantity": Decimal(10),
        "limit_price": Decimal(100),
        "reference_price": Decimal(100),
        "submitted_at": NOW,
    }
    fields.update(changes)
    order_id = fields.pop("order_id")
    quantity = fields.pop("quantity")
    reference_price = fields.pop("reference_price")
    submitted_at = fields.pop("submitted_at")
    sell_mark = fields.pop("sell_mark", None)
    locate_id = fields.pop("locate_id", None)
    terms = OrderTerms.model_validate(
        {
            "account_id": fields.pop("account_id"),
            "strategy_id": fields.pop("strategy_id"),
            "issue": fields.pop("issue"),
            "side": fields.pop("side"),
            "order_type": OrderType.LIMIT,
            "time_in_force": TimeInForce.DAY,
            "limit_price": fields.pop("limit_price"),
            "control_price": changes.get("limit_price", Decimal(100)),
        }
    )
    assert not fields
    event = seal_order_event(
        event_id=EventId(_id("evt_", 90)),
        lifecycle_id=LifecycleId(_id("lif_", 90)),
        parent_ids=(),
        rule_version="oms1-test/1",
        times=_event(90).times,
        actor=_actor(),
        idempotency_key="oms1-order",
        payload=OrderEventPayload.model_validate(
            {
                "order_id": order_id,
                "parent_order_id": None,
                "intent_id": IntentId(_id("int_", 90)),
                "state": LifecycleState.PARENT_ACTIVE,
                "reason": "sealed test order",
                "quantity": Recorded.model_validate({"value": quantity}),
                "terms": terms,
            }
        ),
        prior_event_digest=None,
    )
    register = LifecycleRegister(lifecycle_id=event.lifecycle_id).append(event)
    return ChildRelease.model_validate(
        {
            "order_event": event,
            "lifecycle_register": register,
            "reference_price": reference_price,
            "sell_mark": sell_mark,
            "locate_id": locate_id,
            "submitted_at": submitted_at,
        }
    )


def decide(
    gate: MarketAccessGate,
    item: ChildRelease,
    *,
    lim: LimitsManifest | None = None,
    cap: AttestedInput | None = None,
    loc: AttestedInput | None = None,
    firm: Decimal = Decimal(0),
    account: Decimal = Decimal(0),
):
    del firm, account
    return gate.evaluate(
        item,
        limits=lim or limits(),
        capacity_input=capacity() if cap is None else cap,
        locates_input=locates() if loc is None else loc,
    )


def failed(decision, code: str, reason: str) -> None:
    check = next(item for item in decision.checks if item.code == code)
    assert not check.passed and check.reason == reason
    assert decision.disposition is Disposition.HOLD and decision.terminal


def test_pass_duplicate_and_limit_change_record() -> None:
    gate = MarketAccessGate()
    first = decide(gate, order())
    assert first.disposition is Disposition.PASS and not first.terminal
    assert first.capacity_journal_digest == "sha256:" + "1" * 64
    assert first.capacity_margin_result_digest == "sha256:" + "2" * 64
    failed(decide(gate, order()), "DUPLICATE", "DUPLICATE_ORDER")
    change = record_limit_change(limits(), limits(version="2"), NOW)
    assert change.previous_version == "1" and change.manifest_digest.startswith("sha256:")


def test_producer_attestation_parser_refuses_type_and_tamper() -> None:
    inner = capacity()
    wrapper = json.dumps(
        {
            "schema_version": "atreides.attested_document/1",
            "document_type": "ACCOUNT_CAPACITY",
            "payload_digest": inner.payload_digest,
            "payload": inner.payload.decode(),
        },
        separators=(",", ":"),
    ).encode()
    assert consume_producer_attestation(wrapper, "ACCOUNT_CAPACITY") == inner
    with pytest.raises(ValueError, match="expected LOCATES"):
        consume_producer_attestation(wrapper, "LOCATES")
    tampered = wrapper.replace(inner.payload_digest.encode(), ("sha256:" + "0" * 64).encode())
    with pytest.raises(ValueError, match="digest mismatch"):
        consume_producer_attestation(tampered, "ACCOUNT_CAPACITY")


def test_decision_is_recorded_in_lifecycle(monkeypatch) -> None:
    decision = decide(MarketAccessGate(), order())
    captured = {}
    monkeypatch.setattr(
        "lc.market_access.transition",
        lambda register, **values: captured.update(values) or register,
    )
    register = object()
    assert record_decision(register, decision, actor=object(), event=object()) is register  # type: ignore[arg-type]
    assert captured["target"].value == "MARKET_ACCESS_PASSED"


@pytest.mark.parametrize(
    ("code", "reason", "order_changes", "limit_changes", "firm", "account"),
    [
        ("ACCOUNT_CREDIT", "ACCOUNT_CREDIT_LIMIT", {}, {"account_credit_limits": {}}, 0, 0),
        (
            "FIRM_AGGREGATE",
            "FIRM_AGGREGATE_LIMIT",
            {},
            {"firm_aggregate_threshold": Decimal(500)},
            0,
            0,
        ),
        ("PRICE_COLLAR", "ERRONEOUS_PRICE", {"limit_price": Decimal(120)}, {}, 0, 0),
        ("MAXIMUM_QUANTITY", "MAXIMUM_QUANTITY", {"quantity": Decimal(1001)}, {}, 0, 0),
        ("MAXIMUM_NOTIONAL", "MAXIMUM_NOTIONAL", {"quantity": Decimal(600)}, {}, 0, 0),
        ("RESTRICTED_LIST", "RESTRICTED_ISSUE", {}, {"restricted_issues": ("ACME",)}, 0, 0),
        ("TRADING_HALT", "TRADING_HALT", {}, {"halted_issues": ("ACME",)}, 0, 0),
        ("ACCOUNT_RESTRICTION", "ACCOUNT_RESTRICTED", {}, {"restricted_accounts": ("A",)}, 0, 0),
        ("SELL_MARKING", "SELL_MARK_MISSING", {"side": "SELL"}, {}, 0, 0),
        (
            "LONG_OWNERSHIP",
            "LONG_POSITION_INSUFFICIENT",
            {"side": "SELL", "sell_mark": SellMark.LONG, "quantity": Decimal(101)},
            {},
            0,
            0,
        ),
    ],
)
def test_individual_control_refusals(
    code, reason, order_changes, limit_changes, firm, account
) -> None:
    failed(
        decide(
            MarketAccessGate(),
            order(**order_changes),
            lim=limits(**limit_changes),
            firm=Decimal(firm),
            account=Decimal(account),
        ),
        code,
        reason,
    )


def test_capacity_missing_tampered_stale_and_unreadable() -> None:
    gate = MarketAccessGate()
    missing = gate.evaluate(
        order(),
        limits=limits(),
        capacity_input=None,
        locates_input=locates(),
    )
    failed(missing, "CAPACITY_DOCUMENT", "DOCUMENT_MISSING")
    good = capacity()
    tampered = good.model_copy(update={"payload": good.payload + b" "})
    failed(
        decide(MarketAccessGate(), order(), cap=tampered), "CAPACITY_DOCUMENT", "DOCUMENT_TAMPERED"
    )
    failed(
        decide(MarketAccessGate(), order(), cap=capacity(NOW - timedelta(hours=1))),
        "CAPACITY_DOCUMENT",
        "DOCUMENT_STALE",
    )
    bad = AttestedInput(payload=b"{}", payload_digest=digest_bytes(b"{}"))
    failed(decide(MarketAccessGate(), order(), cap=bad), "CAPACITY_DOCUMENT", "DOCUMENT_UNREADABLE")


@pytest.mark.parametrize(
    ("loc_input", "order_changes", "reason"),
    [
        (None, {"locate_id": "L1"}, "DOCUMENT_MISSING"),
        (locates(), {"locate_id": "L1"}, "LOCATE_MISSING"),
        (locates(locate(issue="OTHER")), {"locate_id": "L1"}, "LOCATE_ISSUE_MISMATCH"),
        (
            locates(locate(expires_at=NOW - timedelta(seconds=1))),
            {"locate_id": "L1"},
            "LOCATE_EXPIRED",
        ),
    ],
)
def test_locate_refusals(loc_input, order_changes, reason) -> None:
    item = order(side="SELL", sell_mark=SellMark.SHORT, **order_changes)
    decision = MarketAccessGate().evaluate(
        item,
        limits=limits(),
        capacity_input=capacity(),
        locates_input=loc_input,
    )
    failed(decision, "LOCATE", reason)


def test_locate_consumes_and_cannot_be_reused_past_quantity() -> None:
    gate = MarketAccessGate()
    document = locates(locate(quantity=Decimal(15)))
    first = gate.evaluate(
        order(side="SELL", sell_mark=SellMark.SHORT, locate_id="L1", quantity=Decimal(10)),
        limits=limits(),
        capacity_input=capacity(),
        locates_input=document,
    )
    assert first.disposition is Disposition.PASS
    second = gate.evaluate(
        order(
            order_id=OrderId("ord_" + "2" * 26),
            side="SELL",
            sell_mark=SellMark.SHORT,
            locate_id="L1",
            quantity=Decimal(6),
            submitted_at=NOW + timedelta(seconds=61),
        ),
        limits=limits(),
        capacity_input=capacity(),
        locates_input=document,
    )
    failed(second, "LOCATE", "LOCATE_INSUFFICIENT")
