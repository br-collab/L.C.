"""Settlement obligations from listed options: premium and exercise (ORDER SC-3, WP-4).

EXPERIMENTAL (charter section 18.6). Nothing here is production evidence. L.C.
never submits to a rail: an obligation formed here is handed to Atreides'
acceptance boundary, and nothing more.

WHAT IS FORMED
--------------
Through the existing obligation path (:mod:`lc.obligation`): the frozen kernel
:class:`~cannae_kernel.envelopes.SettlementObligationEnvelope`, binding the
digest of L.C.'s :class:`~lc.obligation.ObligationPayload` and of a kernel
:class:`~cannae_kernel.envelopes.ClearingTransformation`. Every obligation
faces a central counterparty, as a novated position does.

- **Premium** (:func:`form_premium_obligation`). One payment-only obligation
  per side of a novated trade, bound to that side's execution event: the buyer
  pays the premium to the central counterparty, and the central counterparty
  pays it to the seller.
- **Exercise** (:func:`form_exercise_obligations`). For every account that
  exercised and every account that was assigned, the deliverable against the
  strike, bound to the exercise and the assignment it was matched to. Exercise
  and assignment settle as stock obligations at NSCC (the National Securities
  Clearing Corporation), not at OCC (the Options Clearing Corporation); the
  settlement counterparty is the caller's to name. A premium is never part of
  an exercise obligation.

WHICH WAY THINGS MOVE
---------------------
On a call, the deliverable moves from the assigned writer to the exercising
holder and the strike the other way. On a put, the deliverable moves from the
exercising holder to the assigned writer and the strike the other way.

AN ADJUSTED DELIVERABLE
-----------------------
A payload carries one securities leg, so a deliverable of several components
forms one obligation per component, in the order OCC's adjustment states them:

- the first security moves against the strike (delivery versus payment);
- each further security moves free of payment;
- each cash component, such as cash in lieu, is paid on its own;
- with no security at all, the strike is paid on its own too.

The strike for a standard deliverable is the strike times the deliverable's
share quantity. For an adjusted deliverable the strike multiplier is stated in
OCC's adjustment and must be supplied: it is never inferred.

WHAT THE CALLER SUPPLIES
------------------------
The strike currency (a series does not state one), the settlement date (L.C.
holds no business calendar), the settlement session,
each clearing member's settlement accounts, the central counterparty's, the
route, and the lifecycle each position belongs to. L.C. mints no lifecycle
identifier. It does derive each obligation identifier, deterministically, from
the digest of what the obligation was formed from.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Final, Self

from cannae_kernel.canonical import digest, digest_bytes
from cannae_kernel.envelopes import ClearingTransformation, SettlementObligationEnvelope
from cannae_kernel.finality import FinalityType
from cannae_kernel.ids import LifecycleId, ObligationId, encode_ulid
from cannae_kernel.provenance import Provenance
from cannae_kernel.session import SessionContext
from pydantic import BaseModel, ConfigDict, Field, model_validator

from lc.asset_profile import AssetProfile
from lc.obligation import (
    AccountRole,
    CandidatePathDescriptor,
    CashLeg,
    ExpectedFinality,
    LegKind,
    ObligationPayload,
    ParticipantAccount,
    SecuritiesLeg,
    SourceKind,
    SourceManifest,
    SourceReference,
)
from lc.option_clearing import AdmittedNovation
from lc.option_exercise import (
    AssignmentResult,
    ExerciseNotice,
    ExpirationResult,
    Outcome,
    deliverable_for,
)
from lc.options import (
    LISTED_OPTION_EXERCISE,
    LISTED_OPTION_EXERCISE_CASH,
    LISTED_OPTION_EXERCISE_FREE_DELIVERY,
    LISTED_OPTION_PREMIUM,
    DeliverableKind,
    OptionRight,
    OptionSeries,
    PositionAccount,
    SharesComponent,
)

__all__ = [
    "PREMIUM_RULE_SET",
    "FormedOptionObligation",
    "NovationSide",
    "ObligationPurpose",
    "SettlementParty",
    "SettlementRoute",
    "form_exercise_obligations",
    "form_premium_obligation",
]

#: The rule set a premium obligation's transformation names: novation at the CCP.
PREMIUM_RULE_SET: Final = "lc-sc3-option-premium/1.0"
_ZERO: Final = Decimal(0)


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class SettlementParty(_Record):
    """A participant's settlement accounts."""

    participant_id: str = Field(min_length=1)
    securities_account_id: str = Field(min_length=1)
    cash_account_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _accounts_are_distinct(self) -> Self:
        if self.securities_account_id == self.cash_account_id:
            raise ValueError("a party's securities and cash accounts must be distinct")
        return self


