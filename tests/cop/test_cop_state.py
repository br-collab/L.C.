"""Deploy drift, the AUR-I-17 pending-drop marker, and the CI combination rule."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

import pytest
from cannae_kernel.disposition import Disposition
from cannae_kernel.provenance import Provenance
from cop_fakes import MAIN_SHA, Rig, login

from cop.aureon import AureonSnapshot
from cop.github import Tag, WorkflowRun
from cop.observation import (
    InputUnavailableError,
    Observation,
    SourceFieldAbsentError,
    SourceMalformedError,
)
from cop.state import combine_ci, compute_drift, detect_pending_drop, latest_tag
from cop.view import build_page

NOW = datetime(2026, 9, 17, 15, 0, tzinfo=UTC)
T = TypeVar("T")


def _obs(value: T) -> Observation[T]:
    return Observation("k", "https://example.test", Provenance.FACT_EXTERNAL, NOW, value, NOW)


def _failed() -> Observation[Any]:
    return Observation(
        "k", "https://example.test", Provenance.FACT_EXTERNAL, NOW, error_class="Timeout"
    )


# Deploy drift ----------------------------------------------------------------------------


def test_drift_false_when_deployed_commit_is_main() -> None:
    result = compute_drift(_obs(AureonSnapshot(deploy_sha="a" * 40)), _obs("a" * 40))
    assert result.drift is False and result.disposition is Disposition.PASS


def test_drift_false_for_matching_short_sha() -> None:
    result = compute_drift(_obs(AureonSnapshot(deploy_sha="A" * 12)), _obs("a" * 40))
    assert result.drift is False


def test_drift_true_when_deployed_commit_differs() -> None:
    result = compute_drift(_obs(AureonSnapshot(deploy_sha="f" * 40)), _obs("a" * 40))
    assert result.drift is True and result.disposition is Disposition.HOLD


@pytest.mark.parametrize(
    ("deploy_sha", "error"),
    [
        (None, SourceFieldAbsentError),
        ("unset", SourceFieldAbsentError),
        ("not-a-sha", SourceMalformedError),
    ],
)
def test_drift_unknown_without_a_usable_deploy_sha(deploy_sha: str | None, error: type) -> None:
    with pytest.raises(error):
        compute_drift(_obs(AureonSnapshot(deploy_sha=deploy_sha)), _obs("a" * 40))


def test_drift_unknown_when_an_input_failed() -> None:
    with pytest.raises(InputUnavailableError):
        compute_drift(_failed(), _obs("a" * 40))
    with pytest.raises(InputUnavailableError):
        compute_drift(_obs(AureonSnapshot(deploy_sha="a" * 40)), _failed())


def test_drift_through_the_refresher_true_then_false() -> None:
    rig = Rig()
    rig.aureon.body["deploy_sha"] = "9" * 40
    snap = rig.refresher.refresh_once()
    assert snap.aureon.drift.value is not None and snap.aureon.drift.value.drift is True
    page = build_page(snap, rig.clock.now)
    assert page.aureon.drift.badge.code == "HOLD"
    assert "Drift" in page.aureon.drift.badge.label

    rig.clock.advance(seconds=60)
    rig.aureon.body["deploy_sha"] = MAIN_SHA["aureon"]
    snap = rig.refresher.refresh_once()
    assert snap.aureon.drift.value is not None and snap.aureon.drift.value.drift is False
    assert build_page(snap, rig.clock.now).aureon.drift.badge.code == "PASS"


def test_missing_snapshot_field_is_indeterminate_on_its_own() -> None:
    rig = Rig()
    del rig.aureon.body["market_open"]
    del rig.aureon.body["deploy_sha"]
    snap = rig.refresher.refresh_once()
    assert snap.aureon.snapshot.ok
    page = build_page(snap, rig.clock.now)
    fields = {f.label: f for f in page.aureon.fields}
    assert fields["Market open"].badge.code == "INDETERMINATE"
    assert fields["Deployed commit"].badge.code == "INDETERMINATE"
    assert fields["Positions"].text == "12"
    assert snap.aureon.drift.error_class == "FieldAbsent"


def test_wrongly_typed_snapshot_field_makes_the_snapshot_malformed() -> None:
    rig = Rig()
    rig.aureon.body["pending"] = "4"
    snap = rig.refresher.refresh_once()
    assert snap.aureon.snapshot.error_class == "MalformedResponse"


# Pending drop (AUR-I-17) -----------------------------------------------------------------


def _snap(pending: int, sha: str) -> AureonSnapshot:
    return AureonSnapshot(deploy_sha=sha, pending=pending)


def test_pending_drop_detected_when_pending_falls_to_zero_at_a_deploy() -> None:
    result = detect_pending_drop(_snap(4, "a" * 40), _snap(0, "b" * 40), NOW, None)
    assert result.detected_now
    assert result.last_event is not None and result.last_event.pending_before == 4
    assert result.disposition is Disposition.HOLD


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (_snap(4, "a" * 40), _snap(0, "a" * 40)),  # resolved normally, no deploy
        (_snap(0, "a" * 40), _snap(0, "b" * 40)),  # deploy with nothing pending
        (_snap(4, "a" * 40), _snap(2, "b" * 40)),  # deploy, pending kept (partly)
    ],
)
def test_no_pending_drop(before: AureonSnapshot, after: AureonSnapshot) -> None:
    result = detect_pending_drop(before, after, NOW, None)
    assert not result.detected_now and result.last_event is None
    assert result.disposition is Disposition.PASS


def test_pending_drop_needs_two_snapshots() -> None:
    with pytest.raises(InputUnavailableError):
        detect_pending_drop(None, _snap(0, "b" * 40), NOW, None)


def test_pending_drop_through_the_refresher_is_latched() -> None:
    rig = Rig()
    first = rig.refresher.refresh_once()
    assert first.aureon.pending_drop.error_class == "InputUnavailable"

    rig.clock.advance(seconds=60)
    rig.aureon.body.update(pending=0, deploy_sha="b" * 40)
    second = rig.refresher.refresh_once()
    drop = second.aureon.pending_drop.value
    assert drop is not None and drop.detected_now and drop.last_event is not None
    assert drop.last_event.detected_at == rig.clock.now

    rig.clock.advance(seconds=60)
    third = rig.refresher.refresh_once()
    drop = third.aureon.pending_drop.value
    assert drop is not None and not drop.detected_now and drop.last_event is not None
    page = build_page(third, rig.clock.now)
    assert "AUR-I-17" in page.aureon.pending_drop.badge.label
    client = rig.app_client()
    login(client)
    html = client.get("/panel/aureon").get_data(as_text=True)
    assert "COP detection clock · 17 Sep 2026 15:01 UTC" in html


# CI combination and tags -----------------------------------------------------------------


def _run(run_id: int, workflow_id: int, conclusion: str | None, event: str = "push") -> WorkflowRun:
    return WorkflowRun(
        id=run_id,
        workflow_id=workflow_id,
        name=f"wf{workflow_id}",
        status="completed" if conclusion else "in_progress",
        conclusion=conclusion,
        event=event,
        head_sha="a" * 40,
        created_at=NOW + timedelta(minutes=run_id),
        html_url="https://example.test",
    )


def test_no_runs_is_never_a_pass() -> None:
    assert combine_ci([], "a" * 40).disposition is Disposition.INDETERMINATE


def test_only_skipped_runs_is_not_a_pass() -> None:
    assert combine_ci([_run(1, 1, "skipped")], "a" * 40).disposition is Disposition.INDETERMINATE


def test_failure_wins_and_newest_run_per_workflow_counts() -> None:
    runs = [_run(1, 1, "failure"), _run(2, 1, "success"), _run(3, 2, "failure")]
    result = combine_ci(runs, "a" * 40)
    assert result.disposition is Disposition.BLOCK and result.summary == "1 of 2 workflows failed"


def test_running_is_hold_and_scheduled_runs_are_ignored() -> None:
    runs = [_run(1, 1, None), _run(2, 2, "failure", event="schedule")]
    assert combine_ci(runs, "a" * 40).disposition is Disposition.HOLD
    assert combine_ci([_run(1, 1, "success")], "a" * 40).disposition is Disposition.PASS


def test_latest_tag_uses_version_order() -> None:
    tags = [Tag(name="v0.3.9"), Tag(name="v0.3.10"), Tag(name="nightly")]
    assert latest_tag(tags).name == "v0.3.10"
    assert latest_tag([Tag(name="nightly")]).name == "nightly"
    assert latest_tag([]).name is None
