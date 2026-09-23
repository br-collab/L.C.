"""M3 acceptance tests for the deterministic lifecycle register."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from cannae_kernel.actor import ActorKind, ActorRef
from cannae_kernel.canonical import digest
from cannae_kernel.clocks import EventTimes
from cannae_kernel.effects import OperationEffects
from cannae_kernel.envelopes import ApprovedIntentEnvelope
from cannae_kernel.ids import ActorId, EventId, IntentId, LifecycleId, OrderId
from cannae_kernel.provenance import Provenance
from cannae_kernel.session import BusinessDate, MarketSession, SessionContext

from lc.events import LifecycleState
from lc.lifecycle import (
    ApprovedIntentPayload,
    AuthorityManifest,
    ChildOrder,
    EventInput,
    IntentOutcome,
    OrderSide,
    PolicyManifest,
    accept_intent,
    activate_parent,
    authorize_strategy,
    replay,
    split_parent,
    transition,
)

NOW = datetime(2026, 9, 23, 17, 0, tzinfo=UTC)


def _id(prefix: str, n: int) -> str:
    return prefix + f"{n:026d}"


def _actor() -> ActorRef:
    return ActorRef(
        actor_id=ActorId(_id("act_", 1)),
        actor_kind=ActorKind.DETERMINISTIC_SERVICE,
        role="lc-register",
        entitlement_refs=("T1",),
        authenticated=True,
    )


def _event(n: int) -> EventInput:
    at = NOW + timedelta(seconds=n)
    return EventInput(
        event_id=EventId(_id("evt_", n)),
        times=EventTimes(
            event_time=at,
            observation_time=at,
            processing_time=at,
            decision_time=at,
        ),
        idempotency_key=f"event-{n}",
    )


def _payload() -> ApprovedIntentPayload:
    policy = PolicyManifest(
        policy_id="vwap-us-treasury",
        version="1.0",
        bounds_digest="sha256:" + "1" * 64,
        enabled=True,
    )
    return ApprovedIntentPayload(
        intent_id=IntentId(_id("int_", 2)),
        instrument_id="CUSIP-TEST-001",
        side=OrderSide.BUY,
        quantity=Decimal("100"),
        policy_manifest=policy,
        authority_manifest=AuthorityManifest(
            authority_id=_actor().actor_id,
            policy_manifest_digest=digest(policy),
            authorized=True,
        ),
        allocation_accounts=("ACCOUNT-A", "ACCOUNT-B"),
    )


def _envelope(payload: ApprovedIntentPayload | None = None) -> ApprovedIntentEnvelope:
    payload = payload or _payload()
    return ApprovedIntentEnvelope(
        envelope_id=payload.intent_id,
        lifecycle_id=LifecycleId(_id("lif_", 3)),
        revision=1,
        prior_digest=None,
        session=SessionContext(
            session=MarketSession.REGULAR,
            business_date=BusinessDate(
                value=date(2026, 9, 23),
                calendar="SIFMA-US",
                established_by="scenario input",
            ),
        ),
        approved_by=_actor(),
        provenance=Provenance.HUMAN_JUDGMENT,
        effects=OperationEffects(
            operation="approve intent",
            effects=(),
            note="records approval inside Aureon; L.C. consumption is read-only",
        ),
        payload_digest=digest(payload),
    )


def _accepted() -> IntentOutcome:
    payload = _payload()
    outcome = accept_intent(
        _envelope(payload),
        payload,
        parent_order_id=OrderId(_id("ord_", 10)),
        actor=_actor(),
        event=_event(10),
    )
    outcome = authorize_strategy(outcome, actor=_actor(), event=_event(11))
    return activate_parent(outcome, actor=_actor(), event=_event(12))


def test_same_inputs_produce_a_byte_identical_register() -> None:
    assert _accepted().journal.canonical_bytes() == _accepted().journal.canonical_bytes()


def test_tampered_envelope_digest_is_a_first_class_refusal() -> None:
    payload = _payload().model_copy(update={"quantity": Decimal("101")})
    outcome = accept_intent(
        _envelope(),
        payload,
        parent_order_id=OrderId(_id("ord_", 10)),
        actor=_actor(),
        event=_event(10),
    )
    assert outcome.refusal is not None
    assert outcome.refusal.predicate == "payload_digest"
    assert outcome.journal.events[-1].payload.state is LifecycleState.REFUSED


@pytest.mark.parametrize("field", ["enabled", "authorized"])
def test_policy_and_authority_manifests_are_verified(field: str) -> None:
    payload = _payload()
    if field == "enabled":
        payload = payload.model_copy(
            update={"policy_manifest": payload.policy_manifest.model_copy(update={field: False})}
        )
    else:
        payload = payload.model_copy(
            update={
                "authority_manifest": payload.authority_manifest.model_copy(update={field: False})
            }
        )
    outcome = accept_intent(
        _envelope(payload),
        payload,
        parent_order_id=OrderId(_id("ord_", 10)),
        actor=_actor(),
        event=_event(10),
    )
    assert outcome.refusal is not None


def test_parent_to_child_split_conserves_quantity() -> None:
    outcome = split_parent(
        _accepted(),
        actor=_actor(),
        children=(
            ChildOrder(order_id=OrderId(_id("ord_", 11)), quantity=Decimal("40"), event=_event(13)),
            ChildOrder(order_id=OrderId(_id("ord_", 12)), quantity=Decimal("60"), event=_event(14)),
        ),
    )
    snapshot = replay(outcome.journal.events)
    child_total = sum(
        order.quantity
        for order in snapshot.orders.values()
        if order.parent_order_id == outcome.parent_order_id
    )
    assert child_total == snapshot.orders[outcome.parent_order_id].quantity


def test_non_conserving_split_appends_nothing() -> None:
    outcome = _accepted()
    before = outcome.journal.canonical_bytes()
    with pytest.raises(ValueError, match="child quantity"):
        split_parent(
            outcome,
            actor=_actor(),
            children=(
                ChildOrder(
                    order_id=OrderId(_id("ord_", 11)),
                    quantity=Decimal("99"),
                    event=_event(13),
                ),
            ),
        )
    assert outcome.journal.canonical_bytes() == before


def test_replay_reconstructs_state_without_an_emulator() -> None:
    outcome = _accepted()
    first = replay(outcome.journal.events)
    second = replay(tuple(outcome.journal.events))
    assert first == second
    assert first.last_event_digest == outcome.journal.events[-1].envelope_digest


def test_a_silent_skip_is_recorded_as_not_reached() -> None:
    outcome = _accepted()
    register = transition(
        outcome.journal,
        order_id=outcome.parent_order_id,
        target=LifecycleState.AFFIRMED,
        reason="capture, allocation and match have not happened",
        actor=_actor(),
        event=_event(13),
    )
    event = register.events[-1]
    assert event.payload.state is LifecycleState.NOT_REACHED
    assert "AFFIRMED not reached" in event.payload.reason


def test_refusal_records_later_states_as_not_reached() -> None:
    payload = _payload().model_copy(update={"quantity": Decimal("101")})
    outcome = accept_intent(
        _envelope(),
        payload,
        parent_order_id=OrderId(_id("ord_", 10)),
        actor=_actor(),
        event=_event(10),
    )
    outcome = authorize_strategy(outcome, actor=_actor(), event=_event(11))
    assert outcome.journal.events[-1].payload.state is LifecycleState.NOT_REACHED
    assert "STRATEGY_AUTHORIZED not reached" in outcome.journal.events[-1].payload.reason


def test_parent_activation_cannot_skip_strategy_authorization() -> None:
    payload = _payload()
    outcome = accept_intent(
        _envelope(payload),
        payload,
        parent_order_id=OrderId(_id("ord_", 10)),
        actor=_actor(),
        event=_event(10),
    )
    outcome = activate_parent(outcome, actor=_actor(), event=_event(11))
    assert outcome.journal.events[-1].payload.state is LifecycleState.NOT_REACHED
    assert "PARENT_ACTIVE not reached" in outcome.journal.events[-1].payload.reason