class SettlementRoute(_Record):
    """Where an obligation would settle: a candidate, never an instruction."""

    rail: str = Field(min_length=1)
    securities_route: str = Field(min_length=1)
    cash_route: str = Field(min_length=1)
    securities_rule_set: str = Field(min_length=1)
    cash_rule_set: str = Field(min_length=1)


class NovationSide(StrEnum):
    BUYER = "BUYER"
    SELLER = "SELLER"


class ObligationPurpose(StrEnum):
    PREMIUM = "PREMIUM"
    #: The deliverable's first security against the strike.
    DELIVERY_AGAINST_STRIKE = "DELIVERY_AGAINST_STRIKE"
    #: A further security of an adjusted deliverable.
    FREE_DELIVERY = "FREE_DELIVERY"
    #: A cash component of an adjusted deliverable, such as cash in lieu.
    DELIVERABLE_CASH = "DELIVERABLE_CASH"
    #: The strike, where the deliverable holds no security.
    STRIKE = "STRIKE"


class FormedOptionObligation(_Record):
    purpose: ObligationPurpose
    account: PositionAccount | None
    envelope: SettlementObligationEnvelope
    payload: ObligationPayload
    transformation: ClearingTransformation
    asset_profile: AssetProfile


def _obligation_id(formed_at: datetime, seed: str) -> ObligationId:
    milliseconds = int(formed_at.timestamp() * 1000)
    randomness = hashlib.sha256(seed.encode()).digest()[:10]
    return ObligationId(ObligationId.prefix + encode_ulid(milliseconds, randomness))


def _finality(leg: LegKind, route: SettlementRoute) -> ExpectedFinality:
    if leg is LegKind.SECURITIES:
        return ExpectedFinality(
            leg=leg,
            finality_type=FinalityType.ASSET_FINAL,
            governing_rule_set=route.securities_rule_set,
        )
    return ExpectedFinality(
        leg=leg, finality_type=FinalityType.CASH_FINAL, governing_rule_set=route.cash_rule_set
    )


def _participants(
    *pairs: tuple[str, str, AccountRole],
) -> tuple[ParticipantAccount, ...]:
    return tuple(
        ParticipantAccount(participant_id=participant, account_id=account, role=role)
        for participant, account, role in pairs
    )


def _form(  # noqa: PLR0913 - every part of the envelope stays explicit
    *,
    purpose: ObligationPurpose,
    account: PositionAccount | None,
    profile: AssetProfile,
    manifest: tuple[SourceReference, ...],
    transformation: ClearingTransformation,
    securities: SecuritiesLeg | None,
    cash: CashLeg | None,
    participants: tuple[ParticipantAccount, ...],
    route: SettlementRoute,
    session: SessionContext,
    formed_at: datetime,
    seed: str,
) -> FormedOptionObligation:
    pattern = profile.settlement_pattern
    legs = [
        leg for leg, present in ((LegKind.SECURITIES, securities), (LegKind.CASH, cash)) if present
    ]
    payload = ObligationPayload(
        source_manifest=SourceManifest(
            references=(
                *manifest,
                SourceReference(
                    kind=SourceKind.CLEARING_TRANSFORMATION,
                    identifier=transformation.rule_set_version,
                    digest=digest(transformation),
                ),
            )
        ),
        securities_leg=securities,
        cash_leg=cash,
        participants=participants,
        delivery_pattern=pattern,
        candidate_paths=(
            CandidatePathDescriptor(
                path_id=f"{route.rail}:{pattern.value}",
                rail=route.rail,
                securities_route=route.securities_route,
                cash_route=route.cash_route,
                delivery_pattern=pattern,
            ),
        ),
        expected_finality=tuple(_finality(leg, route) for leg in legs),
        corrections=(),
    )
    envelope = SettlementObligationEnvelope(
        obligation_id=_obligation_id(formed_at, seed),
        lifecycle_id=transformation.lifecycle_id,
        transformation_digest=digest(transformation),
        session=session,
        provenance=Provenance.POLICY_RESULT,
        payload_digest=digest(payload),
    )
    return FormedOptionObligation(
        purpose=purpose,
        account=account,
        envelope=envelope,
        payload=payload,
        transformation=transformation,
        asset_profile=profile,
    )


