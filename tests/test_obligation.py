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
from test_lifecycle import _actor, _envelope, _event, _id, _payload, _wire_bytes
from test_policy_and_venue import INTENT_DIGEST, default_terms
from test_trade import _allocated, _fact

from emulators.matching import MatchingOutcome
from lc.asset_profile import BILATERAL_TREASURY
from lc.clearing import clear_gross
from lc.events import LifecycleState, OrderTerms
from lc.institutional import AssetClass, InstrumentReference, QuantityUnit, TreasuryReference
from lc.lifecycle import ApprovedIntentPayload, OrderSide, replay
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
    gross_trade_from_capture,
)
from lc.trade import affirm, record_match

OBLIGATION_ID = ObligationId(_id("obl_", 31))


def _sha(value: str) -> str:
    return "sha256:" + sha256(value.encode()).hexdigest()


def _manifest(transformation_digest: str, intent_digest: str = INTENT_DIGEST) -> SourceManifest:
    digests = {
        SourceKind.APPROVED_INTENT: intent_digest,
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


def _formed(  # noqa: PLR0913 - each lie the tests tell is its own keyword
    *,
    supplied_cash: Decimal = Decimal("100.00"),
    terms: OrderTerms | None = None,
    instrument_asset_class: AssetClass = AssetClass.US_TREASURY,
    intent_payload: ApprovedIntentPayload | None = None,
    supplied_intent: ApprovedIntentPayload | None = None,
    securities_quantity: Decimal | None = None,
    gross_cash: Decimal | None = None,
    gross_instrument: InstrumentReference | None = None,
    participants: tuple[ParticipantAccount, ...] | None = None,
    session_date: date = date(2026, 9, 24),
) -> FormedObligation:
    journal, captured, _ = _allocated(terms, intent_payload)
    supplied = supplied_intent or intent_payload or _payload()
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
    instrument = (
        InstrumentReference(
            cusip="91282CJL6",
            asset_class=AssetClass.US_TREASURY,
            quantity_unit=QuantityUnit.FACE,
            treasury=TreasuryReference(
                annual_coupon_rate=Decimal(0),
                dated_date=date(2026, 3, 24),
                maturity_date=date(2036, 3, 24),
                payments_per_year=2,
            ),
        )
        if instrument_asset_class is AssetClass.US_TREASURY
        else InstrumentReference(
            cusip="037833100",
            asset_class=AssetClass.US_EQUITY,
            quantity_unit=QuantityUnit.SHARES,
        )
    )
    gross_trade = gross_trade_from_capture(
        captured,
        trade_digest=digest(captured),
        obligation_id=OBLIGATION_ID,
        instrument=instrument if gross_instrument is None else gross_instrument,
        settlement_date=date(2026, 9, 24),
    )
    if gross_cash is not None:
        gross_trade = gross_trade.model_copy(update={"cash": gross_cash})
    transformation, clearing = clear_gross(
        lifecycle_id=affirmed.journal.lifecycle_id, trades=(gross_trade,)
    )
    participants = participants or (
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
                value=session_date,
                calendar="Fedwire Funds",
                established_by="scenario settlement calendar",
            ),
        ),
        source_manifest=_manifest(digest(transformation), digest(_envelope(supplied))),
        asset_profile=BILATERAL_TREASURY,
        approved_intent=_envelope(supplied),
        approved_intent_bytes=_wire_bytes(supplied),
        instrument=instrument,
        securities_leg=SecuritiesLeg(
            instrument_id=captured.instrument_id,
            quantity=captured.quantity if securities_quantity is None else securities_quantity,
            delivering_account_id="SEC-DELIVER",
            receiving_account_id="SEC-RECEIVE",
        ),
        cash_leg=CashLeg(
            principal=supplied_cash,
            accrued=Decimal("0"),
            total=supplied_cash,
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
        "approved_intent_digest",
        "firm_securities_direction",
        "session",
        "provenance",
        "payload_digest",
    }
    assert formed.envelope.payload_digest == digest(formed.payload)
    assert formed.envelope.approved_intent_digest == INTENT_DIGEST
    assert formed.envelope.firm_securities_direction.value == "RECEIVE"
    cash = formed.payload.cash_leg
    assert cash is not None and cash.total == cash.principal + cash.accrued
    assert {reference.kind for reference in formed.payload.source_manifest.references} == {
        SourceKind.APPROVED_INTENT,
        SourceKind.EXECUTION,
        SourceKind.TRADE_CAPTURE,
        SourceKind.ALLOCATION,
        SourceKind.MATCH_AFFIRMATION,
        SourceKind.CLEARING_TRANSFORMATION,
    }
    assert (
        SettlementObligationEnvelope.model_validate_json(formed.envelope.model_dump_json())
        == formed.envelope
    )


def test_payload_digest_is_stable_and_a_perturbed_field_changes_it() -> None:
    payload = _formed().payload
    assert canonical_bytes_of(payload) == payload.canonical_bytes()
    assert digest(payload) == digest(payload.model_copy(deep=True))
    assert payload.cash_leg is not None
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
            principal=Decimal("9.95"),
            accrued=Decimal("0"),
            total=Decimal("9.94"),
            currency="USD",
            value_date=date(2026, 9, 24),
            paying_account_id="CASH-PAY",
            receiving_account_id="CASH-RECEIVE",
        )


def test_formation_refuses_a_supplied_cash_total_that_is_wrong_by_one_cent() -> None:
    with pytest.raises(ValueError, match="CASH_NOT_PRICE_TIMES_QUANTITY"):
        _formed(supplied_cash=Decimal("99.51"))


