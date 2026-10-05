"""SC-3 WP-3 acceptance, as amended by A1 and A2: exercise, assignment and expiry.

Acceptance criteria, mapped:

- Exercise of N contracts produces exactly N times the deliverable:
  ``test_exercising_n_contracts_delivers_n_times_the_deliverable`` and
  ``test_expiration_through_assignment_conserves_contracts_and_shares``.
- Assigned plus unassigned equals total:
  ``test_assigned_plus_unassigned_equals_the_wheel_on_every_grid_point`` and
  ``test_expiration_through_assignment_conserves_contracts_and_shares``.
- A1, OCC's wheel with an injected start, reproducing OCC's worked example:
  ``test_the_wheel_reproduces_occs_worked_example`` and
  ``test_assignment_by_account_reproduces_occs_worked_example``.
- A2, the threshold as cited data, exercise by exception in both directions, no late
  exercise: ``test_exercise_by_exception_at_the_threshold``,
  ``test_an_instruction_governs_in_both_directions`` and
  ``test_there_is_no_late_exercise``.
- The procedure's figures come from the cited table, and an absent one is INDETERMINATE:
  ``test_a_missing_procedure_figure_is_indeterminate``.

Every series, account, position and price below is SYNTHETIC. The worked example's
figures are OCC's own, from its Standard Assignment Procedure.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from cannae_kernel.provenance import Provenance
from pydantic import ValidationError

from lc.occ_rules import DEFAULT_TABLE_PATH, OccRuleTable, load_rule_table
from lc.option_exercise import (
    DECIMAL_PLACES_ITEM,
    INCREMENT_ITEM,
    THRESHOLD_ITEM,
    AssignmentMethod,
    ClosingPrice,
    ExerciseBasis,
    ExerciseNotice,
    ExpiringLongPosition,
    Outcome,
    ShortPositionEntry,
    assign_exercises,
    check_exercise_notice,
    deliverable_for,
    exercise_at_expiration,
    wheel_positions,
)
from lc.options import (
    AdjustmentEvidence,
    CashComponent,
    Deliverable,
    DeliverableKind,
    ExerciseStyle,
    OptionRight,
    OptionSeries,
    PositionAccount,
    PositionAccountType,
    SharesComponent,
)

D = Decimal
TABLE = load_rule_table(DEFAULT_TABLE_PATH)
UNDERLYING = "SYNTHETIC-XYZ"
EXPIRY = date(2026, 12, 18)
STANDARD = Deliverable(
    kind=DeliverableKind.STANDARD,
    components=(SharesComponent(security_id=UNDERLYING, quantity=D(100)),),
)
ADJUSTED = Deliverable(
    kind=DeliverableKind.ADJUSTED,
    components=(
        SharesComponent(security_id=UNDERLYING, quantity=D(150)),
        SharesComponent(security_id="SYNTHETIC-ABC", quantity=D(12)),
        CashComponent(amount=D("31.25"), currency="USD"),
    ),
    evidence=AdjustmentEvidence(
        notice_id="SYNTHETIC-MEMO-1",
        url="https://example.invalid/synthetic-memo-1",
        retrieved_dtg="202610051200",
        sha256="ab" * 32,
    ),
)


def series(**changes: Any) -> OptionSeries:
    base: dict[str, Any] = {
        "root": "SYN",
        "underlying_security_id": UNDERLYING,
        "expiry": EXPIRY,
        "right": OptionRight.CALL,
        "strike": D("150.5"),
        "style": ExerciseStyle.AMERICAN,
        "deliverable": STANDARD,
    }
    return OptionSeries(**(base | changes))


def account(n: int, kind: PositionAccountType = PositionAccountType.CUSTOMER) -> PositionAccount:
    return PositionAccount(
        clearing_member_id=f"CM{n % 7}", account_id=f"A{n:05d}", account_type=kind
    )


def shorts(*sizes: int) -> tuple[ShortPositionEntry, ...]:
    return tuple(
        ShortPositionEntry(account=account(i), wheel_id=1000 + i, short_contracts=size)
        for i, size in enumerate(sizes)
    )


def assign(
    exercised: int,
    entries: tuple[ShortPositionEntry, ...],
    start: int = 1,
    table: OccRuleTable = TABLE,
    method: AssignmentMethod = AssignmentMethod.STANDARD,
) -> Any:
    return assign_exercises(
        series(), exercised, entries, method=method, start_position=start, table=table
    )


def figures() -> tuple[int, int]:
    increment, places = TABLE.item(INCREMENT_ITEM), TABLE.item(DECIMAL_PLACES_ITEM)
    assert increment is not None and places is not None
    return int(increment.decimal_value), int(places.decimal_value)


# --- A1: the wheel -----------------------------------------------------------------------------

#: OCC's worked example: 175 exercised, 355 open, increment 25, random start 1.
OCC_EXAMPLE_RANGES = ((1, 25), (51, 75), (102, 126), (153, 177), (203, 227), (254, 278), (305, 329))


def test_the_wheel_reproduces_occs_worked_example() -> None:
    increment, places = figures()
    assert (increment, places) == (25, 6)
    expected = tuple(p for low, high in OCC_EXAMPLE_RANGES for p in range(low, high + 1))
    assert wheel_positions(175, 355, increment, 1, places) == expected


def test_assignment_by_account_reproduces_occs_worked_example() -> None:
    entries = shorts(*([1] * 355))
    result = assign(175, entries)
    assert result.outcome is Outcome.DETERMINED
    assigned = {a.wheel_id - 1000 + 1 for a in result.assignments if a.assigned_contracts}
    assert assigned == {p for low, high in OCC_EXAMPLE_RANGES for p in range(low, high + 1)}
    assert {c.item_id for c in result.criteria} == {INCREMENT_ITEM, DECIMAL_PLACES_ITEM}


def test_the_wheel_follows_the_database_identifier_not_the_input_order() -> None:
    entries = shorts(30, 30, 30)
    reversed_input = tuple(reversed(entries))
    assert assign(40, entries, start=5) == assign(40, reversed_input, start=5)


WHEEL_SIZES = (1, 2, 24, 25, 26, 49, 50, 51, 99, 100, 101, 355, 1000)


@pytest.mark.parametrize("wheel_size", WHEEL_SIZES)
def test_assigned_plus_unassigned_equals_the_wheel_on_every_grid_point(wheel_size: int) -> None:
    increment, places = figures()
    for exercised in range(wheel_size + 1):
        for start in sorted({1, wheel_size // 2 + 1, wheel_size}):
            slots = wheel_positions(exercised, wheel_size, increment, start, places)
            assert len(slots) == exercised
            assert len(set(slots)) == exercised, "a position was assigned twice"
            assert all(1 <= s <= wheel_size for s in slots)


def test_assigned_plus_unassigned_equals_each_accounts_position() -> None:
    entries = shorts(7, 0, 40, 13, 25, 1, 64)
    for exercised in range(sum(e.short_contracts for e in entries) + 1):
        result = assign(exercised, entries, start=1 + exercised % 150)
        assert result.outcome is Outcome.DETERMINED
        assert sum(a.assigned_contracts for a in result.assignments) == exercised
        for entry, made in zip(entries, result.assignments, strict=True):
            assert made.assigned_contracts + made.unassigned_contracts == entry.short_contracts


def test_every_open_contract_exercised_assigns_the_whole_open_interest() -> None:
    result = assign(150, shorts(100, 50), start=77)
    assert [a.assigned_contracts for a in result.assignments] == [100, 50]


def test_a_negative_skip_interval_is_zero() -> None:
    # 354 of 355: T1 = 15, T/T1 = 23.666667, so the initial skip is below zero.
    increment, places = figures()
    slots = wheel_positions(354, 355, increment, 1, places)
    assert slots == tuple(range(1, 355))


def test_the_start_position_is_injected_and_changes_the_result() -> None:
    entries = shorts(*([1] * 100))
    first, again, other = assign(30, entries, 1), assign(30, entries, 1), assign(30, entries, 51)
    assert first == again and first.result_digest == again.result_digest
    assert first.assignments != other.assignments


@pytest.mark.parametrize(
    ("exercised", "start", "said"),
    [(101, 1, "against 100 open short"), (-1, 1, "against 100"), (10, 0, "not on a wheel")],
)
def test_inconsistent_inputs_are_refused(exercised: int, start: int, said: str) -> None:
    result = assign(exercised, shorts(60, 40), start=start)
    assert result.outcome is Outcome.REFUSED and said in result.reason
    assert result.assignments == ()


def test_duplicate_accounts_or_identifiers_are_refused() -> None:
    entry = shorts(10)[0]
    twin = entry.model_copy(update={"wheel_id": 2000})
    assert assign(1, (entry, twin)).reason == "a position account appears twice"
    other = ShortPositionEntry(account=account(9), wheel_id=entry.wheel_id, short_contracts=5)
    assert assign(1, (entry, other)).reason == "two position accounts share a wheel identifier"


# --- Rule 803 ----------------------------------------------------------------------------------


def test_a_short_covered_by_a_confirmed_closing_purchase_is_not_assigned() -> None:
    covered = ShortPositionEntry(
        account=account(1), wheel_id=1, short_contracts=50, closing_purchase_contracts=50
    )
    partly = ShortPositionEntry(
        account=account(2), wheel_id=2, short_contracts=50, closing_purchase_contracts=20
    )
    result = assign(30, (covered, partly))
    assert result.wheel_contracts == 30
    assert [a.assigned_contracts for a in result.assignments] == [0, 30]
    with pytest.raises(ValidationError):
        ShortPositionEntry(
            account=account(3), wheel_id=3, short_contracts=5, closing_purchase_contracts=6
        )


def test_pro_rata_is_indeterminate() -> None:
    result = assign(10, shorts(60, 40), method=AssignmentMethod.PRO_RATA)
    assert result.outcome is Outcome.INDETERMINATE and "pro rata" in result.reason


def table_without(tmp_path: Path, *item_ids: str) -> OccRuleTable:
    document = json.loads(DEFAULT_TABLE_PATH.read_text(encoding="utf-8"))
    document["items"] = [i for i in document["items"] if i["item_id"] not in item_ids]
    path = tmp_path / "table.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return load_rule_table(path)


@pytest.mark.parametrize("item_id", [INCREMENT_ITEM, DECIMAL_PLACES_ITEM])
def test_a_missing_procedure_figure_is_indeterminate(tmp_path: Path, item_id: str) -> None:
    result = assign(10, shorts(60, 40), table=table_without(tmp_path, item_id))
    assert result.outcome is Outcome.INDETERMINATE and item_id in result.reason


# --- A2: exercise by exception -----------------------------------------------------------------


def closing(price: str, security_id: str = UNDERLYING) -> ClosingPrice:
    return ClosingPrice(
        security_id=security_id,
        price=D(price),
        currency="USD",
        trading_date=EXPIRY,
        source="SYNTHETIC consolidated last sale",
        provenance=Provenance.FACT_SYNTHETIC,
    )


def held(n: int, instruction: int | None = None) -> ExpiringLongPosition:
    return ExpiringLongPosition(
        account=account(n), long_contracts=10, exercise_instruction=instruction
    )


@pytest.mark.parametrize(
    ("right", "price", "basis"),
    [
        (OptionRight.CALL, "150.51", ExerciseBasis.IN_THE_MONEY_BY_THRESHOLD),
        (OptionRight.CALL, "150.50", ExerciseBasis.NOT_IN_THE_MONEY_BY_THRESHOLD),
        (OptionRight.CALL, "150.509", ExerciseBasis.NOT_IN_THE_MONEY_BY_THRESHOLD),
        (OptionRight.PUT, "150.49", ExerciseBasis.IN_THE_MONEY_BY_THRESHOLD),
        (OptionRight.PUT, "150.491", ExerciseBasis.NOT_IN_THE_MONEY_BY_THRESHOLD),
        (OptionRight.PUT, "160", ExerciseBasis.NOT_IN_THE_MONEY_BY_THRESHOLD),
    ],
)
def test_exercise_by_exception_at_the_threshold(
    right: OptionRight, price: str, basis: ExerciseBasis
) -> None:
    result = exercise_at_expiration(series(right=right), (held(1),), closing(price), TABLE)
    assert result.outcome is Outcome.DETERMINED
    (decision,) = result.decisions
    assert decision.basis is basis
    in_the_money = basis is ExerciseBasis.IN_THE_MONEY_BY_THRESHOLD
    assert decision.exercised_contracts == (10 if in_the_money else 0)
    assert decision.expired_contracts == (0 if in_the_money else 10)
    assert "by $0.01 or more" in decision.reason
    assert [c.item_id for c in result.criteria] == [THRESHOLD_ITEM]


@pytest.mark.parametrize(
    ("price", "instruction"),
    [("200", 0), ("200", 3), ("100", 10), ("100", 4)],
    ids=["none-of-in-the-money", "fewer-than-all", "all-out-of-the-money", "some-out-of-the-money"],
)
def test_an_instruction_governs_in_both_directions(price: str, instruction: int) -> None:
    result = exercise_at_expiration(series(), (held(1, instruction),), closing(price), TABLE)
    (decision,) = result.decisions
    assert decision.basis is ExerciseBasis.INSTRUCTION
    assert decision.exercised_contracts == instruction
    assert decision.expired_contracts == 10 - instruction


def test_an_instruction_cannot_exceed_the_position() -> None:
    with pytest.raises(ValidationError):
        held(1, 11)


def test_without_a_closing_price_only_instructed_positions_are_determined() -> None:
    result = exercise_at_expiration(series(), (held(1, 4), held(2)), None, TABLE)
    assert result.outcome is Outcome.INDETERMINATE
    instructed, uninstructed = result.decisions
    assert instructed.exercised_contracts == 4
    assert uninstructed.basis is ExerciseBasis.INDETERMINATE
    assert uninstructed.exercised_contracts is None
    assert "no closing price" in result.reason


def test_an_adjusted_deliverable_needs_an_instruction() -> None:
    adjusted = series(deliverable=ADJUSTED)
    result = exercise_at_expiration(adjusted, (held(1),), closing("500"), TABLE)
    assert result.outcome is Outcome.INDETERMINATE and "adjusted deliverable" in result.reason
    instructed = exercise_at_expiration(adjusted, (held(1, 10),), closing("500"), TABLE)
    assert instructed.outcome is Outcome.DETERMINED


def test_a_closing_price_for_another_security_is_indeterminate() -> None:
    result = exercise_at_expiration(series(), (held(1),), closing("200", "OTHER"), TABLE)
    assert result.outcome is Outcome.INDETERMINATE and "another security" in result.reason


def test_a_missing_threshold_is_indeterminate(tmp_path: Path) -> None:
    table = table_without(tmp_path, THRESHOLD_ITEM)
    result = exercise_at_expiration(series(), (held(1),), closing("200"), table)
    assert result.outcome is Outcome.INDETERMINATE and THRESHOLD_ITEM in result.reason


# --- exercise notices --------------------------------------------------------------------------


def notice(contracts: int, on: date, osi: str | None = None) -> ExerciseNotice:
    return ExerciseNotice(
        series_osi=osi or series().osi_identifier,
        account=account(1),
        contracts=contracts,
        tendered_on=on,
    )


def test_an_american_option_is_exercisable_before_expiration() -> None:
    assert check_exercise_notice(notice(5, date(2026, 11, 2)), series(), 10)[0] is (
        Outcome.DETERMINED
    )


@pytest.mark.parametrize(
    ("style", "contracts", "on", "said"),
    [
        (ExerciseStyle.EUROPEAN, 5, date(2026, 11, 2), "only at expiration"),
        (ExerciseStyle.AMERICAN, 11, EXPIRY, "exceed the 10 held"),
    ],
)
def test_a_notice_that_cannot_be_tendered_is_refused(
    style: ExerciseStyle, contracts: int, on: date, said: str
) -> None:
    outcome, reason = check_exercise_notice(notice(contracts, on), series(style=style), 10)
    assert outcome is Outcome.REFUSED and said in reason


def test_there_is_no_late_exercise() -> None:
    outcome, reason = check_exercise_notice(notice(1, date(2026, 12, 19)), series(), 10)
    assert outcome is Outcome.REFUSED and "no late exercise" in reason


def test_a_notice_for_another_series_is_refused() -> None:
    other = series(strike=D(160)).osi_identifier
    assert check_exercise_notice(notice(1, EXPIRY, other), series(), 10)[0] is Outcome.REFUSED


# --- conservation ------------------------------------------------------------------------------


@pytest.mark.parametrize("contracts", [1, 2, 7, 25, 1000])
def test_exercising_n_contracts_delivers_n_times_the_deliverable(contracts: int) -> None:
    standard = deliverable_for(series(), contracts)
    assert standard == (SharesComponent(security_id=UNDERLYING, quantity=D(100) * contracts),)
    adjusted = deliverable_for(series(deliverable=ADJUSTED), contracts)
    assert adjusted == (
        SharesComponent(security_id=UNDERLYING, quantity=D(150) * contracts),
        SharesComponent(security_id="SYNTHETIC-ABC", quantity=D(12) * contracts),
        CashComponent(amount=D("31.25") * contracts, currency="USD"),
    )
    with pytest.raises(ValueError, match="positive number of contracts"):
        deliverable_for(series(), 0)


def test_expiration_through_assignment_conserves_contracts_and_shares() -> None:
    longs = (held(1), held(2, 3), held(3, 0), held(4))
    expiration = exercise_at_expiration(series(), longs, closing("175"), TABLE)
    assert expiration.outcome is Outcome.DETERMINED
    for position, decision in zip(longs, expiration.decisions, strict=True):
        assert decision.exercised_contracts is not None and decision.expired_contracts is not None
        assert decision.exercised_contracts + decision.expired_contracts == (
            position.long_contracts
        )
    exercised = expiration.exercised_contracts
    assert exercised == 23

    entries = shorts(12, 9, 19)
    assignment = assign(exercised, entries, start=17)
    assigned = sum(a.assigned_contracts for a in assignment.assignments)
    assert assigned == exercised
    assert sum(a.unassigned_contracts for a in assignment.assignments) == 40 - exercised

    def shares(contracts: int) -> Decimal:
        (component,) = deliverable_for(series(), contracts)
        assert isinstance(component, SharesComponent)
        return component.quantity

    delivered = sum(
        shares(a.assigned_contracts) for a in assignment.assignments if a.assigned_contracts
    )
    assert delivered == shares(exercised) == D(100) * exercised
