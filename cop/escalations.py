"""The escalation queue: C2 packages awaiting human authority (COP-1 WP-2, panel 9).

COP = Common Operating Picture. C2 = Command and Control.

This module parses a document; it does not import the harness
-------------------------------------------------------------
The order names `harness_c2` as the source, and this page **cannot import it** —
`tests/test_import_boundaries.py` has forbidden `cop` from importing
`harness_c2` since before either existed. The boundary is the older and stronger
rule, and it is there so the surface cannot reach into the thing it displays.

So the shapes below are this package's own reading of a published escalation
document, exactly as :mod:`cop.agents` is its own reading of the Atreides
activation snapshot. If the harness changes the document, that shows up here as a
malformed source rather than as a silently different meaning.

Oldest first, and the queue says what each is waiting for
----------------------------------------------------------
A queue sorted newest-first hides its own worst case. The escalation that has
been waiting longest is the one most likely to have been forgotten, and it
belongs at the top where somebody trips over it.

**The page displays; it does not resolve.** There is no acknowledge control, no
assign, no dismiss. Acting on an escalation happens where authority lives
(COP-1 rule 4), and a button here would be the first step to the page deciding
something.

What the packet may not contain, restated at the boundary
----------------------------------------------------------
The harness refuses to put a recommendation, a resolution, a severity or a
recipient in a packet, because C2 does not interpret doctrine. This reader
**forbids those fields too**, with `extra="forbid"` and a test: a document that
grew one would be a harness that had started deciding, and the surface should
refuse it rather than render it.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Protocol

import httpx
from cannae_kernel.disposition import Disposition
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cop.http import get_bytes
from cop.observation import SourceFieldAbsentError, SourceMalformedError
from cop.settings import HTTP_TIMEOUT_SECONDS, PRODUCT_NAME

SUPPORTED_SCHEMA_VERSION = 1

#: Fields a packet may never carry. The harness will not produce them; a
#: document that did would be a harness that had started resolving what it
#: escalates, and this reader refuses it at the boundary rather than rendering it.
FORBIDDEN_FIELDS = frozenset(
    {
        "recommendation",
        "recommended_action",
        "resolution",
        "resolved",
        "severity",
        "priority",
        "recipient",
        "assigned_to",
        "verdict",
        "decision",
    }
)


class Unknown(BaseModel):
    """One thing the escalation does not know, and why."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    what: str = Field(min_length=1)
    why: str = Field(min_length=1)


class EscalationPacket(BaseModel):
    """One escalation, as the document describes it.

    ``extra="forbid"`` rather than ``ignore``, unlike the other readers in this
    package. Elsewhere a field the producer adds ahead of the reader is not a
    reason to refuse the whole document. Here it is: the fields that must not
    appear are the point of the contract, and quietly ignoring one would let a
    harness start resolving escalations without the surface noticing.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    packet_id: str = Field(min_length=1)
    lifecycle_id: str = Field(min_length=1)
    trigger: str = Field(min_length=1)
    raised_at: datetime
    disposition: Disposition
    summary: str = Field(min_length=1)
    findings: tuple[str, ...] = ()
    unknowns: tuple[Unknown, ...] = ()
    requires_human_authority: bool = True


class EscalationQueue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", strict=True)

    schema_version: int
    taken_at: datetime
    packets: tuple[EscalationPacket, ...] = ()

    @property
    def oldest_first(self) -> tuple[EscalationPacket, ...]:
        """The queue as it should be read. See the module docstring."""
        return tuple(sorted(self.packets, key=lambda p: p.raised_at))


class EscalationSource(Protocol):
    def queue(self) -> EscalationQueue: ...


def parse_queue(raw: bytes) -> EscalationQueue:
    """Parse the escalation document, or raise a typed ``SourceError``.

    From bytes for the reason :mod:`cop.agents` does: these models are strict,
    and in JSON mode a datetime may be an ISO string and an enum member its name.
    """
    try:
        data = json.loads(raw)
    except ValueError:
        raise SourceMalformedError("Escalation queue is not valid JSON") from None
    if not isinstance(data, dict):
        raise SourceMalformedError("Escalation queue is not a JSON object")
    version = data.get("schema_version")
    if version is None:
        raise SourceFieldAbsentError("Escalation queue has no schema_version")
    if version != SUPPORTED_SCHEMA_VERSION:
        raise SourceMalformedError(
            f"Escalation queue is schema version {version!r}; this reader understands only "
            f"{SUPPORTED_SCHEMA_VERSION}"
        )
    for packet in data.get("packets", []) or []:
        if isinstance(packet, dict):
            trespass = FORBIDDEN_FIELDS & set(packet)
            if trespass:
                raise SourceMalformedError(
                    f"an escalation packet carries {sorted(trespass)}, which C2 may not "
                    f"decide. A packet that resolves what it escalates is not one this "
                    f"page will render"
                )
    try:
        return EscalationQueue.model_validate_json(raw)
    except ValidationError as exc:
        raise SourceMalformedError(
            f"Unexpected escalation queue shape ({exc.error_count()} problems)"
        ) from None


class HttpxEscalationClient:
    """Reads the escalation queue from ``url``, which the caller must supply."""

    def __init__(self, url: str, http: httpx.Client | None = None) -> None:
        self._http = http or httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)
        self._url = url

    def queue(self) -> EscalationQueue:
        raw = get_bytes(
            self._http,
            self._url,
            headers={"Accept": "application/json", "User-Agent": PRODUCT_NAME},
        )
        return parse_queue(raw)
