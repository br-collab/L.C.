"""Refresher with fake GitHub and Aureon servers: happy path and each failure mode.

Each failure must show INDETERMINATE with its error class, and the last good value must
be kept separately and marked stale, never shown as current.
"""

from __future__ import annotations

import logging
import threading

import httpx
import pytest
from cannae_kernel.disposition import Disposition
from cannae_kernel.provenance import Provenance
from cop_fakes import (
    EXPECTED_ERROR_CLASS,
    FAILURE_MODES,
    GOOD_ENV,
    MAIN_SHA,
    FakeClock,
    Rig,
    login,
    section,
)

from cop.app import build_refresher
from cop.github import HttpxGitHubClient
from cop.http import get_json
from cop.observation import NOT_YET_REFRESHED, SourceHttpError, SourceRateLimitedError
from cop.settings import load_settings
from cop.view import STALE_ERROR, build_page


def _all_observations(rig: Rig) -> list[object]:
    snap = rig.refresher.snapshot
    observations: list[object] = [snap.program, snap.aureon.snapshot, snap.aureon.drift]
    for repo in snap.repos:
        observations += [repo.main_head, repo.main_ci, repo.latest_tag, repo.pulls, repo.scheduled]
    return observations


def test_nothing_is_fetched_or_started_before_refresh_once() -> None:
    threads_before = threading.active_count()
    rig = Rig()
    client = rig.app_client()
    login(client)
    page = client.get("/").get_data(as_text=True)
    assert rig.github.calls == [] and rig.aureon.calls == 0
    assert threading.active_count() == threads_before
    assert not rig.refresher.running
    # Before the first refresh every source tile is indeterminate, never green.
    snap = rig.refresher.snapshot
    assert snap.aureon.snapshot.error_class == NOT_YET_REFRESHED
    assert "All sources current" not in page
    assert "INDETERMINATE" in section(page, "banner")


def test_happy_path() -> None:
    rig = Rig()
    snap = rig.refresher.refresh_once()

    assert rig.github.unexpected == [] and rig.aureon.unexpected == []
    assert all(getattr(o, "ok", False) for o in _all_observations(rig))
    # The AUR-I-17 comparison needs a second snapshot, so the first refresh cannot pass it.
    assert snap.aureon.pending_drop.error_class == "InputUnavailable"
    repos = {r.name: r for r in snap.repos}
    assert set(repos) == set(MAIN_SHA)
    assert repos["aureon"].main_head.value == MAIN_SHA["aureon"]
    assert repos["aureon"].main_head.provenance is Provenance.FACT_EXTERNAL
    ci = repos["aureon"].main_ci.value
    assert ci is not None and ci.disposition is Disposition.PASS
    atreides = repos["Project-Atreides"]
    assert atreides.latest_tag.value is not None
    assert atreides.latest_tag.value.name == "v0.3.10"
    pulls = atreides.pulls.value
    assert pulls is not None and [p.number for p in pulls] == [12]
    assert pulls[0].merge.value is not None and pulls[0].merge.value.needs_update_branch
    scheduled = atreides.scheduled.value
    assert scheduled is not None and scheduled[0].workflow == "Nightly"
    assert scheduled[0].disposition is Disposition.BLOCK
    assert snap.aureon.snapshot.value is not None and snap.aureon.snapshot.value.pending == 4
    assert snap.aureon.drift.provenance is Provenance.POLICY_RESULT
    assert snap.last_clean_refresh_at == rig.clock.now

    # The token is sent to GitHub, in the Authorization header only.
    assert all(h.get("authorization") == "Bearer test-token" for h in rig.github.headers_seen)
    assert all("test-token" not in url for url in rig.github.calls)

    client = rig.app_client()
    login(client)
    page = client.get("/").get_data(as_text=True)
    repos_html = section(page, "repositories")
    assert "aaaaaaa" in repos_html and "INDETERMINATE" not in repos_html
    assert "Needs update branch" in repos_html
    assert (
        "FACT_EXTERNAL" in repos_html
        and "COP observation clock: 17 Sep 2026 15:00:00 UTC" in repos_html
    )
    assert "https://api.github.com/repos/br-collab/aureon/commits/main" in repos_html
    assert "GitHub clock · opened 17 Sep 2026" in repos_html
    scheduled_html = section(page, "scheduled")
    assert "Nightly" in scheduled_html
    assert "GitHub clock · run 17 Sep 2026" in scheduled_html
    assert "Atreides clock · 17 Sep 2026 14:59:30 UTC" in section(page, "agents")
    banner_html = section(page, "banner")
    assert banner_html.count("COP clock · 17 Sep 2026 15:00:00 UTC") == 3
    # A failing nightly makes the overall state BLOCK, not green.
    assert "BLOCK" in banner_html


