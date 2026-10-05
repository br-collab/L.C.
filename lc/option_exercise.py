"""Exercise, assignment and expiry of listed options (ORDER SC-3, WP-3).

EXPERIMENTAL (charter section 18.6). Nothing here is production evidence, and
nothing here exercises, assigns or settles anything at OCC (the Options
Clearing Corporation). It models what OCC's published rules and procedure say
would happen, from the inputs it is given.

ASSIGNMENT: OCC'S WHEEL
-----------------------
OCC Rule 803 assigns exercise notices "in accordance with the Corporation's
procedures" to clearing members with open short positions in the series.
OCC's Standard Assignment Procedure (last revised 9 October 2007) states the
procedure, and :func:`wheel_positions` implements it as written (amendment A1
to ORDER SC-3):

- Every short contract in the series is a position on a wheel, in the order of
  each position account's database identification code. Each sub-account is a
  separate position account.
- If fewer contracts are exercised than are open short, they are assigned in
  increments, starting at a randomly chosen position. The random start is an
  input here: the caller chooses it, so a run is reproducible.
- Between increments the procedure skips an interval derived from the
  contracts exercised (S), the contracts on the wheel (T) and the increment
  (I): T1 = S/I rounded up, then an initial skip of T/T1 - I, never below
  zero. Each skip is the initial skip plus the fraction carried from the last
  one, truncated, with the new fraction carried on.
- If every open short contract is exercised, the whole open interest is
  assigned.

The increment and the number of decimal places come from the OCC table
(:mod:`lc.occ_rules`). Two readings are the builder's, because the procedure
does not state them, and the test vector fixes the first:

1. "Decimals carried to six places" rounds half up. OCC's worked example gives
   355 / 7 = 50.714286, which is rounding; truncation would give 50.714285.
2. The wheel is circular: an increment that runs past the last position
   continues from the first. That is what a wheel is, and the random start
   requires it. An assignment that would land on a position already assigned
   is refused rather than guessed at; the tests show it does not occur.

Rule 803 also says, and :func:`assign_exercises` applies:

- (a) a short position opened by writing on the day the exercise notice was
  accepted may be assigned, so same-day opening writes are on the wheel;
- (b) a short position is not assigned once OCC holds confirmed trade
  information for a closing purchase that will eliminate it, so contracts
  covered that way are taken off the wheel.

Some option classes are assigned pro rata instead. OCC's procedure says so but
does not list them, and no list is among the saved sources. The caller states
the class's method, and a pro rata class is INDETERMINATE here. Allocation of
an assignment to a member's own customers is the member's procedure (Rule 804)
and is not modelled.

EXERCISE AT EXPIRATION: EXERCISE BY EXCEPTION
---------------------------------------------
OCC Rule 805(d) deems a clearing member to have tendered exercise notices for
the contracts it instructed OCC to exercise, and for every contract in the
money by the threshold in the OCC table "unless the Clearing Member shall have
duly instructed the Corporation [...] to exercise none, or fewer than all".
:func:`exercise_at_expiration` applies that: an instruction governs in both
directions; without one, a contract in the money by the threshold is exercised
and any other expires. In the money is measured against the underlying's
closing price (Rule 805(g)), which the caller supplies as evidence.

There is no late exercise path. OCC stopped accepting late exercise
submissions on 28 May 2024 (Information Memo 54580).

An adjusted deliverable is not a quantity of the underlying, so the
underlying's closing price does not say whether it is in the money; without an
instruction it is INDETERMINATE.

WHAT ONE CONTRACT DELIVERS
--------------------------
:func:`deliverable_for` multiplies the series' stated deliverable, component
by component, by a number of contracts. Nothing here assumes a contract size.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Final, Self

from cannae_kernel.canonical import digest
from cannae_kernel.provenance import Provenance
from pydantic import BaseModel, ConfigDict, Field, model_validator

from lc.occ_rules import OccRuleItem, OccRuleTable
from lc.options import (
    CashComponent,
    DeliverableKind,
    ExerciseStyle,
    OptionRight,
    OptionSeries,
    PositionAccount,
    SharesComponent,
)

__all__ = [
    "DECIMAL_PLACES_ITEM",
    "INCREMENT_ITEM",
    "THRESHOLD_ITEM",
    "AccountAssignment",
    "AssignmentMethod",
    "AssignmentResult",
    "ClosingPrice",
    "ExerciseBasis",
    "ExerciseDecision",
    "ExerciseNotice",
    "ExpirationResult",
    "ExpiringLongPosition",
    "Outcome",
    "ShortPositionEntry",
    "WheelCollisionError",
    "assign_exercises",
    "check_exercise_notice",
    "deliverable_for",
    "exercise_at_expiration",
    "wheel_positions",
]

#: The table item holding the standard assignment increment, in contracts.
INCREMENT_ITEM: Final = "occ.assignment.standard_increment"
#: The table item holding the decimal places the skip interval is carried to.
DECIMAL_PLACES_ITEM: Final = "occ.assignment.decimal_places"
#: The table item holding the exercise-by-exception threshold.
THRESHOLD_ITEM: Final = "occ.expiration.exercise_by_exception_threshold"


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class Outcome(StrEnum):
    DETERMINED = "DETERMINED"
    #: The inputs are inconsistent: for example more exercised than open short.
    REFUSED = "REFUSED"
    #: A rule, source or input needed to determine the result is missing.
    INDETERMINATE = "INDETERMINATE"


# --- the wheel ---------------------------------------------------------------------------------


class WheelCollisionError(ValueError):
    """An increment would land on a position already assigned. The procedure is silent."""


def wheel_positions(
    exercised: int, wheel_size: int, increment: int, start: int, decimal_places: int
) -> tuple[int, ...]:
    """The wheel positions (1 to ``wheel_size``) OCC's standard procedure assigns, in order.

    ``exercised`` is S, ``wheel_size`` is T, ``increment`` is I and ``start`` is the
    random starting position. Pure.
    """
    if not 0 <= exercised <= wheel_size:
        raise ValueError(f"cannot assign {exercised} exercises from {wheel_size} open contracts")
    if exercised == 0:
        return ()
    if exercised == wheel_size:
        return tuple(range(1, wheel_size + 1))
    if not 1 <= start <= wheel_size:
        raise ValueError(f"start position {start} is not on a wheel of {wheel_size}")
    if increment < 1 or decimal_places < 0:
        raise ValueError("the increment and the decimal places must be positive")
    places = Decimal(1).scaleb(-decimal_places)
    t1 = (Decimal(exercised) / increment).quantize(places, ROUND_HALF_UP)
    t1 = t1.to_integral_value(ROUND_CEILING)
    initial = (Decimal(wheel_size) / t1).quantize(places, ROUND_HALF_UP) - increment
    initial = max(initial, Decimal(0))

    assigned: list[int] = []
    taken: set[int] = set()
    position, carried = start, Decimal(0)
    while len(assigned) < exercised:
        take = min(increment, exercised - len(assigned))
        for offset in range(take):
            slot = (position - 1 + offset) % wheel_size + 1
            if slot in taken:
                raise WheelCollisionError(f"position {slot} would be assigned twice")
            taken.add(slot)
            assigned.append(slot)
        interval = initial + carried
        skip = interval.to_integral_value(ROUND_DOWN)
        carried = interval - skip
        position += take + int(skip)
    return tuple(assigned)


# --- assignment --------------------------------------------------------------------------------


class AssignmentMethod(StrEnum):
    #: OCC's standard wheel.
    STANDARD = "STANDARD"
    #: Pro rata, for the classes OCC assigns that way. Not modelled.
    PRO_RATA = "PRO_RATA"


class ShortPositionEntry(_Record):
    """One position account's short position in the series being assigned."""

    account: PositionAccount
    #: OCC's database identification code for the position account: the wheel's order.
    wheel_id: int = Field(ge=0)
    #: Open short contracts, including any written today (Rule 803(a)).
    short_contracts: int = Field(ge=0)
    #: Contracts a confirmed closing purchase will eliminate (Rule 803(b)): not assigned.
    closing_purchase_contracts: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _closing_within_short(self) -> Self:
        if self.closing_purchase_contracts > self.short_contracts:
            raise ValueError("a closing purchase cannot eliminate more than the short position")
        return self

    @property
    def on_the_wheel(self) -> int:
        return self.short_contracts - self.closing_purchase_contracts


