"""One lineage record, assembled from the five frozen envelopes (Stop 4).

C2 = Command and Control. DSOR = Decision System of Record.

What this is for
----------------
A lifecycle crosses three domains and leaves five envelopes behind it: an
approved intent, the executions against it, the clearing transformation that
consumed them, the settlement obligation that transformation formed, and
Atreides' acceptance record answering that obligation. Each is sealed by its own
domain. None of them is the lineage. The lineage is the claim that *these five
are the same story*, and that claim is what this module makes — or refuses to.

**Gaps are flagged explicitly, never silently filled.** That is Stop 4, and it
is the only reason this module is hard. Assembling a chain when everything is
present is bookkeeping. The work is in the cases where something is missing,
where two envelopes disagree, or where a link points at a digest nothing here
produced — because every one of those has a cheerful reading available, and
taking it produces a lineage record that looks complete and is not.

Why it links by digest and not by identifier
--------------------------------------------
The envelopes reference each other by digest on purpose: ``ExecutionEvent``
carries ``intent_digest`` as well as ``intent_id`` because *which intent* and
*which revision of it* are different questions, and a break investigation needs
the second. So the assembler recomputes every digest from the payload it was
given and checks the links against what it computed, never against what the
envelope claims about itself. An envelope asserting its own digest would be
checking its own homework.

What a complete lineage does **not** mean
-----------------------------------------
It means the five envelopes agree about which thing they describe, which
revision, in which session, and that none of the payloads changed after sealing.
It does not mean the numbers inside them are right. Those need domain knowledge
to validate and stay with the domain that has it — the same rule the kernel
applies to itself.

No authority, no execution
--------------------------
Nothing here decides anything, and nothing here acts. Assembly is a read over
records that already exist. Stop 1 is satisfied by this module having no write
path at all rather than by a check somebody could remove, and
``tests/harness_c2/test_lineage.py`` asserts that the module's public surface
contains no function that takes a mutable store.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from cannae_kernel.absence import AbsenceKind, Absent, Recorded
from cannae_kernel.canonical import digest
from cannae_kernel.disposition import Disposition
from cannae_kernel.envelopes import (
    ApprovedIntentEnvelope,
    ClearingTransformation,
    ExecutionEvent,
    ObligationAcceptanceRecord,
    SettlementObligationEnvelope,
)
from cannae_kernel.ids import LifecycleId

__all__ = [
    "GapCode",
    "LineageGap",
    "LineageRecord",
    "LineageStage",
    "assemble_lineage",
]


class LineageStage(StrEnum):
    """The five stages, in the order a lifecycle passes through them."""

    APPROVED_INTENT = "APPROVED_INTENT"
    EXECUTION = "EXECUTION"
    CLEARING = "CLEARING"
    SETTLEMENT_OBLIGATION = "SETTLEMENT_OBLIGATION"
    OBLIGATION_ACCEPTANCE = "OBLIGATION_ACCEPTANCE"


class GapCode(StrEnum):
    """Why a stage is not part of the lineage. Each is a different fact.

    Collapsing these into one "missing" would be the defect this module exists
    to prevent: *not reached yet* is a lifecycle in flight, *absent* is a hole,
    and *broken link* is a record that exists and does not belong. A reader told
    only "incomplete" cannot tell which, and the three want different responses.
    """

    NOT_REACHED = "NOT_REACHED"
    """The lifecycle has not got here yet. Nothing is wrong."""

    MISSING = "MISSING"
    """A later stage exists, so this one should too, and it was not supplied."""

    LIFECYCLE_MISMATCH = "LIFECYCLE_MISMATCH"
    """A record was supplied whose ``lifecycle_id`` is not this lifecycle."""

    BROKEN_LINK = "BROKEN_LINK"
    """A record points at a digest nothing in this lineage produced."""

    AMBIGUOUS = "AMBIGUOUS"
    """More than one record claims a stage that admits only one."""


@dataclass(frozen=True)
class LineageGap:
    """One thing wrong with the chain, named rather than summarised."""

    stage: LineageStage
    code: GapCode
    detail: str

    @property
    def is_hole(self) -> bool:
        """True when the gap means the record is wrong, not merely unfinished.

        ``NOT_REACHED`` is the one that does not: a lifecycle that has reached
        clearing and no further is a correct lineage of a lifecycle in flight.
        """
        return self.code is not GapCode.NOT_REACHED


@dataclass(frozen=True)
class LineageRecord:
    """The assembled lineage, and every reason it is not more than it is.

    Each stage is ``Recorded`` or ``Absent`` — the kernel's pair, so an absence
    crosses a boundary carrying its reason and cannot arrive as a null.
    """

    lifecycle_id: LifecycleId
    intent: Recorded[ApprovedIntentEnvelope] | Absent
    executions: tuple[ExecutionEvent, ...]
    clearing: Recorded[ClearingTransformation] | Absent
    obligation: Recorded[SettlementObligationEnvelope] | Absent
    acceptance: Recorded[ObligationAcceptanceRecord] | Absent
    gaps: tuple[LineageGap, ...]

    @property
    def holes(self) -> tuple[LineageGap, ...]:
        """The gaps that mean something is wrong, as against not yet done."""
        return tuple(g for g in self.gaps if g.is_hole)

    @property
    def complete(self) -> bool:
        """All five stages present and every link checked."""
        return not self.gaps

    @property
    def disposition(self) -> Disposition:
        """``PASS`` only for a complete, unbroken chain.

        A lifecycle merely in flight is ``INDETERMINATE``, not ``PASS`` — it may
        still be fine and it is not yet evidence of anything. A chain with a hole
        in it is ``BLOCK``: something is wrong now.
        """
        if self.holes:
            return Disposition.BLOCK
        if self.gaps:
            return Disposition.INDETERMINATE
        return Disposition.PASS

    @property
    def escalation_is_complete(self) -> bool:
        """Stop 5's precondition: is this a whole picture, or a partial one?

        C2 never escalates a partial picture. A record carrying a
        ``LIFECYCLE_MISMATCH`` or a ``BROKEN_LINK`` is not a partial picture of
        one lifecycle — it is a picture of something that is not one lifecycle,
        and escalating it would send an operator to reconcile a story that was
        never true. Escalation packaging is a later work package; this property
        is the question it will have to ask, stated here where the evidence is.
        """
        return not any(g.code in {GapCode.LIFECYCLE_MISMATCH, GapCode.AMBIGUOUS} for g in self.gaps)


def _absent(kind: AbsenceKind, reason: str) -> Absent:
    return Absent(kind=kind, reason=reason)


def _of_this_lifecycle(
    records: Sequence[object],
    lifecycle_id: LifecycleId,
    stage: LineageStage,
    gaps: list[LineageGap],
) -> list[object]:
    """Split records by lifecycle, recording every stray rather than dropping it.

    A record for another lifecycle is not noise to be filtered out. Somebody
    handed it to this assembly, which means either the caller's query is wrong
    or the record's ``lifecycle_id`` is — and both are worth an operator's time.
    Silently discarding it produces a lineage that is correct about what it
    contains and wrong about what it was asked to describe.
    """
    kept: list[object] = []
    for record in records:
        if getattr(record, "lifecycle_id", None) == lifecycle_id:
            kept.append(record)
        else:
            gaps.append(
                LineageGap(
                    stage=stage,
                    code=GapCode.LIFECYCLE_MISMATCH,
                    detail=(
                        f"a {stage.value} record for lifecycle "
                        f"{getattr(record, 'lifecycle_id', '<none>')} was supplied while "
                        f"assembling {lifecycle_id}; it was not used and not discarded "
                        f"quietly"
                    ),
                )
            )
    return kept


def _exactly_one(
    records: Sequence[object],
    stage: LineageStage,
    gaps: list[LineageGap],
    *,
    required: bool,
    not_reached_reason: str,
) -> Recorded[object] | Absent:
    """The single record for a stage that admits only one, or the reason there is none.

    ``required`` is what makes the difference between "not reached yet" and a
    hole: a stage is required once a *later* stage exists, because a settlement
    obligation whose clearing transformation was never supplied is not a
    lifecycle in flight — it is a lineage missing its middle.
    """
    if len(records) > 1:
        gaps.append(
            LineageGap(
                stage=stage,
                code=GapCode.AMBIGUOUS,
                detail=(
                    f"{len(records)} {stage.value} records were supplied for one lifecycle; "
                    f"this stage admits exactly one, so none was chosen"
                ),
            )
        )
        return _absent(
            AbsenceKind.NOT_YET_KNOWN,
            f"{len(records)} records claim this stage; which one is authoritative is unknown",
        )
    if not records:
        if required:
            gaps.append(
                LineageGap(
                    stage=stage,
                    code=GapCode.MISSING,
                    detail=(
                        f"a later stage exists, so {stage.value} must too, and no record "
                        f"was supplied"
                    ),
                )
            )
            return _absent(
                AbsenceKind.NOTHING_RECORDED,
                f"a later stage exists but no {stage.value} record was supplied",
            )
        gaps.append(LineageGap(stage=stage, code=GapCode.NOT_REACHED, detail=not_reached_reason))
        return _absent(AbsenceKind.NOT_YET_KNOWN, not_reached_reason)
    return Recorded(value=records[0])


def assemble_lineage(  # noqa: PLR0913 - five envelopes are the contract, not a parameter list
    lifecycle_id: LifecycleId,
    *,
    intents: Sequence[ApprovedIntentEnvelope] = (),
    executions: Sequence[ExecutionEvent] = (),
    clearings: Sequence[ClearingTransformation] = (),
    obligations: Sequence[SettlementObligationEnvelope] = (),
    acceptances: Sequence[ObligationAcceptanceRecord] = (),
) -> LineageRecord:
    """Assemble one lifecycle's lineage, flagging every gap and never filling one.

    Pure. It reads the records it is given, recomputes the digests they link by,
    and returns what it found. It writes nothing, fetches nothing and decides
    nothing — Stop 1 holds because there is no path here that could act, not
    because a flag says not to.

    Every argument defaults to empty, so a lifecycle that has only been approved
    assembles correctly: four stages ``NOT_REACHED``, no holes, and a
    disposition of ``INDETERMINATE`` rather than ``PASS``, because a lifecycle in
    flight is not yet evidence of anything.
    """
    gaps: list[LineageGap] = []

    kept_intents = _of_this_lifecycle(intents, lifecycle_id, LineageStage.APPROVED_INTENT, gaps)
    kept_executions = _of_this_lifecycle(executions, lifecycle_id, LineageStage.EXECUTION, gaps)
    kept_clearings = _of_this_lifecycle(clearings, lifecycle_id, LineageStage.CLEARING, gaps)
    kept_obligations = _of_this_lifecycle(
        obligations, lifecycle_id, LineageStage.SETTLEMENT_OBLIGATION, gaps
    )
    # An acceptance record carries no lifecycle_id — it answers an obligation by
    # identity and digest, and the obligation carries the lifecycle. So it is
    # placed by its link below rather than filtered here.
    kept_acceptances = list(acceptances)

    # A stage is required once anything downstream of it exists.
    has_acceptance = bool(kept_acceptances)
    has_obligation = bool(kept_obligations) or has_acceptance
    has_clearing = bool(kept_clearings) or has_obligation
    has_execution = bool(kept_executions) or has_clearing

    intent = _exactly_one(
        kept_intents,
        LineageStage.APPROVED_INTENT,
        gaps,
        required=has_execution,
        not_reached_reason="no approved intent has been supplied for this lifecycle",
    )
    clearing = _exactly_one(
        kept_clearings,
        LineageStage.CLEARING,
        gaps,
        required=has_obligation,
        not_reached_reason="the executions have not been cleared yet",
    )
    obligation = _exactly_one(
        kept_obligations,
        LineageStage.SETTLEMENT_OBLIGATION,
        gaps,
        required=has_acceptance,
        not_reached_reason="no settlement obligation has been formed yet",
    )
    acceptance = _exactly_one(
        kept_acceptances,
        LineageStage.OBLIGATION_ACCEPTANCE,
        gaps,
        required=False,
        not_reached_reason="Atreides has not answered the obligation yet",
    )

    if not kept_executions and has_clearing:
        gaps.append(
            LineageGap(
                stage=LineageStage.EXECUTION,
                code=GapCode.MISSING,
                detail=(
                    "a clearing transformation exists, so the executions it consumed must "
                    "too, and none was supplied"
                ),
            )
        )
    elif not kept_executions:
        gaps.append(
            LineageGap(
                stage=LineageStage.EXECUTION,
                code=GapCode.NOT_REACHED,
                detail="nothing has executed against this intent yet",
            )
        )

    stages = _Stages(
        intent=intent,
        executions=tuple(kept_executions),
        clearing=clearing,
        obligation=obligation,
        acceptance=acceptance,
    )
    _check_links(stages, gaps)

    return LineageRecord(
        lifecycle_id=lifecycle_id,
        intent=stages.intent,  # type: ignore[arg-type]
        executions=stages.executions,  # type: ignore[arg-type]
        clearing=stages.clearing,  # type: ignore[arg-type]
        obligation=stages.obligation,  # type: ignore[arg-type]
        acceptance=stages.acceptance,  # type: ignore[arg-type]
        gaps=tuple(gaps),
    )


def _recorded(slot: Recorded[object] | Absent) -> object | None:
    return slot.value if isinstance(slot, Recorded) else None


@dataclass(frozen=True)
class _Stages:
    """The five stages as assembly found them, before the links are checked.

    Grouped rather than passed separately because they are one thing — the
    chain — and a function taking five of them as loose arguments invites a
    caller to pass four.
    """

    intent: Recorded[object] | Absent
    executions: tuple[object, ...]
    clearing: Recorded[object] | Absent
    obligation: Recorded[object] | Absent
    acceptance: Recorded[object] | Absent


def _check_links(stages: _Stages, gaps: list[LineageGap]) -> None:
    """Check every link against a digest recomputed here, not one an envelope claims.

    Each check below follows the same shape: take the digest the downstream
    record points at, recompute the digest of the upstream record from its own
    bytes, and compare. An envelope that carried and was trusted for its own
    digest would be marking its own homework — and the failure that produces is
    a lineage that is internally consistent and describes nothing real.

    Split one function per link rather than one long ladder, so that a failure
    names the link rather than the assembler.
    """
    intent_value = _recorded(stages.intent)
    clearing_value = _recorded(stages.clearing)
    obligation_value = _recorded(stages.obligation)
    acceptance_value = _recorded(stages.acceptance)
    executions = stages.executions

    if intent_value is not None:
        _executions_against_intent(executions, intent_value, gaps)
    if clearing_value is not None and executions:
        _clearing_against_executions(clearing_value, executions, gaps)
    if obligation_value is not None and clearing_value is not None:
        _obligation_against_clearing(obligation_value, clearing_value, gaps)
    if acceptance_value is not None:
        _acceptance_against_obligation(acceptance_value, obligation_value, gaps)


def _executions_against_intent(
    executions: Sequence[Any], intent_value: Any, gaps: list[LineageGap]
) -> None:
    """Each fill must name this intent *and* the revision it was filled against.

    Both, because "which intent" and "which version of it" are different
    questions and a break investigation needs the second one.
    """
    intent_digest = digest(intent_value)
    for execution in executions:
        if execution.intent_id != intent_value.envelope_id:
            gaps.append(
                LineageGap(
                    stage=LineageStage.EXECUTION,
                    code=GapCode.BROKEN_LINK,
                    detail=(
                        f"execution {execution.event_id} executes against intent "
                        f"{execution.intent_id}, which is not this lifecycle's intent "
                        f"{intent_value.envelope_id}"
                    ),
                )
            )
        elif execution.intent_digest != intent_digest:
            gaps.append(
                LineageGap(
                    stage=LineageStage.EXECUTION,
                    code=GapCode.BROKEN_LINK,
                    detail=(
                        f"execution {execution.event_id} was executed against intent "
                        f"revision {execution.intent_digest}, and the intent supplied "
                        f"digests to {intent_digest}. Either a different revision was "
                        f"supplied, or the intent changed after the fill. Both are "
                        f"answerable; neither is assumed here"
                    ),
                )
            )


def _clearing_against_executions(
    clearing_value: Any, executions: Sequence[Any], gaps: list[LineageGap]
) -> None:
    """The set consumed and the set supplied must be the same set, both ways.

    Consuming something nothing produced means the transformation cannot be
    reproduced from this lineage. Leaving a fill unconsumed is a netting hole:
    an execution that cleared into nothing.
    """
    supplied = {digest(e) for e in executions}
    consumed = set(clearing_value.input_digests)
    unknown = consumed - supplied
    unconsumed = supplied - consumed
    if unknown:
        gaps.append(
            LineageGap(
                stage=LineageStage.CLEARING,
                code=GapCode.BROKEN_LINK,
                detail=(
                    f"clearing consumed {len(unknown)} input(s) that nothing supplied "
                    f"here produced: {sorted(unknown)}. The transformation cannot be "
                    f"reproduced from this lineage"
                ),
            )
        )
    if unconsumed:
        gaps.append(
            LineageGap(
                stage=LineageStage.CLEARING,
                code=GapCode.BROKEN_LINK,
                detail=(
                    f"{len(unconsumed)} execution(s) in this lifecycle were not consumed "
                    f"by the clearing transformation: {sorted(unconsumed)}. A fill that "
                    f"cleared into nothing is a netting hole, not a rounding detail"
                ),
            )
        )


def _obligation_against_clearing(
    obligation_value: Any, clearing_value: Any, gaps: list[LineageGap]
) -> None:
    """An obligation that cannot name what produced it is one nobody can reconcile."""
    expected = digest(clearing_value)
    if obligation_value.transformation_digest != expected:
        gaps.append(
            LineageGap(
                stage=LineageStage.SETTLEMENT_OBLIGATION,
                code=GapCode.BROKEN_LINK,
                detail=(
                    f"the obligation was formed by transformation "
                    f"{obligation_value.transformation_digest}, and the clearing "
                    f"transformation supplied digests to {expected}"
                ),
            )
        )


def _acceptance_against_obligation(
    acceptance_value: Any, obligation_value: Any | None, gaps: list[LineageGap]
) -> None:
    """Atreides answers an obligation by identity and by digest, never by copy.

    Holding the digest rather than restating the economics is what makes the one
    disagreement that matters answerable: "this is not the obligation I sent".
    """
    if obligation_value is None:
        gaps.append(
            LineageGap(
                stage=LineageStage.OBLIGATION_ACCEPTANCE,
                code=GapCode.BROKEN_LINK,
                detail=(
                    f"an acceptance answers obligation {acceptance_value.obligation_id}, "
                    f"and no obligation was supplied for this lifecycle to answer"
                ),
            )
        )
        return
    expected = digest(obligation_value)
    if acceptance_value.obligation_id != obligation_value.obligation_id:
        gaps.append(
            LineageGap(
                stage=LineageStage.OBLIGATION_ACCEPTANCE,
                code=GapCode.BROKEN_LINK,
                detail=(
                    f"the acceptance answers obligation "
                    f"{acceptance_value.obligation_id}; this lifecycle's obligation is "
                    f"{obligation_value.obligation_id}"
                ),
            )
        )
    elif acceptance_value.obligation_digest != expected:
        gaps.append(
            LineageGap(
                stage=LineageStage.OBLIGATION_ACCEPTANCE,
                code=GapCode.BROKEN_LINK,
                detail=(
                    f"the acceptance answers obligation digest "
                    f"{acceptance_value.obligation_digest}, and the obligation supplied "
                    f"digests to {expected}. This is the one disagreement the digest "
                    f"design makes answerable: 'this is not the obligation I sent'"
                ),
            )
        )
