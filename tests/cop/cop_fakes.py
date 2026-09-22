"""Fake GitHub, Aureon and Atreides servers, a fake clock and builders for COP-0 tests.

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

from cop.agents import HttpxAgentsClient
from cop.app import create_app
from cop.aureon import HttpxAureonClient
from cop.cash_leg import HttpxCashLegClient
from cop.demo import DemoBreaks, DemoEscalations, DemoExceptions, DemoGrc, DemoLifecycles
from cop.github import HttpxGitHubClient
from cop.refresher import Refresher, RefresherOptions, Sources
from cop.settings import AUREON_CASH_LEG_URL, AUREON_SNAPSHOT_URL, PROGRAM_FILE, load_settings

AGENTS_URL = "https://atreides.example.invalid/api/activation"

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


class FakeCashLeg:
    def __init__(self) -> None:
        self.failure: str | None = None
        self.calls = 0
        self.body: dict[str, Any] = {
            "status": "ok",
            "scenario": "USD 1,000,000 cash leg against a Treasury purchase",
            "boundary": "Atreides prepares, governs, reconciles. The entitled member submits.",
            "stages": [
                {
                    "stage": "1. Funding — can it settle?",
                    "headline": "will_queue — shortfall 750000, clears at +5400s",
                    "detail": {
                        "disposition": "will_queue",
                        "net_debit_cap_headroom": "49250000",
                    },
                },
                {
                    "stage": "2. CATO-F — which rail, how final?",
                    "headline": "PROCEED — fedwire (GROSS_FINAL)",
                    "detail": {
                        "decision": "PROCEED",
                        "recommended_rail": "fedwire",
                        "finality_class": "GROSS_FINAL",
                    },
                },
            ],
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if str(request.url) != AUREON_CASH_LEG_URL:
            raise UnexpectedRequestError(f"unexpected request {request.url}")
        if self.failure is not None:
            return failure_response(self.failure, request)
        return httpx.Response(200, json=self.body)


def activation_body(
    *,
    halted: bool = False,
    stopped: bool = False,
    probe_admitted: bool = False,
    schema_version: int = 1,
) -> dict[str, Any]:
    """An Atreides Phase A activation document, as JSON on the wire.

    Written as a literal rather than built from the Atreides types on purpose:
    ``cop`` must never import a domain package, and a fixture that did would make
    this suite pass for a reason the application cannot rely on.

    ``stopped`` stops the Tier 2 agent, which is the A-T4 case. ``probe_admitted``
    turns the standing lateral-handoff probe's last output into a recommendation,
    which is the state that must take the whole picture to BLOCK.
    """
    seen = _iso(START - timedelta(seconds=30))
    operator_direct = {
        "state": "absent",
        "kind": "NOTHING_RECORDED",
        "reason": "operator-direct under CAOM-001",
    }
    running = {
        "state": "absent",
        "kind": "NOT_APPLICABLE",
        "reason": "this agent is running",
    }
    no_refusal = {
        "state": "absent",
        "kind": "NOTHING_RECORDED",
        "reason": "this agent has refused nothing since activation",
    }
    stopped_absent = {
        "state": "absent",
        "kind": "NOTHING_RECORDED",
        "reason": "agent stopped: the operator stopped this agent",
    }
    halt_refusal = {
        "state": "recorded",
        "value": {
            "code": "HALT_ACTIVE",
            "detail": "Refused: a halt is active and covers Atreides.",
            "observed_at": seen,
        },
    }

    def agent(**fields: Any) -> dict[str, Any]:
        """One agent row. A stopped agent's every value becomes the same absence,
        because a stopped agent has no current state — which is the A-T4 claim."""
        up = fields["up"]
        absent = None if up else stopped_absent
        return {
            "agent_id": fields["agent_id"],
            "tier": fields["tier"],
            "role": fields["role"],
            "up": up,
            "expects_refusal": fields.get("probe", False),
            "disposition": fields["disposition"],
            "stopped_reason": (
                running if up else {"state": "recorded", "value": "the operator stopped this agent"}
            ),
            "last_summary": absent or {"state": "recorded", "value": fields["summary"]},
            "last_observed_at": absent or {"state": "recorded", "value": seen},
            "last_provenance": absent or {"state": "recorded", "value": "POLICY_RESULT"},
            "last_handoff_basis": absent or operator_direct,
            "last_refusal": absent or fields["refusal"],
            "recommendations": fields["recommendations"],
            "refusals": fields["refusals"],
        }

    tier2_up = not stopped
    return {
        "schema_version": schema_version,
        "domain": "ATREIDES",
        "phase": "A",
        "synthetic": True,
        "taken_at": _iso(START),
        "tick": {"state": "recorded", "value": 7},
        "last_tick_at": {"state": "recorded", "value": seen},
        "halted": halted,
        "halt_reason": (
            {"state": "recorded", "value": "Tier 0 emergency halt"}
            if halted
            else {
                "state": "absent",
                "kind": "NOT_APPLICABLE",
                "reason": "no halt covering Atreides is in effect",
            }
        ),
        "disposition": "BLOCK" if (halted or probe_admitted) else "PASS",
        "effects": {
            "operation": "atreides.activation.advisory_output",
            "effects": [],
            "note": "Contained. An advisory agent output never submits to a rail.",
        },
        "agents": [
            agent(
                agent_id="settlement-operations-analyst",
                tier="TIER_1",
                role="Settlement Operations Analyst",
                up=True,
                probe=False,
                disposition="BLOCK" if halted else "PASS",
                summary="Pre-routing gates clear for ficc_gsd_dvp",
                refusal=halt_refusal if halted else no_refusal,
                recommendations=7,
                refusals=1 if halted else 0,
            ),
            agent(
                agent_id="fiat-operations-specialist",
                tier="TIER_2",
                role="FIAT Operations Specialist",
                up=tier2_up,
                probe=False,
                disposition="INDETERMINATE" if stopped else ("BLOCK" if halted else "PASS"),
                summary="Path selected: fedwire",
                refusal=halt_refusal if halted else no_refusal,
                recommendations=7,
                refusals=1 if halted else 0,
            ),
            agent(
                agent_id="lateral-handoff-probe",
                tier="TIER_1",
                role="Lateral handoff probe (WP-A2)",
                up=True,
                probe=True,
                disposition="BLOCK" if probe_admitted else "PASS",
                summary=(
                    "a recommendation that should not exist"
                    if probe_admitted
                    else "Input refused at the receiving agent's own type check"
                ),
                refusal=(
                    no_refusal
                    if probe_admitted
                    else {
                        "state": "recorded",
                        "value": {
                            "code": "NO_RECORDED_HANDOFF",
                            "detail": (
                                "Refused at the receiving agent: a lateral input with no "
                                "recorded handoff authorization."
                            ),
                            "observed_at": seen,
                        },
                    }
                ),
                recommendations=1 if probe_admitted else 0,
                refusals=0 if probe_admitted else 7,
            ),
        ],
    }


class FakeAtreides:
    """Serves the activation document at :data:`AGENTS_URL`."""

    def __init__(self) -> None:
        self.failure: str | None = None
        self.calls = 0
        self.unexpected: list[str] = []
        self.body: dict[str, Any] = activation_body()

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if str(request.url) != AGENTS_URL:
            self.unexpected.append(str(request.url))
            raise UnexpectedRequestError(f"unexpected request {request.url}")
        if self.failure is not None:
            return failure_response(self.failure, request)
        return httpx.Response(200, json=self.body)


class Rig:
    """A refresher wired to fake servers and a fake clock."""

    def __init__(  # noqa: PLR0913 - switches deliberately expose source availability
        self,
        program_path: Path = PROGRAM_FILE,
        token: str | None = "test-token",
        *,
        agents_configured: bool = True,
        lifecycles_configured: bool = True,
        escalations_configured: bool = True,
        exceptions_configured: bool = False,
        grc_configured: bool = False,
    ) -> None:
        self.clock = FakeClock()
        self.github = FakeGitHub()
        self.aureon = FakeAureon()
        self.cash_leg = FakeCashLeg()
        self.atreides = FakeAtreides()
        self.github_client = HttpxGitHubClient(
            token, http=httpx.Client(transport=httpx.MockTransport(self.github.handler))
        )
        grc = DemoGrc(self.clock) if grc_configured else None
        self.refresher = Refresher(
            sources=Sources(
                github=self.github_client,
                aureon=HttpxAureonClient(
                    http=httpx.Client(transport=httpx.MockTransport(self.aureon.handler))
                ),
                agents=(
                    HttpxAgentsClient(
                        AGENTS_URL,
                        http=httpx.Client(transport=httpx.MockTransport(self.atreides.handler)),
                    )
                    if agents_configured
                    else None
                ),
                lifecycles=DemoLifecycles(self.clock) if lifecycles_configured else None,
                escalations=(DemoEscalations(self.clock) if escalations_configured else None),
                breaks=DemoBreaks(self.clock),
                cash_leg=HttpxCashLegClient(
                    http=httpx.Client(transport=httpx.MockTransport(self.cash_leg.handler))
                ),
                exceptions=DemoExceptions(self.clock) if exceptions_configured else None,
                governance=grc,
                controls=grc,
                risks=grc,
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