# --- premium -----------------------------------------------------------------------------------


def form_premium_obligation(  # noqa: PLR0913 - every boundary input remains explicit
    novation: AdmittedNovation,
    payload_bytes: bytes,
    *,
    side: NovationSide,
    member: SettlementParty,
    central_counterparty: SettlementParty,
    settlement_date: date,
    route: SettlementRoute,
    session: SessionContext,
    formed_at: datetime,
) -> FormedOptionObligation:
    """The premium obligation for one side of ``novation``, bound to its execution event.

    ``side`` says which party the admitted event attests. ``payload_bytes`` are the
    novation bytes L.C. admitted, re-checked against the event here.
    """
    event, trade = novation.event, novation.trade
    if digest_bytes(payload_bytes) != event.payload_digest:
        raise ValueError("the novation bytes are not the bytes the event attests")
    party = trade.buyer if side is NovationSide.BUYER else trade.seller
    if member.participant_id != party.clearing_member_id:
        raise ValueError(f"the {side.value.lower()} is {party.clearing_member_id}, not the member")
    payer, payee = (
        (member, central_counterparty)
        if side is NovationSide.BUYER
        else (central_counterparty, member)
    )
    transformation = ClearingTransformation(
        lifecycle_id=event.lifecycle_id,
        input_digests=(digest(event),),
        output_digest=event.payload_digest,
        rule_set_version=PREMIUM_RULE_SET,
        provenance=Provenance.POLICY_RESULT,
    )
    cash = CashLeg(
        principal=trade.premium,
        accrued=_ZERO,
        total=trade.premium,
        currency=trade.currency,
        value_date=settlement_date,
        paying_account_id=payer.cash_account_id,
        receiving_account_id=payee.cash_account_id,
    )
    return _form(
        purpose=ObligationPurpose.PREMIUM,
        account=None,
        profile=LISTED_OPTION_PREMIUM,
        manifest=(
            SourceReference(
                kind=SourceKind.APPROVED_INTENT,
                identifier=str(event.intent_id),
                digest=event.intent_digest,
            ),
            SourceReference(
                kind=SourceKind.EXECUTION, identifier=str(event.event_id), digest=digest(event)
            ),
            SourceReference(
                kind=SourceKind.OPTION_NOVATION,
                identifier=trade.novation_id,
                digest=event.payload_digest,
            ),
        ),
        transformation=transformation,
        securities=None,
        cash=cash,
        participants=_participants(
            (payer.participant_id, payer.cash_account_id, AccountRole.DELIVERER),
            (payee.participant_id, payee.cash_account_id, AccountRole.RECEIVER),
        ),
        route=route,
        session=session,
        formed_at=formed_at,
        seed=f"premium:{event.payload_digest}:{side.value}",
    )


# --- exercise ----------------------------------------------------------------------------------


def _exercised_by_account(
    series: OptionSeries, exercise: ExpirationResult | ExerciseNotice
) -> dict[PositionAccount, int]:
    if isinstance(exercise, ExerciseNotice):
        if exercise.series_osi != series.osi_identifier:
            raise ValueError("the exercise notice names another series")
        return {exercise.account: exercise.contracts}
    if exercise.series_osi != series.osi_identifier:
        raise ValueError("the expiration result names another series")
    if exercise.outcome is not Outcome.DETERMINED:
        raise ValueError(f"the expiration result is {exercise.outcome.value}: {exercise.reason}")
    return {d.account: d.exercised_contracts for d in exercise.decisions if d.exercised_contracts}


