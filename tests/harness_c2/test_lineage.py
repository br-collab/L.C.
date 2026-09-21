"""Stop 4: one lineage record, gaps flagged explicitly and never silently filled.

Assembling a chain when everything is present is bookkeeping, and one test
covers it. The rest of this module is the cases where something is missing,
where two records disagree, or where a link points at a digest nothing here
produced — because each of those has a cheerful reading available, and taking it
produces a lineage that looks complete and is not.
"""

from __future__ import annotations

import inspect

from cannae_kernel.absence import AbsenceKind, Absent, Recorded
from cannae_kernel.canonical import digest
from cannae_kernel.disposition import Disposition
from lineage_fixtures import (
    EVENT_B,
    LIFECYCLE,
    OTHER_INTENT,
    OTHER_LIFECYCLE,
    OTHER_OBLIGATION,
    acceptance,
    clearing,
    complete_chain,
    execution,
    intent,
    obligation,
)

from harness_c2 import lineage as lineage_module
from harness_c2.lineage import (
    GapCode,
    LineageGap,
    LineageRecord,
    LineageStage,
    assemble_lineage,
)


def _codes(record: LineageRecord) -> set[GapCode]:
    return {g.code for g in record.gaps}


def _gap(record: LineageRecord, stage: LineageStage) -> LineageGap:
    return next(g for g in record.gaps if g.stage is stage)


class TestACompleteChain:
    def test_every_stage_is_recorded_and_there_are_no_gaps(self) -> None:
        record = assemble_lineage(LIFECYCLE, **complete_chain())
        assert record.complete
        assert record.gaps == ()
        assert record.disposition is Disposition.PASS
        for slot in (record.intent, record.clearing, record.obligation, record.acceptance):
            assert isinstance(slot, Recorded)
        assert len(record.executions) == 1

    def test_it_escalates_as_a_whole_picture(self) -> None:
        record = assemble_lineage(LIFECYCLE, **complete_chain())
        assert record.escalation_is_complete


class TestALifecycleInFlightIsNotAHole:
    """A lifecycle that has only been approved is a correct lineage of a
    lifecycle in flight. It is not complete, and nothing is wrong with it."""

    def test_an_approved_intent_alone_reaches_no_further_and_says_so(self) -> None:
        record = assemble_lineage(LIFECYCLE, intents=[intent()])
        assert not record.complete
        assert record.holes == ()
        assert _codes(record) == {GapCode.NOT_REACHED}

    def test_it_is_indeterminate_and_never_a_pass(self) -> None:
        """A lifecycle in flight is not yet evidence of anything."""
        record = assemble_lineage(LIFECYCLE, intents=[intent()])
        assert record.disposition is Disposition.INDETERMINATE

    def test_each_unreached_stage_carries_its_own_reason(self) -> None:
        record = assemble_lineage(LIFECYCLE, intents=[intent()])
        for stage in (
            LineageStage.EXECUTION,
            LineageStage.CLEARING,
            LineageStage.SETTLEMENT_OBLIGATION,
            LineageStage.OBLIGATION_ACCEPTANCE,
        ):
            gap = _gap(record, stage)
            assert gap.code is GapCode.NOT_REACHED
            assert gap.detail.strip()
            assert not gap.is_hole

    def test_an_absent_stage_is_absent_with_a_reason_never_a_null(self) -> None:
        record = assemble_lineage(LIFECYCLE, intents=[intent()])
        assert isinstance(record.clearing, Absent)
        assert record.clearing.kind is AbsenceKind.NOT_YET_KNOWN
        assert record.clearing.reason
        assert record.clearing.disposition is Disposition.INDETERMINATE

    def test_an_entirely_empty_lifecycle_assembles_rather_than_raising(self) -> None:
        record = assemble_lineage(LIFECYCLE)
        assert record.holes == ()
        assert record.disposition is Disposition.INDETERMINATE
        assert len(record.gaps) == len(LineageStage)


class TestAMissingMiddleIsAHole:
    """A later stage existing means an earlier one should too. That is the
    difference between not-yet and never, and it is the whole of Stop 4."""

    def test_an_obligation_with_no_clearing_is_missing_not_unreached(self) -> None:
        chain = complete_chain()
        del chain["clearings"]
        record = assemble_lineage(LIFECYCLE, **chain)
        gap = _gap(record, LineageStage.CLEARING)
        assert gap.code is GapCode.MISSING
        assert gap.is_hole
        assert record.disposition is Disposition.BLOCK

    def test_a_clearing_with_no_executions_is_missing(self) -> None:
        chain = complete_chain()
        del chain["executions"]
        record = assemble_lineage(LIFECYCLE, **chain)
        gap = _gap(record, LineageStage.EXECUTION)
        assert gap.code is GapCode.MISSING
        assert "must too" in gap.detail

    def test_an_execution_with_no_intent_is_missing(self) -> None:
        i = intent()
        record = assemble_lineage(LIFECYCLE, executions=[execution(i)])
        gap = _gap(record, LineageStage.APPROVED_INTENT)
        assert gap.code is GapCode.MISSING
        assert record.disposition is Disposition.BLOCK

    def test_the_absence_for_a_hole_is_settled_not_in_flight(self) -> None:
        """NOTHING_RECORDED, not NOT_YET_KNOWN: waiting will not produce it."""
        chain = complete_chain()
        del chain["clearings"]
        record = assemble_lineage(LIFECYCLE, **chain)
        assert isinstance(record.clearing, Absent)
        assert record.clearing.kind is AbsenceKind.NOTHING_RECORDED


