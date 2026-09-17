"""Fake GitHub and Aureon servers, a fake clock and builders for COP-0 tests.

The fakes sit behind ``httpx.MockTransport``, so the real clients' error handling is
exercised end to end and no test can reach the network: any request the fakes do not
recognise fails the test.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from flask.testing import FlaskClient
from werkzeug.test import TestResponse

from cop.app import create_app
from cop.aureon import HttpxAureonClient
from cop.github import HttpxGitHubClient
from cop.refresher import Refresher, RefresherOptions
from cop.settings import AUREON_SNAPSHOT_URL, PROGRAM_FILE, load_settings

START = datetime(2026, 9, 17, 15, 0, tzinfo=UTC)
FAILURE_MODES = ("timeout", "http500", "malformed_json", "rate_limit")
EXPECTED_ERROR_CLASS = {
    "timeout": "Timeout",
    "http500": "HttpError",
    "malformed_json": "MalformedResponse",
    "rate_limit": "RateLimited",
}
OPERATOR_KEY = "correct-horse-battery-staple"
SESSION_SECRET = "s" * 40
GOOD_ENV = {"LEGATE_OPERATOR_KEY": OPERATOR_KEY, "LEGATE_SESSION_SECRET": SESSION_SECRET}

MAIN_SHA = {
    "aureon": "a" * 40,
    "Project-Atreides": "b" * 40,
    "cannae-kernel": "c" * 40,
    "L.C.": "d" * 40,
}
PR_HEAD_SHA = "e" * 40


class FakeClock:
    def __init__(self, now: datetime = START) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


def failure_response(mode: str, request: httpx.Request) -> httpx.Response:
    if mode == "timeout":
        raise httpx.ReadTimeout("timed out", request=request)
    if mode == "http500":
        return httpx.Response(500, text="Internal Server Error")
    if mode == "malformed_json":
        return httpx.Response(
            200, content=b"{this is not json", headers={"Content-Type": "application/json"}
        )
    if mode == "rate_limit":
        return httpx.Response(
            403,
            headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1789660800"},
            json={"message": "API rate limit exceeded"},
        )
    raise AssertionError(f"unknown failure mode {mode}")


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


class UnexpectedRequestError(AssertionError):
    """Raised by a fake for a request it does not serve. The refresher contains every
    exception (it fails closed), so fakes also record these for tests to assert on."""


class FakeGitHub:
    """Enough of the GitHub REST API for the four repositories."""

    _REPO_PATH = re.compile(r"^/repos/br-collab/(?P<repo>[^/]+)(?P<rest>/.*)$")

    def __init__(self) -> None:
        self.failure: str | None = None
        self.calls: list[str] = []
        self.unexpected: list[str] = []
        self.headers_seen: list[Mapping[str, str]] = []
        self.main_sha = dict(MAIN_SHA)
        self.main_conclusion = "success"
        self.nightly_conclusion = "failure"
        self.pr_mergeable_state = "behind"

    def _run(self, run_id: int, name: str, event: str, sha: str, conclusion: str) -> dict[str, Any]:
        return {
            "id": run_id,
            "workflow_id": run_id // 10,
            "name": name,
            "status": "completed",
            "conclusion": conclusion,
            "event": event,
            "head_sha": sha,
            "head_branch": "main",
            "created_at": _iso(START - timedelta(hours=run_id % 5)),
            "html_url": f"https://github.com/br-collab/x/actions/runs/{run_id}",
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        try:
            return self._handle(request)
        except UnexpectedRequestError:
            self.unexpected.append(str(request.url))
            raise

    def _handle(self, request: httpx.Request) -> httpx.Response:  # noqa: PLR0911
        self.calls.append(str(request.url))
        self.headers_seen.append(dict(request.headers))
        if request.url.host != "api.github.com":
            raise UnexpectedRequestError(f"unexpected host {request.url.host}")
        if self.failure is not None:
            return failure_response(self.failure, request)
        match = self._REPO_PATH.match(request.url.path)
        if match is None:
            raise UnexpectedRequestError(f"unexpected path {request.url.path}")
        repo, rest = match["repo"], match["rest"]
        params = request.url.params
        if rest == "/commits/main":
            return httpx.Response(
                200, json={"sha": self.main_sha[repo], "html_url": f"https://github.com/{repo}"}
            )
        if rest == "/actions/runs" and params.get("event") == "schedule":
            runs = []
            if repo == "Project-Atreides":
                runs = [
                    self._run(
                        91, "Nightly", "schedule", self.main_sha[repo], self.nightly_conclusion
                    )
                ]
            return httpx.Response(200, json={"total_count": len(runs), "workflow_runs": runs})
        if rest == "/actions/runs" and "head_sha" in params:
            sha = params["head_sha"]
            runs = [self._run(11, "CI", "push", sha, self.main_conclusion)]
            return httpx.Response(200, json={"total_count": len(runs), "workflow_runs": runs})
        if rest == "/tags":
            tags = [{"name": "v0.3.2"}, {"name": "v0.3.10"}] if repo == "Project-Atreides" else []
            return httpx.Response(200, json=tags)
        if rest == "/pulls" and params.get("state") == "open":
            pulls = []
            if repo == "Project-Atreides":
                pulls = [
                    {
                        "number": 12,
                        "title": "W2A-1: T1 integrity fixes",
                        "html_url": "https://github.com/br-collab/Project-Atreides/pull/12",
                        "created_at": _iso(START - timedelta(hours=3)),
                        "draft": False,
                        "head": {"sha": PR_HEAD_SHA},
                    }
                ]
            return httpx.Response(200, json=pulls)
        if rest == "/pulls/12":
            return httpx.Response(
                200, json={"number": 12, "mergeable_state": self.pr_mergeable_state}
            )
        raise UnexpectedRequestError(f"unexpected request {request.url}")


class FakeAureon:
    def __init__(self) -> None:
        self.failure: str | None = None
        self.calls = 0
        self.unexpected: list[str] = []
        self.body: dict[str, Any] = {
            "portfolio_value": 100.0,
            "positions": 12,
            "pending": 4,
            "stack": "ready",
            "market_open": True,
            "deploy_sha": "a" * 40,
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if str(request.url) != AUREON_SNAPSHOT_URL:
            self.unexpected.append(str(request.url))
            raise UnexpectedRequestError(f"unexpected request {request.url}")
        if self.failure is not None:
            return failure_response(self.failure, request)
        return httpx.Response(200, json=self.body)


class Rig:
    """A refresher wired to fake servers and a fake clock."""

    def __init__(self, program_path: Path = PROGRAM_FILE, token: str | None = "test-token") -> None:
        self.clock = FakeClock()
        self.github = FakeGitHub()
        self.aureon = FakeAureon()
        self.github_client = HttpxGitHubClient(
            token, http=httpx.Client(transport=httpx.MockTransport(self.github.handler))
        )
        self.refresher = Refresher(
            github=self.github_client,
            aureon=HttpxAureonClient(
                http=httpx.Client(transport=httpx.MockTransport(self.aureon.handler))
            ),
            clock=self.clock,
            options=RefresherOptions(
                program_path=program_path,
                refresh_seconds=60,
                github_authenticated=token is not None,
            ),
        )

    def app_client(self, env: Mapping[str, str] | None = None) -> FlaskClient:
        app = create_app(
            load_settings(GOOD_ENV if env is None else env),
            self.refresher,
            self.clock,
            start_refresher=False,
        )
        return app.test_client()


def section(html: str, panel: str) -> str:
    """The HTML of one panel, found by its data-panel attribute."""
    match = re.search(
        rf'<(section|header)[^>]*data-panel="{panel}"[^>]*>(?P<body>.*?)</\1>', html, re.DOTALL
    )
    if match is None:
        raise AssertionError(f"panel {panel} not rendered")
    return match["body"]


def login(client: FlaskClient, key: str = OPERATOR_KEY) -> TestResponse:
    return client.post("/login", data={"operator_key": key})
