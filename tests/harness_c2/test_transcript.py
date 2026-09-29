from datetime import UTC, date, datetime

from cannae_kernel.actor import ActorKind, ActorRef
from cannae_kernel.canonical import digest_bytes
from cannae_kernel.clocks import EventTimes
from cannae_kernel.disposition import Disposition
from cannae_kernel.effects import OperationEffects
from cannae_kernel.envelopes import ApprovedIntentEnvelope
from cannae_kernel.ids import ActorId, IntentId, ScenarioId
from cannae_kernel.provenance import Provenance
from cannae_kernel.session import BusinessDate, MarketSession, SessionContext

from harness_c2.scenario import start_scenario
from harness_c2.transcript import Crossing, CrossingTranscript, record_crossing

NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)
TIMES = EventTimes(
    event_time=NOW,
    observation_time=NOW,
    processing_time=NOW,
    decision_time=NOW,
)
SCENARIO = start_scenario(
    scenario_id=ScenarioId("scn_01K6C7FBR0R4JBQMT6HTZB79E1"),
    seed=29,
    pinned_commits={"aureon": "a" * 40, "lc": "b" * 40, "atreides": "c" * 40},
    policy_versions={"execution": "1.0"},
    business_date=BusinessDate(
        value=date(2026, 9, 29),
        calendar="Fedwire Funds Service",
        established_by="rail published calendar",
    ),
)
PAYLOAD = b'{"instrument":"US91282CJL63","quantity":"1000000"}'
ENVELOPE = ApprovedIntentEnvelope(
    envelope_id=IntentId("int_01K6C7FBR0R4JBQMT6HTZB79E2"),
    lifecycle_id=SCENARIO.lifecycle_id,
    revision=1,
    prior_digest=None,
    session=SessionContext(
        session=MarketSession.REGULAR,
        business_date=SCENARIO.business_date,
    ),
    approved_by=ActorRef(
        actor_id=ActorId("act_01K6C7FBR0R4JBQMT6HTZB79E3"),
        actor_kind=ActorKind.HUMAN,
        role="Synthetic operator",
        entitlement_refs=("synthetic-approval",),
        authenticated=True,
    ),
    provenance=Provenance.HUMAN_JUDGMENT,
    effects=OperationEffects(
        operation="approve synthetic intent",
        effects=(),
        note="contained test fixture; submission belongs to the synthetic member",
    ),
    payload_digest=digest_bytes(PAYLOAD),
)


def _crossing(payload: bytes = PAYLOAD) -> Crossing:
    return record_crossing(
        scenario=SCENARIO,
        producer="aureon",
        consumer="lc",
        lifecycle_id=SCENARIO.lifecycle_id,
        envelope=ENVELOPE,
        payload_bytes=payload,
        producer_asserted_digest=str(ENVELOPE.payload_digest),
        times=TIMES,
        accepted_disposition=Disposition.PASS,
        accepted_reason="payload digest verified",
    )


def test_transcript_replays_to_a_byte_identical_copy() -> None:
    transcript = CrossingTranscript().append(_crossing())
    encoded = transcript.to_bytes()
    assert CrossingTranscript.from_bytes(encoded).to_bytes() == encoded


def test_perturbed_payload_records_refusal_and_keeps_both_digests() -> None:
    crossing = _crossing(PAYLOAD[:-1] + b"0")
    assert crossing.disposition is Disposition.BLOCK
    assert crossing.reason.startswith("DIGEST_MISMATCH")
    assert crossing.producer_asserted_digest == ENVELOPE.payload_digest
    assert crossing.consumer_computed_digest == digest_bytes(PAYLOAD[:-1] + b"0")
    assert crossing.consumer_computed_digest != crossing.producer_asserted_digest


def test_transcript_contains_bytes_and_a_frozen_envelope_not_domain_objects() -> None:
    crossing = _crossing()
    assert isinstance(crossing.payload_bytes, bytes)
    assert isinstance(crossing.envelope, ApprovedIntentEnvelope)
    assert not hasattr(crossing, "payload")
