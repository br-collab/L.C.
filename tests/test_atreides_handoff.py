"""W4 WP-6b: the formed obligation through Atreides' real predicates."""

from __future__ import annotations

import importlib.util
import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from cannae_kernel.absence import Absent, Recorded
from cannae_kernel.canonical import digest
from cannae_kernel.disposition import Disposition
from cannae_kernel.envelopes import ObligationAcceptanceRecord
from test_lifecycle import _actor, _event
from test_obligation import _formed

if importlib.util.find_spec("atreides") is None:
    if os.environ.get("HANDOFF_EXTRA_REQUIRED") == "1":
        pytest.fail("the pinned Atreides handoff boundary is required but not installed")
    pytest.skip("runs in the pinned Atreides handoff CI job", allow_module_level=True)

from atreides.acceptance.service import evaluate_candidate
from atreides.rails.cato_cash import (
    CashRail,
    CatoCashDecision,
    FundingState,
    OperationContext,
    RailState,
    RailStatus,
    evaluate,
)

from lc.events import LifecycleState
from lc.lifecycle import replay
from lc.obligation import FormedObligation, HandoffOutcome, record_atreides_handoff

AT = datetime(2026, 9, 28, 13, 0, tzinfo=UTC)


def _gate(formed: FormedObligation, *, funded: bool) -> CatoCashDecision:
    amount = formed.payload.cash_leg.total
    return evaluate(
        operation=OperationContext(
            notional=amount,
            currency=formed.payload.cash_leg.currency,
            is_material=False,
            is_lvps_material=False,
        ),
        funding=FundingState(
            Decimal("5000") if funded else Decimal(0),
            amount,
            Decimal("10000"),
            True,
        ),
        rails={CashRail.FEDWIRE: RailState(CashRail.FEDWIRE, RailStatus.AVAILABLE, 7200)},
        ofr_stlfsi4=0.0,
        obligation_id=formed.envelope.obligation_id,
        obligation_digest=digest(formed.envelope),
    )


def _acceptance(*, funded: bool) -> tuple[FormedObligation, ObligationAcceptanceRecord]:
    formed = _formed()
    record = evaluate_candidate(
        formed.envelope,
        formed.payload.canonical_bytes(),
        acceptance_id=uuid.UUID("00000000-0000-4000-8000-000000000006"),
        evaluated_at=AT,
        decided_by=_actor(),
        gate_decision=_gate(formed, funded=funded),
        halt=None,
    )
    return formed, record


def test_real_atreides_predicates_accept_and_lc_records_the_handoff() -> None:
    formed, acceptance = _acceptance(funded=True)
    assert acceptance.disposition is Disposition.PASS
    assert isinstance(acceptance.dsor_record, Recorded)
    assert acceptance.obligation_digest == digest(formed.envelope)

    result = record_atreides_handoff(
        formed,
        order_id=formed.journal.events[-1].payload.order_id,
        acceptance=acceptance,
        actor=_actor(),
        event=_event(29),
    )

    assert isinstance(result, HandoffOutcome)
    assert result.acceptance is acceptance
    assert (
        replay(result.journal.events).orders[formed.journal.events[-1].payload.order_id].state
        is LifecycleState.HANDED_TO_ATREIDES
    )


def test_short_cash_leg_is_a_recorded_hold_and_refusal() -> None:
    formed, acceptance = _acceptance(funded=False)
    assert acceptance.disposition is Disposition.HOLD
    assert isinstance(acceptance.dsor_record, Absent)
    assert acceptance.dsor_record.reason == "CASH_GATE_HOLD:UNFUNDED_AT_SETTLEMENT_INSTANT"

    result = record_atreides_handoff(
        formed,
        order_id=formed.journal.events[-1].payload.order_id,
        acceptance=acceptance,
        actor=_actor(),
        event=_event(29),
    )

    assert (
        replay(result.journal.events).orders[formed.journal.events[-1].payload.order_id].state
        is LifecycleState.REFUSED_BY_ATREIDES
    )


def test_acceptance_record_references_digest_and_copies_no_economics() -> None:
    formed, acceptance = _acceptance(funded=True)
    dumped = acceptance.model_dump_json()
    assert acceptance.obligation_digest == digest(formed.envelope)
    for economic in (
        str(formed.payload.cash_leg.total),
        formed.payload.cash_leg.currency,
        formed.payload.securities_leg.instrument_id,
    ):
        assert economic not in dumped


def test_lc_refuses_an_acceptance_for_another_envelope() -> None:
    formed, acceptance = _acceptance(funded=True)
    wrong = acceptance.model_copy(update={"obligation_digest": "sha256:" + "0" * 64})
    with pytest.raises(ValueError, match="does not bind"):
        record_atreides_handoff(
            formed,
            order_id=formed.journal.events[-1].payload.order_id,
            acceptance=wrong,
            actor=_actor(),
            event=_event(29),
        )
