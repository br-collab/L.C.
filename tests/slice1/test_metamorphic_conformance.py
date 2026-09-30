"""BB3 generated variants assert one shared set of lifecycle invariants."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from cannae_kernel.canonical import canonical_bytes_of
from cannae_kernel.clocks import EventTimes
from cannae_kernel.ids import LifecycleId
from test_runner import _lineage, _run

from harness_c2.transcript import CrossingArtifactEnvelope
from lc.asset_profile import BILATERAL_TREASURY, TOKENIZED_TREASURY_SINGLE_PLATFORM
from lc.clearing import ConservationResult, clear_gross
from lc.metamorphic import (
    InvariantObservation,
    MetamorphicInvariantError,
    MetamorphicVariant,
    Transformation,
    assert_invariants,
    generate_variants,
)

BASE_TIMES = EventTimes(
    event_time=datetime(2026, 9, 29, 15, 0, tzinfo=UTC),
    observation_time=datetime(2026, 9, 29, 15, 0, tzinfo=UTC),
    processing_time=datetime(2026, 9, 29, 15, 0, tzinfo=UTC),
    decision_time=datetime(2026, 9, 29, 15, 0, tzinfo=UTC),
)
PROFILES = (BILATERAL_TREASURY, TOKENIZED_TREASURY_SINGLE_PLATFORM)


def _variants() -> tuple[MetamorphicVariant, ...]:
    return generate_variants(seed=20260930, times=BASE_TIMES, profiles=PROFILES)


@pytest.mark.parametrize("variant", _variants(), ids=lambda item: item.transformation.value)
def test_generated_variants_preserve_the_four_shared_invariants(
    variant: MetamorphicVariant,
) -> None:
    lifecycle_id = LifecycleId("lif_" + "7" * 26)
    _first_envelope, first = clear_gross(lifecycle_id=lifecycle_id, trades=variant.trades)
    _second_envelope, second = clear_gross(lifecycle_id=lifecycle_id, trades=variant.trades)
    scenario, transcript = _run(funded=True, asset_profile=variant.profile)
    artifact_kinds = {
        crossing.envelope.artifact_kind
        for crossing in transcript.crossings
        if isinstance(crossing.envelope, CrossingArtifactEnvelope)
    }
    assert_invariants(
        InvariantObservation(
            transformation=variant.transformation,
            clearing=first,
            lineage_complete=_lineage(scenario, transcript).complete,
            first_replay=canonical_bytes_of(first),
            second_replay=canonical_bytes_of(second),
            settlement_instruction_exists="prepared_instruction" in artifact_kinds,
            external_finality_evidence_exists="rail_response" in artifact_kinds,
            settled="reconciliation" in artifact_kinds,
        )
    )


def test_each_named_transformation_is_genuinely_exercised() -> None:
    variants = {item.transformation: item for item in _variants()}
    assert set(variants) == set(Transformation)

    order = variants[Transformation.ORDER_PERMUTATION]
    baseline = variants[Transformation.TIME_SHIFT]
    assert tuple(item.trade_id for item in order.trades) != tuple(
        item.trade_id for item in baseline.trades
    )

    netting = variants[Transformation.NETTING_SET_EQUIVALENCE]
    assert len(netting.netting_sets) == 2
    assert {item for group in netting.netting_sets for item in group} == {
        trade.trade_id for trade in netting.trades
    }

    shifted = variants[Transformation.TIME_SHIFT]
    assert shifted.times.event_time > BASE_TIMES.event_time
    assert shifted.times.observation_time > BASE_TIMES.observation_time

    scaled = variants[Transformation.CURRENCY_SCALING]
    unscaled_cash = sum((trade.cash for trade in baseline.trades), Decimal(0))
    scaled_cash = sum((trade.cash for trade in scaled.trades), Decimal(0))
    assert scaled_cash / scaled.currency_scale == unscaled_cash

    profile = variants[Transformation.ASSET_PROFILE_SUBSTITUTION]
    assert profile.profile == TOKENIZED_TREASURY_SINGLE_PLATFORM


def test_broken_conservation_names_the_exposing_transformation() -> None:
    variant = _variants()[0]
    _envelope, clearing = clear_gross(
        lifecycle_id=LifecycleId("lif_" + "6" * 26), trades=variant.trades
    )
    broken = ConservationResult.model_construct(
        **{
            **clearing.conservation.model_dump(mode="python"),
            "output_cash": clearing.conservation.output_cash + Decimal(1),
        }
    )
    observation = InvariantObservation(
        transformation=variant.transformation,
        clearing=clearing.model_copy(update={"conservation": broken}),
        lineage_complete=True,
        first_replay=b"same",
        second_replay=b"same",
        settlement_instruction_exists=False,
        external_finality_evidence_exists=False,
        settled=False,
    )
    with pytest.raises(
        MetamorphicInvariantError,
        match="order_permutation: conservation invariant failed",
    ):
        assert_invariants(observation)


def test_instruction_existence_cannot_substitute_for_finality_evidence() -> None:
    variant = _variants()[0]
    _envelope, clearing = clear_gross(
        lifecycle_id=LifecycleId("lif_" + "5" * 26), trades=variant.trades
    )
    observation = InvariantObservation(
        transformation=Transformation.ASSET_PROFILE_SUBSTITUTION,
        clearing=clearing,
        lineage_complete=True,
        first_replay=b"same",
        second_replay=b"same",
        settlement_instruction_exists=True,
        external_finality_evidence_exists=False,
        settled=True,
    )
    with pytest.raises(MetamorphicInvariantError, match="finality separation"):
        assert_invariants(observation)
