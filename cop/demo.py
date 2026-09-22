"""Fake GitHub and Aureon sources for local demonstration and screenshots.

Enabled only by ``LEGATE_DEMO=1`` (the variable name is in ``cop.settings``), and refused
when a production marker such as ``PORT`` is set. Every value here is invented. The page
shows a "demo data" warning in the banner whenever these sources are in use.

The fake data deliberately includes a failing scheduled run, a pull request that needs
an update, a timed-out source, deploy drift and a pending drop at a deploy, so that each
display state is visible.

``DemoAgents`` does the same for the Atreides Agents panel: one agent reporting cleanly,
one holding work for the operator, one stopped and therefore absent-with-reason, and the
standing lateral-handoff probe being refused. Those four are every state the panel can
show, which is the point — a demo that only showed the happy row would not tell anyone
whether the unhappy ones render at all.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal

from cannae_kernel.absence import AbsenceKind, Absent, Recorded
from cannae_kernel.actor import ActorKind, ActorRef
from cannae_kernel.clocks import EventTimes
from cannae_kernel.disposition import Disposition
from cannae_kernel.ids import ActorId
from cannae_kernel.provenance import Provenance

from cop.agents import AgentsSnapshot, AgentView, RefusalView
from cop.aureon import AureonSnapshot
from cop.breaks import BreakRecord
from cop.cash_leg import CashLeg
from cop.escalations import EscalationPacket, EscalationQueue, Unknown
from cop.exceptions import (
    ExceptionKind,
    ExceptionRecord,
    ExceptionRegister,
    ExceptionStatus,
    TrailEntry,
)
from cop.github import Commit, Pull, PullDetail, PullHead, Tag, WorkflowRun
from cop.grc import ControlRecord, GovernanceEvent, GovernanceKind, RiskLimit
from cop.lifecycle import Checkpoint, Layer, LayerReading, LifecycleRow, build_row
from cop.observation import SourceTimeoutError

_NIGHTLY = (9, "Nightly")
_BEHIND_PULL = 102


def _sha(seed: str) -> str:
    return (seed * 40)[:40]


class DemoGitHub:
    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock

    def _ago(self, **delta: float) -> datetime:
        return self._clock() - timedelta(**delta)

    def _run(
        self,
        run_id: int,
        *,
        workflow: tuple[int, str],
        event: str,
        sha: str,
        conclusion: str | None,
    ) -> WorkflowRun:
        workflow_id, name = workflow
        return WorkflowRun(
            id=run_id,
            workflow_id=workflow_id,
            name=name,
            status="completed" if conclusion else "in_progress",
            conclusion=conclusion,
            event=event,
            head_sha=sha,
            head_branch="main",
            created_at=self._ago(hours=run_id % 7 + 1),
            html_url=f"https://github.com/example/demo/actions/runs/{run_id}",
        )

    def commit(self, repo: str, ref: str) -> Commit:
        seeds = {"aureon": "a1", "Project-Atreides": "b2", "cannae-kernel": "c3", "L.C.": "d4"}
        return Commit(sha=_sha(seeds[repo]), html_url="https://github.com/example/demo/commit")

    def runs_for_commit(self, repo: str, sha: str) -> list[WorkflowRun]:
        if repo == "cannae-kernel":
            return []
        conclusion = None if sha.startswith("e5") else "success"
        return [
            self._run(11, workflow=(1, "CI"), event="push", sha=sha, conclusion=conclusion),
            self._run(
                12, workflow=(2, "Lint"), event="pull_request", sha=sha, conclusion="success"
            ),
        ]

    def tags(self, repo: str) -> list[Tag]:
        tags = {
            "aureon": [],
            "Project-Atreides": ["v0.3.2", "v0.3.3", "v0.3.10"],
            "cannae-kernel": ["v0.1.0"],
            "L.C.": [],
        }
        return [Tag(name=name) for name in tags[repo]]

    def open_pulls(self, repo: str) -> list[Pull]:
        if repo != "Project-Atreides":
            return []
        return [
            Pull(
                number=101,
                title="Demo: integrity fixes",
                html_url="https://github.com/example/demo/pull/101",
                created_at=self._ago(hours=5),
                head=PullHead(sha=_sha("e5")),
            ),
            Pull(
                number=102,
                title="Demo: pure validation",
                html_url="https://github.com/example/demo/pull/102",
                created_at=self._ago(days=2),
                head=PullHead(sha=_sha("f6")),
            ),
        ]

    def pull_detail(self, repo: str, number: int) -> PullDetail:
        return PullDetail(
            number=number, mergeable_state="behind" if number == _BEHIND_PULL else "clean"
        )

    def scheduled_runs(self, repo: str) -> list[WorkflowRun]:
        if repo == "L.C.":
            raise SourceTimeoutError("Demo: no response in time")
        if repo != "Project-Atreides":
            return []
        return [
            self._run(
                21, workflow=_NIGHTLY, event="schedule", sha=_sha("b2"), conclusion="failure"
            ),
            self._run(
                20, workflow=_NIGHTLY, event="schedule", sha=_sha("b2"), conclusion="success"
            ),
        ]


class DemoAureon:
    """The first snapshot has 3 pending decisions; every later one shows a new deploy with
    0 pending, so the AUR-I-17 marker appears from the second refresh."""

    def __init__(self) -> None:
        self._calls = 0

    def snapshot(self) -> AureonSnapshot:
        self._calls += 1
        first = self._calls == 1
        return AureonSnapshot(
            deploy_sha=_sha("8e") if first else _sha("9f"),
            stack="ready",
            positions=12,
            pending=3 if first else 0,
            market_open=False,
        )


class DemoCashLeg:
    """An invented cash leg for offline demo mode; production reads Aureon live."""

    def cash_leg(self) -> CashLeg:
        return CashLeg(
            scenario="Demo USD 1,000,000 cash leg against a Treasury purchase",
            boundary="Demo only: the entitled member submits",
            funding_disposition="will_queue",
            funding_headline="will_queue — demo shortfall 750000, clears at +5400s",
            rail="fedwire",
            finality_class="GROSS_FINAL",
            decision="PROCEED",
            net_debit_cap_headroom="49250000",
        )


class DemoAgents:
    """A fake Atreides activation snapshot covering every display state.

    Deliberately includes the stopped agent. WP-A4 is the claim that an agent
    which produced nothing renders absent-with-reason and never as a pass, and a
    demonstration that never stops an agent cannot show whether that is true.
    """

    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock

    def _absent(self, kind: AbsenceKind, reason: str) -> Absent:
        return Absent(kind=kind, reason=reason)

    def snapshot(self) -> AgentsSnapshot:
        now = self._clock()
        seen = now - timedelta(seconds=30)
        operator_direct = self._absent(
            AbsenceKind.NOTHING_RECORDED, "operator-direct under CAOM-001"
        )
        no_refusal = self._absent(
            AbsenceKind.NOTHING_RECORDED, "this agent has refused nothing since activation"
        )
        running = self._absent(AbsenceKind.NOT_APPLICABLE, "this agent is running")
        stopped_reason = "demo: stopped by the operator"
        stopped = self._absent(AbsenceKind.NOTHING_RECORDED, f"agent stopped: {stopped_reason}")
        return AgentsSnapshot(
            schema_version=1,
            phase="A",
            synthetic=True,
            taken_at=now,
            tick=Recorded[int](value=42),
            last_tick_at=Recorded[datetime](value=seen),
            halted=False,
            halt_reason=self._absent(
                AbsenceKind.NOT_APPLICABLE, "no halt covering Atreides is in effect"
            ),
            disposition=Disposition.INDETERMINATE,
            agents=(
                AgentView(
                    agent_id="settlement-operations-analyst",
                    tier="TIER_1",
                    role="Settlement Operations Analyst",
                    up=True,
                    expects_refusal=False,
                    disposition=Disposition.PASS,
                    stopped_reason=running,
                    last_summary=Recorded[str](value="Pre-routing gates clear for ficc_gsd_dvp"),
                    last_observed_at=Recorded[datetime](value=seen),
                    last_provenance=Recorded[Provenance](value=Provenance.POLICY_RESULT),
                    last_handoff_basis=operator_direct,
                    last_refusal=no_refusal,
                    recommendations=42,
                    refusals=0,
                ),
                AgentView(
                    agent_id="settlement-investigation-analyst",
                    tier="TIER_1",
                    role="Settlement Investigation Analyst",
                    up=True,
                    expects_refusal=False,
                    disposition=Disposition.HOLD,
                    stopped_reason=running,
                    last_summary=Recorded[str](
                        value="Investigation escalated (evidence_incomplete): 1 source "
                        "unaccounted for"
                    ),
                    last_observed_at=Recorded[datetime](value=seen),
                    last_provenance=Recorded[Provenance](value=Provenance.POLICY_RESULT),
                    last_handoff_basis=operator_direct,
                    last_refusal=no_refusal,
                    recommendations=42,
                    refusals=0,
                ),
                AgentView(
                    agent_id="fiat-operations-specialist",
                    tier="TIER_2",
                    role="FIAT Operations Specialist",
                    up=False,
                    expects_refusal=False,
                    disposition=Disposition.INDETERMINATE,
                    stopped_reason=Recorded[str](value=stopped_reason),
                    last_summary=stopped,
                    last_observed_at=stopped,
                    last_provenance=stopped,
                    last_handoff_basis=stopped,
                    last_refusal=stopped,
                    recommendations=17,
                    refusals=0,
                ),
                AgentView(
                    agent_id="lateral-handoff-probe",
                    tier="TIER_1",
                    role="Lateral handoff probe (WP-A2)",
                    up=True,
                    expects_refusal=True,
                    disposition=Disposition.PASS,
                    stopped_reason=running,
                    last_summary=Recorded[str](
                        value="Input refused at the receiving agent's own type check"
                    ),
                    last_observed_at=Recorded[datetime](value=seen),
                    last_provenance=Recorded[Provenance](value=Provenance.POLICY_RESULT),
                    last_handoff_basis=self._absent(
                        AbsenceKind.NOTHING_RECORDED, "operator-direct under CAOM-001"
                    ),
                    last_refusal=Recorded[RefusalView](
                        value=RefusalView(
                            code="NO_RECORDED_HANDOFF",
                            detail=(
                                "Refused at the receiving agent: a lateral agent-to-agent "
                                "input with no recorded handoff authorization. No C2 runtime "
                                "exists to issue one."
                            ),
                            observed_at=seen,
                        )
                    ),
                    recommendations=0,
                    refusals=42,
                ),
            ),
        )


#: The layers that exist. The middle one is Wave 4, and the board says so rather
#: than leaving three columns blank.
_BUILT_LAYERS = frozenset({Layer.AUREON, Layer.ATREIDES})


class DemoLifecycles:
    """Four synthetic lifecycle objects, covering every state the board can show.

    Deliberately includes the two a demonstration would rather skip: a row held
    at acceptance with nothing written to the Decision System of Record, and a
    row whose Atreides reading is stale. A board that only ever showed clean rows
    would not tell anyone whether the unhappy ones render at all.

    Every value is invented, and three of the six columns are absent on every row
    because the layer that would fill them does not exist yet. **That is the
    picture, not a gap in it.**
    """

    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock

    def _aureon(self, at: datetime, detail: str) -> LayerReading:
        return LayerReading(
            layer=Layer.AUREON,
            built=True,
            current=True,
            detail=detail,
            disposition=Disposition.PASS,
            stamped_at=at,
            provenance=Provenance.HUMAN_JUDGMENT.value,
        )

    def rows(self) -> tuple[LifecycleRow, ...]:
        now = self._clock()
        approved = now - timedelta(minutes=45)
        answered = now - timedelta(minutes=12)

        clean = {
            Checkpoint.APPROVED_INTENT: self._aureon(approved, "approved by operator-bill"),
            Checkpoint.OBLIGATION_ACCEPTANCE: LayerReading(
                layer=Layer.ATREIDES,
                built=True,
                current=True,
                detail="accepted; DSOR-2026-09-21-0001",
                disposition=Disposition.PASS,
                stamped_at=answered,
                provenance=Provenance.POLICY_RESULT.value,
            ),
            Checkpoint.SETTLED: LayerReading(
                layer=Layer.ATREIDES,
                built=True,
                current=True,
                detail="settled, gross-final on fedwire",
                disposition=Disposition.PASS,
                stamped_at=answered,
                provenance=Provenance.FACT_SYNTHETIC.value,
            ),
        }
        held = {
            Checkpoint.APPROVED_INTENT: self._aureon(approved, "approved by operator-bill"),
            Checkpoint.OBLIGATION_ACCEPTANCE: LayerReading(
                layer=Layer.ATREIDES,
                built=True,
                current=True,
                # W2B7-V-01, in the column it belongs in: nothing was written,
                # because no instruction was issued, and the reason is the record.
                detail="quorum hold — nothing recorded: no instruction was issued",
                disposition=Disposition.HOLD,
                stamped_at=answered,
                provenance=Provenance.POLICY_RESULT.value,
            ),
        }
        stale = {
            Checkpoint.APPROVED_INTENT: self._aureon(approved, "approved by operator-bill"),
            Checkpoint.OBLIGATION_ACCEPTANCE: LayerReading(
                layer=Layer.ATREIDES,
                built=True,
                current=False,
                detail="accepted",
                disposition=Disposition.PASS,
                stamped_at=answered,
                provenance=Provenance.POLICY_RESULT.value,
                stale_reason="the Atreides activation snapshot did not answer",
            ),
        }
        intent_only = {
            Checkpoint.APPROVED_INTENT: self._aureon(
                now - timedelta(minutes=3), "approved by operator-bill"
            )
        }
        return tuple(
            build_row(f"lif_01M2P20SY0000000000000{i:04d}", readings, _BUILT_LAYERS)
            for i, readings in enumerate((clean, held, stale, intent_only), start=1)
        )


class DemoEscalations:
    """Three escalations, the oldest deliberately old.

    The queue exists to make the forgotten one visible, so the demonstration data
    includes one that has been waiting nine hours. A demo where everything is
    fresh would show the panel working and not show what it is for.

    No packet carries a recommendation, a resolution or a recipient, because the
    harness cannot produce one and this reader refuses one.
    """

    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock

    def queue(self) -> EscalationQueue:
        now = self._clock()
        return EscalationQueue(
            schema_version=1,
            taken_at=now,
            packets=(
                EscalationPacket(
                    packet_id="ESC-0003",
                    lifecycle_id="lif_01M2P20SY0000000000000003",
                    trigger="LINEAGE_HOLE",
                    raised_at=now - timedelta(minutes=20),
                    disposition=Disposition.BLOCK,
                    summary="the clearing transformation that formed this obligation is missing",
                    findings=(
                        "CLEARING — MISSING: a later stage exists, so CLEARING must too, "
                        "and no record was supplied",
                    ),
                    unknowns=(),
                ),
                EscalationPacket(
                    packet_id="ESC-0002",
                    lifecycle_id="lif_01M2P20SY0000000000000002",
                    trigger="DOCTRINE_AMBIGUITY",
                    raised_at=now - timedelta(hours=2, minutes=40),
                    disposition=Disposition.HOLD,
                    summary="doctrine does not determine which rail applies to this leg",
                    findings=(),
                    unknowns=(
                        Unknown(
                            what="SETTLEMENT_OBLIGATION outcome",
                            why="the executions have not been cleared yet",
                        ),
                    ),
                ),
                EscalationPacket(
                    packet_id="ESC-0001",
                    lifecycle_id="lif_01M2P20SY0000000000000001",
                    trigger="HANDOFF_REFUSED",
                    raised_at=now - timedelta(hours=9, minutes=12),
                    disposition=Disposition.BLOCK,
                    summary=("a lateral agent-to-agent input was refused and the work stopped"),
                    findings=("EXECUTION — BROKEN_LINK: no recorded C2 handoff authorization",),
                    unknowns=(
                        Unknown(
                            what="who would authorize this handoff",
                            why="no C2_HANDOFF authority record exists for this lifecycle",
                        ),
                    ),
                ),
            ),
        )


class DemoBreaks:
    """The flat-position disagreement, until a domain publishes break records."""

    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock

    def breaks(self) -> tuple[BreakRecord, ...]:
        now = self._clock()
        return (
            BreakRecord(
                object_id="position:UST-10Y:demo",
                left_layer="Aureon",
                left_claim="flat position",
                left_stamped_at=now - timedelta(minutes=4),
                right_layer="Atreides",
                right_claim="USD 1,000,000 cash obligation remains",
                right_stamped_at=now - timedelta(minutes=2),
                disposition=Disposition.HOLD,
            ),
        )


class DemoExceptions:
    """Illustrative panel-13 records, admitted only under ``LEGATE_DEMO=1``."""

    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock

    @staticmethod
    def _actor(name: str) -> ActorRef:
        actor_ids = {
            "Ops desk": "act_01K5T7DT000000000000000001",
            "Bill": "act_01K5T7DT000000000000000002",
        }
        return ActorRef(
            actor_id=ActorId(actor_ids[name]),
            actor_kind=ActorKind.HUMAN,
            role=name,
            entitlement_refs=("CAOM-001",),
            authenticated=True,
        )

    def register(self) -> ExceptionRegister:
        now = self._clock()
        specs = (
            (
                "E-101",
                ExceptionKind.BREAK,
                "Break record mismatch",
                "Record mismatch",
                190,
                240,
                "Ops desk",
                Disposition.HOLD,
                ExceptionStatus.INVESTIGATING,
                "Investigating",
                False,
            ),
            (
                "E-102",
                ExceptionKind.ESCALATION,
                "Escalation authority required",
                "Authority gap",
                42,
                60,
                "Bill",
                Disposition.HOLD,
                ExceptionStatus.OPEN,
                "Awaiting decision",
                False,
            ),
            (
                "E-104",
                ExceptionKind.HOLD,
                "Hold has no message format",
                "Message format",
                26,
                120,
                None,
                Disposition.HOLD,
                ExceptionStatus.OPEN,
                "No owner",
                False,
            ),
            (
                "E-105",
                ExceptionKind.BREAK,
                "Break CNS allocation",
                "Allocation",
                1560,
                1440,
                "Ops desk",
                Disposition.BLOCK,
                ExceptionStatus.OPEN,
                "Past SLA",
                False,
            ),
            (
                "E-103",
                ExceptionKind.HOLD,
                "Hold funding timing",
                "Funding timing",
                18,
                90,
                "Ops desk",
                Disposition.HOLD,
                ExceptionStatus.OPEN,
                "Monitoring",
                False,
            ),
            (
                "E-106",
                ExceptionKind.OVERRIDE,
                "Override manual release",
                "Authority gap",
                298,
                480,
                "Bill",
                Disposition.HOLD,
                ExceptionStatus.OPEN,
                "Review due",
                False,
            ),
            (
                "E-099",
                ExceptionKind.BREAK,
                "Break rounding",
                "Rounding",
                2900,
                1440,
                "Ops desk",
                Disposition.PASS,
                ExceptionStatus.WRITTEN_OFF,
                "Written off",
                True,
            ),
        )
        records = []
        for (
            exception_id,
            kind,
            title,
            cause,
            minutes,
            sla,
            owner,
            disposition,
            status,
            status_text,
            written_off,
        ) in specs:
            event_time = now - timedelta(minutes=minutes)
            times = EventTimes(
                event_time=event_time,
                observation_time=event_time + timedelta(seconds=8),
                processing_time=event_time + timedelta(seconds=15),
            )
            records.append(
                ExceptionRecord(
                    exception_id=exception_id,
                    kind=kind,
                    lifecycle_id=f"lif_demo_{exception_id[2:]}",
                    title=title,
                    root_cause=cause,
                    disposition=disposition,
                    status=status,
                    status_text=status_text,
                    first_layer="Atreides" if kind is ExceptionKind.BREAK else "Aureon",
                    first_times=times,
                    sla_target=timedelta(minutes=sla),
                    owner=self._actor(owner) if owner else None,
                    detail=f"Demo detail for {title.lower()}.",
                    close_condition=(
                        f"A published closure record resolves {exception_id} with evidence."
                    ),
                    authority_uri=f"https://example.invalid/dsor/{exception_id}",
                    written_off=written_off,
                    resolved_at=None,
                    trail=(
                        TrailEntry(
                            layer="Atreides" if kind is ExceptionKind.BREAK else "Aureon",
                            times=times,
                            disposition=Disposition.BLOCK if owner is None else disposition,
                            status_text=status_text,
                            evidence=f"demo://exceptions/{exception_id}",
                            provenance=Provenance.FACT_SYNTHETIC,
                        ),
                    ),
                )
            )
        return ExceptionRegister(
            taken_at=now,
            synthetic=True,
            records=tuple(records),
            trend=(9, 8, 8, 7),
        )


class DemoGrc:
    """Illustrative GRC records; production never constructs this source."""

    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock

    @staticmethod
    def _bill() -> ActorRef:
        return ActorRef(
            actor_id=ActorId("act_01K5T7DT000000000000000002"),
            actor_kind=ActorKind.HUMAN,
            role="Single operator under CAOM-001",
            entitlement_refs=("CAOM-001",),
            authenticated=True,
        )

    @staticmethod
    def _times(at: datetime, *, decided: bool = False) -> EventTimes:
        processed = at + timedelta(seconds=12)
        return EventTimes(
            event_time=at,
            observation_time=at + timedelta(seconds=5),
            processing_time=processed,
            decision_time=processed + timedelta(seconds=8) if decided else None,
        )

    def governance(self) -> tuple[GovernanceEvent, ...]:
        now = self._clock()
        specs = (
            ("GOV-104", GovernanceKind.DECISION, "Release funding hold", 18, Disposition.PASS),
            (
                "GOV-103",
                GovernanceKind.OVERRIDE,
                "Manual release after stale stress reading",
                74,
                Disposition.HOLD,
            ),
            (
                "GOV-102",
                GovernanceKind.HALT_RELEASED,
                "Operating halt released",
                180,
                Disposition.PASS,
            ),
            (
                "GOV-101",
                GovernanceKind.DOCTRINE_CHANGE,
                "CATO doctrine v1.1 adopted",
                420,
                Disposition.PASS,
            ),
            ("GOV-100", GovernanceKind.DEPLOY, "COP deployment recorded", 510, Disposition.PASS),
        )
        return tuple(
            GovernanceEvent(
                event_id=event_id,
                kind=kind,
                summary=summary,
                actor=self._bill(),
                times=self._times(now - timedelta(minutes=minutes), decided=True),
                doctrine_version="CATO-1.1",
                evidence=(f"demo://dsor/{event_id}", "CAOM-001"),
                disposition=disposition,
                provenance=Provenance.FACT_SYNTHETIC,
                source_uri=f"https://example.invalid/dsor/{event_id}",
            )
            for event_id, kind, summary, minutes, disposition in specs
        )

    def controls(self) -> tuple[ControlRecord, ...]:
        now = self._clock()
        return (
            ControlRecord(
                "CTL-OFAC",
                "OFAC sanctions screening",
                "Sanctions",
                Disposition.PASS,
                "Screened",
                self._times(now - timedelta(minutes=8)),
                ("demo://verana/ofac/104",),
                ("OFAC",),
                Provenance.FACT_SYNTHETIC,
            ),
            ControlRecord(
                "CTL-ELIG",
                "Security eligibility",
                "Eligibility",
                Disposition.PASS,
                "Eligible",
                self._times(now - timedelta(minutes=12)),
                ("demo://aureon/eligibility/104",),
                ("SEC-ELIG-01",),
                Provenance.FACT_SYNTHETIC,
            ),
            ControlRecord(
                "CTL-AML",
                "AML monitoring coverage",
                "AML",
                Disposition.HOLD,
                "Coverage review due",
                self._times(now - timedelta(hours=3)),
                ("demo://verana/aml/coverage",),
                ("BSA/AML",),
                Provenance.FACT_SYNTHETIC,
            ),
            ControlRecord(
                "CTL-MAP",
                "Regulatory mapping",
                "Mapping",
                Disposition.PASS,
                "No test evidence",
                None,
                (),
                ("OFAC", "BSA/AML"),
                Provenance.FACT_SYNTHETIC,
            ),
        )

    def risks(self) -> tuple[RiskLimit, ...]:
        now = self._clock()
        specs = (
            ("RSK-FUND", "Funding exposure", "820000", "1000000", "USD", 6),
            ("RSK-FAIL", "Settlement-fail rate", "1.8", "2.0", "%", 14),
            ("RSK-CONC", "Issuer concentration", "26", "25", "%", 20),
        )
        return tuple(
            RiskLimit(
                risk_id=risk_id,
                name=name,
                exposure=Decimal(exposure),
                limit=Decimal(limit_),
                unit=unit,
                times=self._times(now - timedelta(minutes=minutes)),
                provenance=Provenance.FACT_SYNTHETIC,
                source_uri=f"https://example.invalid/limits/{risk_id}",
            )
            for risk_id, name, exposure, limit_, unit, minutes in specs
        )