class TestARecordFromAnotherLifecycleIsNeverQuietlyDropped:
    """Filtering it out produces a lineage that is correct about what it
    contains and wrong about what it was asked to describe."""

    def test_a_stray_execution_is_reported(self) -> None:
        i = intent()
        stray = execution(i, lifecycle_id=OTHER_LIFECYCLE, event_id=EVENT_B)
        record = assemble_lineage(LIFECYCLE, intents=[i], executions=[execution(i), stray])
        codes = _codes(record)
        assert GapCode.LIFECYCLE_MISMATCH in codes
        assert len(record.executions) == 1

    def test_it_names_both_lifecycles_so_the_caller_can_tell_which_is_wrong(self) -> None:
        i = intent()
        stray = execution(i, lifecycle_id=OTHER_LIFECYCLE, event_id=EVENT_B)
        record = assemble_lineage(LIFECYCLE, intents=[i], executions=[stray])
        gap = next(g for g in record.gaps if g.code is GapCode.LIFECYCLE_MISMATCH)
        assert OTHER_LIFECYCLE in gap.detail
        assert LIFECYCLE in gap.detail

    def test_a_mismatch_is_not_a_partial_picture_so_it_does_not_escalate(self) -> None:
        """Stop 5. It is a picture of something that is not one lifecycle, and
        escalating it would send an operator to reconcile a story never true."""
        i = intent()
        stray = execution(i, lifecycle_id=OTHER_LIFECYCLE, event_id=EVENT_B)
        record = assemble_lineage(LIFECYCLE, intents=[i], executions=[stray])
        assert not record.escalation_is_complete


class TestTwoRecordsForOneStageIsNotAChoice:
    def test_two_intents_are_ambiguous_and_neither_is_chosen(self) -> None:
        record = assemble_lineage(LIFECYCLE, intents=[intent(), intent(revision=1)])
        gap = _gap(record, LineageStage.APPROVED_INTENT)
        assert gap.code is GapCode.AMBIGUOUS
        assert isinstance(record.intent, Absent)
        assert "which one is authoritative is unknown" in record.intent.reason

    def test_ambiguity_does_not_escalate_either(self) -> None:
        record = assemble_lineage(LIFECYCLE, intents=[intent(), intent()])
        assert not record.escalation_is_complete


