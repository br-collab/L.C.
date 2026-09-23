"""Deterministic, append-only L.C. order lifecycle register."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Self

from cannae_kernel.absence import Recorded, require_recorded
from cannae_kernel.actor import ActorRef
from cannae_kernel.canonical import canonical_bytes_of, digest
from cannae_kernel.clocks import EventTimes
from cannae_kernel.envelopes import ApprovedIntentEnvelope
from cannae_kernel.events import verify
from cannae_kernel.ids import ActorId, EventId, IntentId, LifecycleId, OrderId
from pydantic import BaseModel, ConfigDict, Field, model_validator

from lc.events import LifecycleState, OrderEvent, OrderEventPayload, seal_order_event

__all__ = [
    "ApprovedIntentPayload",
    "AuthorityManifest",
    "ChildOrder",
    "EventInput",
    "IntentOutcome",
    "IntentRefused",
    "LifecycleRegister",
    "OrderSide",
    "PolicyManifest",
    "RegisterSnapshot",
    "accept_intent",
    "activate_parent",
    "authorize_strategy",
    "record_not_reached",
    "replay",
    "split_parent",
    "transition",
]


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class PolicyManifest(_Record):
    policy_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    bounds_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    enabled: bool


class AuthorityManifest(_Record):
    authority_id: ActorId
    policy_manifest_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    authorized: bool


class ApprovedIntentPayload(_Record):
    """L.C.'s verified reading of the Aureon-owned intent payload."""

    intent_id: IntentId
    instrument_id: str = Field(min_length=1)
    side: OrderSide
    quantity: Decimal = Field(gt=0)
    policy_manifest: PolicyManifest
    authority_manifest: AuthorityManifest
    allocation_accounts: tuple[str, ...]

    @model_validator(mode="after")
    def _allocation_intent_is_explicit(self) -> Self:
        if not self.allocation_accounts or any(not account for account in self.allocation_accounts):
            raise ValueError("allocation intent must name at least one non-empty account")
        if len(set(self.allocation_accounts)) != len(self.allocation_accounts):
            raise ValueError("allocation intent cannot name the same account twice")
        return self


class EventInput(_Record):
    event_id: EventId
    times: EventTimes
    idempotency_key: str = Field(min_length=1)


class IntentRefused(_Record):
    predicate: str = Field(min_length=1)
    evidence: str = Field(min_length=1)


class LifecycleRegister(_Record):
    lifecycle_id: LifecycleId
    events: tuple[OrderEvent, ...] = ()

    def append(self, event: OrderEvent) -> LifecycleRegister:
        if event.lifecycle_id != self.lifecycle_id:
            raise ValueError("event lifecycle_id does not match the register")
        if not verify(event):
            raise ValueError("event digest verification failed")
        expected_prior = self.events[-1].envelope_digest if self.events else None
        if event.prior_event_digest != expected_prior:
            raise ValueError("event prior digest does not extend the register")
        if any(existing.event_id == event.event_id for existing in self.events):
            raise ValueError("duplicate event_id")
        return self.model_copy(update={"events": (*self.events, event)})

    def canonical_bytes(self) -> bytes:
        return canonical_bytes_of(self)


class IntentOutcome(_Record):
    journal: LifecycleRegister
    parent_order_id: OrderId
    refusal: IntentRefused | None = None


class ChildOrder(_Record):
    order_id: OrderId
    quantity: Decimal = Field(gt=0)
    event: EventInput


class OrderSnapshot(_Record):
    state: LifecycleState
    quantity: Decimal
    executed_quantity: Decimal = Decimal(0)
    parent_order_id: OrderId | None


class RegisterSnapshot(_Record):
    lifecycle_id: LifecycleId
    orders: dict[OrderId, OrderSnapshot]
    last_event_digest: str | None


def _append(  # noqa: PLR0913 - every sealed-event field remains explicit
    register: LifecycleRegister,
    *,
    event: EventInput,
    actor: ActorRef,
    order_id: OrderId,
    parent_order_id: OrderId | None,
    intent_id: IntentId,
    state: LifecycleState,
    reason: str,
    quantity: Decimal,
    rule_version: str,
) -> LifecycleRegister:
    sealed = seal_order_event(
        event_id=event.event_id,
        lifecycle_id=register.lifecycle_id,
        parent_ids=(register.events[-1].event_id,) if register.events else (),
        rule_version=rule_version,
        times=event.times,
        actor=actor,
        idempotency_key=event.idempotency_key,
        payload=OrderEventPayload(
            order_id=order_id,
            parent_order_id=parent_order_id,
            intent_id=intent_id,
            state=state,
            reason=reason,
            quantity=Recorded[Decimal](value=quantity),
        ),
        prior_event_digest=register.events[-1].envelope_digest if register.events else None,
    )
    return register.append(sealed)


