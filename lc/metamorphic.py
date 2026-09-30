"""Seeded metamorphic variants and the four BB3 invariants."""

from __future__ import annotations

import random
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum

from cannae_kernel.canonical import canonical_bytes_of
from cannae_kernel.clocks import EventTimes
from cannae_kernel.ids import ObligationId
from pydantic import BaseModel, ConfigDict, Field

from lc.asset_profile import AssetProfile
from lc.clearing import GrossClearingResult, GrossTrade

__all__ = [
    "InvariantObservation",
    "MetamorphicInvariantError",
    "MetamorphicVariant",
    "Transformation",
    "assert_invariants",
    "generate_variants",
]


class Transformation(StrEnum):
    ORDER_PERMUTATION = "order_permutation"
    NETTING_SET_EQUIVALENCE = "netting_set_equivalence"
    TIME_SHIFT = "time_shift"
    CURRENCY_SCALING = "currency_scaling"
    ASSET_PROFILE_SUBSTITUTION = "asset_profile_substitution"


class MetamorphicInvariantError(AssertionError):
    """Names the transformation and invariant that exposed a break."""


class MetamorphicVariant(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    transformation: Transformation
    trades: tuple[GrossTrade, ...]
    netting_sets: tuple[tuple[str, ...], ...]
    times: EventTimes
    profile: AssetProfile
    currency_scale: Decimal = Field(gt=0)


class InvariantObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    transformation: Transformation
    clearing: GrossClearingResult
    lineage_complete: bool
    first_replay: bytes
    second_replay: bytes
    settlement_instruction_exists: bool
    external_finality_evidence_exists: bool
    settled: bool


def _trade(index: int, quantity: Decimal, cash: Decimal) -> GrossTrade:
    return GrossTrade(
        trade_id=f"generated-trade-{index}",
        trade_digest="sha256:" + f"{index:064x}",
        obligation_id=ObligationId("obl_" + f"{index:026d}"),
        quantity=quantity,
        cash=cash,
    )


def generate_variants(
    *, seed: int, times: EventTimes, profiles: tuple[AssetProfile, AssetProfile]
) -> tuple[MetamorphicVariant, ...]:
    """Generate all named transformations from values not hand-written into a test."""
    rng = random.Random(seed)
    base = tuple(
        _trade(index, Decimal(rng.randint(1, 100)), Decimal(rng.randint(100, 10_000)))
        for index in range(1, 4)
    )
    permuted = tuple(rng.sample(base, len(base)))
    scale = Decimal(rng.randint(2, 9))
    shift = timedelta(seconds=rng.randint(60, 3_600))
    shifted = times.model_copy(
        update={
            field: value + shift if value else None
            for field, value in times.model_dump(mode="python").items()
        }
    )
    scaled = tuple(trade.model_copy(update={"cash": trade.cash * scale}) for trade in base)
    base_set = (tuple(trade.trade_id for trade in base),)
    return (
        MetamorphicVariant(
            transformation=Transformation.ORDER_PERMUTATION,
            trades=permuted,
            netting_sets=base_set,
            times=times,
            profile=profiles[0],
            currency_scale=Decimal(1),
        ),
        MetamorphicVariant(
            transformation=Transformation.NETTING_SET_EQUIVALENCE,
            trades=base,
            netting_sets=((base[0].trade_id,), tuple(item.trade_id for item in base[1:])),
            times=times,
            profile=profiles[0],
            currency_scale=Decimal(1),
        ),
        MetamorphicVariant(
            transformation=Transformation.TIME_SHIFT,
            trades=base,
            netting_sets=base_set,
            times=shifted,
            profile=profiles[0],
            currency_scale=Decimal(1),
        ),
        MetamorphicVariant(
            transformation=Transformation.CURRENCY_SCALING,
            trades=scaled,
            netting_sets=base_set,
            times=times,
            profile=profiles[0],
            currency_scale=scale,
        ),
        MetamorphicVariant(
            transformation=Transformation.ASSET_PROFILE_SUBSTITUTION,
            trades=base,
            netting_sets=base_set,
            times=times,
            profile=profiles[1],
            currency_scale=Decimal(1),
        ),
    )


def assert_invariants(observation: InvariantObservation) -> None:
    """Assert conservation, lineage, idempotency and finality separation once."""
    name = observation.transformation.value
    conservation = observation.clearing.conservation
    if not (
        conservation.conserved
        and conservation.input_quantity == conservation.output_quantity
        and conservation.input_cash == conservation.output_cash
    ):
        raise MetamorphicInvariantError(f"{name}: conservation invariant failed")
    if not observation.lineage_complete:
        raise MetamorphicInvariantError(f"{name}: lineage invariant failed")
    if observation.first_replay != observation.second_replay:
        raise MetamorphicInvariantError(f"{name}: idempotency invariant failed")
    if observation.settled and not observation.external_finality_evidence_exists:
        instruction = " after an instruction" if observation.settlement_instruction_exists else ""
        raise MetamorphicInvariantError(
            f"{name}: finality separation invariant failed{instruction}"
        )


def canonical_variant_bytes(variant: MetamorphicVariant) -> bytes:
    return canonical_bytes_of(variant)
