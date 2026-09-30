"""BB2 acceptance: deterministic four-clock chaos without future information."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from cannae_kernel.clocks import EventTimes
from cannae_kernel.disposition import Disposition
from cannae_kernel.ids import ScenarioId
from cannae_kernel.provenance import Provenance
from cannae_kernel.session import BusinessDate

from harness_c2.chaos import (
    ChaosEvent,
    FutureObservationError,
    apply_injections,
    authority_reading,
    generate_plan,
    observe,
    record_injection_conditions,
)
from harness_c2.scenario import start_scenario
from harness_c2.transcript import CrossingArtifactEnvelope, CrossingTranscript
from lc.policy import ExecutionPolicy, MarketEvidenceSnapshot, evaluate_policy

NOW = datetime(2026, 9, 30, 15, 0, tzinfo=UTC)


def _times(seconds: int = 0) -> EventTimes:
    at = NOW + timedelta(seconds=seconds)
    return EventTimes(
        event_time=at,
        observation_time=at,
        processing_time=at,
        decision_time=at,
    )


def _events() -> tuple[ChaosEvent, ...]:
    return (
        ChaosEvent(
            event_id="market-1",
            event_class="market_evidence",
            source="market",
            payload=b"market",
            times=_times(),
        ),
        ChaosEvent(
            event_id="authority-1",
            event_class="authority",
            source="authority",
            payload=b"authority",
            times=_times(1),
        ),
        ChaosEvent(
            event_id="execution-1",
            event_class="execution",
            source="venue",
            payload=b"execution-1",
            times=_times(2),
        ),
        ChaosEvent(
            event_id="execution-2",
            event_class="execution",
            source="venue",
            payload=b"execution-2",
            times=_times(3),
        ),
        ChaosEvent(
            event_id="reference-1",
            event_class="reference_data",
            source="reference-data",
            payload=b"reference",
            times=_times(4),
        ),
    )


def test_same_seed_reproduces_the_same_injected_event_stream_byte_for_byte() -> None:
    first = apply_injections(_events(), generate_plan(29))
    second = apply_injections(_events(), generate_plan(29))
    assert first.canonical_bytes() == second.canonical_bytes()
    assert [event.event_id for event in first.events if event.event_class == "execution"] == [
        "execution-2",
        "execution-1",
    ]
    assert first.absent_sources == ("reference-data",)


def test_delayed_market_evidence_holds_the_policy_gate_and_names_staleness() -> None:
    result = apply_injections(_events(), generate_plan(29))
    market = next(event for event in result.events if event.event_class == "market_evidence")
    decision = evaluate_policy(
        ExecutionPolicy(
            policy_id="bb2-stale-gate",
            version="1",
            allowed_venues=("VENUE-SYNTHETIC",),
            max_child_quantity=Decimal("100"),
            minimum_price=Decimal("90"),
            maximum_price=Decimal("110"),
            expires_at=NOW + timedelta(hours=1),
            max_evidence_age=timedelta(minutes=5),
        ),
        child_quantity=Decimal("100"),
        evidence=MarketEvidenceSnapshot(
            venue="VENUE-SYNTHETIC",
            price=Decimal("100"),
            observed_at=market.times.event_time,
            provenance=Provenance.FACT_SYNTHETIC,
            eligible=True,
        ),
        processing_time=market.times.processing_time,
    )
    assert decision.disposition is Disposition.HOLD
    assert "stale" in decision.reason


def test_partitioned_authority_is_indeterminate_and_never_defaults() -> None:
    result = apply_injections(_events(), generate_plan(29))
    disposition, reason = authority_reading(result.events, at=NOW + timedelta(seconds=30))
    assert disposition is Disposition.INDETERMINATE
    assert reason == "authority evidence is not observable yet"


def test_future_observation_is_refused_and_the_leak_probe_fails() -> None:
    result = apply_injections(_events(), generate_plan(29))
    market = next(event for event in result.events if event.event_class == "market_evidence")
    with pytest.raises(FutureObservationError, match="FUTURE_OBSERVATION"):
        observe(market, at=market.times.observation_time - timedelta(microseconds=1))
    assert observe(market, at=market.times.observation_time) == b"market"


def test_every_injection_is_recorded_in_the_transcript() -> None:
    scenario = start_scenario(
        scenario_id=ScenarioId("scn_01K6C7FBR0R4JBQMT6HTZB79E1"),
        seed=29,
        pinned_commits={"lc": "a" * 40},
        policy_versions={"chaos": "bb2/1"},
        business_date=BusinessDate(
            value=date(2026, 9, 30),
            calendar="scenario",
            established_by="BB2",
        ),
    )
    plan = generate_plan(29)
    transcript = record_injection_conditions(
        scenario,
        CrossingTranscript(),
        plan,
        recorded_at=_times(),
    )
    assert len(transcript.crossings) == len(plan.conditions)
    assert all(
        isinstance(crossing.envelope, CrossingArtifactEnvelope)
        and crossing.envelope.artifact_kind == "injection_condition"
        for crossing in transcript.crossings
    )
    assert CrossingTranscript.from_bytes(transcript.to_bytes()) == transcript