def accept_intent(
    envelope: ApprovedIntentEnvelope,
    payload: ApprovedIntentPayload,
    *,
    parent_order_id: OrderId,
    actor: ActorRef,
    event: EventInput,
) -> IntentOutcome:
    """Verify the payload and both manifests before recording acceptance."""
    checks = (
        ("payload_digest", envelope.payload_digest == digest(payload), "payload digest mismatch"),
        ("intent_identity", payload.intent_id == envelope.envelope_id, "intent id mismatch"),
        ("policy_enabled", payload.policy_manifest.enabled, "policy manifest is disabled"),
        (
            "authority_policy_binding",
            payload.authority_manifest.policy_manifest_digest == digest(payload.policy_manifest),
            "authority manifest does not bind this policy manifest",
        ),
        (
            "authority_identity",
            payload.authority_manifest.authority_id == envelope.approved_by.actor_id,
            "authority manifest does not name the approving actor",
        ),
        (
            "authority_authorized",
            payload.authority_manifest.authorized,
            "authority manifest records no authorization",
        ),
    )
    failed = next(((name, evidence) for name, passed, evidence in checks if not passed), None)
    state = LifecycleState.REFUSED if failed else LifecycleState.INTENT_ACCEPTED
    reason = failed[1] if failed else "payload, policy manifest and authority manifest verified"
    register = _append(
        LifecycleRegister(lifecycle_id=envelope.lifecycle_id),
        event=event,
        actor=actor,
        order_id=parent_order_id,
        parent_order_id=None,
        intent_id=envelope.envelope_id,
        state=state,
        reason=reason,
        quantity=payload.quantity,
        rule_version="lc-m3-intent/1.0",
    )
    refusal = IntentRefused(predicate=failed[0], evidence=failed[1]) if failed else None
    return IntentOutcome(journal=register, parent_order_id=parent_order_id, refusal=refusal)


def authorize_strategy(
    outcome: IntentOutcome, *, actor: ActorRef, event: EventInput
) -> IntentOutcome:
    accepted = outcome.journal.events[-1].payload
    if accepted.state is not LifecycleState.INTENT_ACCEPTED:
        register = _append(
            outcome.journal,
            event=event,
            actor=actor,
            order_id=outcome.parent_order_id,
            parent_order_id=None,
            intent_id=accepted.intent_id,
            state=LifecycleState.NOT_REACHED,
            reason=f"STRATEGY_AUTHORIZED not reached from {accepted.state.value}",
            quantity=require_recorded(accepted.quantity),
            rule_version="lc-m3-strategy/1.0",
        )
        return outcome.model_copy(update={"journal": register})
    register = _append(
        outcome.journal,
        event=event,
        actor=actor,
        order_id=outcome.parent_order_id,
        parent_order_id=None,
        intent_id=accepted.intent_id,
        state=LifecycleState.STRATEGY_AUTHORIZED,
        reason="named policy manifest is authorized",
        quantity=require_recorded(accepted.quantity),
        rule_version="lc-m3-strategy/1.0",
    )
    return outcome.model_copy(update={"journal": register})


def activate_parent(outcome: IntentOutcome, *, actor: ActorRef, event: EventInput) -> IntentOutcome:
    current = outcome.journal.events[-1].payload
    if current.state is not LifecycleState.STRATEGY_AUTHORIZED:
        register = _append(
            outcome.journal,
            event=event,
            actor=actor,
            order_id=outcome.parent_order_id,
            parent_order_id=None,
            intent_id=current.intent_id,
            state=LifecycleState.NOT_REACHED,
            reason=f"PARENT_ACTIVE not reached from {current.state.value}",
            quantity=require_recorded(current.quantity),
            rule_version="lc-m3-lifecycle/1.0",
        )
        return outcome.model_copy(update={"journal": register})
    return outcome.model_copy(
        update={
            "journal": _append(
                outcome.journal,
                event=event,
                actor=actor,
                order_id=outcome.parent_order_id,
                parent_order_id=None,
                intent_id=current.intent_id,
                state=LifecycleState.PARENT_ACTIVE,
                reason="authorized strategy activated parent order",
                quantity=require_recorded(current.quantity),
                rule_version="lc-m3-lifecycle/1.0",
            )
        }
    )