class AccountAssignment(_Record):
    account: PositionAccount
    wheel_id: int
    on_the_wheel: int
    assigned_contracts: int
    #: Short contracts that were on the wheel and were not assigned.
    unassigned_contracts: int


class AssignmentResult(_Record):
    """What the wheel assigned in one series, and from what."""

    outcome: Outcome
    reason: str
    series_osi: str
    exercised_contracts: int
    method: AssignmentMethod
    start_position: int
    wheel_contracts: int
    assignments: tuple[AccountAssignment, ...] = ()
    #: The table items the result was computed from.
    criteria: tuple[OccRuleItem, ...] = ()
    table_version: str
    table_items_digest: str

    @property
    def result_digest(self) -> str:
        """``sha256:`` over the canonical form of this result, for a source reference."""
        return digest(self)


def assign_exercises(  # noqa: PLR0911, PLR0913 - one return per refusal; inputs stay explicit
    series: OptionSeries,
    exercised_contracts: int,
    shorts: tuple[ShortPositionEntry, ...],
    *,
    method: AssignmentMethod,
    start_position: int,
    table: OccRuleTable,
) -> AssignmentResult:
    """Assign ``exercised_contracts`` of ``series`` across ``shorts``. Pure.

    Assigned plus unassigned equals the contracts on the wheel, in every account.
    """
    ordered = tuple(sorted(shorts, key=lambda entry: entry.wheel_id))
    wheel_contracts = sum(entry.on_the_wheel for entry in ordered)
    increment, places = table.item(INCREMENT_ITEM), table.item(DECIMAL_PLACES_ITEM)

    def result(
        outcome: Outcome,
        reason: str,
        assignments: tuple[AccountAssignment, ...] = (),
    ) -> AssignmentResult:
        return AssignmentResult(
            outcome=outcome,
            reason=reason,
            series_osi=series.osi_identifier,
            exercised_contracts=exercised_contracts,
            method=method,
            start_position=start_position,
            wheel_contracts=wheel_contracts,
            assignments=assignments,
            criteria=tuple(item for item in (increment, places) if item is not None),
            table_version=table.table_version,
            table_items_digest=table.items_digest,
        )

    if len({entry.wheel_id for entry in ordered}) != len(ordered):
        return result(Outcome.REFUSED, "two position accounts share a wheel identifier")
    if len({entry.account for entry in ordered}) != len(ordered):
        return result(Outcome.REFUSED, "a position account appears twice")
    if not 0 <= exercised_contracts <= wheel_contracts:
        return result(
            Outcome.REFUSED,
            f"{exercised_contracts} contracts exercised against {wheel_contracts} open short",
        )
    if method is AssignmentMethod.PRO_RATA:
        return result(
            Outcome.INDETERMINATE,
            "pro rata assignment is not modelled, and no list of pro rata classes is among "
            "the saved OCC sources",
        )
    for item_id, found in ((INCREMENT_ITEM, increment), (DECIMAL_PLACES_ITEM, places)):
        if found is None:
            return result(Outcome.INDETERMINATE, table.why_absent(item_id))
    assert increment is not None and places is not None
    for found, unit in ((increment, "contracts"), (places, "decimal places")):
        if found.unit != unit or found.decimal_value != found.decimal_value.to_integral_value():
            return result(
                Outcome.INDETERMINATE,
                f"table item {found.item_id} is not a whole number of {unit}",
            )

    try:
        slots = wheel_positions(
            exercised_contracts,
            wheel_contracts,
            int(increment.decimal_value),
            start_position,
            int(places.decimal_value),
        )
    except WheelCollisionError as error:
        return result(Outcome.REFUSED, str(error))
    except ValueError as error:
        return result(Outcome.REFUSED, str(error))

    # Position p on the wheel belongs to the account whose contracts span it.
    owner: list[int] = []
    for index, entry in enumerate(ordered):
        owner.extend([index] * entry.on_the_wheel)
    counts = [0] * len(ordered)
    for slot in slots:
        counts[owner[slot - 1]] += 1
    assignments = tuple(
        AccountAssignment(
            account=entry.account,
            wheel_id=entry.wheel_id,
            on_the_wheel=entry.on_the_wheel,
            assigned_contracts=counts[index],
            unassigned_contracts=entry.on_the_wheel - counts[index],
        )
        for index, entry in enumerate(ordered)
    )
    return result(
        Outcome.DETERMINED,
        f"{exercised_contracts} of {wheel_contracts} contracts assigned by OCC's standard "
        f"procedure from position {start_position}",
        assignments,
    )


