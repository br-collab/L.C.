"""Consume the pinned Aureon producer's real approved-intent bytes."""

from __future__ import annotations

import importlib.util
import os
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from cannae_kernel.actor import ActorKind, ActorRef
from cannae_kernel.clocks import EventTimes
from cannae_kernel.disposition import Disposition
from cannae_kernel.envelopes import ApprovedIntentEnvelope
from cannae_kernel.ids import ActorId, EventId, LifecycleId, OrderId
from cannae_kernel.session import BusinessDate, MarketSession, SessionContext

from lc.lifecycle import EventInput, accept_intent

if importlib.util.find_spec("aureon") is None:
    if os.environ.get("AUREON_WIRE_REQUIRED") == "1":
        pytest.fail("the pinned Aureon approved-intent producer is required but not installed")
    pytest.skip("runs in the pinned Aureon wire CI job", allow_module_level=True)

from aureon.contracts.approved_intent import ApprovalRecord, seal_approved_intent

AT = datetime(2026, 9, 29, 15, 0, tzinfo=UTC)
ACTOR = ActorRef(
    actor_id=ActorId("act_00000000000000000000000001"),
    actor_kind=ActorKind.HUMAN,
    role="CAOM-001 operator",
    entitlement_refs=("TRADER",),
    authenticated=True,
)
SESSION = SessionContext(
    session=MarketSession.REGULAR,
    business_date=BusinessDate(
        value=date(2026, 9, 29), calendar="SIFMA-US", established_by=str(ACTOR.actor_id)
    ),
)


def _seal(
    *, allocation_accounts: tuple[str, ...] | None
) -> tuple[ApprovedIntentEnvelope, Any, bytes]:
    decision = {
        "id": "DEC-W5-TREASURY",
        "symbol": "91282CJL6",
        "action": "BUY",
        "asset_class": "fixed_income",
        "shares": 100,
        "quantity_unit": "FACE",
        "price": 99.5,
        "notional": 9950,
        "signal_type": "W5_SYNTHETIC",
        "rationale": "deterministic bilateral Treasury DvP scenario",
        "allocation_accounts": allocation_accounts,
    }
    policy = SimpleNamespace(
        disposition=Disposition.PASS,
        record_id="policy-W5",
        record_digest="sha256:" + "1" * 64,
        decision_digest="sha256:" + "2" * 64,
        rule_set_version="w5-policy/1.0",
        rules_digest="sha256:" + "3" * 64,
        gates=(),
    )
    return cast(
        tuple[ApprovedIntentEnvelope, Any, bytes],
        seal_approved_intent(
            lifecycle_id=LifecycleId("lif_00000000000000000000000003"),
            decision=decision,
            policy_record=policy,
            hold_exception_ids=(),
            approvals=(
                ApprovalRecord(
                    role="TRADER",
                    actor=ACTOR,
                    approved_at=AT,
                    authority_hash="AUTH-W5",
                ),
            ),
            required_roles=("TRADER",),
            release_id="release-W5",
            session=SESSION,
            now=AT,
        ),
    )


def test_lc_consumes_exact_bytes_from_the_pinned_aureon_producer() -> None:
    envelope, _payload, payload_bytes = _seal(
        allocation_accounts=("TREASURY-DELIVERY", "TREASURY-RECEIPT")
    )

    outcome = accept_intent(
        envelope,
        payload_bytes,
        parent_order_id=OrderId("ord_00000000000000000000000010"),
        actor=ACTOR,
        event=EventInput(
            event_id=EventId("evt_00000000000000000000000010"),
            times=EventTimes(
                event_time=AT,
                observation_time=AT,
                processing_time=AT,
                decision_time=AT,
            ),
            idempotency_key="accept-W5",
        ),
    )

    assert outcome.refusal is None
    assert outcome.journal.lifecycle_id == envelope.lifecycle_id


def test_lc_refuses_to_invent_a_missing_allocation_instruction() -> None:
    envelope, _payload, payload_bytes = _seal(allocation_accounts=None)

    with pytest.raises(ValueError, match="no explicit allocation_accounts"):
        accept_intent(
            envelope,
            payload_bytes,
            parent_order_id=OrderId("ord_00000000000000000000000010"),
            actor=ACTOR,
            event=EventInput(
                event_id=EventId("evt_00000000000000000000000010"),
                times=EventTimes(
                    event_time=AT,
                    observation_time=AT,
                    processing_time=AT,
                    decision_time=AT,
                ),
                idempotency_key="accept-W5",
            ),
        )
