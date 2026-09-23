"""Form frozen settlement-obligation envelopes from gross-clearing outputs."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Self

from cannae_kernel.actor import ActorRef
from cannae_kernel.canonical import canonical_bytes_of, digest
from cannae_kernel.delivery import DeliveryPattern
from cannae_kernel.envelopes import ClearingTransformation, SettlementObligationEnvelope
from cannae_kernel.finality import FinalityType
from cannae_kernel.ids import ObligationId, OrderId
from cannae_kernel.provenance import Provenance
from cannae_kernel.session import SessionContext
from pydantic import BaseModel, ConfigDict, Field, model_validator

from lc.clearing import GrossClearingResult
from lc.events import LifecycleState
from lc.lifecycle import EventInput, LifecycleRegister, replay, transition

__all__ = [
    "AccountRole",
    "CandidatePathDescriptor",
    "CashLeg",
    "Correction",
    "ExpectedFinality",
    "FormedObligation",
    "LegKind",
    "ObligationPayload",
    "ParticipantAccount",
    "SecuritiesLeg",
    "SourceKind",
    "SourceManifest",
    "SourceReference",
    "form_obligation",
]


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class SourceKind(StrEnum):
    APPROVED_INTENT = "APPROVED_INTENT"
    EXECUTION = "EXECUTION"
    TRADE_CAPTURE = "TRADE_CAPTURE"
    ALLOCATION = "ALLOCATION"
    MATCH_AFFIRMATION = "MATCH_AFFIRMATION"
    CLEARING_TRANSFORMATION = "CLEARING_TRANSFORMATION"


_REQUIRED_SOURCES = frozenset(SourceKind)


class SourceReference(_Record):
    kind: SourceKind
    identifier: str = Field(min_length=1)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class SourceManifest(_Record):
    """All upstream identities and digests required to reproduce an obligation."""

    references: tuple[SourceReference, ...]

    @model_validator(mode="after")
    def _is_complete_and_unambiguous(self) -> Self:
        if frozenset(reference.kind for reference in self.references) != _REQUIRED_SOURCES:
            raise ValueError("source manifest must contain every required upstream source kind")
        identities = {(reference.kind, reference.identifier) for reference in self.references}
        if len(identities) != len(self.references):
            raise ValueError("source manifest kind and identifier pairs must be unique")
        return self


class AccountRole(StrEnum):
    DELIVERER = "DELIVERER"
    RECEIVER = "RECEIVER"


class ParticipantAccount(_Record):
    participant_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    role: AccountRole


class SecuritiesLeg(_Record):
    instrument_id: str = Field(min_length=1)
    quantity: Decimal = Field(gt=0)
    delivering_account_id: str = Field(min_length=1)
    receiving_account_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _accounts_are_distinct(self) -> Self:
        if self.delivering_account_id == self.receiving_account_id:
            raise ValueError("securities leg must name distinct delivering and receiving accounts")
        return self


class CashLeg(_Record):
    principal: Decimal = Field(gt=0)
    accrued: Decimal = Field(ge=0)
    total: Decimal = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    value_date: date
    paying_account_id: str = Field(min_length=1)
    receiving_account_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _total_is_derived(self) -> Self:
        if self.total != self.principal + self.accrued:
            raise ValueError("cash total must equal principal plus accrued")
        if self.paying_account_id == self.receiving_account_id:
            raise ValueError("cash leg must name distinct paying and receiving accounts")
        return self


class CandidatePathDescriptor(_Record):
    """A possible route, not a settlement instruction or submission."""

    path_id: str = Field(min_length=1)
    rail: str = Field(min_length=1)
    securities_route: str = Field(min_length=1)
    cash_route: str = Field(min_length=1)
    delivery_pattern: DeliveryPattern


class LegKind(StrEnum):
    SECURITIES = "SECURITIES"
    CASH = "CASH"


class ExpectedFinality(_Record):
    leg: LegKind
    finality_type: FinalityType
    governing_rule_set: str = Field(min_length=1)

    @model_validator(mode="after")
    def _finality_matches_leg(self) -> Self:
        expected = {
            LegKind.SECURITIES: FinalityType.ASSET_FINAL,
            LegKind.CASH: FinalityType.CASH_FINAL,
        }
        if self.finality_type is not expected[self.leg]:
            raise ValueError("expected finality type does not match its leg")
        return self


class Correction(_Record):
    correction_id: str = Field(min_length=1)
    supersedes_payload_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    reason: str = Field(min_length=1)


class ObligationPayload(_Record):
    """L.C.-owned economics referenced by the frozen kernel envelope."""

    source_manifest: SourceManifest
    securities_leg: SecuritiesLeg
    cash_leg: CashLeg
    participants: tuple[ParticipantAccount, ...]
    delivery_pattern: DeliveryPattern
    candidate_paths: tuple[CandidatePathDescriptor, ...]
    expected_finality: tuple[ExpectedFinality, ...]
    corrections: tuple[Correction, ...]

    @model_validator(mode="after")
    def _payload_is_complete(self) -> Self:
        accounts = {participant.account_id for participant in self.participants}
        required_accounts = {
            self.securities_leg.delivering_account_id,
            self.securities_leg.receiving_account_id,
            self.cash_leg.paying_account_id,
            self.cash_leg.receiving_account_id,
        }
        if not required_accounts.issubset(accounts):
            raise ValueError("every leg account must belong to a named participant")
        if len(accounts) != len(self.participants):
            raise ValueError("participant accounts must be unique")
        if not self.candidate_paths:
            raise ValueError("at least one candidate path descriptor is required")
        if any(path.delivery_pattern is not self.delivery_pattern for path in self.candidate_paths):
            raise ValueError("candidate paths must use the obligation delivery pattern")
        if {item.leg for item in self.expected_finality} != set(LegKind):
            raise ValueError("expected finality must name securities and cash exactly once")
        if len(self.expected_finality) != len(LegKind):
            raise ValueError("expected finality cannot duplicate a leg")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_bytes_of(self)


class FormedObligation(_Record):
    journal: LifecycleRegister
    envelope: SettlementObligationEnvelope
    payload: ObligationPayload


def form_obligation(  # noqa: PLR0913 - every boundary input remains explicit
    *,
    register: LifecycleRegister,
    order_id: OrderId,
    transformation: ClearingTransformation,
    clearing: GrossClearingResult,
    obligation_id: ObligationId,
    session: SessionContext,
    source_manifest: SourceManifest,
    securities_leg: SecuritiesLeg,
    cash_leg: CashLeg,
    participants: tuple[ParticipantAccount, ...],
    delivery_pattern: DeliveryPattern,
    candidate_paths: tuple[CandidatePathDescriptor, ...],
    expected_finality: tuple[ExpectedFinality, ...],
    corrections: tuple[Correction, ...],
    actor: ActorRef,
    clearing_event: EventInput,
    obligation_event: EventInput,
    candidate_event: EventInput,
) -> FormedObligation:
    """Form one gross obligation and record readiness without building instructions."""
    if replay(register.events).orders[order_id].state is not LifecycleState.AFFIRMED:
        raise ValueError("an obligation requires an AFFIRMED trade")
    if transformation.lifecycle_id != register.lifecycle_id:
        raise ValueError("clearing transformation belongs to another lifecycle")
    if transformation.output_digest != digest(clearing):
        raise ValueError("clearing transformation does not bind this clearing result")
    if obligation_id not in clearing.output_obligation_ids:
        raise ValueError("obligation identifier is not a gross-clearing output")
    gross = next(item for item in clearing.obligations if item.obligation_id == obligation_id)
    if securities_leg.quantity != gross.quantity:
        raise ValueError("securities leg quantity does not match gross-clearing output")
    if cash_leg.total != gross.cash:
        raise ValueError("cash leg total does not match gross-clearing output")
    transformation_references = tuple(
        reference
        for reference in source_manifest.references
        if reference.kind is SourceKind.CLEARING_TRANSFORMATION
    )
    if not any(
        reference.digest == digest(transformation) for reference in transformation_references
    ):
        raise ValueError("source manifest does not bind the clearing transformation")

    payload = ObligationPayload(
        source_manifest=source_manifest,
        securities_leg=securities_leg,
        cash_leg=cash_leg,
        participants=participants,
        delivery_pattern=delivery_pattern,
        candidate_paths=candidate_paths,
        expected_finality=expected_finality,
        corrections=corrections,
    )
    envelope = SettlementObligationEnvelope(
        obligation_id=obligation_id,
        lifecycle_id=register.lifecycle_id,
        transformation_digest=digest(transformation),
        session=session,
        provenance=Provenance.POLICY_RESULT,
        payload_digest=digest(payload),
    )
    journal = transition(
        register,
        order_id=order_id,
        target=LifecycleState.CLEARING_TRANSFORMED,
        reason=f"gross clearing transformation formed {obligation_id}",
        actor=actor,
        event=clearing_event,
    )
    journal = transition(
        journal,
        order_id=order_id,
        target=LifecycleState.OBLIGATION_READY,
        reason=f"frozen settlement obligation {obligation_id} emitted",
        actor=actor,
        event=obligation_event,
    )
    journal = transition(
        journal,
        order_id=order_id,
        target=LifecycleState.SETTLEMENT_CANDIDATE_READY,
        reason="candidate path descriptors recorded; no settlement instruction built",
        actor=actor,
        event=candidate_event,
    )
    return FormedObligation(journal=journal, envelope=envelope, payload=payload)