def _strike_per_contract(series: OptionSeries, strike_multiplier: Decimal | None) -> Decimal:
    if series.deliverable.kind is DeliverableKind.STANDARD:
        (component,) = series.deliverable.components
        assert isinstance(component, SharesComponent)
        if strike_multiplier is not None and strike_multiplier != component.quantity:
            raise ValueError("a standard deliverable's strike multiplier is its share quantity")
        return series.strike * component.quantity
    if strike_multiplier is None:
        raise ValueError(
            "an adjusted deliverable's strike multiplier is stated in OCC's adjustment "
            "and must be supplied"
        )
    return series.strike * strike_multiplier


def form_exercise_obligations(  # noqa: PLR0913 - every boundary input remains explicit
    series: OptionSeries,
    *,
    exercise: ExpirationResult | ExerciseNotice,
    assignment: AssignmentResult,
    parties: tuple[SettlementParty, ...],
    settlement_counterparty: SettlementParty,
    lifecycles: dict[PositionAccount, LifecycleId],
    strike_currency: str,
    settlement_date: date,
    route: SettlementRoute,
    session: SessionContext,
    formed_at: datetime,
    strike_multiplier: Decimal | None = None,
) -> tuple[FormedOptionObligation, ...]:
    """Every obligation an exercise and its assignment form, holder side then writer side.

    ``parties`` holds each clearing member's settlement accounts, keyed by participant
    identifier. ``lifecycles`` names the lifecycle each position account belongs to.
    """
    exercised = _exercised_by_account(series, exercise)
    if assignment.outcome is not Outcome.DETERMINED:
        raise ValueError(f"the assignment is {assignment.outcome.value}: {assignment.reason}")
    if assignment.series_osi != series.osi_identifier:
        raise ValueError("the assignment names another series")
    if sum(exercised.values()) != assignment.exercised_contracts:
        raise ValueError(
            f"{sum(exercised.values())} contracts exercised, but the assignment is for "
            f"{assignment.exercised_contracts}"
        )
    assigned = {
        a.account: a.assigned_contracts for a in assignment.assignments if a.assigned_contracts
    }
    by_member = {party.participant_id: party for party in parties}
    strike_each = _strike_per_contract(series, strike_multiplier)

    exercise_digest = (
        exercise.result_digest if isinstance(exercise, ExpirationResult) else digest(exercise)
    )
    manifest = (
        SourceReference(
            kind=SourceKind.OPTION_EXERCISE,
            identifier=series.osi_identifier,
            digest=exercise_digest,
        ),
        SourceReference(
            kind=SourceKind.OPTION_ASSIGNMENT,
            identifier=series.osi_identifier,
            digest=assignment.result_digest,
        ),
    )
    # The holder receives the deliverable on a call and delivers it on a put.
    holder_receives = series.right is OptionRight.CALL
    formed: list[FormedOptionObligation] = []
    for holder, sides in ((True, exercised), (False, assigned)):
        receives_deliverable = holder is holder_receives
        for account, contracts in sorted(sides.items(), key=lambda item: item[0].account_id):
            member = by_member.get(account.clearing_member_id)
            if member is None:
                raise ValueError(f"no settlement accounts for {account.clearing_member_id}")
            lifecycle = lifecycles.get(account)
            if lifecycle is None:
                raise ValueError(f"no lifecycle for account {account.account_id}")
            transformation = ClearingTransformation(
                lifecycle_id=lifecycle,
                input_digests=(exercise_digest,),
                output_digest=assignment.result_digest,
                rule_set_version=f"occ-standard-assignment/{assignment.table_version}",
                provenance=Provenance.POLICY_RESULT,
            )
            formed.extend(
                _account_obligations(
                    series,
                    account=account,
                    contracts=contracts,
                    receives_deliverable=receives_deliverable,
                    member=member,
                    counterparty=settlement_counterparty,
                    strike=strike_each * contracts,
                    strike_currency=strike_currency,
                    manifest=manifest,
                    transformation=transformation,
                    settlement_date=settlement_date,
                    route=route,
                    session=session,
                    formed_at=formed_at,
                    seed=f"exercise:{assignment.result_digest}:{'holder' if holder else 'writer'}",
                )
            )
    return tuple(formed)


