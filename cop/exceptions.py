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
    def register(self) -> ExceptionRegister: ...


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
