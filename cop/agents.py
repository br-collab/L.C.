"""Atreides agent-activation source for the Agents panel (W3 § WP-A3).

Reads the Phase A activation snapshot that Atreides publishes. C2 = Command and
Control. CAOM = Consolidated Authority Operating Mode. COP = Common Operating
Picture.

This module parses a document; it does not import Atreides
-----------------------------------------------------------
``cop`` never imports a domain package, and ``tests/test_import_boundaries.py``
enforces it. So the shapes below are this repository's own reading of the
published document, exactly as :mod:`cop.aureon` is its own reading of Aureon's
snapshot. If Atreides changes the document, that shows up here as a malformed
source rather than as a silently different meaning.

Absence arrives as a value, and stays one
-----------------------------------------
Every optional field in the document is ``{"state": "recorded", "value": …}`` or
``{"state": "absent", "kind": …, "reason": …}``. Parsing it back into the
kernel's :class:`~cannae_kernel.absence.Recorded` and
:class:`~cannae_kernel.absence.Absent` is the whole point of the panel: an
absence flattened to ``None`` on the way in would leave the surface with nothing
truthful to show, and the surface would then pick the cheerful reading. That is
the defect class this panel exists to pre-empt on a new surface, so the parsing
refuses to lose it.

A source that changed shape is not trusted field by field
---------------------------------------------------------
:func:`parse_snapshot` refuses an unknown ``schema_version`` outright rather than
reading a renamed field as absent. An absence the reader invented is worse than
an error, because it looks like a fact about the agents instead of a fact about
the reader.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Protocol

import httpx
from cannae_kernel.absence import Absent, Recorded
from cannae_kernel.disposition import Disposition
from cannae_kernel.provenance import Provenance
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cop.http import get_bytes
from cop.observation import SourceFieldAbsentError, SourceMalformedError
from cop.settings import HTTP_TIMEOUT_SECONDS, PRODUCT_NAME

#: The document version this reader understands. Atreides bumps its own constant
#: whenever a field is added, removed or changes meaning.
SUPPORTED_SCHEMA_VERSION = 1


class RefusalView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", strict=True)

    code: str
    detail: str
    observed_at: datetime


class AgentView(BaseModel):
    """One agent as the document describes it.

    ``extra="ignore"``: a field Atreides adds ahead of this reader is not a
    reason to refuse the whole document. A field that *changes type* is, and
    strict mode makes that a validation error rather than a coercion.
    """

    model_config = ConfigDict(frozen=True, extra="ignore", strict=True)

    agent_id: str = Field(min_length=1)
    tier: str = Field(min_length=1)
    role: str = Field(min_length=1)
    up: bool
    expects_refusal: bool
    """True for the standing lateral-handoff probe, whose healthy state is to be
    refused. A reader ignoring this would report a working refusal as a failure."""
    disposition: Disposition
    stopped_reason: Recorded[str] | Absent
    last_summary: Recorded[str] | Absent
    last_observed_at: Recorded[datetime] | Absent
    last_provenance: Recorded[Provenance] | Absent
    last_handoff_basis: Recorded[str] | Absent
    last_refusal: Recorded[RefusalView] | Absent
    recommendations: int = Field(ge=0)
    refusals: int = Field(ge=0)


class AgentsSnapshot(BaseModel):
    """The whole activation document."""

    model_config = ConfigDict(frozen=True, extra="ignore", strict=True)

    schema_version: int
    phase: str = Field(min_length=1)
    synthetic: bool
    """Phase A runs against a synthetic flow. Shown on the panel so no reader
    mistakes these for venue facts."""
    taken_at: datetime
    tick: Recorded[int] | Absent
    last_tick_at: Recorded[datetime] | Absent
    halted: bool
    halt_reason: Recorded[str] | Absent
    disposition: Disposition
    agents: tuple[AgentView, ...]


class AgentsSource(Protocol):
    def snapshot(self) -> AgentsSnapshot: ...


def parse_snapshot(raw: bytes) -> AgentsSnapshot:
    """Parse the activation document from its bytes, or raise a typed ``SourceError``.

    From bytes rather than from a decoded object: these models are strict, and in
    JSON mode a datetime may be an ISO string and an enum member its name, which
    is how the document is written. Validating a pre-decoded dictionary would
    apply Python-mode rules to values that never were Python objects, and every
    timestamp in the document would be refused.

    The version is checked first, against the undecoded body, so an unknown shape
    is reported as an unknown version rather than as thirty validation errors.
    """
    try:
        data = json.loads(raw)
    except ValueError:
        raise SourceMalformedError("Activation snapshot is not valid JSON") from None
    if not isinstance(data, dict):
        raise SourceMalformedError("Activation snapshot is not a JSON object")
    version = data.get("schema_version")
    if version is None:
        raise SourceFieldAbsentError("Activation snapshot has no schema_version")
    if version != SUPPORTED_SCHEMA_VERSION:
        raise SourceMalformedError(
            f"Activation snapshot is schema version {version!r}; this reader understands "
            f"only {SUPPORTED_SCHEMA_VERSION}"
        )
    try:
        return AgentsSnapshot.model_validate_json(raw)
    except ValidationError as exc:
        raise SourceMalformedError(
            f"Unexpected activation snapshot shape ({exc.error_count()} problems)"
        ) from None


class HttpxAgentsClient:
    """Reads the activation snapshot from ``url``, which the caller must supply.

    No default: a client built without one would have to invent an address, and
    the failure that produced would read like an outage instead of like an
    unconfigured source.
    """

    def __init__(self, url: str, http: httpx.Client | None = None) -> None:
        self._http = http or httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)
        self._url = url

    def snapshot(self) -> AgentsSnapshot:
        raw = get_bytes(
            self._http,
            self._url,
            headers={"Accept": "application/json", "User-Agent": PRODUCT_NAME},
        )
        return parse_snapshot(raw)
