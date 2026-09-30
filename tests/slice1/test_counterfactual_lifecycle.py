"""BB1: one scenario, two policies, two whole-lifecycle outcome vectors."""

from __future__ import annotations

import inspect

import pytest
from cannae_kernel.canonical import digest
from cannae_kernel.envelopes import ApprovedIntentEnvelope
from test_runner import _lineage, _run

from emulators.counterfactual import CounterfactualMarketInput, emit_outcomes
from lc.counterfactual import CandidateOutcome, compare_policies, parse_outcomes
from lc.policy import CounterfactualExecutionPolicy


def _market() -> CounterfactualMarketInput:
    scenario, transcript = _run(funded=True)
    lineage = _lineage(scenario, transcript)
    intent = next(
        crossing.envelope
        for crossing in transcript.crossings
        if isinstance(crossing.envelope, ApprovedIntentEnvelope)
    )
    assert lineage.complete
    return CounterfactualMarketInput(
        scenario_id=scenario.scenario_id,
        sealed_intent_digest=digest(intent),
        candidate_ids=("venue-a", "venue-b"),
    )


def _policies() -> tuple[CounterfactualExecutionPolicy, CounterfactualExecutionPolicy]:
    return (
        CounterfactualExecutionPolicy(
            policy_id="price-only",
            version="bb1/1",
            objective="PRICE_ONLY",
        ),
        CounterfactualExecutionPolicy(
            policy_id="settlement-aware",
            version="bb1/1",
            objective="SETTLEMENT_AWARE",
        ),
    )


def test_real_emitter_bytes_feed_the_real_parser_without_translation() -> None:
    raw = emit_outcomes(_market())
    document = parse_outcomes(raw)
    assert document.scenario_digest == digest(_market())
    assert len(document.candidates) == 2


def test_same_scenario_produces_two_complete_attributable_outcome_vectors() -> None:
    market = _market()
    raw = emit_outcomes(market)
    price_only, settlement_aware = _policies()
    result = compare_policies(
        raw,
        expected_scenario_digest=digest(market),
        price_only=price_only,
        settlement_aware=settlement_aware,
    )
    assert result.price_only.selected_candidate_id == "venue-a"
    assert result.settlement_aware.selected_candidate_id == "venue-b"
    assert set(result.differing_variables) == set(CandidateOutcome.model_fields) - {"candidate_id"}
    assert result.price_only.outcome.execution_price < (
        result.settlement_aware.outcome.execution_price
    )
    assert result.price_only.outcome.funding_peak > result.settlement_aware.outcome.funding_peak
    assert result.price_only.outcome.collateral_requirement > (
        result.settlement_aware.outcome.collateral_requirement
    )
    assert result.price_only.outcome.failure_probability > (
        result.settlement_aware.outcome.failure_probability
    )
    assert result.price_only.outcome.finality_delay_seconds > (
        result.settlement_aware.outcome.finality_delay_seconds
    )


def test_emulator_receives_identical_input_and_cannot_observe_the_policy() -> None:
    market = _market()
    first_input = market.canonical_bytes()
    second_input = market.canonical_bytes()
    assert first_input == second_input
    assert emit_outcomes(market) == emit_outcomes(market)
    assert tuple(inspect.signature(emit_outcomes).parameters) == ("market",)


def test_foreign_scenario_outcomes_are_refused_before_ranking() -> None:
    price_only, settlement_aware = _policies()
    with pytest.raises(ValueError, match="another scenario"):
        compare_policies(
            emit_outcomes(_market()),
            expected_scenario_digest="sha256:" + "f" * 64,
            price_only=price_only,
            settlement_aware=settlement_aware,
        )
