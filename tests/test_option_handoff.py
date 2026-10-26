"""SC-3 WP-4: listed option obligations through Atreides' real acceptance boundary.

The order: "The exact envelope and L.C. payload bytes pass Project-Atreides
``evaluate_candidate`` in the pinned out-of-tree CI job. No direct CNS consumption is
claimed. L.C. never imports Atreides." Workspace rule 20: L.C.'s real emitter and
Atreides' real parser, in one execution, with no translation between them.

Runs in the pinned Atreides handoff CI job; skipped elsewhere. Everything is SYNTHETIC.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from cannae_kernel.canonical import digest
from cannae_kernel.disposition import Disposition
from test_lifecycle import _actor
from test_option_exercise import ADJUSTED, series
from test_option_obligation import exercise_obligations, premium

if importlib.util.find_spec("atreides") is None:
    if os.environ.get("HANDOFF_EXTRA_REQUIRED") == "1":
        pytest.fail("the pinned Atreides handoff boundary is required but not installed")
    pytest.skip("runs in the pinned Atreides handoff CI job", allow_module_level=True)

from atreides.acceptance.candidate import ObligationCandidate
from atreides.acceptance.service import evaluate_candidate
from atreides.customer_protection.control import (
    ControlInputs,
    ControlResult,
    Holding,
    IssueRequirement,
    check_possession_or_control,
)
from atreides.customer_protection.rules import load_rule_table
from test_atreides_handoff import _gate

from lc.option_obligation import FormedOptionObligation, NovationSide, ObligationPurpose
from lc.options import OptionRight

AT = datetime(2026, 12, 18, 22, 0, tzinfo=UTC)


def every_option_obligation() -> tuple[FormedOptionObligation, ...]:
    return (
        premium(NovationSide.BUYER)[0],
        premium(NovationSide.SELLER)[0],
        *exercise_obligations(series())[0],
        *exercise_obligations(series(right=OptionRight.PUT))[0],
        *exercise_obligations(series(deliverable=ADJUSTED), Decimal(100))[0],
    )


#: Rule 15c3-3(c)(1): a clearing corporation is a good control location. SYNTHETIC holdings.
CLEARING_CONTROL = "15c3-3.control.c1"


def settlement_day(formed: FormedOptionObligation) -> date:
    """The day a control result must be as of: the envelope's settlement business date."""
    return formed.envelope.session.business_date.value


def customer_control(
    formed: FormedOptionObligation, *, held: Decimal | None = None, as_of: date | None = None
) -> ControlResult | None:
    """Atreides' real possession-or-control check over a SYNTHETIC stock record.

    INST-1 Amendment 3 (V4): a CUSTOMER delivery passes only with evidence the real engine
    produced for its issue, day and quantity; nothing here asserts a PASS.
    """
    securities = formed.payload.securities_leg
    if formed.payload.seller_capacity != "CUSTOMER" or securities is None:
        return None
    quantity = securities.quantity if held is None else held
    return check_possession_or_control(
        ControlInputs(
            as_of=as_of or settlement_day(formed),
            issues=(
                IssueRequirement(
                    issue=securities.instrument_id,
                    fully_paid=securities.quantity,
                    excess_margin=Decimal(0),
                    books_total=quantity,
                    holdings=(Holding(location=CLEARING_CONTROL, quantity=quantity),),
                ),
            ),
        ),
        load_rule_table(),
    )


def accept(
    formed: FormedOptionObligation,
    *,
    funded: bool = True,
    control: ControlResult | bool | None = True,
) -> Disposition:
    """``control=True`` supplies the real engine's result for this obligation."""
    evidence = customer_control(formed) if control is True else control or None
    record = evaluate_candidate(
        formed.envelope,
        formed.payload.canonical_bytes(),
        acceptance_id=uuid.UUID("00000000-0000-4000-8000-000000000301"),
        evaluated_at=AT,
        decided_by=_actor(),
        gate_decision=_gate(formed.envelope, formed.payload.cash_leg, funded=funded),
        halt=None,
        customer_control=evidence,
    )
    assert record.obligation_id == formed.envelope.obligation_id
    assert record.obligation_digest == digest(formed.envelope)
    return record.disposition


def test_every_option_obligation_passes_the_real_atreides_predicates() -> None:
    formed = every_option_obligation()
    assert {f.purpose for f in formed} == {
        ObligationPurpose.PREMIUM,
        ObligationPurpose.DELIVERY_AGAINST_STRIKE,
        ObligationPurpose.FREE_DELIVERY,
        ObligationPurpose.DELIVERABLE_CASH,
    }
    for obligation in formed:
        assert accept(obligation) is Disposition.PASS, obligation.purpose


def test_atreides_parses_the_exact_bytes_without_translation() -> None:
    for obligation in every_option_obligation():
        raw = obligation.payload.canonical_bytes()
        parsed = ObligationCandidate.model_validate_json(raw)
        assert parsed.delivery_pattern == obligation.payload.delivery_pattern.value
        assert (parsed.securities_leg is None) is (obligation.payload.securities_leg is None)
        assert (parsed.cash_leg is None) is (obligation.payload.cash_leg is None)
        assert [r.digest for r in parsed.source_manifest.references] == [  # type: ignore[union-attr]
            r.digest for r in obligation.payload.source_manifest.references
        ]


def test_an_unfunded_cash_leg_is_held_and_a_free_delivery_needs_no_gate() -> None:
    formed = every_option_obligation()
    premium_obligation = formed[0]
    assert accept(premium_obligation, funded=False) is Disposition.HOLD
    free = next(f for f in formed if f.purpose is ObligationPurpose.FREE_DELIVERY)
    assert free.payload.cash_leg is None
    assert accept(free, funded=False) is Disposition.PASS


def test_altered_bytes_are_blocked() -> None:
    formed = premium(NovationSide.BUYER)[0]
    record = evaluate_candidate(
        formed.envelope,
        formed.payload.canonical_bytes() + b" ",
        acceptance_id=uuid.UUID("00000000-0000-4000-8000-000000000302"),
        evaluated_at=AT,
        decided_by=_actor(),
        gate_decision=None,
        halt=None,
    )
    assert record.disposition is Disposition.BLOCK


def _customer_deliveries() -> tuple[FormedOptionObligation, ...]:
    return tuple(
        f
        for f in every_option_obligation()
        if f.payload.seller_capacity == "CUSTOMER" and f.payload.securities_leg is not None
    )


def test_v4_customer_deliveries_exist_and_carry_their_capacity_under_the_digest() -> None:
    """Before WP1b no DELIVER obligation said whose securities it delivered, so all HOLD."""
    deliveries = _customer_deliveries()
    assert {f.purpose for f in deliveries} == {
        ObligationPurpose.DELIVERY_AGAINST_STRIKE,
        ObligationPurpose.FREE_DELIVERY,
    }
    for formed in deliveries:
        parsed = ObligationCandidate.model_validate_json(formed.payload.canonical_bytes())
        assert parsed.seller_capacity == "CUSTOMER"


def test_v4_a_customer_delivery_without_control_evidence_holds() -> None:
    for formed in _customer_deliveries():
        assert accept(formed, control=False) is Disposition.HOLD


def test_v4_control_short_by_one_unit_or_a_day_stale_holds() -> None:
    for formed in _customer_deliveries():
        securities = formed.payload.securities_leg
        assert securities is not None
        short = customer_control(formed, held=securities.quantity - 1)
        stale = customer_control(formed, as_of=settlement_day(formed) - timedelta(days=1))
        assert accept(formed, control=short) is Disposition.HOLD
        assert accept(formed, control=stale) is Disposition.HOLD
