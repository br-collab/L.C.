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

from cannae_kernel.absence import AbsenceKind, Absent, Recorded
from cannae_kernel.disposition import Disposition
from cannae_kernel.provenance import Provenance

from cop.agents import AgentsSnapshot, AgentView, RefusalView
from cop.aureon import AureonSnapshot
from cop.github import Commit, Pull, PullDetail, PullHead, Tag, WorkflowRun
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
