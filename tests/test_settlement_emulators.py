from datetime import UTC, datetime

from cannae_kernel.clocks import EventTimes
from cannae_kernel.disposition import Disposition
from cannae_kernel.ids import LifecycleId

from emulators.settlement import (
    RailOutcome,
    SettlementSubmission,
    SyntheticEntitledMember,
    SyntheticRail,
)

TIMES = EventTimes(
    event_time=datetime(2026, 9, 29, 12, tzinfo=UTC),
    observation_time=datetime(2026, 9, 29, 12, tzinfo=UTC),
    processing_time=datetime(2026, 9, 29, 12, tzinfo=UTC),
    decision_time=None,
)
LIFECYCLE_ID = LifecycleId("lif_01K6C7FBR0R4JBQMT6HTZB79E1")


def _submission() -> SettlementSubmission:
    return SyntheticEntitledMember(seed=29).submit(
        lifecycle_id=LIFECYCLE_ID,
        instruction=b'{"quantity":"1000000","security":"US91282CJL63"}',
        times=TIMES,
    )


def test_member_submission_is_deterministic_and_preserves_bytes() -> None:
    first = _submission()
    second = _submission()
    assert first == second
    assert first.instruction == b'{"quantity":"1000000","security":"US91282CJL63"}'
    assert first.instruction_digest.startswith("sha256:")


def test_chosen_seeds_cover_every_rail_outcome() -> None:
    results = {
        SyntheticRail(seed=seed).respond(_submission(), times=TIMES).outcome for seed in range(20)
    }
    assert results == set(RailOutcome)


def test_indeterminate_is_never_defaulted_to_settlement() -> None:
    responses = [SyntheticRail(seed=seed).respond(_submission(), times=TIMES) for seed in range(20)]
    indeterminate = next(r for r in responses if r.outcome is RailOutcome.INDETERMINATE)
    assert indeterminate.disposition is Disposition.INDETERMINATE
    assert indeterminate.outcome is not RailOutcome.SETTLED


def test_emulators_are_not_submission_authority_for_atreides() -> None:
    assert "synthetic" in (SyntheticEntitledMember.__doc__ or "").lower()
    assert "credential" in (SyntheticEntitledMember.__doc__ or "").lower()
