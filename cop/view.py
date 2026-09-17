"""Turns a ``Snapshot`` into what the templates show, at the time of the request.

Rules applied here, so that templates cannot get them wrong:
- a tile whose source failed, or whose value is older than ``STALE_AFTER``, shows
  INDETERMINATE and puts its value under ``stale_value`` (never ``value``);
- every status badge carries a plain-language label as well as a colour (tone);
- the overall program state is the worst of its inputs and is never PASS while any
  source is unknown.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from typing import Any, Generic, TypeVar

from cannae_kernel.disposition import Disposition
from cannae_kernel.provenance import Provenance

from cop.aureon import AureonSnapshot
from cop.observation import Observation
from cop.program import Program, WorkStatus
from cop.settings import (
    AUREON_SNAPSHOT_URL,
    PRODUCT_NAME,
    REFRESH_SECONDS_WITHOUT_TOKEN,
    STALE_AFTER,
)
from cop.state import (
    CiResult,
    DriftResult,
    MergeInfo,
    PendingDropResult,
    PullState,
    RepoState,
    RunSummary,
    Snapshot,
    TagInfo,
)

T = TypeVar("T")

STALE_ERROR = "StaleData"
STACK_READY = "ready"
STACK_STARTING = frozenset({"initializing", "running"})
SHORT_SHA = 7
_MINUTE = 60
_HOUR = 3600
_DAY = 86400


class Tone(StrEnum):
    GOOD = "good"
    ATTENTION = "attention"
    BAD = "bad"
    UNKNOWN = "unknown"
    PROGRESS = "progress"
    NEUTRAL = "neutral"


@dataclass(frozen=True)
class Badge:
    code: str
    label: str
    tone: Tone


_DISPOSITION_TONES = {
    Disposition.PASS: Tone.GOOD,
    Disposition.HOLD: Tone.ATTENTION,
    Disposition.BLOCK: Tone.BAD,
    Disposition.INDETERMINATE: Tone.UNKNOWN,
}

WORK_STATUS_BADGES = {
    WorkStatus.DONE: Badge(WorkStatus.DONE, "Done", Tone.GOOD),
    WorkStatus.IN_PROGRESS: Badge(WorkStatus.IN_PROGRESS, "In progress", Tone.PROGRESS),
    WorkStatus.BLOCKED: Badge(WorkStatus.BLOCKED, "Blocked", Tone.BAD),
    WorkStatus.NOT_STARTED: Badge(WorkStatus.NOT_STARTED, "Not started", Tone.NEUTRAL),
}

OBSERVED = Badge("OBSERVED", "Read from source", Tone.NEUTRAL)

_PROVENANCE_LABELS = {
    Provenance.FACT_EXTERNAL: "Fetched fact",
    Provenance.POLICY_RESULT: "Computed result",
    Provenance.HUMAN_JUDGMENT: "Maintained by hand",
}


def disposition_badge(disposition: Disposition, label: str) -> Badge:
    return Badge(disposition.value, label, _DISPOSITION_TONES[disposition])


def unknown_badge(reason: str) -> Badge:
    return disposition_badge(Disposition.INDETERMINATE, f"Unknown: {reason}")


# Time ------------------------------------------------------------------------------------


def fmt_time(value: datetime | None) -> str:
    if value is None:
        return "never"
    return value.strftime("%d %b %Y %H:%M:%S UTC")


def fmt_date(value: datetime) -> str:
    return value.strftime("%d %b %Y")


def fmt_age(delta: timedelta) -> str:
    seconds = max(0, int(delta.total_seconds()))
    if seconds < _MINUTE:
        return f"{seconds} s"
    if seconds < _HOUR:
        return f"{seconds // _MINUTE} min"
    if seconds < _DAY:
        return f"{seconds // _HOUR} h {(seconds % _HOUR) // _MINUTE} min"
    days = seconds // _DAY
    return f"{days} day" if days == 1 else f"{days} days"


# Tiles -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProvenanceView:
    source_url: str
    is_link: bool
    observed_at: str
    kind: str
    kind_label: str


@dataclass(frozen=True)
class TileView(Generic[T]):
    current: bool
    value: T | None
    stale_value: T | None
    stale_at: str | None
    badge: Badge
    error_class: str | None
    error_detail: str | None
    provenance: ProvenanceView

    @property
    def shown(self) -> T | None:
        return self.value if self.current else self.stale_value


def _provenance(obs: Observation[Any]) -> ProvenanceView:
    return ProvenanceView(
        source_url=obs.source_url,
        is_link=obs.source_url.startswith("https://"),
        observed_at=fmt_time(obs.observed_at if obs.ok else obs.attempted_at),
        kind=obs.provenance.value,
        kind_label=_PROVENANCE_LABELS.get(obs.provenance, obs.provenance.value),
    )


def tile(obs: Observation[T], now: datetime, badge_for: Callable[[T], Badge]) -> TileView[T]:
    provenance = _provenance(obs)
    if obs.is_current(now) and obs.value is not None:
        return TileView(True, obs.value, None, None, badge_for(obs.value), None, None, provenance)
    error_class: str
    detail: str | None
    if obs.ok:
        # The source answered, but too long ago: the refresher may have stopped.
        stale_value, stale_at = obs.value, obs.observed_at
        error_class = STALE_ERROR
        detail = f"Last refreshed more than {int(STALE_AFTER.total_seconds() // 60)} minutes ago"
    else:
        stale_value, stale_at = obs.last_good_value, obs.last_good_at
        error_class = obs.error_class or STALE_ERROR
        detail = obs.error_detail
    return TileView(
        current=False,
        value=None,
        stale_value=stale_value,
        stale_at=fmt_time(stale_at) if stale_value is not None else None,
        badge=unknown_badge(error_class),
        error_class=error_class,
        error_detail=detail,
        provenance=provenance,
    )


def _observed(_: object) -> Badge:
    return OBSERVED


def ci_badge(result: CiResult) -> Badge:
    return disposition_badge(result.disposition, result.summary)


def run_badge(run: RunSummary) -> Badge:
    labels = {
        Disposition.PASS: "Passed",
        Disposition.HOLD: "Running",
        Disposition.BLOCK: f"Failed ({run.conclusion})",
        Disposition.INDETERMINATE: f"Not confirmed ({run.conclusion or 'no result'})",
    }
    return disposition_badge(run.disposition, labels[run.disposition])


def merge_badge(info: MergeInfo) -> Badge:
    if info.needs_update_branch:
        return disposition_badge(Disposition.HOLD, "Needs update branch")
    if info.mergeable_state == "unknown":
        return unknown_badge("GitHub is still computing")
    if info.mergeable_state == "dirty":
        return disposition_badge(Disposition.BLOCK, "Merge conflict")
    return Badge(
        info.mergeable_state.upper(),
        f"No update needed (GitHub merge state: {info.mergeable_state})",
        Tone.NEUTRAL,
    )


def drift_badge(result: DriftResult) -> Badge:
    if result.drift:
        return disposition_badge(Disposition.HOLD, "Drift: deployed commit is not aureon main")
    return disposition_badge(Disposition.PASS, "No drift: deployed commit is aureon main")


def pending_drop_badge(result: PendingDropResult) -> Badge:
    if result.last_event is not None:
        return disposition_badge(
            Disposition.HOLD, "AUR-I-17 seen: pending decisions fell to 0 at a deploy"
        )
    return disposition_badge(Disposition.PASS, "No pending drop seen at a deploy")


def program_badge(_: Program) -> Badge:
    return disposition_badge(Disposition.PASS, "Program file valid")


def stack_badge(stack: str) -> Badge:
    if stack == STACK_READY:
        return disposition_badge(Disposition.PASS, "Ready")
    if stack in STACK_STARTING:
        return disposition_badge(Disposition.HOLD, f"Starting ({stack})")
    return unknown_badge(f"unrecognised stack state {stack!r}")


# Panels ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class PullView:
    number: int
    title: str
    html_url: str
    draft: bool
    age: str
    opened: str
    checks: TileView[CiResult]
    merge: TileView[MergeInfo]


@dataclass(frozen=True)
class RepoView:
    name: str
    html_url: str
    main_head: TileView[str]
    main_ci: TileView[CiResult]
    latest_tag: TileView[TagInfo]
    pulls: TileView[tuple[PullView, ...]]
    scheduled: TileView[tuple[RunSummary, ...]]


@dataclass(frozen=True)
class FieldView:
    label: str
    text: str
    badge: Badge


@dataclass(frozen=True)
class AureonView:
    snapshot: TileView[AureonSnapshot]
    fields: tuple[FieldView, ...]
    drift: TileView[DriftResult]
    pending_drop: TileView[PendingDropResult]


@dataclass(frozen=True)
class Reason:
    badge: Badge
    text: str


@dataclass(frozen=True)
class BannerView:
    product_name: str
    overall: Badge
    reasons: tuple[Reason, ...]
    last_refresh_at: str
    last_clean_refresh_at: str
    now: str
    stale_sources: tuple[str, ...]
    github_authenticated: bool
    refresh_seconds: int
    demo: bool


@dataclass(frozen=True)
class DecisionView:
    id: str
    title: str
    owner: str
    opened: str
    age: str


@dataclass(frozen=True)
class PageView:
    banner: BannerView
    program: TileView[Program]
    decisions: tuple[DecisionView, ...]
    repos: tuple[RepoView, ...]
    aureon: AureonView
    work_status_badges: dict[WorkStatus, Badge] = field(
        default_factory=lambda: dict(WORK_STATUS_BADGES)
    )


def _pull_view(pull: PullState, now: datetime) -> PullView:
    return PullView(
        number=pull.number,
        title=pull.title,
        html_url=pull.html_url,
        draft=pull.draft,
        age=fmt_age(now - pull.created_at),
        opened=fmt_date(pull.created_at),
        checks=tile(pull.checks, now, ci_badge),
        merge=tile(pull.merge, now, merge_badge),
    )


def _repo_view(repo: RepoState, now: datetime) -> RepoView:
    pulls_tile = tile(repo.pulls, now, _observed)
    # Pull request rows are built from whichever list is shown (current or stale).
    pull_views = tuple(_pull_view(p, now) for p in (pulls_tile.shown or ()))
    pulls = TileView(
        current=pulls_tile.current,
        value=pull_views if pulls_tile.current else None,
        stale_value=None if pulls_tile.current or pulls_tile.stale_value is None else pull_views,
        stale_at=pulls_tile.stale_at,
        badge=pulls_tile.badge,
        error_class=pulls_tile.error_class,
        error_detail=pulls_tile.error_detail,
        provenance=pulls_tile.provenance,
    )
    return RepoView(
        name=repo.name,
        html_url=repo.html_url,
        main_head=tile(repo.main_head, now, _observed),
        main_ci=tile(repo.main_ci, now, ci_badge),
        latest_tag=tile(repo.latest_tag, now, _observed),
        pulls=pulls,
        scheduled=tile(repo.scheduled, now, _observed),
    )


def _aureon_fields(snapshot: TileView[AureonSnapshot]) -> tuple[FieldView, ...]:
    value = snapshot.value if snapshot.current else None

    def absent(label: str) -> FieldView:
        reason = "field absent from the snapshot" if value is not None else "snapshot not current"
        return FieldView(label, "—", unknown_badge(reason))

    if value is None:
        labels = ("Deployed commit", "Stack", "Positions", "Pending decisions", "Market open")
        return tuple(absent(label) for label in labels)
    fields: list[FieldView] = []
    if value.deploy_sha is None:
        fields.append(absent("Deployed commit"))
    else:
        fields.append(FieldView("Deployed commit", value.deploy_sha[:12], OBSERVED))
    if value.stack is None:
        fields.append(absent("Stack"))
    else:
        fields.append(FieldView("Stack", value.stack, stack_badge(value.stack)))
    for label, number in (("Positions", value.positions), ("Pending decisions", value.pending)):
        if number is None:
            fields.append(absent(label))
        else:
            fields.append(FieldView(label, str(number), OBSERVED))
    if value.market_open is None:
        fields.append(absent("Market open"))
    else:
        text = "Open" if value.market_open else "Closed"
        fields.append(FieldView("Market open", text, Badge(text.upper(), text, Tone.NEUTRAL)))
    return tuple(fields)


_SEVERITY_CODES = frozenset(d.value for d in Disposition)
_SEVERITY = {
    Disposition.BLOCK: 3,
    Disposition.INDETERMINATE: 2,
    Disposition.HOLD: 1,
    Disposition.PASS: 0,
}


Found = list[tuple[Disposition, str]]


def _unknown_if_not_current(found: Found, t: TileView[Any], what: str) -> bool:
    if not t.current:
        found.append((Disposition.INDETERMINATE, f"{what}: {t.error_class}"))
    return t.current


def _program_reasons(found: Found, program: TileView[Program]) -> None:
    if _unknown_if_not_current(found, program, "Program file") and program.value is not None:
        for wave in program.value.waves:
            if wave.status is WorkStatus.BLOCKED:
                found.append((Disposition.BLOCK, f"Wave {wave.number} is blocked"))


def _repo_reasons(found: Found, repo: RepoView) -> None:
    _unknown_if_not_current(found, repo.main_head, f"{repo.name} main commit")
    _unknown_if_not_current(found, repo.latest_tag, f"{repo.name} latest tag")
    _unknown_if_not_current(found, repo.pulls, f"{repo.name} pull requests")
    ci = repo.main_ci.value
    if _unknown_if_not_current(found, repo.main_ci, f"{repo.name} CI on main") and ci:
        if ci.disposition is not Disposition.PASS:
            found.append((ci.disposition, f"{repo.name} CI on main: {ci.summary}"))
    if _unknown_if_not_current(found, repo.scheduled, f"{repo.name} scheduled checks"):
        for run in repo.scheduled.value or ():
            if run.disposition is not Disposition.PASS:
                text = f"{repo.name} scheduled {run.workflow}: {run_badge(run).label}"
                found.append((run.disposition, text))


def _aureon_reasons(found: Found, aureon: AureonView) -> None:
    if _unknown_if_not_current(found, aureon.snapshot, "Aureon snapshot"):
        for fld in aureon.fields:
            if fld.badge.code in _SEVERITY_CODES and fld.badge.code != Disposition.PASS:
                disposition = Disposition(fld.badge.code)
                found.append((disposition, f"Aureon {fld.label}: {fld.badge.label}"))
    drift = aureon.drift.value
    if _unknown_if_not_current(found, aureon.drift, "Aureon deploy drift") and drift:
        if drift.drift:
            found.append((Disposition.HOLD, "Aureon deployed commit is not aureon main"))
    drop = aureon.pending_drop.value
    if _unknown_if_not_current(found, aureon.pending_drop, "AUR-I-17 marker") and drop:
        if drop.last_event is not None:
            found.append((Disposition.HOLD, "Pending decisions fell to 0 at a deploy (AUR-I-17)"))


_OVERALL_LABELS = {
    Disposition.BLOCK: "Something has failed",
    Disposition.INDETERMINATE: "Not confirmed: some sources are unavailable or stale",
    Disposition.HOLD: "Needs attention",
}


def _overall(
    program: TileView[Program],
    repos: Sequence[RepoView],
    aureon: AureonView,
) -> tuple[Badge, tuple[Reason, ...]]:
    """Worst of: every source tile, CI on main, scheduled runs, Aureon stack, drift,
    the AUR-I-17 marker and blocked waves. Open pull request checks are not included."""
    found: Found = []
    _program_reasons(found, program)
    for repo in repos:
        _repo_reasons(found, repo)
    _aureon_reasons(found, aureon)
    if not found:
        return disposition_badge(Disposition.PASS, "All sources current; nothing failing"), ()
    worst = max((d for d, _ in found), key=lambda d: _SEVERITY[d])
    ordered = sorted(found, key=lambda item: -_SEVERITY[item[0]])
    reasons = tuple(Reason(disposition_badge(d, d.value), text) for d, text in ordered)
    return disposition_badge(worst, _OVERALL_LABELS[worst]), reasons


def _stale_sources(snapshot: Snapshot, now: datetime) -> tuple[str, ...]:
    """Sources with no good value, or whose newest good value is older than STALE_AFTER."""

    def too_old(obs: Observation[Any]) -> bool:
        good_at = obs.good_at()
        return good_at is None or now - good_at > STALE_AFTER

    stale: list[str] = []
    for repo in snapshot.repos:
        observations: list[Observation[Any]] = [
            repo.main_head,
            repo.main_ci,
            repo.latest_tag,
            repo.pulls,
            repo.scheduled,
        ]
        if any(too_old(o) for o in observations):
            stale.append(f"GitHub: {repo.name}")
    if too_old(snapshot.aureon.snapshot):
        stale.append(f"Aureon snapshot ({AUREON_SNAPSHOT_URL})")
    return tuple(stale)


def build_page(snapshot: Snapshot, now: datetime) -> PageView:
    program = tile(snapshot.program, now, program_badge)
    repos = tuple(_repo_view(repo, now) for repo in snapshot.repos)
    snapshot_tile = tile(snapshot.aureon.snapshot, now, _observed)
    aureon = AureonView(
        snapshot=snapshot_tile,
        fields=_aureon_fields(snapshot_tile),
        drift=tile(snapshot.aureon.drift, now, drift_badge),
        pending_drop=tile(snapshot.aureon.pending_drop, now, pending_drop_badge),
    )
    overall, reasons = _overall(program, repos, aureon)
    decisions: tuple[DecisionView, ...] = ()
    if program.current and program.value is not None:
        decisions = tuple(
            DecisionView(
                id=d.id,
                title=d.title,
                owner=d.owner.value,
                opened=d.opened.strftime("%d %b %Y"),
                age=fmt_age(now - datetime.combine(d.opened, time(), tzinfo=UTC)),
            )
            for d in program.value.decisions
        )
    banner = BannerView(
        product_name=PRODUCT_NAME,
        overall=overall,
        reasons=reasons,
        last_refresh_at=fmt_time(snapshot.last_refresh_at),
        last_clean_refresh_at=fmt_time(snapshot.last_clean_refresh_at),
        now=fmt_time(now),
        stale_sources=_stale_sources(snapshot, now),
        github_authenticated=snapshot.github_authenticated,
        refresh_seconds=snapshot.refresh_seconds,
        demo=snapshot.demo,
    )
    return PageView(
        banner=banner,
        program=program,
        decisions=decisions,
        repos=repos,
        aureon=aureon,
    )


def short_sha(sha: str | None) -> str:
    return sha[:SHORT_SHA] if sha else "—"


UNAUTHENTICATED_REFRESH_MINUTES = REFRESH_SECONDS_WITHOUT_TOKEN // 60
