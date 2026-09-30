"""Wave 5 slice 1: one deterministic bilateral Treasury DvP lifecycle."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Literal

import pytest
from atreides.acceptance.service import evaluate_candidate, prepare_instruction
from atreides.messaging.canonical import (
    CashLegInstruction,
    FinancialInstitution,
    SettlementMethod,
)
from atreides.messaging.readback import (
    SettlementStatus,
    StatusEntry,
    StatusReport,
    reconcile,
)
from atreides.rails.cato_cash import (
    CashRail,
    FundingState,
    OperationContext,
    RailState,
    RailStatus,
    evaluate,
)
from aureon.contracts.approved_intent import ApprovalRecord, seal_approved_intent
from cannae_kernel.absence import Recorded
from cannae_kernel.actor import ActorKind, ActorRef
from cannae_kernel.canonical import canonical_bytes_of, digest, digest_bytes
from cannae_kernel.clocks import EventTimes
from cannae_kernel.delivery import DeliveryPattern
from cannae_kernel.disposition import Disposition
from cannae_kernel.envelopes import (
    ApprovedIntentEnvelope,
    ClearingTransformation,
    ExecutionEvent,
    ObligationAcceptanceRecord,
    SettlementObligationEnvelope,
)
from cannae_kernel.finality import FinalityType
from cannae_kernel.ids import (
    ActorId,
    AllocationId,
    EventId,
    LifecycleId,
    ObligationId,
    OrderId,
    ScenarioId,
)
from cannae_kernel.session import BusinessDate, MarketSession, SessionContext

from emulators.matching import MatchingEmulator, MatchingOutcome, MatchRequest
from emulators.settlement import RailOutcome, SyntheticEntitledMember, SyntheticRail
from emulators.venue import VenueEmulator, VenueOrder, VenueOutcome
from harness_c2.escalation import (
    EscalationRefusedError,
    EscalationRequest,
    EscalationTrigger,
    package_escalation,
)
from harness_c2.lineage import GapCode, LineageRecord, assemble_lineage
from harness_c2.scenario import ScenarioRecord, start_scenario
from harness_c2.transcript import (
    CrossingArtifactEnvelope,
    CrossingTranscript,
    Domain,
    Envelope,
    record_crossing,
)
from lc.clearing import GrossTrade, clear_gross
from lc.lifecycle import (
    ChildOrder,
    EventInput,
    _read_aureon_payload,
    accept_intent,
    activate_parent,
    authorize_strategy,
    split_parent,
)
from lc.obligation import (
    AccountRole,
    CandidatePathDescriptor,
    CashLeg,
    ExpectedFinality,
    LegKind,
    ParticipantAccount,
    SecuritiesLeg,
    SourceKind,
    SourceManifest,
    SourceReference,
    form_obligation,
)
from lc.policy import ExecutionReport, apply_execution
from lc.trade import MatchFact, affirm, allocate, capture, record_match

AUREON_COMMIT = "3e90f72a1ffa177045d4e4f3e7f9770689e037f1"
ATREIDES_COMMIT = "7929b84d4b68c42ed857de42c40e64a9bb48b8a6"
AT = datetime(2026, 9, 29, 15, 0, tzinfo=UTC)


def _id(prefix: str, n: int) -> str:
    return prefix + f"{n:026d}"


ACTOR = ActorRef(
    actor_id=ActorId(_id("act_", 1)),
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


def _event(n: int) -> EventInput:
    moment = AT + timedelta(seconds=n)
    return EventInput(
        event_id=EventId(_id("evt_", n)),
        times=EventTimes(
            event_time=moment,
            observation_time=moment,
            processing_time=moment,
            decision_time=moment,
        ),
        idempotency_key=f"slice1-{n}",
    )


def _scenario() -> ScenarioRecord:
    return start_scenario(
        scenario_id=ScenarioId(_id("scn_", 1)),
        seed=29,
        pinned_commits={"aureon": AUREON_COMMIT, "atreides": ATREIDES_COMMIT},
        policy_versions={"aureon": "w5-policy/1.0", "lc": "lc-m7/1.0"},
        business_date=SESSION.business_date,
    )


def _times(n: int) -> EventTimes:
    return _event(n).times


def _crossing(  # noqa: PLR0913 - every crossing field is evidence
    transcript: CrossingTranscript,
    scenario: ScenarioRecord,
    *,
    producer: Domain,
    consumer: Domain,
    envelope: Envelope,
    payload_bytes: bytes,
    disposition: Disposition,
    reason: str,
    n: int,
) -> CrossingTranscript:
    return transcript.append(
        record_crossing(
            scenario=scenario,
            producer=producer,
            consumer=consumer,
            lifecycle_id=scenario.lifecycle_id,
            envelope=envelope,
            payload_bytes=payload_bytes,
            producer_asserted_digest=digest_bytes(payload_bytes),
            times=_times(n),
            accepted_disposition=disposition,
            accepted_reason=reason,
        )
    )


def _artifact_envelope(
    scenario: ScenarioRecord,
    *,
    artifact_id: str,
    artifact_kind: Literal[
        "prepared_instruction", "member_submission", "rail_response", "reconciliation"
    ],
    payload_bytes: bytes,
) -> CrossingArtifactEnvelope:
    return CrossingArtifactEnvelope(
        artifact_id=artifact_id,
        lifecycle_id=scenario.lifecycle_id,
        artifact_kind=artifact_kind,
        payload_digest=digest_bytes(payload_bytes),
    )


def _run(  # noqa: PLR0915 - the ordered lifecycle remains visible as one experiment
    *, funded: bool
) -> tuple[ScenarioRecord, CrossingTranscript]:
    scenario = _scenario()
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
        "allocation_accounts": ("ACCOUNT-A", "ACCOUNT-B"),
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
    intent, _aureon_payload, intent_bytes = seal_approved_intent(
        lifecycle_id=scenario.lifecycle_id,
        decision=decision,
        policy_record=policy,
        hold_exception_ids=(),
        approvals=(
            ApprovalRecord(role="TRADER", actor=ACTOR, approved_at=AT, authority_hash="AUTH-W5"),
        ),
        required_roles=("TRADER",),
        release_id="release-W5",
        session=SESSION,
        now=AT,
    )
    internal = _read_aureon_payload(intent, intent_bytes)
    accepted = accept_intent(
        intent,
        intent_bytes,
        parent_order_id=OrderId(_id("ord_", 10)),
        actor=ACTOR,
        event=_event(10),
    )
    accepted = authorize_strategy(accepted, actor=ACTOR, event=_event(11))
    accepted = activate_parent(accepted, actor=ACTOR, event=_event(12))
    child_id = OrderId(_id("ord_", 11))
    register = split_parent(
        accepted,
        actor=ACTOR,
        children=(ChildOrder(order_id=child_id, quantity=Decimal("100"), event=_event(13)),),
    ).journal
    venue_order = VenueOrder(
        lifecycle_id=scenario.lifecycle_id,
        order_id=child_id,
        intent_id=intent.envelope_id,
        intent_digest=digest(intent),
        quantity=Decimal("100"),
        limit_price=Decimal("99.5"),
        policy_expires_at=AT + timedelta(hours=1),
        session=SESSION,
    )
    venue = VenueEmulator(seed=8).execute(venue_order, outcome=VenueOutcome.FILL, event_time=AT)
    assert isinstance(venue.event, Recorded) and isinstance(venue.report, Recorded)
    report = ExecutionReport.model_validate(venue.report.value.model_dump(mode="python"))
    applied = apply_execution(register, venue.event.value, report, actor=ACTOR, event=_event(21))
    register, captured = capture(applied.journal, report, internal, actor=ACTOR, event=_event(22))
    register, allocation = allocate(
        register,
        captured,
        internal,
        allocation_id=AllocationId(_id("alc_", 23)),
        actor=ACTOR,
        event=_event(23),
    )
    response = MatchingEmulator(seed=9).respond(
        MatchRequest(
            trade_id=captured.trade_id,
            instrument_id=captured.instrument_id,
            quantity=str(captured.quantity),
            allocation_digest=digest(allocation),
        ),
        outcome=MatchingOutcome.MATCHED_AND_AFFIRMED,
    )
    fact = MatchFact(
        response_id=response.response_id,
        trade_id=response.trade_id,
        matched=response.matched,
        affirmed=response.affirmed,
        reason=response.reason,
        provenance=response.provenance,
    )
    matched = record_match(
        register, order_id=child_id, fact=fact, exception_id="unused", actor=ACTOR, event=_event(24)
    )
    affirmed = affirm(matched, order_id=child_id, fact=fact, actor=ACTOR, event=_event(25))
    obligation_id = ObligationId(_id("obl_", 31))
    gross = GrossTrade(
        trade_id=captured.trade_id,
        trade_digest=digest(venue.event.value),
        obligation_id=obligation_id,
        quantity=captured.quantity,
        cash=Decimal("9950"),
    )
    transformation, clearing = clear_gross(lifecycle_id=scenario.lifecycle_id, trades=(gross,))
    source_digests = {
        SourceKind.APPROVED_INTENT: digest(intent),
        SourceKind.EXECUTION: digest(venue.event.value),
        SourceKind.TRADE_CAPTURE: digest(captured),
        SourceKind.ALLOCATION: digest(allocation),
        SourceKind.MATCH_AFFIRMATION: digest(fact),
        SourceKind.CLEARING_TRANSFORMATION: digest(transformation),
    }
    manifest = SourceManifest(
        references=tuple(
            SourceReference(kind=kind, identifier=f"source-{kind.value.lower()}", digest=value)
            for kind, value in source_digests.items()
        )
    )
    participants = (
        ParticipantAccount(
            participant_id="seller", account_id="SEC-DELIVER", role=AccountRole.DELIVERER
        ),
        ParticipantAccount(
            participant_id="buyer", account_id="SEC-RECEIVE", role=AccountRole.RECEIVER
        ),
        ParticipantAccount(
            participant_id="buyer", account_id="CASH-PAY", role=AccountRole.DELIVERER
        ),
        ParticipantAccount(
            participant_id="seller", account_id="CASH-RECEIVE", role=AccountRole.RECEIVER
        ),
    )
    formed = form_obligation(
        register=affirmed.journal,
        order_id=child_id,
        transformation=transformation,
        clearing=clearing,
        obligation_id=obligation_id,
        session=SESSION,
        source_manifest=manifest,
        securities_leg=SecuritiesLeg(
            instrument_id=internal.instrument_id,
            quantity=Decimal("100"),
            delivering_account_id="SEC-DELIVER",
            receiving_account_id="SEC-RECEIVE",
        ),
        cash_leg=CashLeg(
            principal=Decimal("9950"),
            accrued=Decimal("0"),
            total=Decimal("9950"),
            currency="USD",
            value_date=SESSION.business_date.value,
            paying_account_id="CASH-PAY",
            receiving_account_id="CASH-RECEIVE",
        ),
        participants=participants,
        delivery_pattern=DeliveryPattern.DVP,
        candidate_paths=(
            CandidatePathDescriptor(
                path_id="fedwire-dvp",
                rail="Fedwire",
                securities_route="bilateral securities",
                cash_route="Fedwire Funds",
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
        actor=ACTOR,
        clearing_event=_event(26),
        obligation_event=_event(27),
        candidate_event=_event(28),
    )
    gate = evaluate(
        operation=OperationContext(
            notional=Decimal("9950"), currency="USD", is_material=False, is_lvps_material=False
        ),
        funding=FundingState(
            Decimal("10000") if funded else Decimal(0), Decimal("9950"), Decimal("20000"), True
        ),
        rails={CashRail.FEDWIRE: RailState(CashRail.FEDWIRE, RailStatus.AVAILABLE, 7200)},
        ofr_stlfsi4=0.0,
        obligation_id=formed.envelope.obligation_id,
        obligation_digest=digest(formed.envelope),
    )
    acceptance = evaluate_candidate(
        formed.envelope,
        formed.payload.canonical_bytes(),
        acceptance_id=uuid.UUID("00000000-0000-4000-8000-000000000006"),
        evaluated_at=AT,
        decided_by=ACTOR,
        gate_decision=gate,
        halt=None,
    )
    transcript = CrossingTranscript()
    transcript = _crossing(
        transcript,
        scenario,
        producer="aureon",
        consumer="lc",
        envelope=intent,
        payload_bytes=intent_bytes,
        disposition=Disposition.PASS,
        reason="approved intent accepted",
        n=30,
    )
    transcript = _crossing(
        transcript,
        scenario,
        producer="emulator",
        consumer="lc",
        envelope=venue.event.value,
        payload_bytes=canonical_bytes_of(report),
        disposition=Disposition.PASS,
        reason="execution recorded",
        n=31,
    )
    transcript = _crossing(
        transcript,
        scenario,
        producer="lc",
        consumer="lc",
        envelope=transformation,
        payload_bytes=canonical_bytes_of(clearing),
        disposition=Disposition.PASS,
        reason="gross clearing conserved",
        n=32,
    )
    transcript = _crossing(
        transcript,
        scenario,
        producer="lc",
        consumer="atreides",
        envelope=formed.envelope,
        payload_bytes=formed.payload.canonical_bytes(),
        disposition=acceptance.disposition,
        reason="obligation evaluated",
        n=33,
    )
    transcript = _crossing(
        transcript,
        scenario,
        producer="atreides",
        consumer="harness_c2",
        envelope=acceptance,
        payload_bytes=canonical_bytes_of(acceptance),
        disposition=acceptance.disposition,
        reason="Atreides boundary result",
        n=34,
    )
    if funded:
        debtor = FinancialInstitution(bicfi="AAAAUS33XXX")
        creditor = FinancialInstitution(bicfi="BBBBUS33XXX")
        cash_instruction = CashLegInstruction(
            message_id="W5-MSG-001",
            end_to_end_id="W5-E2E-001",
            created_at=AT,
            amount=Decimal("9950"),
            currency="USD",
            debtor=debtor,
            creditor=creditor,
            settlement_method=SettlementMethod.CLEARING_SYSTEM,
            sender=debtor,
            receiver=creditor,
            dsor_lineage_uri=f"urn:cannae:lifecycle:{scenario.lifecycle_id}",
        )
        prepared = prepare_instruction(
            formed.envelope,
            formed.payload.canonical_bytes(),
            acceptance,
            cash_instruction,
        )
        prepared_bytes = prepared.header_xml + b"\n" + prepared.document_xml
        prepared_envelope = _artifact_envelope(
            scenario,
            artifact_id=cash_instruction.message_id,
            artifact_kind="prepared_instruction",
            payload_bytes=prepared_bytes,
        )
        transcript = _crossing(
            transcript,
            scenario,
            producer="atreides",
            consumer="synthetic_member",
            envelope=prepared_envelope,
            payload_bytes=prepared_bytes,
            disposition=Disposition.PASS,
            reason="Atreides prepared; synthetic member may submit",
            n=35,
        )
        submission = SyntheticEntitledMember(seed=29).submit(
            lifecycle_id=scenario.lifecycle_id, instruction=prepared_bytes, times=_times(36)
        )
        submission_bytes = submission.model_dump_json().encode("utf-8")
        transcript = _crossing(
            transcript,
            scenario,
            producer="synthetic_member",
            consumer="synthetic_rail",
            envelope=_artifact_envelope(
                scenario,
                artifact_id=submission.submission_id,
                artifact_kind="member_submission",
                payload_bytes=submission_bytes,
            ),
            payload_bytes=submission_bytes,
            disposition=Disposition.PASS,
            reason="synthetic entitled member submitted exact prepared bytes",
            n=36,
        )
        rail = SyntheticRail(seed=2).respond(submission, times=_times(37))
        assert rail.outcome is RailOutcome.SETTLED
        rail_bytes = rail.model_dump_json().encode("utf-8")
        transcript = _crossing(
            transcript,
            scenario,
            producer="synthetic_rail",
            consumer="atreides",
            envelope=_artifact_envelope(
                scenario,
                artifact_id=rail.response_id,
                artifact_kind="rail_response",
                payload_bytes=rail_bytes,
            ),
            payload_bytes=rail_bytes,
            disposition=rail.disposition,
            reason=rail.reason,
            n=37,
        )
        status = StatusReport(
            message_id=rail.response_id,
            created_at=rail.times.event_time.isoformat(),
            namespace="synthetic://wave5/rail-response",
            original_message_id=cash_instruction.message_id,
            original_message_name_id=prepared.message_definition,
            group_status_code=None,
            entries=(
                StatusEntry(
                    status_code="ACSC",
                    status=SettlementStatus.SETTLED,
                    end_to_end_id=cash_instruction.end_to_end_id,
                    echoed_amount=cash_instruction.amount,
                    echoed_currency=cash_instruction.currency,
                ),
            ),
        )
        reconciliation = reconcile(status, (cash_instruction,))
        assert reconciliation.clean
        assert reconciliation.settled_ids == (cash_instruction.end_to_end_id,)
        reconciliation_bytes = canonical_bytes_of(
            {
                "report_message_id": reconciliation.report.message_id,
                "original_message_id": reconciliation.report.original_message_id,
                "matched": {
                    key: value.value for key, value in sorted(reconciliation.matched.items())
                },
                "settled_ids": reconciliation.settled_ids,
                "breaks": tuple(
                    {
                        "code": item.code.value,
                        "detail": item.detail,
                        "end_to_end_id": item.end_to_end_id,
                    }
                    for item in reconciliation.breaks
                ),
                "is_absent": reconciliation.is_absent,
            }
        )
        transcript = _crossing(
            transcript,
            scenario,
            producer="atreides",
            consumer="harness_c2",
            envelope=_artifact_envelope(
                scenario,
                artifact_id=f"reconciliation-{rail.response_id}",
                artifact_kind="reconciliation",
                payload_bytes=reconciliation_bytes,
            ),
            payload_bytes=reconciliation_bytes,
            disposition=Disposition.PASS,
            reason="Atreides reconciled prepared instruction to settled rail response",
            n=38,
        )
    return scenario, transcript


def _lineage(scenario: ScenarioRecord, transcript: CrossingTranscript) -> LineageRecord:
    envelopes = [crossing.envelope for crossing in transcript.crossings]
    return assemble_lineage(
        scenario.lifecycle_id,
        intents=[item for item in envelopes if isinstance(item, ApprovedIntentEnvelope)],
        executions=[item for item in envelopes if isinstance(item, ExecutionEvent)],
        clearings=[item for item in envelopes if isinstance(item, ClearingTransformation)],
        obligations=[item for item in envelopes if isinstance(item, SettlementObligationEnvelope)],
        acceptances=[item for item in envelopes if isinstance(item, ObligationAcceptanceRecord)],
    )


def test_funded_lifecycle_passes_and_clean_lineage_refuses_escalation() -> None:
    scenario, transcript = _run(funded=True)
    record = _lineage(scenario, transcript)
    assert len(transcript.crossings) == 9
    assert record.disposition is Disposition.PASS
    with pytest.raises(EscalationRefusedError, match="nothing to escalate"):
        package_escalation(
            record,
            EscalationRequest(
                packet_id="esc-clean",
                trigger=EscalationTrigger.LINEAGE_HOLE,
                summary="clean",
                raised_at=AT,
            ),
        )


def test_unfunded_lifecycle_holds_at_atreides_and_does_not_submit() -> None:
    scenario, transcript = _run(funded=False)
    assert len(transcript.crossings) == 5
    assert transcript.crossings[-1].disposition is Disposition.HOLD
    assert _lineage(scenario, transcript).complete


def test_lineage_escalation_four_cases_from_transcript() -> None:
    scenario, transcript = _run(funded=True)
    envelopes = [crossing.envelope for crossing in transcript.crossings]
    intent = next(item for item in envelopes if isinstance(item, ApprovedIntentEnvelope))
    execution = next(item for item in envelopes if isinstance(item, ExecutionEvent))
    obligation = next(item for item in envelopes if isinstance(item, SettlementObligationEnvelope))
    acceptance = next(item for item in envelopes if isinstance(item, ObligationAcceptanceRecord))
    in_flight = assemble_lineage(scenario.lifecycle_id, intents=(intent,), executions=(execution,))
    assert in_flight.disposition is Disposition.INDETERMINATE
    packet = package_escalation(
        in_flight,
        EscalationRequest(
            packet_id="esc-flight",
            trigger=EscalationTrigger.LINEAGE_HOLE,
            summary="in flight",
            raised_at=AT,
        ),
    )
    assert packet.findings == () and packet.unknowns
    hole = assemble_lineage(
        scenario.lifecycle_id,
        intents=(intent,),
        executions=(execution,),
        obligations=(obligation,),
        acceptances=(acceptance,),
    )
    assert any(gap.code is GapCode.MISSING for gap in hole.gaps)
    hole_packet = package_escalation(
        hole,
        EscalationRequest(
            packet_id="esc-hole",
            trigger=EscalationTrigger.LINEAGE_HOLE,
            summary="missing clearing",
            raised_at=AT,
        ),
    )
    assert hole_packet.findings and hole_packet.requires_human_authority
    foreign = intent.model_copy(update={"lifecycle_id": LifecycleId(_id("lif_", 99))})
    blocked = assemble_lineage(scenario.lifecycle_id, intents=(foreign,), executions=(execution,))
    with pytest.raises(EscalationRefusedError) as refused:
        package_escalation(
            blocked,
            EscalationRequest(
                packet_id="esc-blocked",
                trigger=EscalationTrigger.LINEAGE_HOLE,
                summary="not one lifecycle",
                raised_at=AT,
            ),
        )
    assert refused.value.gaps and "LIFECYCLE_MISMATCH" in refused.value.gaps[0]