# --- exercise ----------------------------------------------------------------------------------


class ExerciseNotice(_Record):
    """A holder's notice to exercise before expiration."""

    series_osi: str
    account: PositionAccount
    contracts: int = Field(gt=0)
    tendered_on: date


def check_exercise_notice(
    notice: ExerciseNotice, series: OptionSeries, long_contracts: int
) -> tuple[Outcome, str]:
    """Whether a notice can be tendered: the series, the style, the date and the position."""
    if notice.series_osi != series.osi_identifier:
        return Outcome.REFUSED, "the notice names another series"
    if notice.tendered_on > series.expiry:
        return Outcome.REFUSED, "the series has expired; there is no late exercise"
    if series.style is ExerciseStyle.EUROPEAN and notice.tendered_on != series.expiry:
        return Outcome.REFUSED, "a European option is exercisable only at expiration"
    if notice.contracts > long_contracts:
        return Outcome.REFUSED, f"{notice.contracts} contracts exceed the {long_contracts} held"
    return Outcome.DETERMINED, "the notice can be tendered"


class ClosingPrice(_Record):
    """The underlying's closing price as Rule 805(g) defines it, as a caller supplies it."""

    security_id: str = Field(min_length=1)
    price: Decimal = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    trading_date: date
    source: str = Field(min_length=1)
    provenance: Provenance


