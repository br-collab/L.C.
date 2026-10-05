"""SC-3 WP-4 acceptance: premium and exercise obligations through the existing obligation path.

Acceptance criteria, mapped:

- Each obligation names the correct source transformation and manifest:
  ``test_a_premium_obligation_is_bound_to_its_execution_event`` and
  ``test_an_exercise_obligation_is_bound_to_its_exercise_and_assignment``.
- Premium is absent from exercise obligations:
  ``test_no_exercise_obligation_carries_the_premium``.
- Exercise and assignment conserve quantity (order section 3):
  ``test_exercise_obligations_conserve_shares_and_strike``.
- Adjusted deliverables use the ingested components, including cash in lieu:
  ``test_an_adjusted_deliverable_forms_one_obligation_per_component`` and
  ``test_an_all_cash_deliverable_pays_the_strike_on_its_own``.
- The current real Atreides acceptance parser consumes the exact attested bytes: see
  ``tests/test_option_handoff.py``, which runs in the pinned Atreides CI job.

This test is the independent caller: it drives the OCC emulator and hands its output to
L.C. Everything here is SYNTHETIC.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from cannae_kernel.canonical import canonical_bytes_of, digest, digest_bytes
from cannae_kernel.delivery import DeliveryPattern
from cannae_kernel.envelopes import ExecutionEvent
from cannae_kernel.ids import LifecycleId
from test_occ_boundary import novated
from test_option_exercise import ADJUSTED, TABLE, UNDERLYING, account, closing, series, shorts

from lc.obligation import (
    AccountRole,
    LegKind,
    ObligationPayload,
    SourceKind,
    SourceManifest,
    SourceReference,
)
from lc.option_clearing import admit_novation
from lc.option_exercise import (
    AssignmentMethod,
    AssignmentResult,
    ExerciseNotice,
    ExpirationResult,
    ExpiringLongPosition,
    assign_exercises,
    exercise_at_expiration,
)
from lc.option_obligation import (
    PREMIUM_RULE_SET,
    FormedOptionObligation,
    NovationSide,
    ObligationPurpose,
    SettlementParty,
    SettlementRoute,
    form_exercise_obligations,
    form_premium_obligation,
)
from lc.options import (
    CashComponent,
    Deliverable,
    DeliverableKind,
    OptionRight,
    OptionSeries,
    PositionAccount,
)

D = Decimal
FORMED_AT = datetime(2026, 12, 18, 21, 0, tzinfo=UTC)
SETTLES = date(2026, 12, 21)
ROUTE = SettlementRoute(
    rail="SYNTHETIC equity settlement",
    securities_route="SYNTHETIC central counterparty securities",
    cash_route="SYNTHETIC central counterparty cash",
    securities_rule_set="synthetic-securities/1.0",
    cash_rule_set="synthetic-cash/1.0",
)
CCP = SettlementParty(
    participant_id="SYNTHETIC-CCP", securities_account_id="CCP-SEC", cash_account_id="CCP-CASH"
)


def party(member: str) -> SettlementParty:
    return SettlementParty(
        participant_id=member,
        securities_account_id=f"{member}-SEC",
        cash_account_id=f"{member}-CASH",
    )


# --- premium -----------------------------------------------------------------------------------


def premium(side: NovationSide) -> tuple[FormedOptionObligation, ExecutionEvent, bytes]:
    buyer_event, seller_event, payload = novated()
    event = buyer_event if side is NovationSide.BUYER else seller_event
    admitted = admit_novation(event, payload)
    member = admitted.trade.buyer if side is NovationSide.BUYER else admitted.trade.seller
    formed = form_premium_obligation(
        admitted,
        payload,
        side=side,
        member=party(member.clearing_member_id),
        central_counterparty=CCP,
        settlement_date=SETTLES,
        route=ROUTE,
        session=event.session,
        formed_at=FORMED_AT,
    )
    return formed, event, payload


@pytest.mark.parametrize("side", list(NovationSide))
def test_a_premium_obligation_is_bound_to_its_execution_event(side: NovationSide) -> None:
    formed, event, payload = premium(side)
    trade = admit_novation(event, payload).trade
    assert formed.purpose is ObligationPurpose.PREMIUM
    assert formed.payload.delivery_pattern is DeliveryPattern.PAYMENT_ONLY
    assert formed.payload.securities_leg is None
    cash = formed.payload.cash_leg
    assert cash is not None and cash.total == cash.principal == trade.premium
    payer, payee = (
        ("CM-A-CASH", "CCP-CASH") if side is NovationSide.BUYER else ("CCP-CASH", "CM-B-CASH")
    )
    assert (cash.paying_account_id, cash.receiving_account_id) == (payer, payee)

    assert formed.transformation.input_digests == (digest(event),)
    assert formed.transformation.output_digest == digest_bytes(payload)
    assert formed.transformation.rule_set_version == PREMIUM_RULE_SET
    assert formed.envelope.lifecycle_id == event.lifecycle_id
    assert formed.envelope.transformation_digest == digest(formed.transformation)
    assert formed.envelope.payload_digest == digest(formed.payload)
    references = {r.kind: r for r in formed.payload.source_manifest.references}
    assert set(references) == {
        SourceKind.APPROVED_INTENT,
        SourceKind.EXECUTION,
        SourceKind.OPTION_NOVATION,
        SourceKind.CLEARING_TRANSFORMATION,
    }
    assert references[SourceKind.APPROVED_INTENT].digest == event.intent_digest
    assert references[SourceKind.EXECUTION].digest == digest(event)
    assert references[SourceKind.OPTION_NOVATION].digest == event.payload_digest
    assert [e.leg for e in formed.payload.expected_finality] == [LegKind.CASH]


def test_premium_obligations_for_the_two_sides_are_distinct_and_deterministic() -> None:
    buyer, seller = premium(NovationSide.BUYER)[0], premium(NovationSide.SELLER)[0]
    assert buyer.envelope.obligation_id != seller.envelope.obligation_id
    assert premium(NovationSide.BUYER)[0] == buyer


def test_a_premium_needs_the_attested_bytes_and_the_right_member() -> None:
    buyer_event, _, payload = novated()
    admitted = admit_novation(buyer_event, payload)

    def form(payload_bytes: bytes, member: str) -> FormedOptionObligation:
        return form_premium_obligation(
            admitted,
            payload_bytes,
            side=NovationSide.BUYER,
            member=party(member),
            central_counterparty=CCP,
            settlement_date=SETTLES,
            route=ROUTE,
            session=buyer_event.session,
            formed_at=FORMED_AT,
        )

    with pytest.raises(ValueError, match="not the bytes the event attests"):
        form(payload + b" ", "CM-A")
    with pytest.raises(ValueError, match="the buyer is CM-A"):
        form(payload, "CM-B")


# --- exercise ----------------------------------------------------------------------------------

# Holders and writers are distinct position accounts.
HOLDERS = (account(101), account(102))
WRITERS = shorts(12, 9, 19)


def expiration(chosen: OptionSeries) -> ExpirationResult:
    # An adjusted deliverable is exercised only on instruction, so the first holder declines.
    declined = 0 if chosen.deliverable.kind is DeliverableKind.ADJUSTED else None
    positions = (
        ExpiringLongPosition(account=HOLDERS[0], long_contracts=10, exercise_instruction=declined),
        ExpiringLongPosition(account=HOLDERS[1], long_contracts=10, exercise_instruction=3),
    )
    price = "175" if chosen.right is OptionRight.CALL else "120"
    return exercise_at_expiration(chosen, positions, closing(price), TABLE)


def assignment_for(chosen: OptionSeries, exercised: int) -> AssignmentResult:
    return assign_exercises(
        chosen,
        exercised,
        WRITERS,
        method=AssignmentMethod.STANDARD,
        start_position=17,
        table=TABLE,
    )


def lifecycles() -> dict[PositionAccount, LifecycleId]:
    buyer_event, _, _ = novated()
    every = (*HOLDERS, *(entry.account for entry in WRITERS))
    return dict.fromkeys(every, buyer_event.lifecycle_id)


def exercise_obligations(
    chosen: OptionSeries, multiplier: Decimal | None = None
) -> tuple[tuple[FormedOptionObligation, ...], ExpirationResult, AssignmentResult]:
    expired = expiration(chosen)
    assigned = assignment_for(chosen, expired.exercised_contracts)
    members = sorted({a.clearing_member_id for a in (*HOLDERS, *(e.account for e in WRITERS))})
    formed = form_exercise_obligations(
        chosen,
        exercise=expired,
        assignment=assigned,
        parties=tuple(party(m) for m in members),
        settlement_counterparty=CCP,
        lifecycles=lifecycles(),
        strike_currency="USD",
        settlement_date=SETTLES,
        route=ROUTE,
        session=novated()[0].session,
        formed_at=FORMED_AT,
        strike_multiplier=multiplier,
    )
    return formed, expired, assigned


def test_an_exercise_obligation_is_bound_to_its_exercise_and_assignment() -> None:
    formed, expired, assigned = exercise_obligations(series())
    assert expired.exercised_contracts == 13
    assert {f.purpose for f in formed} == {ObligationPurpose.DELIVERY_AGAINST_STRIKE}
    for obligation in formed:
        assert obligation.transformation.input_digests == (expired.result_digest,)
        assert obligation.transformation.output_digest == assigned.result_digest
        assert obligation.envelope.transformation_digest == digest(obligation.transformation)
        assert obligation.envelope.payload_digest == digest(obligation.payload)
        references = {r.kind: r.digest for r in obligation.payload.source_manifest.references}
        assert references == {
            SourceKind.OPTION_EXERCISE: expired.result_digest,
            SourceKind.OPTION_ASSIGNMENT: assigned.result_digest,
            SourceKind.CLEARING_TRANSFORMATION: digest(obligation.transformation),
        }
        assert obligation.payload.delivery_pattern is DeliveryPattern.DVP
    ids = [f.envelope.obligation_id for f in formed]
    assert len(set(ids)) == len(ids)
    assert exercise_obligations(series())[0] == formed


def test_an_early_exercise_notice_forms_the_same_kind_of_obligation() -> None:
    chosen = series()
    notice = ExerciseNotice(
        series_osi=chosen.osi_identifier,
        account=HOLDERS[0],
        contracts=4,
        tendered_on=date(2026, 11, 2),
    )
    assigned = assignment_for(chosen, 4)
    formed = form_exercise_obligations(
        chosen,
        exercise=notice,
        assignment=assigned,
        parties=tuple(party(f"CM{n}") for n in range(7)),
        settlement_counterparty=CCP,
        lifecycles=lifecycles(),
        strike_currency="USD",
        settlement_date=SETTLES,
        route=ROUTE,
        session=novated()[0].session,
        formed_at=FORMED_AT,
    )
    assert formed[0].transformation.input_digests == (digest(notice),)
    assert formed[0].account == HOLDERS[0]


def legs(formed: tuple[FormedOptionObligation, ...], holder: bool) -> list[FormedOptionObligation]:
    return [f for f in formed if (f.account in HOLDERS) is holder]


@pytest.mark.parametrize("right", list(OptionRight))
def test_exercise_obligations_conserve_shares_and_strike(right: OptionRight) -> None:
    chosen = series(right=right)
    formed, expired, _ = exercise_obligations(chosen)
    exercised = expired.exercised_contracts
    holder_side, writer_side = legs(formed, True), legs(formed, False)

    def shares(side: list[FormedOptionObligation], into: str) -> Decimal:
        return sum(
            (
                f.payload.securities_leg.quantity
                for f in side
                if f.payload.securities_leg is not None
                and (f.payload.securities_leg.receiving_account_id == "CCP-SEC") is (into == "CCP")
            ),
            D(0),
        )

    def strike(side: list[FormedOptionObligation]) -> Decimal:
        return sum((f.payload.cash_leg.total for f in side if f.payload.cash_leg is not None), D(0))

    total_shares, total_strike = D(100) * exercised, chosen.strike * 100 * exercised
    if right is OptionRight.CALL:
        # Writers deliver to the CCP; the CCP delivers to holders.
        assert shares(writer_side, "CCP") == shares(holder_side, "member") == total_shares
    else:
        assert shares(holder_side, "CCP") == shares(writer_side, "member") == total_shares
    assert strike(holder_side) == strike(writer_side) == total_strike
    for obligation in holder_side:
        cash, securities = obligation.payload.cash_leg, obligation.payload.securities_leg
        assert cash is not None and securities is not None
        holder_pays = right is OptionRight.CALL
        assert (cash.paying_account_id != "CCP-CASH") is holder_pays
        assert (securities.receiving_account_id != "CCP-SEC") is holder_pays


def test_no_exercise_obligation_carries_the_premium() -> None:
    premium_amount = premium(NovationSide.BUYER)[0].payload.cash_leg
    assert premium_amount is not None
    formed, _, _ = exercise_obligations(series())
    for obligation in formed:
        kinds = {r.kind for r in obligation.payload.source_manifest.references}
        assert SourceKind.OPTION_NOVATION not in kinds
        cash = obligation.payload.cash_leg
        assert cash is not None and cash.accrued == 0
        assert cash.total == series().strike * 100 * (
            obligation.payload.securities_leg.quantity / 100  # type: ignore[union-attr]
        )
        assert cash.total != premium_amount.total


def test_an_adjusted_deliverable_forms_one_obligation_per_component() -> None:
    adjusted = series(deliverable=ADJUSTED)
    with pytest.raises(ValueError, match="must be supplied"):
        exercise_obligations(adjusted)
    # An adjusted series needs an instruction to exercise; expiration() gives one of 3.
    formed, expired, _ = exercise_obligations(adjusted, D(100))
    holder = [f for f in formed if f.account == HOLDERS[1]]
    assert [f.purpose for f in holder] == [
        ObligationPurpose.DELIVERY_AGAINST_STRIKE,
        ObligationPurpose.FREE_DELIVERY,
        ObligationPurpose.DELIVERABLE_CASH,
    ]
    assert [f.payload.delivery_pattern for f in holder] == [
        DeliveryPattern.DVP,
        DeliveryPattern.FOP,
        DeliveryPattern.PAYMENT_ONLY,
    ]
    first, second, cash_in_lieu = holder
    assert first.payload.securities_leg is not None and first.payload.securities_leg.quantity == 450
    assert first.payload.cash_leg is not None and first.payload.cash_leg.total == D("150.5") * 300
    assert second.payload.cash_leg is None and second.payload.securities_leg is not None
    assert second.payload.securities_leg.instrument_id == "SYNTHETIC-ABC"
    assert cash_in_lieu.payload.securities_leg is None and cash_in_lieu.payload.cash_leg is not None
    assert cash_in_lieu.payload.cash_leg.total == D("31.25") * 3
    # On a call the holder receives the cash in lieu.
    assert cash_in_lieu.payload.cash_leg.receiving_account_id != "CCP-CASH"
    assert expired.exercised_contracts == 3


def test_an_all_cash_deliverable_pays_the_strike_on_its_own() -> None:
    evidence = ADJUSTED.evidence
    cash_only = series(
        deliverable=Deliverable(
            kind=DeliverableKind.ADJUSTED,
            components=(CashComponent(amount=D("16000"), currency="USD"),),
            evidence=evidence,
        )
    )
    formed, _, _ = exercise_obligations(cash_only, D(100))
    holder = [f for f in formed if f.account == HOLDERS[1]]
    assert [f.purpose for f in holder] == [
        ObligationPurpose.DELIVERABLE_CASH,
        ObligationPurpose.STRIKE,
    ]
    assert all(f.payload.delivery_pattern is DeliveryPattern.PAYMENT_ONLY for f in holder)


def test_inconsistent_exercise_and_assignment_are_refused() -> None:
    chosen = series()
    expired = expiration(chosen)
    common = {
        "parties": tuple(party(f"CM{n}") for n in range(7)),
        "settlement_counterparty": CCP,
        "lifecycles": lifecycles(),
        "strike_currency": "USD",
        "settlement_date": SETTLES,
        "route": ROUTE,
        "session": novated()[0].session,
        "formed_at": FORMED_AT,
    }
    with pytest.raises(ValueError, match="13 contracts exercised, but the assignment is for 12"):
        form_exercise_obligations(
            chosen,
            exercise=expired,
            assignment=assignment_for(chosen, 12),
            **common,  # type: ignore[arg-type]
        )
    undetermined = exercise_at_expiration(
        chosen, (ExpiringLongPosition(account=HOLDERS[0], long_contracts=1),), None, TABLE
    )
    with pytest.raises(ValueError, match="INDETERMINATE"):
        form_exercise_obligations(
            chosen,
            exercise=undetermined,
            assignment=assignment_for(chosen, 1),
            **common,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="no settlement accounts"):
        form_exercise_obligations(
            chosen,
            exercise=expired,
            assignment=assignment_for(chosen, 13),
            **(common | {"parties": ()}),  # type: ignore[arg-type]
        )


def test_a_party_needs_distinct_securities_and_cash_accounts() -> None:
    with pytest.raises(ValueError, match="must be distinct"):
        SettlementParty(participant_id="X", securities_account_id="A", cash_account_id="A")


# --- the payload the obligation path carries ---------------------------------------------------


def test_every_option_payload_round_trips_through_its_canonical_bytes() -> None:
    formed = (
        premium(NovationSide.BUYER)[0],
        *exercise_obligations(series())[0],
        *exercise_obligations(series(deliverable=ADJUSTED), D(100))[0],
    )
    for obligation in formed:
        raw = obligation.payload.canonical_bytes()
        assert digest_bytes(raw) == obligation.envelope.payload_digest
        assert canonical_bytes_of(ObligationPayload.model_validate_json(raw)) == raw


def test_the_payload_requires_legs_to_match_the_delivery_pattern() -> None:
    dvp = exercise_obligations(series())[0][0].payload
    paid = premium(NovationSide.BUYER)[0].payload
    with pytest.raises(ValueError, match="securities leg must be present exactly"):
        dvp.model_validate(dvp.model_dump() | {"securities_leg": None})
    with pytest.raises(ValueError, match="securities leg must be present exactly"):
        paid.model_validate(paid.model_dump() | {"securities_leg": dvp.securities_leg.model_dump()})  # type: ignore[union-attr]
    with pytest.raises(ValueError, match="cash leg must be present exactly"):
        paid.model_validate(paid.model_dump() | {"cash_leg": None})


def test_a_manifest_mixing_families_is_refused() -> None:
    digest_value = "sha256:" + "a" * 64
    with pytest.raises(ValueError, match="every required upstream source kind"):
        SourceManifest(
            references=tuple(
                SourceReference(kind=kind, identifier=kind.value, digest=digest_value)
                for kind in (
                    SourceKind.OPTION_EXERCISE,
                    SourceKind.OPTION_NOVATION,
                    SourceKind.CLEARING_TRANSFORMATION,
                )
            )
        )


def test_participants_name_the_member_and_the_counterparty() -> None:
    formed, _, _ = exercise_obligations(series())
    for obligation in formed:
        roles = {(p.participant_id, p.role) for p in obligation.payload.participants}
        assert ("SYNTHETIC-CCP", AccountRole.DELIVERER) in roles or (
            "SYNTHETIC-CCP",
            AccountRole.RECEIVER,
        ) in roles
        assert UNDERLYING == obligation.payload.securities_leg.instrument_id  # type: ignore[union-attr]
