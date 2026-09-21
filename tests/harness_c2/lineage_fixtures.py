"""Builders for a complete, valid lineage, and the ways of breaking one.

Every builder returns a *valid* envelope by default and takes overrides, so a
test names only the thing it is about. A fixture that had to be corrected in
each test would let a broken default hide behind twenty local fixes.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from cannae_kernel.absence import Recorded
from cannae_kernel.actor import ActorKind, ActorRef
from cannae_kernel.canonical import digest
from cannae_kernel.clocks import EventTimes
from cannae_kernel.disposition import Disposition
from cannae_kernel.effects import ExternalEffect, OperationEffects
from cannae_kernel.envelopes import (
    ApprovedIntentEnvelope,
    ClearingTransformation,
    ExecutionEvent,
    ObligationAcceptanceRecord,
    SettlementObligationEnvelope,
)
from cannae_kernel.ids import ActorId, EventId, IntentId, LifecycleId, ObligationId
from cannae_kernel.provenance import Provenance
from cannae_kernel.session import BusinessDate, MarketSession, SessionContext

AT = datetime(2026, 9, 21, 14, 30, tzinfo=UTC)

LIFECYCLE = LifecycleId("lif_01M2P20SY00000000000000001")
OTHER_LIFECYCLE = LifecycleId("lif_01M2P20SY00000000000000002")
INTENT = IntentId("int_01M2P20SY00000000000000001")
OTHER_INTENT = IntentId("int_01M2P20SY00000000000000002")
OBLIGATION = ObligationId("obl_01M2P20SY00000000000000001")
OTHER_OBLIGATION = ObligationId("obl_01M2P20SY00000000000000002")
EVENT_A = EventId("evt_01M2P20SY00000000000000001")
EVENT_B = EventId("evt_01M2P20SY00000000000000002")


def operator() -> ActorRef:
    return ActorRef(
        actor_id=ActorId("act_01M2P20SY00000000000000001"),
        actor_kind=ActorKind.HUMAN,
        role="Tier 1 operator",
        entitlement_refs=("CAOM-001:tier-1",),
        authenticated=True,
    )


def service() -> ActorRef:
    return ActorRef(
        actor_id=ActorId("act_01M2P20SY00000000000000002"),
        actor_kind=ActorKind.DETERMINISTIC_SERVICE,
        role="Atreides acceptance service",
        entitlement_refs=("CAOM-001:service",),
        authenticated=True,
    )


def session() -> SessionContext:
    return SessionContext(
        session=MarketSession.REGULAR,
        business_date=BusinessDate(
            value=date(2026, 9, 21),
            calendar="FEDWIRE_FUNDS",
            established_by="the rail's published calendar",
        ),
    )


def times() -> EventTimes:
    return EventTimes(event_time=AT, observation_time=AT, processing_time=AT)


def intent(**overrides: object) -> ApprovedIntentEnvelope:
    fields: dict[str, object] = {
        "envelope_id": INTENT,
        "lifecycle_id": LIFECYCLE,
        "revision": 1,
        "prior_digest": None,
        "session": session(),
        "approved_by": operator(),
        "provenance": Provenance.HUMAN_JUDGMENT,
        "effects": OperationEffects(
            operation="release_approved_intent",
            effects=(ExternalEffect.SUBMITS,),
            note="releasing an approved intent hands an instruction to the middle layer",
        ),
        "payload_digest": "sha256:" + "a" * 64,
    }
    fields.update(overrides)
    return ApprovedIntentEnvelope(**fields)  # type: ignore[arg-type]


def execution(intent_envelope: ApprovedIntentEnvelope, **overrides: object) -> ExecutionEvent:
    fields: dict[str, object] = {
        "event_id": EVENT_A,
        "lifecycle_id": LIFECYCLE,
        "intent_id": intent_envelope.envelope_id,
        "intent_digest": digest(intent_envelope),
        "times": times(),
        "session": session(),
        "provenance": Provenance.FACT_SYNTHETIC,
        "payload_digest": "sha256:" + "b" * 64,
    }
    fields.update(overrides)
    return ExecutionEvent(**fields)  # type: ignore[arg-type]


def clearing(*executions: ExecutionEvent, **overrides: object) -> ClearingTransformation:
    fields: dict[str, object] = {
        "lifecycle_id": LIFECYCLE,
        "input_digests": tuple(digest(e) for e in executions),
        "output_digest": "sha256:" + "c" * 64,
        "rule_set_version": "cannae.netting/1.0",
        "provenance": Provenance.POLICY_RESULT,
    }
    fields.update(overrides)
    return ClearingTransformation(**fields)  # type: ignore[arg-type]


def obligation(
    transformation: ClearingTransformation, **overrides: object
) -> SettlementObligationEnvelope:
    fields: dict[str, object] = {
        "obligation_id": OBLIGATION,
        "lifecycle_id": LIFECYCLE,
        "transformation_digest": digest(transformation),
        "session": session(),
        "provenance": Provenance.POLICY_RESULT,
        "payload_digest": "sha256:" + "d" * 64,
    }
    fields.update(overrides)
    return SettlementObligationEnvelope(**fields)  # type: ignore[arg-type]


def acceptance(
    settlement: SettlementObligationEnvelope, **overrides: object
) -> ObligationAcceptanceRecord:
    fields: dict[str, object] = {
        "obligation_id": settlement.obligation_id,
        "obligation_digest": digest(settlement),
        "disposition": Disposition.PASS,
        "dsor_record": Recorded[str](value="DSOR-2026-09-21-0001"),
        "decided_by": service(),
        "provenance": Provenance.POLICY_RESULT,
    }
    fields.update(overrides)
    return ObligationAcceptanceRecord(**fields)  # type: ignore[arg-type]


def complete_chain() -> dict[str, list[Any]]:
    """Every stage present and every link sound. The baseline every test varies."""
    i = intent()
    e = execution(i)
    c = clearing(e)
    o = obligation(c)
    a = acceptance(o)
    return {
        "intents": [i],
        "executions": [e],
        "clearings": [c],
        "obligations": [o],
        "acceptances": [a],
    }
