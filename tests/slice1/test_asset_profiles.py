"""Wave 6 WP-0: lifecycle profiles vary paths, never economics."""

from __future__ import annotations

from cannae_kernel.delivery import DeliveryPattern
from cannae_kernel.envelopes import SettlementObligationEnvelope
from test_runner import _run

from lc.asset_profile import (
    BILATERAL_TREASURY,
    DEFAULT_ASSET_PROFILES,
    TOKENIZED_TREASURY_SINGLE_PLATFORM,
    AssetProfile,
)
from lc.obligation import ObligationPayload


def _obligation(profile: AssetProfile) -> ObligationPayload:
    _scenario, transcript = _run(funded=True, asset_profile=profile)
    assert len(transcript.crossings) == 10
    crossing = next(
        item
        for item in transcript.crossings
        if isinstance(item.envelope, SettlementObligationEnvelope)
    )
    return ObligationPayload.model_validate_json(crossing.payload_bytes, strict=True)


def test_third_profile_is_registry_data_and_runs_the_same_lifecycle() -> None:
    third = AssetProfile(
        profile_id="treasury.test-only",
        settlement_pattern=DeliveryPattern.DVP,
        cash_representation="test cash",
        custody_path="test custody",
        conditional_execution="test conditional delivery",
        securities_finality_evidence="test asset finality",
        cash_finality_evidence="test cash finality",
    )
    extended = DEFAULT_ASSET_PROFILES.register(third)
    assert extended.resolve(third.profile_id) is third
    payload = _obligation(third)
    assert payload.candidate_paths[0].path_id == third.profile_id


def test_registered_profiles_run_end_to_end_with_identical_economics() -> None:
    conventional = _obligation(BILATERAL_TREASURY)
    tokenized = _obligation(TOKENIZED_TREASURY_SINGLE_PLATFORM)

    assert conventional.securities_leg == tokenized.securities_leg
    assert conventional.cash_leg == tokenized.cash_leg
    assert conventional.participants == tokenized.participants
    assert conventional.corrections == tokenized.corrections

    assert BILATERAL_TREASURY.custody_path != TOKENIZED_TREASURY_SINGLE_PLATFORM.custody_path
    assert (
        BILATERAL_TREASURY.cash_representation
        != TOKENIZED_TREASURY_SINGLE_PLATFORM.cash_representation
    )
    assert (
        BILATERAL_TREASURY.securities_finality_evidence
        != TOKENIZED_TREASURY_SINGLE_PLATFORM.securities_finality_evidence
    )
    assert conventional.candidate_paths != tokenized.candidate_paths
    assert conventional.expected_finality != tokenized.expected_finality
    assert (
        BILATERAL_TREASURY.conditional_execution
        != TOKENIZED_TREASURY_SINGLE_PLATFORM.conditional_execution
    )


def test_profile_type_carries_no_economics() -> None:
    economic_fields = {
        "instrument_id",
        "quantity",
        "price",
        "principal",
        "accrued",
        "total",
        "currency",
        "value_date",
    }
    assert economic_fields.isdisjoint(AssetProfile.model_fields)
