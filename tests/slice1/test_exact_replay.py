"""WP-4 exact replay: recorded evidence, never a second emulator run."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from atreides.acceptance.service import evaluate_candidate
from atreides.rails.cato_cash import (
    CashRail,
    FundingState,
    OperationContext,
    RailState,
    RailStatus,
    evaluate,
)
from cannae_kernel.actor import ActorRef
from cannae_kernel.envelopes import ObligationAcceptanceRecord, SettlementObligationEnvelope
from pytest import MonkeyPatch
from test_runner import _run

from emulators.matching import MatchingEmulator
from emulators.settlement import SyntheticEntitledMember, SyntheticRail
from emulators.venue import VenueEmulator
from harness_c2.replay import replay_transcript
from harness_c2.scenario import ScenarioRecord
from harness_c2.transcript import CrossingArtifactEnvelope, CrossingTranscript


def _emulator_called(*args: object, **kwargs: object) -> None:
    raise AssertionError("replay invoked an emulator")


def _replay_atreides_acceptance(transcript: CrossingTranscript) -> None:
    gate_crossing = next(
        crossing
        for crossing in transcript.crossings
        if isinstance(crossing.envelope, CrossingArtifactEnvelope)
        and crossing.envelope.artifact_kind == "cash_gate_input"
    )
    obligation = next(
        crossing.envelope
        for crossing in transcript.crossings
        if isinstance(crossing.envelope, SettlementObligationEnvelope)
    )
    obligation_payload = next(
        crossing.payload_bytes
        for crossing in transcript.crossings
        if isinstance(crossing.envelope, SettlementObligationEnvelope)
    )
    recorded = next(
        crossing.envelope
        for crossing in transcript.crossings
        if isinstance(crossing.envelope, ObligationAcceptanceRecord)
    )
    values = json.loads(gate_crossing.payload_bytes)
    operation = values["operation"]
    funding = values["funding"]
    rails = values["rails"]
    gate = evaluate(
        operation=OperationContext(
            notional=Decimal(operation["notional"]),
            currency=operation["currency"],
            is_material=operation["is_material"],
            is_lvps_material=operation["is_lvps_material"],
        ),
        funding=FundingState(
            Decimal(funding["projected_funded_position"]),
            Decimal(funding["net_obligation"]),
            Decimal(funding["net_debit_cap_headroom"]),
            funding["clearing_fund_sufficient"],
            funding["position_is_assertable"],
        ),
        rails={
            CashRail(name): RailState(
                CashRail(name), RailStatus(state["status"]), state["seconds_to_cutoff"]
            )
            for name, state in rails.items()
        },
        ofr_stlfsi4=float(values["ofr_stlfsi4"]),
        obligation_id=obligation.obligation_id,
        obligation_digest=values["obligation_digest"],
    )
    replayed = evaluate_candidate(
        obligation,
        obligation_payload,
        acceptance_id=UUID(values["acceptance_id"]),
        evaluated_at=datetime.fromisoformat(values["evaluated_at"]),
        decided_by=ActorRef.model_validate(values["decided_by"], strict=False),
        gate_decision=gate,
        halt=None,
    )
    assert replayed == recorded


def test_same_scenario_record_produces_byte_identical_transcripts() -> None:
    first_scenario, first = _run(funded=True)
    second_scenario, second = _run(funded=True)
    assert first_scenario.to_canonical_bytes() == second_scenario.to_canonical_bytes()
    assert first.to_bytes() == second.to_bytes()


def test_replay_reproduces_every_disposition_and_reason_without_emulators(
    monkeypatch: MonkeyPatch,
) -> None:
    scenario, transcript = _run(funded=True)
    monkeypatch.setattr(VenueEmulator, "execute", _emulator_called)
    monkeypatch.setattr(MatchingEmulator, "respond", _emulator_called)
    monkeypatch.setattr(SyntheticEntitledMember, "submit", _emulator_called)
    monkeypatch.setattr(SyntheticRail, "respond", _emulator_called)

    first = replay_transcript(scenario, transcript)
    second = replay_transcript(scenario, transcript)
    _replay_atreides_acceptance(transcript)

    assert first.to_bytes() == second.to_bytes()
    assert first.first_changed_gate is None
    assert tuple(gate.disposition for gate in first.gates) == tuple(
        crossing.disposition for crossing in transcript.crossings
    )
    assert tuple(gate.reason for gate in first.gates) == tuple(
        crossing.reason for crossing in transcript.crossings
    )


def test_one_changed_scenario_byte_names_the_first_gate_that_moves() -> None:
    scenario, transcript = _run(funded=True)
    original = scenario.to_canonical_bytes()
    changed = original.replace(b'"seed":29', b'"seed":28', 1)
    assert changed != original and len(changed) == len(original)
    altered = ScenarioRecord.model_validate_json(changed, strict=True)

    result = replay_transcript(altered, transcript)

    assert result.first_changed_gate == "crossing[0].scenario_record"
    assert result.gates[0].reason == "SCENARIO_RECORD_DIGEST_MISMATCH"
