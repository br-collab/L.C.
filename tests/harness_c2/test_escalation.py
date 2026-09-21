"""Stop 5: C2 never escalates a partial picture. Stop 2: it never resolves one.

Most of this module is about the refusals, because packaging a well-formed
escalation is the easy half. The work is in refusing to send one — and in *not*
refusing the cases that are exactly what an operator needs to see.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import pytest
from cannae_kernel.disposition import Disposition
from lineage_fixtures import (
    EVENT_B,
    LIFECYCLE,
    OTHER_LIFECYCLE,
    complete_chain,
    execution,
    intent,
)
from pydantic import ValidationError

import harness_c2.escalation as escalation_module
from harness_c2.escalation import (
    BLOCKING_GAPS,
    EscalationPacket,
    EscalationRefusedError,
    EscalationRequest,
    EscalationTrigger,
    Unknown,
    package_escalation,
)
from harness_c2.lineage import GapCode, LineageRecord, assemble_lineage

AT = datetime(2026, 9, 21, 20, 0, tzinfo=UTC)


def _package(record: LineageRecord, **overrides: object) -> EscalationPacket:
    fields: dict[str, object] = {
        "packet_id": "ESC-0001",
        "trigger": EscalationTrigger.LINEAGE_HOLE,
        "summary": "the clearing transformation that formed this obligation is missing",
        "raised_at": AT,
    }
    fields.update(overrides)
    return package_escalation(record, EscalationRequest(**fields))  # type: ignore[arg-type]


def _with_hole() -> LineageRecord:
    chain = complete_chain()
    del chain["clearings"]
    return assemble_lineage(LIFECYCLE, **chain)


class TestAHoleIsEscalated:
    """A lineage with a hole is a complete picture of a broken thing. Refusing
    to escalate it would bury the finding."""

    def test_it_packages(self) -> None:
        packet = _package(_with_hole())
        assert packet.lifecycle_id == LIFECYCLE
        assert packet.trigger is EscalationTrigger.LINEAGE_HOLE
        assert packet.disposition is Disposition.BLOCK

    def test_the_findings_are_the_assemblers_own_words(self) -> None:
        """Not C2's paraphrase. A summary written here could drop the detail an
        operator needs, and nobody would know it had been dropped."""
        record = _with_hole()
        packet = _package(record)
        detail = next(g.detail for g in record.gaps if g.code is GapCode.MISSING)
        assert any(detail in f for f in packet.findings)

    def test_every_finding_names_its_stage_and_code(self) -> None:
        packet = _package(_with_hole())
        assert packet.findings
        for finding in packet.findings:
            assert "—" in finding and ":" in finding

    def test_it_requires_human_authority_and_says_so(self) -> None:
        assert _package(_with_hole()).requires_human_authority is True


class TestAPartialPictureIsRefused:
    """Not a partial picture of one lifecycle — a picture of something that is
    not one lifecycle. Escalating it sends an operator after a story that was
    never true."""

    def _mismatched(self) -> LineageRecord:
        i = intent()
        stray = execution(i, lifecycle_id=OTHER_LIFECYCLE, event_id=EVENT_B)
        return assemble_lineage(LIFECYCLE, intents=[i], executions=[execution(i), stray])

    def _ambiguous(self) -> LineageRecord:
        return assemble_lineage(LIFECYCLE, intents=[intent(), intent()])

    def test_a_lifecycle_mismatch_is_refused(self) -> None:
        with pytest.raises(EscalationRefusedError, match="not of one lifecycle"):
            _package(self._mismatched())

    def test_an_ambiguous_stage_is_refused(self) -> None:
        with pytest.raises(EscalationRefusedError, match="not of one lifecycle"):
            _package(self._ambiguous())

    def test_the_refusal_carries_the_blocking_gaps_as_data(self) -> None:
        """A refusal to escalate that leaves no trace is indistinguishable from
        nothing having gone wrong."""
        with pytest.raises(EscalationRefusedError) as raised:
            _package(self._mismatched())
        assert raised.value.gaps
        assert any("LIFECYCLE_MISMATCH" in g for g in raised.value.gaps)
        assert raised.value.disposition is Disposition.BLOCK

    def test_the_two_blocking_codes_are_exactly_these(self) -> None:
        """Written down so that adding a gap code forces a decision about it."""
        assert BLOCKING_GAPS == {GapCode.LIFECYCLE_MISMATCH, GapCode.AMBIGUOUS}

    def test_a_hole_alongside_a_mismatch_is_still_refused(self) -> None:
        """The blocking gap wins. A real finding does not buy passage for a
        picture C2 cannot establish the subject of."""
        chain = complete_chain()
        del chain["clearings"]
        chain["intents"] = [intent(), intent()]
        with pytest.raises(EscalationRefusedError):
            _package(assemble_lineage(LIFECYCLE, **chain))


class TestACleanLineageIsRefusedToo:
    def test_nothing_to_escalate_is_refused(self) -> None:
        with pytest.raises(EscalationRefusedError, match="nothing to escalate"):
            _package(assemble_lineage(LIFECYCLE, **complete_chain()))

    def test_the_reason_says_why_a_needless_escalation_costs_something(self) -> None:
        """An escalation nobody needed is how the next one comes to be ignored."""
        with pytest.raises(EscalationRefusedError) as raised:
            _package(assemble_lineage(LIFECYCLE, **complete_chain()))
        assert "operator's attention" in raised.value.reason

    def test_an_escalation_may_never_be_recorded_as_a_pass(self) -> None:
        packet = _package(_with_hole())
        with pytest.raises(ValidationError, match="nobody needed to raise"):
            EscalationPacket.model_validate(packet.model_dump() | {"disposition": Disposition.PASS})


class TestWhatIsNotKnownTravelsWithTheReason:
    def test_an_unreached_stage_becomes_an_unknown_not_a_finding(self) -> None:
        """A lifecycle in flight has not failed. Reporting "not reached" as a
        finding would manufacture a break out of ordinary progress."""
        record = assemble_lineage(LIFECYCLE, intents=[intent()])
        packet = _package(record, trigger=EscalationTrigger.DOCTRINE_AMBIGUITY)
        assert packet.findings == ()
        assert len(packet.unknowns) == 4
        for unknown in packet.unknowns:
            assert unknown.what and unknown.why

    def test_an_unknown_cannot_be_written_without_a_reason(self) -> None:
        with pytest.raises(ValidationError):
            Unknown(what="something", why="")

    def test_extra_unknowns_are_carried_through(self) -> None:
        extra = Unknown(what="venue acknowledgement", why="the venue has not replied")
        packet = _package(_with_hole(), extra_unknowns=(extra,))
        assert extra in packet.unknowns

    def test_an_empty_unknowns_tuple_is_a_claim_not_an_omission(self) -> None:
        """It is required, so a packet cannot simply leave it out."""
        packet = _package(_with_hole())
        payload = packet.model_dump()
        del payload["unknowns"]
        with pytest.raises(ValidationError):
            EscalationPacket.model_validate(payload)


class TestC2DoesNotInterpretDoctrine:
    """Stop 2. The packet presents the ambiguity; it does not resolve it."""

    FORBIDDEN = frozenset(
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

    def test_the_packet_has_no_field_for_a_decision(self) -> None:
        fields = set(EscalationPacket.model_fields)
        assert fields.isdisjoint(self.FORBIDDEN), (
            f"the escalation packet has grown a field C2 must not fill: {fields & self.FORBIDDEN}"
        )

    def test_it_chooses_no_recipient(self) -> None:
        """ "The human authority surface" is singular and defined. C2 selecting
        which human sees an escalation would be a judgment about severity, and
        severity is exactly what is being escalated."""
        assert "recipient" not in EscalationPacket.model_fields
        assert "addressed_to" not in EscalationPacket.model_fields

    def test_a_packet_cannot_claim_human_authority_is_unnecessary(self) -> None:
        packet = _package(_with_hole())
        with pytest.raises(ValidationError, match="C2 resolves nothing"):
            EscalationPacket.model_validate(
                packet.model_dump() | {"requires_human_authority": False}
            )


class TestStopOneHoldsHereToo:
    def test_packaging_cannot_act(self) -> None:
        source = inspect.getsource(escalation_module)
        for forbidden in (
            "import httpx",
            "import requests",
            "sqlite3",
            "open(",
            "urllib",
            "smtplib",
        ):
            assert forbidden not in source, f"escalation packaging reaches for {forbidden!r}"

    def test_packaging_takes_nothing_it_could_send_through(self) -> None:
        writable = ("client", "store", "transport", "conn", "db", "session", "queue")
        params = inspect.signature(EscalationRequest).parameters
        suspicious = [n for n in params if any(w in n.lower() for w in writable)]
        assert suspicious == [], f"packaging must not be handed a way to deliver: {suspicious}"

    def test_the_packet_round_trips_through_json(self) -> None:
        packet = _package(_with_hole())
        assert EscalationPacket.model_validate_json(packet.model_dump_json()) == packet

    def test_packaging_does_not_mutate_the_record(self) -> None:
        record = _with_hole()
        before = (record.gaps, record.executions, record.lifecycle_id)
        _package(record)
        assert (record.gaps, record.executions, record.lifecycle_id) == before


class TestTheTriggersAreDistinct:
    def test_each_trigger_survives_packaging(self) -> None:
        for trigger in EscalationTrigger:
            packet = _package(_with_hole(), trigger=trigger, packet_id=f"ESC-{trigger.value}")
            assert packet.trigger is trigger

    def test_raised_at_must_be_utc_aware(self) -> None:
        with pytest.raises(ValidationError):
            _package(_with_hole(), raised_at=datetime(2026, 9, 21, 20, 0))

    def test_two_packets_for_the_same_record_are_equal_but_for_their_identity(self) -> None:
        """Packaging is deterministic: the same record at the same instant
        produces the same evidence, so two readings cannot disagree."""
        record = _with_hole()
        first = _package(record, packet_id="ESC-A")
        second = _package(record, packet_id="ESC-B", raised_at=AT + timedelta(0))
        assert first.findings == second.findings
        assert first.unknowns == second.unknowns
        assert first.disposition is second.disposition