@pytest.mark.parametrize("mode", FAILURE_MODES)
def test_github_failure_shows_indeterminate_with_stale_last_good(mode: str) -> None:
    rig = Rig()
    first = rig.refresher.refresh_once()
    good_at = rig.clock.now
    rig.clock.advance(seconds=60)
    rig.github.failure = mode
    snap = rig.refresher.refresh_once()

    repo = next(r for r in snap.repos if r.name == "aureon")
    obs = repo.main_head
    assert obs.error_class == EXPECTED_ERROR_CLASS[mode]
    assert obs.value is None, "a failed source must not carry a current value"
    assert obs.last_good_value == MAIN_SHA["aureon"]
    assert obs.last_good_at == good_at
    assert snap.last_clean_refresh_at == first.last_refresh_at

    page = build_page(snap, rig.clock.now)
    tile = next(r for r in page.repos if r.name == "aureon").main_head
    assert tile.current is False
    assert tile.value is None
    assert tile.stale_value == MAIN_SHA["aureon"]
    assert tile.badge.code == Disposition.INDETERMINATE.value
    assert page.banner.overall.code == Disposition.INDETERMINATE.value

    client = rig.app_client()
    login(client)
    html = section(client.get("/").get_data(as_text=True), "repositories")
    assert EXPECTED_ERROR_CLASS[mode] in html
    assert "last good value, from 17 Sep 2026 15:00:00 UTC" in html
    assert "It is stale and is not current" in html


@pytest.mark.parametrize("mode", FAILURE_MODES)
def test_aureon_failure_shows_indeterminate_with_stale_last_good(mode: str) -> None:
    rig = Rig()
    rig.refresher.refresh_once()
    rig.clock.advance(seconds=60)
    rig.aureon.failure = mode
    snap = rig.refresher.refresh_once()

    obs = snap.aureon.snapshot
    assert obs.error_class == EXPECTED_ERROR_CLASS[mode]
    assert obs.value is None
    assert obs.last_good_value is not None and obs.last_good_value.pending == 4
    # Values computed from a failed source are unknown too.
    assert snap.aureon.drift.error_class == "InputUnavailable"
    assert snap.aureon.pending_drop.error_class == "InputUnavailable"

    client = rig.app_client()
    login(client)
    html = section(client.get("/").get_data(as_text=True), "aureon")
    assert EXPECTED_ERROR_CLASS[mode] in html
    assert "Last good snapshot (17 Sep 2026 15:00:00 UTC)" in html
    assert "INDETERMINATE" in html


@pytest.mark.parametrize("mode", FAILURE_MODES)
def test_failure_before_any_good_value_has_nothing_to_show(mode: str) -> None:
    rig = Rig()
    rig.github.failure = mode
    rig.aureon.failure = mode
    snap = rig.refresher.refresh_once()
    for repo in snap.repos:
        assert repo.main_head.error_class == EXPECTED_ERROR_CLASS[mode]
        assert repo.main_head.last_good_value is None
    page = build_page(snap, rig.clock.now)
    assert page.banner.overall.code == Disposition.INDETERMINATE.value
    assert snap.last_clean_refresh_at is None


