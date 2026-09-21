"""Background refresher: the only code that calls GitHub or Aureon.

COP-0 data rule 1 (the same rule as Aureon's Cato doctrine): routes never call a source.
The refresher calls every source, builds a complete ``Snapshot`` and swaps it in under a
lock. Routes read ``Refresher.snapshot`` only.

Nothing starts at import time. ``refresh_once`` runs one refresh synchronously (tests call
it with fake clients and a fake clock). ``start`` launches the daemon thread; the app
starts it on the first request. Because the snapshot lives in process memory, the web
server must run exactly one worker process (``gunicorn --workers=1``).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar, cast

from cannae_kernel.provenance import Provenance

from cop import github as gh
from cop.agents import AgentsSnapshot, AgentsSource
from cop.aureon import AureonSnapshot, AureonSource
from cop.observation import (
    UNEXPECTED_ERROR,
    InputUnavailableError,
    Observation,
    SourceError,
    not_configured,
    pending,
)
from cop.program import Program, load_program
from cop.settings import (
    AGENTS_SNAPSHOT_SOURCE,
    AGENTS_SOURCE_UNSET,
    AUREON_REPOSITORY,
    AUREON_SNAPSHOT_URL,
    GITHUB_OWNER,
    GITHUB_WEB_URL,
    MAIN_BRANCH,
    REPOSITORIES,
    STALE_AFTER,
)
from cop.state import (
    AgentsState,
    AureonState,
    CiResult,
    DriftResult,
    MergeInfo,
    PendingDropResult,
    PullState,
    RepoState,
    RunSummary,
    Snapshot,
    TagInfo,
    combine_ci,
    compute_drift,
    detect_pending_drop,
    latest_tag,
    merge_info,
    scheduled_summary,
)

T = TypeVar("T")
Clock = Callable[[], datetime]

log = logging.getLogger(__name__)

PROGRAM_SOURCE = "cop/program.yaml (packaged with this deploy)"


def utc_now() -> datetime:
    return datetime.now(UTC)


def _drift_source(repo_main_url: str) -> str:
    return f"computed from {AUREON_SNAPSHOT_URL} and {repo_main_url}"


def _pending_drop_source() -> str:
    return f"computed from consecutive {AUREON_SNAPSHOT_URL} responses"


@dataclass(frozen=True)
class RefresherOptions:
    program_path: Path
    refresh_seconds: int
    github_authenticated: bool
    demo: bool = False


class Refresher:
    def __init__(
        self,
        *,
        github: gh.GitHubSource,
        aureon: AureonSource,
        options: RefresherOptions,
        clock: Clock = utc_now,
        agents: AgentsSource | None = None,
    ) -> None:
        self._github = github
        self._aureon = aureon
        # ``None`` means no activation snapshot is configured. That is a state the
        # panel reports, not an error it hides: see ``_refresh_agents``.
        self._agents = agents
        self._clock = clock
        self._program_path = options.program_path
        self._refresh_seconds = options.refresh_seconds
        self._good: dict[str, tuple[Any, datetime]] = {}
        self._previous_aureon: AureonSnapshot | None = None
        self._pending_drop: PendingDropResult | None = None
        self._last_clean_refresh_at: datetime | None = None
        self._refresh_lock = threading.Lock()
        self._snapshot_lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._snapshot = self._initial_snapshot(options.github_authenticated, options.demo)
        self._source_failed = False

    # Snapshot access ---------------------------------------------------------------------

    @property
    def snapshot(self) -> Snapshot:
        with self._snapshot_lock:
            return self._snapshot

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _initial_snapshot(self, github_authenticated: bool, demo: bool) -> Snapshot:
        fact, policy = Provenance.FACT_EXTERNAL, Provenance.POLICY_RESULT
        repos = tuple(
            RepoState(
                name=repo,
                html_url=f"{GITHUB_WEB_URL}/{GITHUB_OWNER}/{repo}",
                main_head=pending(
                    f"{repo}:main", gh.commit_url(repo, MAIN_BRANCH), fact, STALE_AFTER
                ),
                main_ci=pending(f"{repo}:main_ci", gh.runs_url(repo), fact, STALE_AFTER),
                latest_tag=pending(f"{repo}:tag", gh.tags_url(repo), fact, STALE_AFTER),
                pulls=pending(f"{repo}:pulls", gh.pulls_url(repo), fact, STALE_AFTER),
                scheduled=pending(f"{repo}:scheduled", gh.runs_url(repo), fact, STALE_AFTER),
            )
            for repo in REPOSITORIES
        )
        main_url = gh.commit_url(AUREON_REPOSITORY, MAIN_BRANCH)
        return Snapshot(
            started_at=self._clock(),
            last_refresh_at=None,
            last_clean_refresh_at=None,
            refresh_seconds=self._refresh_seconds,
            github_authenticated=github_authenticated,
            demo=demo,
            program=pending("program", PROGRAM_SOURCE, Provenance.HUMAN_JUDGMENT, None),
            repos=repos,
            agents=AgentsState(
                snapshot=pending("agents:snapshot", AGENTS_SNAPSHOT_SOURCE, fact, STALE_AFTER)
            ),
            aureon=AureonState(
                snapshot=pending("aureon:snapshot", AUREON_SNAPSHOT_URL, fact, STALE_AFTER),
                drift=pending("aureon:drift", _drift_source(main_url), policy, STALE_AFTER),
                pending_drop=pending(
                    "aureon:pending_drop", _pending_drop_source(), policy, STALE_AFTER
                ),
            ),
        )

    # One observation ---------------------------------------------------------------------

    def _observe(
        self,
        key: str,
        source_url: str,
        provenance: Provenance,
        fetch: Callable[[], T],
        stale_after: timedelta | None = STALE_AFTER,
    ) -> Observation[T]:
        now = self._clock()
        try:
            value = fetch()
        except SourceError as exc:
            error_class, detail = exc.error_class, exc.detail
        except Exception as exc:
            # A bug or an unforeseen response must still fail closed, not break the refresh.
            log.exception("Unexpected error while refreshing %s", key)
            error_class, detail = UNEXPECTED_ERROR, type(exc).__name__
        else:
            self._good[key] = (value, now)
            return Observation(
                key=key,
                source_url=source_url,
                provenance=provenance,
                attempted_at=now,
                value=value,
                observed_at=now,
                stale_after=stale_after,
            )
        if provenance is not Provenance.POLICY_RESULT:
            # Only a source failing spoils a clean refresh; a computed value that cannot
            # be computed yet (for example the first AUR-I-17 comparison) does not.
            self._source_failed = True
        log.warning("Source %s failed: %s (%s)", key, error_class, detail)
        last = self._good.get(key)
        return Observation(
            key=key,
            source_url=source_url,
            provenance=provenance,
            attempted_at=now,
            error_class=error_class,
            error_detail=detail,
            last_good_value=cast("T", last[0]) if last else None,
            last_good_at=last[1] if last else None,
            stale_after=stale_after,
        )

    # Sources -----------------------------------------------------------------------------

    def _refresh_pull(self, repo: str, pull: gh.Pull) -> PullState:
        base = f"{repo}:pr:{pull.number}"
        checks: Observation[CiResult] = self._observe(
            f"{base}:checks",
            gh.runs_url(repo),
            Provenance.FACT_EXTERNAL,
            lambda: combine_ci(self._github.runs_for_commit(repo, pull.head.sha), pull.head.sha),
        )
        merge: Observation[MergeInfo] = self._observe(
            f"{base}:merge",
            gh.pull_url(repo, pull.number),
            Provenance.FACT_EXTERNAL,
            lambda: merge_info(self._github.pull_detail(repo, pull.number).mergeable_state),
        )
        return PullState(
            number=pull.number,
            title=pull.title,
            html_url=pull.html_url,
            created_at=pull.created_at,
            draft=pull.draft,
            checks=checks,
            merge=merge,
        )

    def _refresh_repo(self, repo: str) -> RepoState:
        fact = Provenance.FACT_EXTERNAL
        main_head: Observation[str] = self._observe(
            f"{repo}:main",
            gh.commit_url(repo, MAIN_BRANCH),
            fact,
            lambda: self._github.commit(repo, MAIN_BRANCH).sha,
        )

        def main_ci() -> CiResult:
            if not main_head.ok or main_head.value is None:
                raise InputUnavailableError("The main commit could not be read in this refresh")
            sha = main_head.value
            return combine_ci(self._github.runs_for_commit(repo, sha), sha)

        ci: Observation[CiResult] = self._observe(
            f"{repo}:main_ci", gh.runs_url(repo), fact, main_ci
        )
        tag: Observation[TagInfo] = self._observe(
            f"{repo}:tag", gh.tags_url(repo), fact, lambda: latest_tag(self._github.tags(repo))
        )

        def pulls() -> tuple[PullState, ...]:
            listed = sorted(self._github.open_pulls(repo), key=lambda p: p.number)
            return tuple(self._refresh_pull(repo, pull) for pull in listed)

        pull_states: Observation[tuple[PullState, ...]] = self._observe(
            f"{repo}:pulls", gh.pulls_url(repo), fact, pulls
        )
        scheduled: Observation[tuple[RunSummary, ...]] = self._observe(
            f"{repo}:scheduled",
            gh.runs_url(repo) + "?event=schedule",
            fact,
            lambda: scheduled_summary(self._github.scheduled_runs(repo)),
        )
        return RepoState(
            name=repo,
            html_url=f"{GITHUB_WEB_URL}/{GITHUB_OWNER}/{repo}",
            main_head=main_head,
            main_ci=ci,
            latest_tag=tag,
            pulls=pull_states,
            scheduled=scheduled,
        )

    def _refresh_aureon(self, aureon_repo: RepoState | None) -> AureonState:
        snapshot: Observation[AureonSnapshot] = self._observe(
            "aureon:snapshot", AUREON_SNAPSHOT_URL, Provenance.FACT_EXTERNAL, self._aureon.snapshot
        )
        main_url = gh.commit_url(AUREON_REPOSITORY, MAIN_BRANCH)

        def drift() -> DriftResult:
            if aureon_repo is None:
                raise InputUnavailableError("The aureon repository is not configured")
            return compute_drift(snapshot, aureon_repo.main_head)

        drift_obs: Observation[DriftResult] = self._observe(
            "aureon:drift", _drift_source(main_url), Provenance.POLICY_RESULT, drift
        )

        def pending_drop() -> PendingDropResult:
            if not snapshot.ok or snapshot.value is None:
                raise InputUnavailableError("The Aureon snapshot is not current")
            current = snapshot.value
            try:
                result = detect_pending_drop(
                    self._previous_aureon, current, self._clock(), self._pending_drop
                )
            finally:
                if current.pending is not None and current.deploy_sha is not None:
                    self._previous_aureon = current
            self._pending_drop = result
            return result

        drop_obs: Observation[PendingDropResult] = self._observe(
            "aureon:pending_drop", _pending_drop_source(), Provenance.POLICY_RESULT, pending_drop
        )
        return AureonState(snapshot=snapshot, drift=drift_obs, pending_drop=drop_obs)

    def _refresh_agents(self) -> AgentsState:
        """Read the activation document, or record why there is none.

        An unconfigured source is not a failed one: nothing is wrong with a
        source that was never set up, so it does not spoil a clean refresh and
        does not appear in the stale-source warning. It is still INDETERMINATE,
        because a picture that cannot see the agents may not report them fine.
        """
        if self._agents is None:
            snapshot: Observation[AgentsSnapshot] = not_configured(
                "agents:snapshot",
                AGENTS_SNAPSHOT_SOURCE,
                Provenance.FACT_EXTERNAL,
                AGENTS_SOURCE_UNSET,
                STALE_AFTER,
            )
            return AgentsState(snapshot=snapshot)
        return AgentsState(
            snapshot=self._observe(
                "agents:snapshot",
                AGENTS_SNAPSHOT_SOURCE,
                Provenance.FACT_EXTERNAL,
                self._agents.snapshot,
            )
        )

    # Refresh -----------------------------------------------------------------------------

    def refresh_once(self) -> Snapshot:
        """Call every source once, then publish the new snapshot. Never raises."""
        with self._refresh_lock:
            self._source_failed = False
            program: Observation[Program] = self._observe(
                "program",
                PROGRAM_SOURCE,
                Provenance.HUMAN_JUDGMENT,
                lambda: load_program(self._program_path),
                stale_after=None,
            )
            repos = tuple(self._refresh_repo(repo) for repo in REPOSITORIES)
            aureon_repo = next((r for r in repos if r.name == AUREON_REPOSITORY), None)
            aureon = self._refresh_aureon(aureon_repo)
            agents = self._refresh_agents()
            now = self._clock()
            if not self._source_failed:
                self._last_clean_refresh_at = now
            previous = self.snapshot
            new = Snapshot(
                started_at=previous.started_at,
                last_refresh_at=now,
                last_clean_refresh_at=self._last_clean_refresh_at,
                refresh_seconds=self._refresh_seconds,
                github_authenticated=previous.github_authenticated,
                demo=previous.demo,
                program=program,
                repos=repos,
                aureon=aureon,
                agents=agents,
            )
            with self._snapshot_lock:
                self._snapshot = new
            return new

    def load_program_now(self) -> None:
        """Validate the program file at startup, before any network refresh has run."""
        with self._refresh_lock:
            self._source_failed = False
            program: Observation[Program] = self._observe(
                "program",
                PROGRAM_SOURCE,
                Provenance.HUMAN_JUDGMENT,
                lambda: load_program(self._program_path),
                stale_after=None,
            )
            with self._snapshot_lock:
                old = self._snapshot
                self._snapshot = Snapshot(
                    started_at=old.started_at,
                    last_refresh_at=old.last_refresh_at,
                    last_clean_refresh_at=old.last_clean_refresh_at,
                    refresh_seconds=old.refresh_seconds,
                    github_authenticated=old.github_authenticated,
                    demo=old.demo,
                    program=program,
                    repos=old.repos,
                    aureon=old.aureon,
                    agents=old.agents,
                )
            if not program.ok:
                log.error("Program file is invalid: %s", program.error_detail)

    # Thread ------------------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.refresh_once()
            except Exception:  # pragma: no cover - refresh_once already contains failures
                log.exception("Refresh failed")
            self._stop.wait(self._refresh_seconds)

    def start(self) -> None:
        """Start the refresh thread once. Safe to call on every request."""
        if self._thread is not None:
            return
        with self._start_lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(target=self._run, name="cop-refresher", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