def split_parent(
    outcome: IntentOutcome, *, actor: ActorRef, children: tuple[ChildOrder, ...]
) -> IntentOutcome:
    """Record a conserved parent-to-child split, or refuse before appending anything."""
    if outcome.refusal is not None:
        return outcome
    parent = outcome.journal.events[-1].payload
    if parent.state is not LifecycleState.PARENT_ACTIVE:
        raise ValueError("parent must be active before it can be split")
    if sum((child.quantity for child in children), Decimal(0)) != require_recorded(parent.quantity):
        raise ValueError("child quantity must equal parent quantity")
    if len({child.order_id for child in children}) != len(children):
        raise ValueError("child order identifiers must be unique")
    register = outcome.journal
    for child in children:
        register = _append(
            register,
            event=child.event,
            actor=actor,
            order_id=child.order_id,
            parent_order_id=outcome.parent_order_id,
            intent_id=parent.intent_id,
            state=LifecycleState.PARENT_ACTIVE,
            reason="child order activated from conserved parent split",
            quantity=child.quantity,
            rule_version="lc-m3-split/1.0",
        )
    return outcome.model_copy(update={"journal": register})


_ALLOWED = {
    LifecycleState.PARENT_ACTIVE: {
        LifecycleState.PARTIALLY_EXECUTED,
        LifecycleState.EXECUTED,
    },
    LifecycleState.PARTIALLY_EXECUTED: {
        LifecycleState.PARTIALLY_EXECUTED,
        LifecycleState.EXECUTED,
    },
    LifecycleState.EXECUTED: {LifecycleState.TRADE_CAPTURED},
    LifecycleState.TRADE_CAPTURED: {LifecycleState.ALLOCATED},
    LifecycleState.ALLOCATED: {LifecycleState.MATCHED, LifecycleState.EXCEPTION_OPEN},
    LifecycleState.MATCHED: {LifecycleState.AFFIRMED, LifecycleState.EXCEPTION_OPEN},
}


def transition(  # noqa: PLR0913 - transition evidence is explicit at the call site
    register: LifecycleRegister,
    *,
    order_id: OrderId,
    target: LifecycleState,
    reason: str,
    actor: ActorRef,
    event: EventInput,
    quantity: Decimal | None = None,
) -> LifecycleRegister:
    snapshot = replay(register.events)
    current = snapshot.orders[order_id]
    recorded_target = target
    recorded_reason = reason
    if target not in _ALLOWED.get(current.state, set()):
        recorded_target = LifecycleState.NOT_REACHED
        recorded_reason = f"{target.value} not reached from {current.state.value}: {reason}"
    intent_id = next(
        e.payload.intent_id for e in reversed(register.events) if e.payload.order_id == order_id
    )
    return _append(
        register,
        event=event,
        actor=actor,
        order_id=order_id,
        parent_order_id=current.parent_order_id,
        intent_id=intent_id,
        state=recorded_target,
        reason=recorded_reason,
        quantity=quantity if quantity is not None else current.quantity,
        rule_version="lc-m3-lifecycle/1.0",
    )


def record_not_reached(  # noqa: PLR0913 - failed transition evidence stays explicit
    register: LifecycleRegister,
    *,
    order_id: OrderId,
    requested: LifecycleState,
    reason: str,
    actor: ActorRef,
    event: EventInput,
) -> LifecycleRegister:
    """Record that a named state was not reached, without pretending to enter it."""
    snapshot = replay(register.events)
    current = snapshot.orders[order_id]
    intent_id = next(
        entry.payload.intent_id
        for entry in reversed(register.events)
        if entry.payload.order_id == order_id
    )
    return _append(
        register,
        event=event,
        actor=actor,
        order_id=order_id,
        parent_order_id=current.parent_order_id,
        intent_id=intent_id,
        state=LifecycleState.NOT_REACHED,
        reason=f"{requested.value} not reached: {reason}",
        quantity=current.quantity,
        rule_version="lc-m3-lifecycle/1.0",
    )


def replay(events: tuple[OrderEvent, ...]) -> RegisterSnapshot:
    if not events:
        raise ValueError("cannot replay an empty journal")
    register = LifecycleRegister(lifecycle_id=events[0].lifecycle_id)
    orders: dict[OrderId, OrderSnapshot] = {}
    for event in events:
        register = register.append(event)
        payload = event.payload
        event_quantity = require_recorded(payload.quantity)
        prior = orders.get(payload.order_id)
        is_execution = payload.state in {
            LifecycleState.PARTIALLY_EXECUTED,
            LifecycleState.EXECUTED,
        }
        orders[payload.order_id] = OrderSnapshot(
            state=payload.state,
            quantity=prior.quantity if prior is not None else event_quantity,
            executed_quantity=(
                prior.executed_quantity + event_quantity
                if prior is not None and is_execution
                else (prior.executed_quantity if prior is not None else Decimal(0))
            ),
            parent_order_id=payload.parent_order_id,
        )
    return RegisterSnapshot(
        lifecycle_id=register.lifecycle_id,
        orders=orders,
        last_event_digest=register.events[-1].envelope_digest,
    )