def test_rate_limit_is_its_own_error_class_and_other_403_is_not() -> None:
    rig = Rig()
    rig.github.failure = "rate_limit"
    snap = rig.refresher.refresh_once()
    assert snap.repos[0].main_head.error_class == "RateLimited"
    assert "resets at" in (snap.repos[0].main_head.error_detail or "")

    def respond(status: int, headers: dict[str, str]) -> httpx.Client:
        return httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(status, headers=headers, json={"message": "no"})
            )
        )

    with pytest.raises(SourceRateLimitedError):
        get_json(respond(429, {}), "https://api.github.com/x")
    with pytest.raises(SourceRateLimitedError):
        get_json(respond(403, {"retry-after": "30"}), "https://api.github.com/x")
    with pytest.raises(SourceHttpError) as plain:
        get_json(respond(403, {"x-ratelimit-remaining": "4999"}), "https://api.github.com/x")
    assert plain.value.status_code == 403


def test_token_is_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    rig = Rig(token="ghp_this_must_not_leak")
    for mode in FAILURE_MODES:
        rig.github.failure = mode
        rig.refresher.refresh_once()
    assert "ghp_this_must_not_leak" not in caplog.text
    assert "ghp_this_must_not_leak" not in repr(rig.github_client)
    assert "ghp_this_must_not_leak" not in repr(rig.refresher.snapshot)


def test_value_older_than_five_minutes_is_not_shown_as_current() -> None:
    rig = Rig()
    snap = rig.refresher.refresh_once()
    rig.clock.advance(minutes=6)  # the refresher has stopped; the snapshot ages
    page = build_page(snap, rig.clock.now)
    tile = page.aureon.snapshot
    assert not tile.current and tile.value is None and tile.stale_value is not None
    assert tile.error_class == STALE_ERROR
    assert page.banner.overall.code == Disposition.INDETERMINATE.value
    assert any("Aureon snapshot" in s for s in page.banner.stale_sources)
    assert any("GitHub: aureon" in s for s in page.banner.stale_sources)


def test_no_staleness_warning_when_fresh() -> None:
    rig = Rig()
    snap = rig.refresher.refresh_once()
    rig.clock.advance(minutes=4)
    assert build_page(snap, rig.clock.now).banner.stale_sources == ()


def test_routes_never_call_sources() -> None:
    rig = Rig()
    rig.refresher.refresh_once()
    github_calls, aureon_calls = len(rig.github.calls), rig.aureon.calls
    client = rig.app_client()
    login(client)
    for path in ("/", "/panel/waves", "/panel/repositories", "/panel/aureon", "/healthz"):
        assert client.get(path).status_code == 200
    assert len(rig.github.calls) == github_calls
    assert rig.aureon.calls == aureon_calls


def test_github_without_token_warns_and_sends_no_authorization() -> None:
    rig = Rig(token=None)
    rig.refresher.refresh_once()
    assert all("authorization" not in h for h in rig.github.headers_seen)
    client = rig.app_client()
    login(client)
    assert "GITHUB RATE LIMIT WARNING" in client.get("/").get_data(as_text=True)


def test_refresher_thread_starts_and_stops() -> None:
    rig = Rig()
    rig.refresher.start()
    try:
        assert rig.refresher.running
        rig.refresher.start()  # idempotent
    finally:
        rig.refresher.stop()
    assert not rig.refresher.running


def test_demo_mode_uses_only_invented_data_and_shows_every_state() -> None:
    clock = FakeClock()
    settings = load_settings({**GOOD_ENV, "LEGATE_DEMO": "1"})
    refresher = build_refresher(settings, clock)
    assert not isinstance(refresher._github, HttpxGitHubClient)  # no network client at all
    refresher.refresh_once()
    clock.advance(seconds=60)
    snap = refresher.refresh_once()
    page = build_page(snap, clock.now)
    assert page.banner.demo
    assert page.aureon.drift.badge.code == "HOLD"
    assert page.aureon.pending_drop.value is not None
    assert page.aureon.pending_drop.value.last_event is not None
    lc_repo = next(r for r in page.repos if r.name == "L.C.")
    assert lc_repo.scheduled.error_class == "Timeout"


def test_refresh_interval_slows_without_a_token() -> None:
    assert load_settings(GOOD_ENV).refresh_seconds == 600
    assert load_settings({**GOOD_ENV, "GITHUB_TOKEN": "t"}).refresh_seconds == 60
