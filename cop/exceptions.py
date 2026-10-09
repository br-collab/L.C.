"""Read-only exception-register contract and deterministic health derivations."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from cannae_kernel.actor import ActorRef
from cannae_kernel.clocks import EventTimes
from cannae_kernel.disposition import Disposition
from cannae_kernel.provenance import Provenance

from cop.breaks import (
    AtreidesBreakRecord,
    BreakPublicationSource,
    BreaksPublication,
    BreakState,
)

EXCEPTIONS_SOURCE = "demo exception register (no production producer)"


class ExceptionKind(StrEnum):
    BREAK = "Break"
    ESCALATION = "Escalation"
    HOLD = "Hold"
    OVERRIDE = "Override"


class ExceptionStatus(StrEnum):
    OPEN = "Open"
    INVESTIGATING = "Investigating"
    RESOLVED = "Resolved"
    WRITTEN_OFF = "Written off"


@dataclass(frozen=True)
class TrailEntry:
    layer: str
    times: EventTimes
    disposition: Disposition
    status_text: str
    evidence: str
    provenance: Provenance


@dataclass(frozen=True)
class ExceptionRecord:
    exception_id: str
    kind: ExceptionKind
    lifecycle_id: str
    title: str
    root_cause: str
    disposition: Disposition
    status: ExceptionStatus
    status_text: str
    first_layer: str
    first_times: EventTimes
    sla_target: timedelta
    owner: ActorRef | None
    detail: str
    close_condition: str
    authority_uri: str | None
    written_off: bool
    resolved_at: datetime | None
    trail: tuple[TrailEntry, ...]

    @property
    def effective_disposition(self) -> Disposition:
        """The missing-owner rule is fail-closed regardless of producer colour."""
        return Disposition.BLOCK if self.owner is None else self.disposition


@dataclass(frozen=True)
class ExceptionRegister:
    taken_at: datetime
    synthetic: bool
    records: tuple[ExceptionRecord, ...]
    trend: tuple[int, ...] | None = None


class ExceptionSource(Protocol):
    @property
    def source_label(self) -> str: ...

    def register(self) -> ExceptionRegister: ...


class PublishedBreakExceptionSource:
    """Adapt a validated Atreides publication to the display register."""

    def __init__(self, source: BreakPublicationSource) -> None:
        self._source = source

    @property
    def source_label(self) -> str:
        return self._source.source_label

    def register(self) -> ExceptionRegister:
        return publication_to_register(self._source.publication())


@dataclass(frozen=True)
class ExceptionHealth:
    under_1h: int
    one_to_4h: int
    four_to_24h: int
    over_24h: int
    resolved_within_sla_percent: int | None
    written_off: int
    repeat_root_causes: tuple[str, ...]
    trend: tuple[int, ...] | None


_STATUS = {
    BreakState.INTAKE_UNASSIGNED: ExceptionStatus.OPEN,
    BreakState.OPEN: ExceptionStatus.OPEN,
    BreakState.INVESTIGATING: ExceptionStatus.INVESTIGATING,
    BreakState.RESOLVED: ExceptionStatus.RESOLVED,
    BreakState.WRITTEN_OFF: ExceptionStatus.WRITTEN_OFF,
}


def break_to_exception(record: AtreidesBreakRecord, observed_at: datetime) -> ExceptionRecord:
    """Map a validated producer record without inventing owner or timing evidence."""
    times = EventTimes(
        event_time=record.originating_event_at,
        observation_time=observed_at,
        processing_time=observed_at,
    )
    ownership_trail = tuple(
        TrailEntry(
            layer="Atreides break ownership",
            times=EventTimes(
                event_time=change.changed_at,
                observation_time=observed_at,
                processing_time=observed_at,
            ),
            disposition=Disposition.HOLD,
            status_text=f"Owner assigned to {change.assigned_owner.role}",
            evidence=f"Assigned by {change.changed_by.role}",
            provenance=change.provenance,
        )
        for change in record.ownership_history
    )
    action_trail = tuple(
        TrailEntry(
            layer="Atreides break investigation",
            times=EventTimes(
                event_time=action.occurred_at,
                observation_time=observed_at,
                processing_time=observed_at,
            ),
            disposition=Disposition.HOLD,
            status_text=action.action,
            evidence=action.action,
            provenance=Provenance.FACT_SYNTHETIC,
        )
        for action in record.actions
    )
    resolution = record.resolution_evidence
    resolution_trail = (
        ()
        if resolution is None
        else (
            TrailEntry(
                layer="Atreides break closure",
                times=EventTimes(
                    event_time=resolution.recorded_at,
                    observation_time=observed_at,
                    processing_time=observed_at,
                ),
                disposition=Disposition.HOLD,
                status_text=resolution.evidence_kind.value,
                evidence=resolution.evidence_ref,
                provenance=resolution.provenance,
            ),
        )
    )
    return ExceptionRecord(
        exception_id=record.break_id,
        kind=ExceptionKind.BREAK,
        lifecycle_id=record.operation_id,
        title=record.symptom,
        root_cause=record.cause_class,
        disposition=Disposition.HOLD,
        status=_STATUS[record.state],
        status_text=(record.owner_absence_reason or record.state.value),
        first_layer=f"Atreides {record.regime}/{record.leg}",
        first_times=times,
        sla_target=record.sla_target - record.originating_event_at,
        owner=record.owner,
        detail=record.difference,
        close_condition="resolution evidence required",
        authority_uri=record.dsor_record_id,
        written_off=record.state is BreakState.WRITTEN_OFF,
        resolved_at=None if resolution is None else resolution.recorded_at,
        trail=ownership_trail + action_trail + resolution_trail,
    )


def publication_to_register(publication: BreaksPublication) -> ExceptionRegister:
    """Map one complete publication to the display register."""
    return ExceptionRegister(
        taken_at=publication.taken_at,
        synthetic=True,
        records=tuple(
            break_to_exception(record, publication.taken_at) for record in publication.records
        ),
    )


def age(record: ExceptionRecord, now: datetime) -> timedelta:
    """SLA time starts at the producer's first event time, never COP observation."""
    return max(timedelta(), now - record.first_times.event_time)


def health(register: ExceptionRegister, now: datetime) -> ExceptionHealth:
    ages = [age(record, now) for record in register.records]
    resolved = [r for r in register.records if r.status is ExceptionStatus.RESOLVED]
    within = [
        r
        for r in resolved
        if r.resolved_at and r.resolved_at - r.first_times.event_time <= r.sla_target
    ]
    causes = Counter(r.root_cause for r in register.records)
    return ExceptionHealth(
        under_1h=sum(value < timedelta(hours=1) for value in ages),
        one_to_4h=sum(timedelta(hours=1) <= value < timedelta(hours=4) for value in ages),
        four_to_24h=sum(timedelta(hours=4) <= value < timedelta(hours=24) for value in ages),
        over_24h=sum(value >= timedelta(hours=24) for value in ages),
        resolved_within_sla_percent=(
            round(100 * len(within) / len(resolved)) if resolved else None
        ),
        written_off=sum(record.written_off for record in register.records),
        repeat_root_causes=tuple(sorted(cause for cause, count in causes.items() if count > 1)),
        trend=register.trend,
    )