class ExpiringLongPosition(_Record):
    account: PositionAccount
    long_contracts: int = Field(ge=0)
    #: The member's instruction under Rule 805(b): how many to exercise. None means none given.
    exercise_instruction: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _instruction_within_position(self) -> Self:
        if self.exercise_instruction is not None and (
            self.exercise_instruction > self.long_contracts
        ):
            raise ValueError("an instruction cannot exercise more contracts than are held")
        return self


class ExerciseBasis(StrEnum):
    INSTRUCTION = "INSTRUCTION"
    IN_THE_MONEY_BY_THRESHOLD = "IN_THE_MONEY_BY_THRESHOLD"
    NOT_IN_THE_MONEY_BY_THRESHOLD = "NOT_IN_THE_MONEY_BY_THRESHOLD"
    INDETERMINATE = "INDETERMINATE"


class ExerciseDecision(_Record):
    account: PositionAccount
    long_contracts: int
    basis: ExerciseBasis
    reason: str
    #: None when indeterminate.
    exercised_contracts: int | None
    expired_contracts: int | None


class ExpirationResult(_Record):
    outcome: Outcome
    reason: str
    series_osi: str
    decisions: tuple[ExerciseDecision, ...]
    closing_price: ClosingPrice | None
    criteria: tuple[OccRuleItem, ...] = ()
    table_version: str
    table_items_digest: str

    @property
    def exercised_contracts(self) -> int:
        """Contracts exercised across every determined decision."""
        return sum(d.exercised_contracts or 0 for d in self.decisions)

    @property
    def result_digest(self) -> str:
        return digest(self)


