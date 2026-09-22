"""Read-only contracts for governance, controls/compliance and risk limits."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from cannae_kernel.actor import ActorRef
from cannae_kernel.clocks import EventTimes
from cannae_kernel.disposition import Disposition
from cannae_kernel.provenance import Provenance

GOVERNANCE_SOURCE = "DSOR authority-decision feed (not published)"
CONTROLS_SOURCE = "Aureon/Verana controls aggregate (not published)"
RISK_SOURCE = "Atreides exposure and Aureon Kaladan limits (not published)"
HOLD_UTILISATION = Decimal(80)
BLOCK_UTILISATION = Decimal(100)


class GovernanceKind(StrEnum):
    DECISION = "Authority decision"
    OVERRIDE = "Override"
    HALT_ENGAGED = "Halt engaged"
    HALT_RELEASED = "Halt released"
    DOCTRINE_CHANGE = "Doctrine version change"
    DEPLOY = "Deploy"


@dataclass(frozen=True)
class GovernanceEvent:
    event_id: str
    kind: GovernanceKind
    summary: str
    actor: ActorRef
    times: EventTimes
    doctrine_version: str
    evidence: tuple[str, ...]
    disposition: Disposition
    provenance: Provenance
    source_uri: str


class GovernanceSource(Protocol):
    def governance(self) -> tuple[GovernanceEvent, ...]: ...


@dataclass(frozen=True)
class ControlRecord:
    control_id: str
    name: str
    category: str
    disposition: Disposition
    status_text: str
    test_times: EventTimes | None
    evidence: tuple[str, ...]
    regulatory_mappings: tuple[str, ...]
    provenance: Provenance

    @property
    def effective_disposition(self) -> Disposition:
        """No test evidence is indeterminate, regardless of a claimed status."""
        if self.test_times is None or not self.evidence:
            return Disposition.INDETERMINATE
        return self.disposition


class ControlsSource(Protocol):
    def controls(self) -> tuple[ControlRecord, ...]: ...


@dataclass(frozen=True)
class RiskLimit:
    risk_id: str
    name: str
    exposure: Decimal
    limit: Decimal
    unit: str
    times: EventTimes
    provenance: Provenance
    source_uri: str

    @property
    def utilisation(self) -> Decimal:
        return self.exposure / self.limit * 100

    @property
    def disposition(self) -> Disposition:
        utilisation = self.utilisation
        if utilisation >= BLOCK_UTILISATION:
            return Disposition.BLOCK
        if utilisation >= HOLD_UTILISATION:
            return Disposition.HOLD
        return Disposition.PASS


class RiskSource(Protocol):
    def risks(self) -> tuple[RiskLimit, ...]: ...
