"""Packaging an escalation, and refusing to package a partial one (Stops 5 and 2).

C2 = Command and Control. CAOM = Consolidated Authority Operating Mode.

Two stops meet here
-------------------
**Stop 5 — escalation completeness.** *C2 never escalates a partial picture.*
**Stop 2 — no doctrine interpretation.** *Doctrine ambiguity escalates to the
human authority surface.*

Together they say something narrower than either alone: when C2 cannot resolve
something, it hands the whole of what it knows to a human — and when it cannot
establish *what it is looking at*, it does not hand over anything at all. The
second is the harder half, and it is most of this module.

"Partial picture" does not mean "incomplete lineage"
-----------------------------------------------------
This distinction is the whole of Stop 5, and getting it backwards would make
this module useless in exactly the cases it exists for.

A lineage with a **hole** in it — a settlement obligation whose clearing
transformation was never supplied — is a *complete picture of a broken thing*.
That is precisely what an operator needs to see, and refusing to escalate it
would bury the finding.

A lineage carrying a **`LIFECYCLE_MISMATCH`** or an **`AMBIGUOUS`** stage is
different in kind. It is not a partial picture of one lifecycle; it is a picture
of something that is not one lifecycle. Escalating it would send an operator to
reconcile a story that was never true, and the operator would have no way to
tell that from a real break until they had spent the afternoon on it.

So :func:`package_escalation` refuses on the second and insists on the first.

What the packet may not contain
-------------------------------
C2 does not interpret doctrine, so the packet carries **no recommendation, no
resolution and no chosen recipient**. There is deliberately no field for any of
them, and ``tests/harness_c2/test_escalation.py`` asserts that none has appeared.

That includes the recipient. "The human authority surface" is singular and
defined; C2 selecting *which* human sees an escalation would be a judgment about
severity, and severity is exactly what is being escalated. The packet states
that human authority is required and stops there.

What it must contain
--------------------
Everything known, including what is *not* known. Absences travel with their
reasons — the same rule as everywhere else — because an escalation that quietly
dropped its gaps would be a partial picture wearing a complete one's clothes.
:attr:`EscalationPacket.unknowns` is required, and an empty tuple is a claim
that nothing is unknown rather than an omission.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Self

from cannae_kernel._model import KernelModel, NonEmptyStr, UtcDatetime
from cannae_kernel.disposition import Disposition
from cannae_kernel.ids import LifecycleId
from pydantic import model_validator

from harness_c2.lineage import GapCode, LineageRecord

__all__ = [
    "BLOCKING_GAPS",
    "EscalationPacket",
    "EscalationRefusedError",
    "EscalationRequest",
    "EscalationTrigger",
    "Unknown",
    "package_escalation",
]

#: Gap codes that make a picture one C2 must not escalate. Both mean the same
#: thing at bottom: *we do not know what we are looking at.*
BLOCKING_GAPS: frozenset[GapCode] = frozenset({GapCode.LIFECYCLE_MISMATCH, GapCode.AMBIGUOUS})


class EscalationTrigger(StrEnum):
    """Why this reached a human. Named, because the four want different readers."""

    LINEAGE_HOLE = "LINEAGE_HOLE"
    """The chain is missing something a later stage proves should exist."""

    DOCTRINE_AMBIGUITY = "DOCTRINE_AMBIGUITY"
    """Doctrine does not determine the answer. Stop 2: C2 does not choose one."""

    HANDOFF_REFUSED = "HANDOFF_REFUSED"
    """An agent refused an input at its own type check and the work stopped."""

    HALT_ACTIVE = "HALT_ACTIVE"
    """A halt covering the domain is in effect and work was refused under it."""


class Unknown(KernelModel):
    """One thing this packet does not know, and why it does not know it.

    A separate type rather than a string so that an unknown cannot be written as
    an empty entry. Every field is required and non-empty: an unknown with no
    reason is the shape that lets a reader assume it is unimportant.
    """

    what: NonEmptyStr
    why: NonEmptyStr


class EscalationPacket(KernelModel):
    """The whole of what C2 knows, handed to human authority.

    Frozen, closed and strict. There is no field for a recommendation, a
    resolution, a severity or a recipient — see the module docstring.
    """

    packet_id: NonEmptyStr
    lifecycle_id: LifecycleId
    trigger: EscalationTrigger
    raised_at: UtcDatetime

    disposition: Disposition
    """What the evidence amounts to. Never ``PASS``: an escalation that resolved
    to a pass would be an escalation nobody needed to raise."""

    summary: NonEmptyStr
    """One line, stating what happened. Not what to do about it."""

    findings: tuple[NonEmptyStr, ...]
    """Each gap in the lineage, in the assembler's own words. May be empty when
    the trigger is not a lineage hole."""

    unknowns: tuple[Unknown, ...]
    """What is not known, each with its reason. Required. An empty tuple claims
    that nothing is unknown, which is a claim and not an omission."""

    requires_human_authority: bool = True
    """Always true, and stated rather than implied. Every approval gate requires
    explicit operator action (CAOM-001); an escalation is not an exception to
    that, it is the case that makes it visible."""

    @model_validator(mode="after")
    def _an_escalation_is_never_a_pass(self) -> Self:
        if self.disposition is Disposition.PASS:
            raise ValueError(
                "an escalation that resolves to PASS is one nobody needed to raise; "
                "if the evidence passes, there is nothing to escalate"
            )
        return self

    @model_validator(mode="after")
    def _human_authority_is_not_optional(self) -> Self:
        if not self.requires_human_authority:
            raise ValueError(
                "an escalation that does not require human authority is not an "
                "escalation; C2 resolves nothing (CAOM-001)"
            )
        return self


class EscalationRefusedError(PermissionError):
    """Raised when the picture is one C2 must not escalate.

    Carries the blocking gaps as data, so the caller records *why* nothing was
    sent rather than discovering an escalation silently failed to appear. A
    refusal to escalate that leaves no trace is indistinguishable from nothing
    having gone wrong.
    """

    def __init__(self, reason: str, gaps: tuple[str, ...]) -> None:
        super().__init__(reason)
        self.reason = reason
        self.gaps = gaps
        self.disposition = Disposition.BLOCK


@dataclass(frozen=True)
class _Picture:
    """What the lineage amounts to, before deciding whether it may be sent."""

    blocking: tuple[str, ...]
    findings: tuple[str, ...]
    unknowns: tuple[Unknown, ...]


def _read(record: LineageRecord) -> _Picture:
    """Separate the gaps that block escalation from the ones that are the point.

    A hole is a finding: it is what the operator is being shown. A mismatch or
    an ambiguity is not a finding about the lifecycle, it is a statement that we
    cannot establish which lifecycle this is.
    """
    blocking, findings, unknowns = [], [], []
    for gap in record.gaps:
        line = f"{gap.stage.value} — {gap.code.value}: {gap.detail}"
        if gap.code in BLOCKING_GAPS:
            blocking.append(line)
        elif gap.code is GapCode.NOT_REACHED:
            unknowns.append(Unknown(what=f"{gap.stage.value} outcome", why=gap.detail))
        else:
            findings.append(line)
    return _Picture(tuple(blocking), tuple(findings), tuple(unknowns))


@dataclass(frozen=True)
class EscalationRequest:
    """What is being raised, kept apart from the evidence being raised about.

    Two values rather than six arguments, and it mirrors
    :class:`~harness_c2.handoff.HandoffRequest` on purpose: in both, the caller
    describes what it wants and the harness supplies what it knows. The record
    is the evidence and this is the heading on it; keeping them apart makes it
    obvious that the heading cannot alter the findings.
    """

    packet_id: str
    trigger: EscalationTrigger
    summary: str
    raised_at: datetime
    extra_unknowns: tuple[Unknown, ...] = ()


def package_escalation(record: LineageRecord, request: EscalationRequest) -> EscalationPacket:
    """Package one escalation, or refuse because the picture is not one.

    Pure, like the rest of the harness: it reads a record and returns a value.
    It sends nothing, writes nothing and decides nothing about what should
    happen next — delivery belongs to whatever surfaces the packet, and the
    decision belongs to the human it reaches (Stops 1 and 2).

    Raises :class:`EscalationRefusedError` when the record carries a
    ``LIFECYCLE_MISMATCH`` or an ``AMBIGUOUS`` stage. That is Stop 5, and it
    raises rather than returning an empty packet because a caller that ignored a
    returned refusal would believe an escalation had been sent.
    """
    picture = _read(record)
    if picture.blocking:
        raise EscalationRefusedError(
            reason=(
                f"refusing to escalate lifecycle {record.lifecycle_id}: the picture is "
                f"not of one lifecycle. {len(picture.blocking)} blocking gap(s) mean C2 "
                f"cannot establish what it is looking at, and escalating would send an "
                f"operator to reconcile a story that was never true (Stop 5)"
            ),
            gaps=picture.blocking,
        )
    if record.disposition is Disposition.PASS:
        raise EscalationRefusedError(
            reason=(
                f"refusing to escalate lifecycle {record.lifecycle_id}: the lineage is "
                f"complete and unbroken, so there is nothing to escalate. Raising one "
                f"anyway would spend an operator's attention on a clean record, and an "
                f"escalation nobody needed is how the next one comes to be ignored"
            ),
            gaps=(),
        )
    return EscalationPacket(
        packet_id=request.packet_id,
        lifecycle_id=record.lifecycle_id,
        trigger=request.trigger,
        raised_at=request.raised_at,
        disposition=record.disposition,
        summary=request.summary,
        findings=picture.findings,
        unknowns=picture.unknowns + request.extra_unknowns,
    )
