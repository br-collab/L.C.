"""M2 acceptance tests for protocol-neutral order events."""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from cannae_kernel.absence import Recorded
from cannae_kernel.actor import ActorKind, ActorRef
from cannae_kernel.canonical import canonical_bytes_of
from cannae_kernel.clocks import EventTimes
from cannae_kernel.domains import Domain
from cannae_kernel.events import verify
from cannae_kernel.ids import ActorId, EventId, IntentId, LifecycleId, OrderId
from cannae_kernel.provenance import Provenance
from pydantic import ValidationError

from lc.events import LifecycleState, OrderEvent, OrderEventPayload, seal_order_event

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 23, 16, 30, tzinfo=UTC)


def _id(prefix: str, suffix: str) -> str:
    return prefix + ("0" * (26 - len(suffix))) + suffix


def _event(*, reason: str = "approved intent verified") -> OrderEvent:
    payload = OrderEventPayload(
        order_id=OrderId(_id("ord_", "1")),
        parent_order_id=None,
        intent_id=IntentId(_id("int_", "2")),
        state=LifecycleState.INTENT_ACCEPTED,
        reason=reason,
        quantity=Recorded[Decimal](value=Decimal("100")),
    )
    return seal_order_event(
        event_id=EventId(_id("evt_", "3")),
        lifecycle_id=LifecycleId(_id("lif_", "4")),
        parent_ids=(),
        rule_version="lc-m2/1.0",
        times=EventTimes(
            event_time=NOW,
            observation_time=NOW + timedelta(seconds=1),
            processing_time=NOW + timedelta(seconds=2),
            decision_time=NOW + timedelta(seconds=3),
        ),
        actor=ActorRef(
            actor_id=ActorId(_id("act_", "5")),
            actor_kind=ActorKind.DETERMINISTIC_SERVICE,
            role="lc-order-register",
            entitlement_refs=(),
            authenticated=True,
        ),
        idempotency_key="intent-2:accepted",
        payload=payload,
        prior_event_digest=None,
    )


def test_canonical_round_trip_is_byte_stable() -> None:
    event = _event()
    encoded = canonical_bytes_of(event)
    restored = OrderEvent.model_validate_json(encoded)
    assert canonical_bytes_of(restored) == encoded
    assert verify(restored)


def test_any_field_change_breaks_the_digest() -> None:
    event = _event()
    changed = event.model_copy(
        update={"payload": event.payload.model_copy(update={"reason": "changed"})}
    )
    assert not verify(changed)
    assert canonical_bytes_of(changed) != canonical_bytes_of(event)


def test_unknown_lifecycle_state_is_rejected() -> None:
    data = _event().model_dump(mode="python")
    data["payload"]["state"] = "FILLED_SOMEHOW"
    with pytest.raises(ValidationError):
        OrderEvent.model_validate(data)


def test_order_cannot_be_its_own_parent() -> None:
    data = _event().payload.model_dump(mode="python")
    data["parent_order_id"] = data["order_id"]
    with pytest.raises(ValidationError, match="own parent"):
        OrderEventPayload.model_validate(data)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("producer_domain", Domain.ATREIDES, "producer_domain must be LC"),
        ("provenance", Provenance.HUMAN_JUDGMENT, "provenance must be POLICY_RESULT"),
    ],
)
def test_order_event_refuses_another_domain_or_authority_claim(
    field: str, value: str, message: str
) -> None:
    data = _event().model_dump(mode="python")
    data[field] = value
    with pytest.raises(ValidationError, match=message):
        OrderEvent.model_validate(data)


def test_lc_has_no_execution_event_construction_path() -> None:
    """A4 carried across: L.C. cannot fabricate the venue fact it consumes later."""
    offenders: list[str] = []
    for path in sorted((ROOT / "lc").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and (
                (isinstance(node.func, ast.Name) and node.func.id == "ExecutionEvent")
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "ExecutionEvent")
            ):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert offenders == [], f"L.C. acquired an execution-fabrication path: {offenders}"