def test_formation_refuses_an_instrument_that_is_not_the_sealed_issue() -> None:
    with pytest.raises(ValueError, match="INSTRUMENT_UNBOUND"):
        _formed(instrument_asset_class=AssetClass.US_EQUITY)


def test_formation_refuses_an_asset_class_outside_the_registered_profile() -> None:
    equity_terms = default_terms(issue="037833100")
    with pytest.raises(ValueError, match="ASSET_PROFILE_MISMATCH"):
        _formed(terms=equity_terms, instrument_asset_class=AssetClass.US_EQUITY)


# INST-1 Amendment 3, V1: cash is derived from the sealed fills, never supplied.


def test_v1_captured_price_100_cannot_form_at_99_5() -> None:
    """The merged fixture formed at 99.5 although the captured print was 100."""
    with pytest.raises(ValueError, match="CASH_NOT_PRICE_TIMES_QUANTITY"):
        _formed(supplied_cash=Decimal("99.50"), gross_cash=Decimal("99.50"))


def test_v1_scaling_price_and_both_cash_figures_together_is_refused() -> None:
    """Price x 1,000 with both cash figures scaled to match was accepted before WP1b."""
    with pytest.raises(ValueError, match="CASH_NOT_PRICE_TIMES_QUANTITY"):
        _formed(supplied_cash=Decimal("100000.00"), gross_cash=Decimal("100000.00"))


def test_v1_clearing_cash_that_disagrees_with_the_fills_is_refused() -> None:
    with pytest.raises(ValueError, match="clearing cash is not derived cash"):
        _formed(gross_cash=Decimal("100.01"))


def test_v1_formed_cash_is_the_captured_consideration_per_100_of_par() -> None:
    cash = _formed().payload.cash_leg
    assert cash is not None
    assert (cash.principal, cash.accrued, cash.total) == (
        Decimal("100.00"),
        Decimal("0.00"),
        Decimal("100.00"),
    )


def test_v1_securities_quantity_other_than_executed_is_refused() -> None:
    with pytest.raises(ValueError, match="QUANTITY_NOT_EXECUTED"):
        _formed(securities_quantity=Decimal(99))


# INST-1 Amendment 3, V2: the side and the firm come from the sealed order.


def test_v2_direction_is_read_from_the_sealed_side_not_a_caller_string() -> None:
    assert _formed().envelope.firm_securities_direction.value == "RECEIVE"


def test_v2_a_sealed_buy_cannot_be_formed_with_the_seller_as_the_firm() -> None:
    """Before WP1b, approved_side=SELL under the same intent formed a DELIVER obligation."""
    swapped = (
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
            participant_id="participant-seller",
            account_id="CASH-PAY",
            role=AccountRole.DELIVERER,
        ),
        ParticipantAccount(
            participant_id="participant-buyer",
            account_id="CASH-RECEIVE",
            role=AccountRole.RECEIVER,
        ),
    )
    with pytest.raises(ValueError, match="PARTY_ROLE_MISMATCH"):
        _formed(participants=swapped)


def test_v2_another_intent_cannot_stand_in_for_the_one_the_order_was_sealed_under() -> None:
    other = _payload().model_copy(update={"quantity": Decimal(200)})
    with pytest.raises(ValueError, match="APPROVED_INTENT_UNBOUND"):
        _formed(supplied_intent=other)


def test_v2_an_approved_sell_cannot_be_executed_as_a_sealed_buy() -> None:
    """The side comes from the sealed intent bytes; a child sealed BUY under it is refused."""
    with pytest.raises(ValueError, match="SIDE_NOT_APPROVED"):
        _formed(
            intent_payload=_sell_intent(),
            terms=default_terms(approved_intent_digest=digest(_envelope(_sell_intent()))),
        )


def _sell_intent() -> ApprovedIntentPayload:
    return _payload().model_copy(update={"side": OrderSide.SELL})


def _sell_terms(**changes: object) -> OrderTerms:
    return default_terms(
        side="SELL", approved_intent_digest=digest(_envelope(_sell_intent())), **changes
    )


def test_v2_a_sealed_sell_without_a_sealed_capacity_is_refused() -> None:
    with pytest.raises(ValueError, match="ACCOUNT_CAPACITY_ABSENT"):
        _formed(intent_payload=_sell_intent(), terms=_sell_terms())


def test_v2_a_sealed_sell_delivers_and_carries_its_capacity_under_the_digest() -> None:
    sell = _sell_terms(account_capacity="CUSTOMER")
    seller_is_firm = (
        ParticipantAccount(
            participant_id="participant-firm",
            account_id="SEC-DELIVER",
            role=AccountRole.DELIVERER,
        ),
        ParticipantAccount(
            participant_id="participant-street",
            account_id="SEC-RECEIVE",
            role=AccountRole.RECEIVER,
        ),
        ParticipantAccount(
            participant_id="participant-street",
            account_id="CASH-PAY",
            role=AccountRole.DELIVERER,
        ),
        ParticipantAccount(
            participant_id="participant-firm",
            account_id="CASH-RECEIVE",
            role=AccountRole.RECEIVER,
        ),
    )
    formed = _formed(intent_payload=_sell_intent(), terms=sell, participants=seller_is_firm)
    assert formed.envelope.firm_securities_direction.value == "DELIVER"
    assert formed.payload.seller_capacity == "CUSTOMER"
    relabelled = formed.payload.model_copy(update={"seller_capacity": "PROPRIETARY"})
    assert digest(relabelled) != formed.envelope.payload_digest


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


def test_the_envelope_session_must_be_the_settlement_date() -> None:
    """Atreides binds customer control evidence to the envelope's business date."""
    with pytest.raises(ValueError, match="SETTLEMENT_DATE_CONFLICT"):
        _formed(session_date=date(2026, 9, 23))