def _exception_basis(  # noqa: PLR0911 - one return per missing input, in checking order
    series: OptionSeries,
    closing: ClosingPrice | None,
    threshold: OccRuleItem | None,
    why_no_threshold: str,
) -> tuple[ExerciseBasis, str]:
    """Rule 805(d)(2) for an uninstructed position."""
    if series.deliverable.kind is DeliverableKind.ADJUSTED:
        return ExerciseBasis.INDETERMINATE, (
            "an adjusted deliverable is not a quantity of the underlying, so its closing "
            "price does not say whether the series is in the money"
        )
    if threshold is None:
        return ExerciseBasis.INDETERMINATE, why_no_threshold
    if closing is None:
        return ExerciseBasis.INDETERMINATE, "no closing price for the underlying was supplied"
    if closing.security_id != series.underlying_security_id:
        return ExerciseBasis.INDETERMINATE, "the closing price is for another security"
    if not threshold.unit.startswith(f"{closing.currency} "):
        return ExerciseBasis.INDETERMINATE, (
            f"the threshold is in {threshold.unit} and the closing price in {closing.currency}"
        )
    if series.right is OptionRight.CALL:
        amount = closing.price - series.strike
    else:
        amount = series.strike - closing.price
    cited = f'"{threshold.source.verbatim}" ({threshold.source.url})'
    if amount >= threshold.decimal_value:
        return ExerciseBasis.IN_THE_MONEY_BY_THRESHOLD, f"in the money by {amount}: {cited}"
    return ExerciseBasis.NOT_IN_THE_MONEY_BY_THRESHOLD, (
        f"not in the money by the threshold ({amount}): {cited}"
    )


def exercise_at_expiration(
    series: OptionSeries,
    positions: tuple[ExpiringLongPosition, ...],
    closing_price: ClosingPrice | None,
    table: OccRuleTable,
) -> ExpirationResult:
    """Apply Rule 805(d) to every long position in ``series`` at its expiration. Pure."""
    threshold = table.item(THRESHOLD_ITEM)
    decisions: list[ExerciseDecision] = []
    for position in positions:
        if position.exercise_instruction is not None:
            basis, reason = ExerciseBasis.INSTRUCTION, "the clearing member's instruction governs"
            exercised: int | None = position.exercise_instruction
        else:
            basis, reason = _exception_basis(
                series, closing_price, threshold, table.why_absent(THRESHOLD_ITEM)
            )
            exercised = {
                ExerciseBasis.IN_THE_MONEY_BY_THRESHOLD: position.long_contracts,
                ExerciseBasis.NOT_IN_THE_MONEY_BY_THRESHOLD: 0,
            }.get(basis)
        decisions.append(
            ExerciseDecision(
                account=position.account,
                long_contracts=position.long_contracts,
                basis=basis,
                reason=reason,
                exercised_contracts=exercised,
                expired_contracts=None
                if exercised is None
                else position.long_contracts - exercised,
            )
        )
    undetermined = [d for d in decisions if d.basis is ExerciseBasis.INDETERMINATE]
    if len({p.account for p in positions}) != len(positions):
        outcome, reason = Outcome.REFUSED, "a position account appears twice"
    elif undetermined:
        outcome, reason = Outcome.INDETERMINATE, undetermined[0].reason
    else:
        outcome, reason = Outcome.DETERMINED, "every position determined"
    return ExpirationResult(
        outcome=outcome,
        reason=reason,
        series_osi=series.osi_identifier,
        decisions=tuple(decisions),
        closing_price=closing_price,
        criteria=() if threshold is None else (threshold,),
        table_version=table.table_version,
        table_items_digest=table.items_digest,
    )


# --- what is delivered -------------------------------------------------------------------------


def deliverable_for(
    series: OptionSeries, contracts: int
) -> tuple[SharesComponent | CashComponent, ...]:
    """The series' deliverable for ``contracts`` contracts, component by component."""
    if contracts <= 0:
        raise ValueError("a deliverable is for a positive number of contracts")
    return tuple(
        component.model_copy(update={"quantity": component.quantity * contracts})
        if isinstance(component, SharesComponent)
        else component.model_copy(update={"amount": component.amount * contracts})
        for component in series.deliverable.components
    )