def _account_obligations(  # noqa: PLR0913 - every part of the obligation stays explicit
    series: OptionSeries,
    *,
    account: PositionAccount,
    contracts: int,
    receives_deliverable: bool,
    member: SettlementParty,
    counterparty: SettlementParty,
    strike: Decimal,
    strike_currency: str,
    manifest: tuple[SourceReference, ...],
    transformation: ClearingTransformation,
    settlement_date: date,
    route: SettlementRoute,
    session: SessionContext,
    formed_at: datetime,
    seed: str,
) -> list[FormedOptionObligation]:
    # Deliverable flows from ``giver`` to ``taker``; the strike flows back.
    giver, taker = (counterparty, member) if receives_deliverable else (member, counterparty)
    components = deliverable_for(series, contracts)
    strike_unpaired = not any(isinstance(c, SharesComponent) for c in components)

    def cash_leg(
        amount: Decimal, currency: str, payer: SettlementParty, payee: SettlementParty
    ) -> CashLeg:
        return CashLeg(
            principal=amount,
            accrued=_ZERO,
            total=amount,
            currency=currency,
            value_date=settlement_date,
            paying_account_id=payer.cash_account_id,
            receiving_account_id=payee.cash_account_id,
        )

    def obligation(
        index: int,
        purpose: ObligationPurpose,
        profile: AssetProfile,
        securities: SecuritiesLeg | None,
        cash: CashLeg | None,
    ) -> FormedOptionObligation:
        pairs: list[tuple[str, str, AccountRole]] = []
        if securities is not None:
            pairs += [
                (giver.participant_id, giver.securities_account_id, AccountRole.DELIVERER),
                (taker.participant_id, taker.securities_account_id, AccountRole.RECEIVER),
            ]
        if cash is not None:
            payer = giver if cash.paying_account_id == giver.cash_account_id else taker
            payee = taker if payer is giver else giver
            pairs += [
                (payer.participant_id, payer.cash_account_id, AccountRole.DELIVERER),
                (payee.participant_id, payee.cash_account_id, AccountRole.RECEIVER),
            ]
        return _form(
            purpose=purpose,
            account=account,
            profile=profile,
            manifest=manifest,
            transformation=transformation,
            securities=securities,
            cash=cash,
            participants=_participants(*pairs),
            route=route,
            session=session,
            formed_at=formed_at,
            seed=f"{seed}:{account.clearing_member_id}:{account.account_id}:{index}",
        )

    formed: list[FormedOptionObligation] = []
    first_security = True
    for index, component in enumerate(components):
        if isinstance(component, SharesComponent):
            securities = SecuritiesLeg(
                instrument_id=component.security_id,
                quantity=component.quantity,
                delivering_account_id=giver.securities_account_id,
                receiving_account_id=taker.securities_account_id,
            )
            if first_security:
                formed.append(
                    obligation(
                        index,
                        ObligationPurpose.DELIVERY_AGAINST_STRIKE,
                        LISTED_OPTION_EXERCISE,
                        securities,
                        cash_leg(strike, strike_currency, taker, giver),
                    )
                )
                first_security = False
            else:
                formed.append(
                    obligation(
                        index,
                        ObligationPurpose.FREE_DELIVERY,
                        LISTED_OPTION_EXERCISE_FREE_DELIVERY,
                        securities,
                        None,
                    )
                )
        else:
            formed.append(
                obligation(
                    index,
                    ObligationPurpose.DELIVERABLE_CASH,
                    LISTED_OPTION_EXERCISE_CASH,
                    None,
                    cash_leg(component.amount, component.currency, giver, taker),
                )
            )
    if strike_unpaired:
        formed.append(
            obligation(
                len(components),
                ObligationPurpose.STRIKE,
                LISTED_OPTION_EXERCISE_CASH,
                None,
                cash_leg(strike, strike_currency, taker, giver),
            )
        )
    return formed
