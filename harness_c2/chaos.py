"""Seeded four-clock fault injection without future information."""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from enum import StrEnum

from cannae_kernel.canonical import canonical_bytes_of, digest_bytes
from cannae_kernel.clocks import EventTimes
from cannae_kernel.disposition import Disposition
from pydantic import BaseModel, ConfigDict, Field

from harness_c2.scenario import ScenarioRecord
from harness_c2.transcript import (
    CrossingArtifactEnvelope,
    CrossingTranscript,
    record_crossing,
)

__all__ = [
    "ChaosEvent",
    "ChaosPlan",
    "ChaosResult",
    "FutureObservationError",
    "InjectionCondition",
    "InjectionKind",
    "apply_injections",
    "authority_reading",
    "generate_plan",
    "observe",
    "record_injection_conditions",
]


class _Record(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )


class InjectionKind(StrEnum):
    DELAY = "delay"
    PARTITION = "partition"
    REORDER = "reorder"
    OUTAGE = "outage"


class InjectionCondition(_Record):
    condition_id: str = Field(min_length=1)
    kind: InjectionKind
    event_class: str = Field(min_length=1)
    duration_seconds: int | None = Field(default=None, gt=0)
    source: str | None = None

    def canonical_bytes(self) -> bytes:
        return canonical_bytes_of(self)


class ChaosPlan(_Record):
    seed: int = Field(ge=0, le=(2**53) - 1)
    conditions: tuple[InjectionCondition, ...]

    def canonical_bytes(self) -> bytes:
        return canonical_bytes_of(self)


class ChaosEvent(_Record):
    event_id: str = Field(min_length=1)
    event_class: str = Field(min_length=1)
    source: str = Field(min_length=1)
    payload: bytes = Field(min_length=1)
    times: EventTimes


class ChaosResult(_Record):
    plan: ChaosPlan
    events: tuple[ChaosEvent, ...]
    absent_sources: tuple[str, ...]

    def canonical_bytes(self) -> bytes:
        return self.model_dump_json().encode("utf-8")


class FutureObservationError(RuntimeError):
    """A consumer attempted to read an event before observation time."""


def generate_plan(seed: int) -> ChaosPlan:
    rng = random.Random(seed)
    return ChaosPlan(
        seed=seed,
        conditions=(
            InjectionCondition(
                condition_id="delay-market-evidence",
                kind=InjectionKind.DELAY,
                event_class="market_evidence",
                duration_seconds=rng.randint(360, 900),
            ),
            InjectionCondition(
                condition_id="partition-authority",
                kind=InjectionKind.PARTITION,
                event_class="authority",
                duration_seconds=rng.randint(60, 300),
            ),
            InjectionCondition(
                condition_id="reorder-executions",
                kind=InjectionKind.REORDER,
                event_class="execution",
            ),
            InjectionCondition(
                condition_id="outage-reference-data",
                kind=InjectionKind.OUTAGE,
                event_class="reference_data",
                source="reference-data",
            ),
        ),
    )


def _shift(event: ChaosEvent, duration: timedelta) -> ChaosEvent:
    values = event.times.model_dump(mode="python")
    for field in ("observation_time", "processing_time", "decision_time"):
        if values[field] is not None:
            values[field] += duration
    return event.model_copy(update={"times": EventTimes.model_validate(values, strict=True)})


def apply_injections(events: tuple[ChaosEvent, ...], plan: ChaosPlan) -> ChaosResult:
    """Apply a plan deterministically; outage is explicit absence, never an empty success."""
    current = list(events)
    absent: list[str] = []
    for condition in plan.conditions:
        if condition.kind in {InjectionKind.DELAY, InjectionKind.PARTITION}:
            if condition.duration_seconds is None:
                raise ValueError(f"{condition.kind.value} requires a duration")
            duration = timedelta(seconds=condition.duration_seconds)
            current = [
                _shift(event, duration) if event.event_class == condition.event_class else event
                for event in current
            ]
        elif condition.kind is InjectionKind.REORDER:
            positions = [
                index
                for index, event in enumerate(current)
                if event.event_class == condition.event_class
            ]
            reordered = [current[index] for index in reversed(positions)]
            for index, event in zip(positions, reordered, strict=True):
                current[index] = event
        else:
            removed = [event for event in current if event.event_class == condition.event_class]
            current = [event for event in current if event.event_class != condition.event_class]
            if removed:
                absent.append(condition.source or condition.event_class)
    return ChaosResult(plan=plan, events=tuple(current), absent_sources=tuple(sorted(set(absent))))


def observe(event: ChaosEvent, *, at: datetime) -> bytes:
    if at.tzinfo is None:
        raise ValueError("observation clock must be timezone-aware")
    if at < event.times.observation_time:
        raise FutureObservationError(
            f"FUTURE_OBSERVATION: {event.event_id} is observable at "
            f"{event.times.observation_time.isoformat()}, not {at.isoformat()}"
        )
    return event.payload


def authority_reading(events: tuple[ChaosEvent, ...], *, at: datetime) -> tuple[Disposition, str]:
    authority = next((event for event in events if event.event_class == "authority"), None)
    if authority is None or at < authority.times.observation_time:
        return Disposition.INDETERMINATE, "authority evidence is not observable yet"
    observe(authority, at=at)
    return Disposition.PASS, "authority evidence observed"


def record_injection_conditions(
    scenario: ScenarioRecord,
    transcript: CrossingTranscript,
    plan: ChaosPlan,
    *,
    recorded_at: EventTimes,
) -> CrossingTranscript:
    """Make every injected condition part of the replayable transcript."""
    result = transcript
    for condition in plan.conditions:
        payload = condition.canonical_bytes()
        envelope = CrossingArtifactEnvelope(
            artifact_id=condition.condition_id,
            lifecycle_id=scenario.lifecycle_id,
            artifact_kind="injection_condition",
            payload_digest=digest_bytes(payload),
        )
        result = result.append(
            record_crossing(
                scenario=scenario,
                producer="harness_c2",
                consumer="harness_c2",
                lifecycle_id=scenario.lifecycle_id,
                envelope=envelope,
                payload_bytes=payload,
                producer_asserted_digest=digest_bytes(payload),
                times=recorded_at,
                accepted_disposition=Disposition.PASS,
                accepted_reason=f"recorded {condition.kind.value} injection condition",
            )
        )
    return result
