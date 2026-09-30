"""Deterministic replay of a recorded crossing transcript.

Replay consumes evidence already recorded by the experiment.  It never calls a
business domain or an emulator.  Each crossing is re-bound to the exact scenario
record, checked for the one lifecycle, and re-digested from its recorded bytes.
"""

from __future__ import annotations

from cannae_kernel.canonical import digest_bytes
from cannae_kernel.disposition import Disposition
from pydantic import BaseModel, ConfigDict, Field

from harness_c2.scenario import ScenarioRecord
from harness_c2.transcript import CrossingTranscript

__all__ = ["ReplayGate", "ReplayResult", "replay_transcript"]


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class ReplayGate(_Record):
    """One recomputed replay check, in transcript order."""

    name: str = Field(min_length=1)
    disposition: Disposition
    reason: str = Field(min_length=1)


class ReplayResult(_Record):
    """The ordered result of replaying every recorded crossing."""

    gates: tuple[ReplayGate, ...]
    first_changed_gate: str | None = None

    def to_bytes(self) -> bytes:
        return self.model_dump_json().encode("utf-8")


def replay_transcript(scenario: ScenarioRecord, transcript: CrossingTranscript) -> ReplayResult:
    """Recompute transcript integrity gates without re-running an emulator.

    A gate whose inputs are intact reproduces the disposition and reason that the
    consumer recorded.  A changed scenario, foreign lifecycle, or changed payload
    moves the first affected gate to ``BLOCK`` and names the reason.
    """
    expected_scenario = scenario.canonical_digest
    gates: list[ReplayGate] = []
    first_changed: str | None = None
    for index, crossing in enumerate(transcript.crossings):
        prefix = f"crossing[{index}]"
        if crossing.scenario_digest != expected_scenario:
            name = f"{prefix}.scenario_record"
            gate = ReplayGate(
                name=name,
                disposition=Disposition.BLOCK,
                reason="SCENARIO_RECORD_DIGEST_MISMATCH",
            )
        elif crossing.lifecycle_id != scenario.lifecycle_id:
            name = f"{prefix}.lifecycle"
            gate = ReplayGate(
                name=name,
                disposition=Disposition.BLOCK,
                reason="FOREIGN_LIFECYCLE_ID",
            )
        elif digest_bytes(crossing.payload_bytes) != crossing.producer_asserted_digest:
            name = f"{prefix}.payload_digest"
            gate = ReplayGate(
                name=name,
                disposition=Disposition.BLOCK,
                reason="DIGEST_MISMATCH",
            )
        else:
            gate = ReplayGate(
                name=f"{prefix}.consumer_result",
                disposition=crossing.disposition,
                reason=crossing.reason,
            )
        if gate.disposition != crossing.disposition or gate.reason != crossing.reason:
            first_changed = first_changed or gate.name
        gates.append(gate)
    return ReplayResult(gates=tuple(gates), first_changed_gate=first_changed)