class TestEveryLinkIsCheckedAgainstARecomputedDigest:
    """An envelope trusted for its own digest would be marking its own homework."""

    def test_an_execution_against_a_different_intent_is_a_broken_link(self) -> None:
        i = intent()
        wrong = execution(i, intent_id=OTHER_INTENT)
        record = assemble_lineage(LIFECYCLE, intents=[i], executions=[wrong])
        gap = _gap(record, LineageStage.EXECUTION)
        assert gap.code is GapCode.BROKEN_LINK
        assert OTHER_INTENT in gap.detail

    def test_an_execution_against_a_different_revision_is_a_broken_link(self) -> None:
        """The same intent, a different revision of it. The identity matches and
        the lineage is still wrong, which is why both are carried."""
        i = intent()
        stale = execution(i, intent_digest="sha256:" + "0" * 64)
        record = assemble_lineage(LIFECYCLE, intents=[i], executions=[stale])
        gap = _gap(record, LineageStage.EXECUTION)
        assert gap.code is GapCode.BROKEN_LINK
        assert "revision" in gap.detail

    def test_a_mutated_intent_breaks_the_link_it_used_to_satisfy(self) -> None:
        """The check that makes the digests worth carrying: the intent changed
        after the fill, every field is well-formed, and the link no longer holds."""
        i = intent()
        e = execution(i)
        mutated = intent(payload_digest="sha256:" + "f" * 64)
        record = assemble_lineage(LIFECYCLE, intents=[mutated], executions=[e])
        assert _gap(record, LineageStage.EXECUTION).code is GapCode.BROKEN_LINK
        assert record.disposition is Disposition.BLOCK

    def test_clearing_that_consumed_something_nothing_produced_is_a_broken_link(self) -> None:
        i = intent()
        e = execution(i)
        invented = clearing(e, input_digests=("sha256:" + "9" * 64,))
        record = assemble_lineage(LIFECYCLE, intents=[i], executions=[e], clearings=[invented])
        gap = _gap(record, LineageStage.CLEARING)
        assert gap.code is GapCode.BROKEN_LINK
        assert "cannot be reproduced" in gap.detail

    def test_an_execution_that_cleared_into_nothing_is_a_netting_hole(self) -> None:
        i = intent()
        first, second = execution(i), execution(i, event_id=EVENT_B)
        record = assemble_lineage(
            LIFECYCLE, intents=[i], executions=[first, second], clearings=[clearing(first)]
        )
        gap = next(
            g
            for g in record.gaps
            if g.stage is LineageStage.CLEARING and g.code is GapCode.BROKEN_LINK
        )
        assert "not consumed" in gap.detail

    def test_an_obligation_formed_by_a_different_transformation_is_a_broken_link(self) -> None:
        i = intent()
        e = execution(i)
        c = clearing(e)
        wrong = obligation(c, transformation_digest="sha256:" + "7" * 64)
        record = assemble_lineage(
            LIFECYCLE, intents=[i], executions=[e], clearings=[c], obligations=[wrong]
        )
        assert _gap(record, LineageStage.SETTLEMENT_OBLIGATION).code is GapCode.BROKEN_LINK

    def test_an_acceptance_answering_another_obligation_is_a_broken_link(self) -> None:
        chain = complete_chain()
        o = chain["obligations"][0]
        chain["acceptances"] = [acceptance(o, obligation_id=OTHER_OBLIGATION)]
        record = assemble_lineage(LIFECYCLE, **chain)
        gap = _gap(record, LineageStage.OBLIGATION_ACCEPTANCE)
        assert gap.code is GapCode.BROKEN_LINK
        assert OTHER_OBLIGATION in gap.detail

    def test_an_acceptance_answering_a_changed_obligation_names_the_one_answerable_question(
        self,
    ) -> None:
        chain = complete_chain()
        o = chain["obligations"][0]
        chain["acceptances"] = [acceptance(o, obligation_digest="sha256:" + "3" * 64)]
        record = assemble_lineage(LIFECYCLE, **chain)
        gap = _gap(record, LineageStage.OBLIGATION_ACCEPTANCE)
        assert gap.code is GapCode.BROKEN_LINK
        assert "this is not the obligation I sent" in gap.detail

    def test_an_acceptance_with_no_obligation_to_answer_is_a_broken_link(self) -> None:
        chain = complete_chain()
        del chain["obligations"]
        record = assemble_lineage(LIFECYCLE, **chain)
        codes = {g.code for g in record.gaps if g.stage is LineageStage.OBLIGATION_ACCEPTANCE}
        assert GapCode.BROKEN_LINK in codes

    def test_a_sound_chain_survives_a_round_trip_through_the_digests(self) -> None:
        """The guard against over-correcting: these checks must not reject a
        chain that is actually fine."""
        chain = complete_chain()
        i = chain["intents"][0]
        e = chain["executions"][0]
        assert e.intent_digest == digest(i)
        assert assemble_lineage(LIFECYCLE, **chain).complete


class TestStopOneHoldsByConstruction:
    """C2 never takes a market action, generates an order, modifies a position
    or issues a settlement instruction. Assembly has no write path at all."""

    def test_the_public_surface_is_one_pure_function(self) -> None:
        public = [n for n in lineage_module.__all__ if not n.startswith("_")]
        assert "assemble_lineage" in public
        callables = [n for n in public if inspect.isfunction(getattr(lineage_module, n))]
        assert callables == ["assemble_lineage"], (
            "a second callable has joined the lineage module's public surface; "
            "assembly is a read and must stay one"
        )

    def test_assembly_takes_no_store_and_no_client(self) -> None:
        writable = ("store", "client", "session_factory", "conn", "db")
        params = inspect.signature(lineage_module.assemble_lineage).parameters
        suspicious = [n for n in params if any(w in n.lower() for w in writable)]
        assert suspicious == [], (
            f"assembly must not be handed something it could write to: {suspicious}"
        )

    def test_the_module_imports_nothing_that_could_act(self) -> None:
        """No network, no filesystem, no database. Read the source rather than
        trusting that nobody added one."""
        source = inspect.getsource(lineage_module)
        for forbidden in ("import httpx", "import requests", "import sqlite3", "open(", "urllib"):
            assert forbidden not in source, f"the lineage assembler reaches for {forbidden!r}"

    def test_assembly_does_not_mutate_what_it_is_given(self) -> None:
        chain = complete_chain()
        before = {k: list(v) for k, v in chain.items()}
        assemble_lineage(LIFECYCLE, **chain)
        assert {k: list(v) for k, v in chain.items()} == before


class TestTheGapCodesAreNotInterchangeable:
    def test_not_reached_is_the_only_code_that_is_not_a_hole(self) -> None:
        for code in GapCode:
            gap = LineageGap(stage=LineageStage.CLEARING, code=code, detail="d")
            assert gap.is_hole == (code is not GapCode.NOT_REACHED)

    def test_every_gap_carries_a_detail_a_reader_can_act_on(self) -> None:
        chain = complete_chain()
        del chain["clearings"]
        record = assemble_lineage(LIFECYCLE, **chain)
        for gap in record.gaps:
            assert len(gap.detail) > 20, f"{gap.code} says too little to be actionable"
