"""Fake GitHub and Aureon sources for local demonstration and screenshots.

Enabled only by ``LEGATE_DEMO=1`` (the variable name is in ``cop.settings``), and refused
when a production marker such as ``PORT`` is set. Every value here is invented. The page
shows a "demo data" warning in the banner whenever these sources are in use.

The fake data deliberately includes a failing scheduled run, a pull request that needs
an update, a timed-out source, deploy drift and a pending drop at a deploy, so that each
display state is visible.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

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
