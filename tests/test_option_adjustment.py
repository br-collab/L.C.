"""SC-3B acceptance for evidence-backed option contract adjustments."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from lc.option_adjustment import (
    AdjustmentOutcome,
    OccContractAdjustment,
    SeriesTerms,
    apply_adjustment,
)
from lc.option_exercise import deliverable_for
from lc.options import (
    AdjustmentEvidence,
    CashComponent,
    Deliverable,
    DeliverableKind,
    ExerciseStyle,
    OptionRight,
    OptionSeries,
    SharesComponent,
)

D = Decimal
EVIDENCE = AdjustmentEvidence(
    notice_id="SYNTHETIC-OCC-ADJUSTMENT-1",
    url="https://example.invalid/occ-adjustment-1",
    retrieved_dtg="202610060100",
    sha256="ab" * 32,
)
STANDARD = Deliverable(
    kind=DeliverableKind.STANDARD,
    components=(SharesComponent(security_id="US0000000001", quantity=D(100)),),
)
ADJUSTED = Deliverable(
    kind=DeliverableKind.ADJUSTED,
    components=(
        SharesComponent(security_id="US0000000002", quantity=D(25)),
        CashComponent(amount=D("4.50"), currency="USD"),
    ),
    evidence=EVIDENCE,
)
SERIES = OptionSeries(
    root="SYN",
    underlying_security_id="US0000000001",
    expiry=date(2027, 1, 15),
    right=OptionRight.CALL,
    strike=D(50),
    style=ExerciseStyle.AMERICAN,
    deliverable=STANDARD,
)
ADJUSTMENT = OccContractAdjustment(
    adjustment_id="ADJ-1",
    series_osi=SERIES.osi_identifier,
    effective_date=date(2026, 10, 10),
    corporate_action_event_id="CA-1",
    corporate_action_source_id="DTC",
    deliverable=ADJUSTED,
    evidence=EVIDENCE,
)
MATCH = {("CA-1", "DTC")}


def test_matching_adjustment_applies_from_effective_date_and_retains_old_terms() -> None:
    before = apply_adjustment(
        SeriesTerms(active=SERIES), ADJUSTMENT, as_of=date(2026, 10, 9),
        known_corporate_actions=MATCH,
    )
    assert before.outcome is AdjustmentOutcome.NOT_YET_EFFECTIVE
    assert before.terms.active == SERIES and before.terms.prior == ()

    after = apply_adjustment(
        before.terms, ADJUSTMENT, as_of=date(2026, 10, 10),
        known_corporate_actions=MATCH,
    )
    assert after.outcome is AdjustmentOutcome.APPLIED
    assert after.terms.active.deliverable == ADJUSTED
    assert after.terms.prior == (SERIES,)


def test_an_adjustment_without_a_matching_event_is_flagged() -> None:
    result = apply_adjustment(
        SeriesTerms(active=SERIES), ADJUSTMENT, as_of=date(2026, 10, 10),
        known_corporate_actions=set(),
    )
    assert result.outcome is AdjustmentOutcome.UNMATCHED_EVENT
    assert result.terms.active == SERIES


def test_invalid_or_inconsistent_evidence_is_refused() -> None:
    with pytest.raises(ValidationError, match="same OCC evidence"):
        OccContractAdjustment.model_validate(
            ADJUSTMENT.model_dump()
            | {"evidence": EVIDENCE.model_copy(update={"sha256": "cd" * 32})},
            strict=True,
        )
    with pytest.raises(ValidationError, match="adjusted deliverable"):
        OccContractAdjustment(
            adjustment_id="ADJ-2", series_osi=SERIES.osi_identifier,
            effective_date=date(2026, 10, 10), corporate_action_event_id="CA-1",
            corporate_action_source_id="DTC", deliverable=STANDARD, evidence=EVIDENCE,
        )


def test_exercise_uses_the_adjusted_deliverable_component_by_component() -> None:
    result = apply_adjustment(
        SeriesTerms(active=SERIES), ADJUSTMENT, as_of=date(2026, 10, 10),
        known_corporate_actions=MATCH,
    )
    assert deliverable_for(result.terms.active, 3) == (
        SharesComponent(security_id="US0000000002", quantity=D(75)),
        CashComponent(amount=D("13.50"), currency="USD"),
    )


def test_adjustment_for_another_series_is_refused() -> None:
    with pytest.raises(ValueError, match="another option series"):
        apply_adjustment(
            SeriesTerms(active=SERIES),
            ADJUSTMENT.model_copy(update={"series_osi": "OTHER 270115C00050000"}),
            as_of=date(2026, 10, 10), known_corporate_actions=MATCH,
        )
