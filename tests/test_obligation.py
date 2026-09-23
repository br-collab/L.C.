"""W4 WP-6a acceptance tests for the formed settlement obligation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from hashlib import sha256

import pytest
from cannae_kernel.canonical import canonical_bytes_of, digest
from cannae_kernel.delivery import DeliveryPattern
from cannae_kernel.envelopes import SettlementObligationEnvelope
from cannae_kernel.finality import FinalityType
from cannae_kernel.ids import ObligationId
from cannae_kernel.session import BusinessDate, MarketSession, SessionContext
from test_lifecycle import _actor, _event, _id
from test_trade import _allocated, _fact

from emulators.matching import MatchingOutcome
from lc.clearing import GrossTrade, clear_gross
from lc.events import LifecycleState
from lc.lifecycle import replay
from lc.obligation import (
    AccountRole,
    CandidatePathDescriptor,
    CashLeg,
    ExpectedFinality,
    FormedObligation,
    LegKind,
    ParticipantAccount,
    SecuritiesLeg,
    SourceKind,
    SourceManifest,
    SourceReference,
    form_obligation,
)
from lc.trade import affirm, record_match

OBLIGATION_ID = ObligationId(_id("obl_", 31))


def _sha(value: str) -> str:
    return "sha256:" + sha256(value.encode()).hexdigest()


def _manifest(transformation_digest: str) -> SourceManifest:
    digests = {
        SourceKind.APPROVED_INTENT: _sha("intent"),
        SourceKind.EXECUTION: _sha("execution"),
        SourceKind.TRADE_CAPTURE: _sha("capture"),
        SourceKind.ALLOCATION: _sha("allocation"),
        SourceKind.MATCH_AFFIRMATION: _sha("match"),
        SourceKind.CLEARING_TRANSFORMATION: transformation_digest,
    }
    return SourceManifest(
        references=tuple(
            SourceReference(kind=kind, identifier=f"source-{kind.value.lower()}", digest=value)
            for kind, value in digests.items()
        )
    )


def _formed() -> FormedObligation:
    journal, captured, _ = _allocated()
    fact = _fact(MatchingOutcome.MATCHED_AND_AFFIRMED)
    matched = record_match(
        journal,
        order_id=captured.order_id,
        fact=fact,
        exception_id="unused",
        actor=_actor(),
        event=_event(24),
    )
    affirmed = affirm(
        matched,
        order_id=captured.order_id,
        fact=fact,
        actor=_actor(),
        event=_event(25),
    )
    gross_trade = GrossTrade(
        trade_id=captured.trade_id,
        trade_digest=digest(captured),
        obligation_id=OBLIGATION_ID,
        quantity=captured.quantity,
        cash=Decimal("998.25"),
    )
    transformation, clearing = clear_gross(
        lifecycle_id=affirmed.journal.lifecycle_id, trades=(gross_trade,)
    )
    participants = (
        ParticipantAccount(
            participant_id="participant-seller",
            account_id="SEC-DELIVER",
            role=AccountRole.DELIVERER,
        ),
        ParticipantAccount(
            participant_id="participant-buyer",
            account_id="SEC-RECEIVE",
            role=AccountRole.RECEIVER,
        ),
        ParticipantAccount(
            participant_id="participant-buyer",
            account_id="CASH-PAY",
            role=AccountRole.DELIVERER,
        ),
        ParticipantAccount(
            participant_id="participant-seller",
            account_id="CASH-RECEIVE",
            role=AccountRole.RECEIVER,
        ),
    )
    return form_obligation(
        register=affirmed.journal,
        order_id=captured.order_id,
        transformation=transformation,
        clearing=clearing,
        obligation_id=OBLIGATION_ID,
        session=SessionContext(
            session=MarketSession.REGULAR,
            business_date=BusinessDate(
                value=date(2026, 9, 24),
                calendar="Fedwire Funds",
                established_by="scenario settlement calendar",
            ),
        ),
        source_manifest=_manifest(digest(transformation)),
        securities_leg=SecuritiesLeg(
            instrument_id=captured.instrument_id,
            quantity=captured.quantity,
            delivering_account_id="SEC-DELIVER",
            receiving_account_id="SEC-RECEIVE",
        ),
        cash_leg=CashLeg(
            principal=Decimal("995.00"),
            accrued=Decimal("3.25"),
            total=Decimal("998.25"),
            currency="USD",
            value_date=date(2026, 9, 24),
            paying_account_id="CASH-PAY",
            receiving_account_id="CASH-RECEIVE",
        ),
        participants=participants,
        delivery_pattern=DeliveryPattern.DVP,
        candidate_paths=(
            CandidatePathDescriptor(
                path_id="candidate-fedwire-dvp",
                rail="Fedwire Securities plus Fedwire Funds",
                securities_route="bilateral securities candidate",
                cash_route="bilateral cash candidate",
                delivery_pattern=DeliveryPattern.DVP,
            ),
        ),
        expected_finality=(
            ExpectedFinality(
                leg=LegKind.SECURITIES,
                finality_type=FinalityType.ASSET_FINAL,
                governing_rule_set="fedwire-securities/2026.1",
            ),
            ExpectedFinality(
                leg=LegKind.CASH,
                finality_type=FinalityType.CASH_FINAL,
                governing_rule_set="fedwire-funds/2026.1",
            ),
        ),
        corrections=(),
        actor=_actor(),
        clearing_event=_event(26),
        obligation_event=_event(27),
        candidate_event=_event(28),
    )


def test_emits_the_frozen_envelope_without_extension_and_with_every_owned_field() -> None:
    formed = _formed()
    assert isinstance(formed.envelope, SettlementObligationEnvelope)
    assert set(formed.envelope.model_fields_set) == {
        "obligation_id",
        "lifecycle_id",
        "transformation_digest",
        "session",
        "provenance",
        "payload_digest",
    }
    assert formed.envelope.payload_digest == digest(formed.payload)
    assert formed.payload.cash_leg.total == (
        formed.payload.cash_leg.principal + formed.payload.cash_leg.accrued
    )
    assert set(formed.payload.source_manifest.references[index].kind for index in range(6)) == set(
        SourceKind
    )
    assert (
        SettlementObligationEnvelope.model_validate_json(formed.envelope.model_dump_json())
        == formed.envelope
    )


def test_payload_digest_is_stable_and_a_perturbed_field_changes_it() -> None:
    payload = _formed().payload
    assert canonical_bytes_of(payload) == payload.canonical_bytes()
    assert digest(payload) == digest(payload.model_copy(deep=True))
    cash = payload.cash_leg.model_copy(
        update={"principal": Decimal("996.00"), "total": Decimal("999.25")}
    )
    perturbed = payload.model_copy(update={"cash_leg": cash})
    assert digest(perturbed) != digest(payload)


def test_obligation_and_candidate_readiness_are_explicit_recorded_states() -> None:
    formed = _formed()
    states = [event.payload.state for event in formed.journal.events]
    assert states[-3:] == [
        LifecycleState.CLEARING_TRANSFORMED,
        LifecycleState.OBLIGATION_READY,
        LifecycleState.SETTLEMENT_CANDIDATE_READY,
    ]
    assert LifecycleState.SETTLEMENT_CANDIDATE_READY in {
        order.state for order in replay(formed.journal.events).orders.values()
    }


def test_cash_leg_is_required_and_cannot_be_short_or_internally_inconsistent() -> None:
    with pytest.raises(ValueError, match="cash total must equal principal plus accrued"):
        CashLeg(
            principal=Decimal("995.00"),
            accrued=Decimal("3.25"),
            total=Decimal("997.00"),
            currency="USD",
            value_date=date(2026, 9, 24),
            paying_account_id="CASH-PAY",
            receiving_account_id="CASH-RECEIVE",
        )


def test_source_manifest_refuses_missing_upstream_evidence() -> None:
    with pytest.raises(ValueError, match="every required upstream source kind"):
        SourceManifest(
            references=(
                SourceReference(
                    kind=SourceKind.APPROVED_INTENT,
                    identifier="source-approved-intent",
                    digest=_sha("intent"),
                ),
            )
        )


def test_payload_contains_descriptors_but_no_settlement_instructions() -> None:
    payload = _formed().payload
    assert payload.candidate_paths[0].path_id == "candidate-fedwire-dvp"
    assert "instruction" not in " ".join(payload.__class__.model_fields).lower()
