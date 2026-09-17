"""The in-memory snapshot and the pure rules that compute its derived values.

Everything here is deterministic and free of input/output, so each rule is tested on its
own. The refresher (``cop.refresher``) calls the sources and assembles a ``Snapshot``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from cannae_kernel.disposition import Disposition

from cop.aureon import DEPLOY_SHA_UNSET, AureonSnapshot
from cop.github import Tag, WorkflowRun
from cop.observation import (
    InputUnavailableError,
    Observation,
    SourceFieldAbsentError,
    SourceMalformedError,
)
from cop.program import Program

# Workflow run conclusions, as GitHub reports them.
_PASSING = frozenset({"success"})
_NO_EVIDENCE = frozenset({"neutral", "skipped"})
_FAILING = frozenset(
    {"failure", "timed_out", "cancelled", "action_required", "startup_failure", "stale"}
)
_COMPLETED = "completed"
_SCHEDULE_EVENT = "schedule"
_SHA_PATTERN = re.compile(r"^[0-9a-f]{7,40}$")
_VERSION_TAG = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


# Values ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class RunSummary:
    workflow: str
    status: str
    conclusion: str | None
    created_at: datetime
    html_url: str
    disposition: Disposition


@dataclass(frozen=True)
class CiResult:
    """Combined result of the GitHub Actions runs for one commit."""

    disposition: Disposition
    summary: str
    runs: tuple[RunSummary, ...]


@dataclass(frozen=True)
class TagInfo:
    name: str | None


@dataclass(frozen=True)
class MergeInfo:
    mergeable_state: str

    @property
    def needs_update_branch(self) -> bool:
        return self.mergeable_state == "behind"


@dataclass(frozen=True)
class PullState:
    number: int
    title: str
    html_url: str
    created_at: datetime
    draft: bool
    checks: Observation[CiResult]
    merge: Observation[MergeInfo]


@dataclass(frozen=True)
class RepoState:
    name: str
    html_url: str
    main_head: Observation[str]
    main_ci: Observation[CiResult]
    latest_tag: Observation[TagInfo]
    pulls: Observation[tuple[PullState, ...]]
    scheduled: Observation[tuple[RunSummary, ...]]


@dataclass(frozen=True)
class DriftResult:
    drift: bool
    deployed_sha: str
    main_sha: str

    @property
    def disposition(self) -> Disposition:
        # Drift is expected for a few minutes after a merge, so it asks for attention
        # (HOLD) rather than reporting a failure.
        return Disposition.HOLD if self.drift else Disposition.PASS


@dataclass(frozen=True)
class PendingDropEvent:
    detected_at: datetime
    pending_before: int
    deploy_sha_before: str
    deploy_sha_after: str


@dataclass(frozen=True)
class PendingDropResult:
    """AUR-I-17 marker. ``last_event`` stays set once seen, for the life of the process."""

    detected_now: bool
    last_event: PendingDropEvent | None
    comparisons: int

    @property
    def disposition(self) -> Disposition:
        return Disposition.HOLD if self.last_event is not None else Disposition.PASS


@dataclass(frozen=True)
class AureonState:
    snapshot: Observation[AureonSnapshot]
    drift: Observation[DriftResult]
    pending_drop: Observation[PendingDropResult]


@dataclass(frozen=True)
class Snapshot:
    started_at: datetime
    last_refresh_at: datetime | None
    last_clean_refresh_at: datetime | None
    refresh_seconds: int
    github_authenticated: bool
    demo: bool
    program: Observation[Program]
    repos: tuple[RepoState, ...]
    aureon: AureonState


# Rules -----------------------------------------------------------------------------------


def run_disposition(status: str | None, conclusion: str | None) -> Disposition:
    if status != _COMPLETED:
        return Disposition.HOLD
    if conclusion in _PASSING:
        return Disposition.PASS
    if conclusion in _FAILING:
        return Disposition.BLOCK
    return Disposition.INDETERMINATE


def _summarise(run: WorkflowRun) -> RunSummary:
    return RunSummary(
        workflow=run.name or f"workflow {run.workflow_id}",
        status=run.status or "unknown",
        conclusion=run.conclusion,
        created_at=run.created_at,
        html_url=run.html_url,
        disposition=run_disposition(run.status, run.conclusion),
    )


def latest_per_workflow(runs: Iterable[WorkflowRun]) -> list[WorkflowRun]:
    """The newest run of each workflow, ordered by workflow name."""
    newest: dict[int, WorkflowRun] = {}
    for run in runs:
        kept = newest.get(run.workflow_id)
        if kept is None or (run.created_at, run.id) > (kept.created_at, kept.id):
            newest[run.workflow_id] = run
    return sorted(newest.values(), key=lambda r: ((r.name or "").lower(), r.workflow_id))


def combine_ci(runs: Sequence[WorkflowRun], sha: str) -> CiResult:
    """Combine the non-scheduled Actions runs for ``sha``. No runs is never a pass."""
    relevant = [r for r in runs if r.head_sha == sha and r.event != _SCHEDULE_EVENT]
    latest = [_summarise(r) for r in latest_per_workflow(relevant)]
    total = len(latest)
    if total == 0:
        return CiResult(Disposition.INDETERMINATE, "No workflow runs reported", ())
    failed = sum(1 for r in latest if r.disposition is Disposition.BLOCK)
    running = sum(1 for r in latest if r.disposition is Disposition.HOLD)
    passed = sum(1 for r in latest if r.disposition is Disposition.PASS)
    runs_tuple = tuple(latest)
    if failed:
        return CiResult(Disposition.BLOCK, f"{failed} of {total} workflows failed", runs_tuple)
    if running:
        return CiResult(Disposition.HOLD, f"{running} of {total} workflows running", runs_tuple)
    if passed == 0:
        return CiResult(
            Disposition.INDETERMINATE, "Workflows ran but none reported success", runs_tuple
        )
    if passed < total:
        return CiResult(
            Disposition.PASS,
            f"{passed} of {total} workflows passed; the rest were skipped or neutral",
            runs_tuple,
        )
    return CiResult(Disposition.PASS, f"All {total} workflows passed", runs_tuple)


def scheduled_summary(runs: Sequence[WorkflowRun]) -> tuple[RunSummary, ...]:
    return tuple(
        _summarise(r) for r in latest_per_workflow(r for r in runs if r.event == _SCHEDULE_EVENT)
    )


def latest_tag(tags: Sequence[Tag]) -> TagInfo:
    """Highest ``vMAJOR.MINOR.PATCH`` tag; otherwise the first tag GitHub lists."""
    versioned = [
        (tuple(int(part) for part in match.groups()), tag.name)
        for tag in tags
        if (match := _VERSION_TAG.match(tag.name))
    ]
    if versioned:
        return TagInfo(max(versioned)[1])
    return TagInfo(tags[0].name if tags else None)


def merge_info(mergeable_state: str | None) -> MergeInfo:
    if mergeable_state is None:
        raise SourceFieldAbsentError("Pull request has no mergeable_state")
    return MergeInfo(mergeable_state)


def compute_drift(
    snapshot: Observation[AureonSnapshot], main_head: Observation[str]
) -> DriftResult:
    """Deploy drift: the SHA Aureon serves differs from aureon ``main``.

    Both inputs must be good in the same refresh; otherwise the drift is unknown.
    """
    if not snapshot.ok or snapshot.value is None:
        raise InputUnavailableError("The Aureon snapshot is not current")
    if not main_head.ok or main_head.value is None:
        raise InputUnavailableError("The aureon main commit is not current")
    deployed = snapshot.value.deploy_sha
    if deployed is None:
        raise SourceFieldAbsentError("The Aureon snapshot has no deploy_sha")
    if deployed == DEPLOY_SHA_UNSET:
        raise SourceFieldAbsentError("Aureon reports deploy_sha as unset")
    deployed = deployed.lower()
    if not _SHA_PATTERN.match(deployed):
        raise SourceMalformedError("deploy_sha is not a commit hash")
    main_sha = main_head.value.lower()
    return DriftResult(
        drift=not main_sha.startswith(deployed), deployed_sha=deployed, main_sha=main_sha
    )


def detect_pending_drop(
    previous: AureonSnapshot | None,
    current: AureonSnapshot,
    now: datetime,
    prior: PendingDropResult | None,
) -> PendingDropResult:
    """AUR-I-17: pending fell to 0 in the same refresh interval as ``deploy_sha`` changed.

    ``previous`` is the last good snapshot before ``current``. The first snapshot has
    nothing to compare with, so it cannot show that no drop happened.
    """
    if current.pending is None or current.deploy_sha is None:
        raise SourceFieldAbsentError("The Aureon snapshot has no pending count or deploy_sha")
    if previous is None:
        raise InputUnavailableError("Needs two snapshots to compare; only one so far")
    if previous.pending is None or previous.deploy_sha is None:
        raise InputUnavailableError("The previous snapshot had no pending count or deploy_sha")
    last_event = prior.last_event if prior else None
    comparisons = (prior.comparisons if prior else 0) + 1
    detected = (
        previous.pending > 0 and current.pending == 0 and previous.deploy_sha != current.deploy_sha
    )
    if detected:
        last_event = PendingDropEvent(
            detected_at=now,
            pending_before=previous.pending,
            deploy_sha_before=previous.deploy_sha,
            deploy_sha_after=current.deploy_sha,
        )
    return PendingDropResult(detected_now=detected, last_event=last_event, comparisons=comparisons)
