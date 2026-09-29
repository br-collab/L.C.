"""Deterministic scenario identity for a complete cross-domain lifecycle.

The scenario owns the replay inputs and mints one ``LifecycleId``.  Domain
identifiers are linked to that lifecycle; they are never replaced by it.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Self

from cannae_kernel.canonical import canonical_bytes, digest
from cannae_kernel.ids import LifecycleId, ScenarioId, new_lifecycle_id
from cannae_kernel.session import BusinessDate
from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "ForeignLifecycleIdError",
    "IdentifierKind",
    "ScenarioRecord",
    "start_scenario",
]

_SHA_LENGTH = 40


class ForeignLifecycleIdError(ValueError):
    """A crossing named a lifecycle other than the scenario's one identity."""


class IdentifierKind(StrEnum):
    INTENT = "intent_id"
    APPROVAL = "approval_id"
    C2_TASK = "c2_task_id"
    C2_HANDOFF = "c2_handoff_id"
    PARENT_ORDER = "parent_order_id"
    CHILD_ORDER = "child_order_id"
    EXECUTION = "execution_id"
    FILL = "fill_id"
    ALLOCATION = "allocation_id"
    CLEARING_OBLIGATION = "clearing_obligation_id"
    SECURITIES_LEG = "securities_leg_id"
    CASH_LEG = "cash_leg_id"
    SETTLEMENT_INSTRUCTION = "settlement_instruction_id"
    RAIL_ACKNOWLEDGEMENT = "rail_acknowledgement_id"
    RECONCILIATION = "reconciliation_id"
    BREAK = "break_id"


class ScenarioRecord(BaseModel):
    """Canonical replay input, including every version that can move an outcome."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    scenario_id: ScenarioId
    seed: int = Field(ge=0, le=(2**53) - 1)
    lifecycle_id: LifecycleId
    pinned_commits: dict[str, str]
    policy_versions: dict[str, str]
    business_date: BusinessDate
    linked_identifiers: dict[IdentifierKind, tuple[str, ...]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _links_are_domain_identifiers(self) -> Self:
        values = [value for group in self.linked_identifiers.values() for value in group]
        if any(not value.strip() for value in values):
            raise ValueError("linked domain identifiers must be non-empty")
        if str(self.lifecycle_id) in values:
            raise ValueError(
                "domain identifiers are linked to lifecycle_id, never collapsed into it"
            )
        if len(values) != len(set(values)):
            raise ValueError("a domain identifier may be linked only once")
        if not self.pinned_commits or any(
            len(commit) != _SHA_LENGTH or any(c not in "0123456789abcdef" for c in commit)
            for commit in self.pinned_commits.values()
        ):
            raise ValueError("every pinned commit must be a full lower-case 40-character SHA")
        if not self.policy_versions or any(
            not key.strip() or not value.strip() for key, value in self.policy_versions.items()
        ):
            raise ValueError("policy versions must be named and non-empty")
        return self

    def require_lifecycle(self, lifecycle_id: LifecycleId) -> None:
        if lifecycle_id != self.lifecycle_id:
            raise ForeignLifecycleIdError(
                f"FOREIGN_LIFECYCLE_ID: expected {self.lifecycle_id}, received {lifecycle_id}"
            )

    def link(self, kind: IdentifierKind, identifier: str) -> ScenarioRecord:
        links = {key: tuple(values) for key, values in self.linked_identifiers.items()}
        links[kind] = (*links.get(kind, ()), identifier)
        values = self.model_dump(mode="python")
        values["linked_identifiers"] = links
        return ScenarioRecord.model_validate(values, strict=True)

    def to_canonical_bytes(self) -> bytes:
        return canonical_bytes(self)

    @property
    def canonical_digest(self) -> str:
        return digest(self)


def _lifecycle_clock(scenario_id: ScenarioId) -> datetime:
    """A stable clock solely for deterministic identifier minting, not a business date."""
    raw = hashlib.sha256(str(scenario_id).encode()).digest()
    milliseconds = int.from_bytes(raw[:6], "big") % (100 * 365 * 24 * 60 * 60 * 1000)
    return datetime(2000, 1, 1, tzinfo=UTC) + timedelta(milliseconds=milliseconds)


def start_scenario(
    *,
    scenario_id: ScenarioId,
    seed: int,
    pinned_commits: dict[str, str],
    policy_versions: dict[str, str],
    business_date: BusinessDate,
) -> ScenarioRecord:
    """Mint the scenario's one lifecycle deterministically from identity and seed."""
    entropy_material = hashlib.sha256(f"{scenario_id}:{seed}".encode()).digest()

    def clock() -> datetime:
        return _lifecycle_clock(scenario_id)

    def entropy(size: int) -> bytes:
        return entropy_material[:size]

    return ScenarioRecord(
        scenario_id=scenario_id,
        seed=seed,
        lifecycle_id=new_lifecycle_id(clock=clock, entropy=entropy),
        pinned_commits=pinned_commits,
        policy_versions=policy_versions,
        business_date=business_date,
    )
