"""GitHub REST (representational state transfer) source for the repositories panels.

``GitHubSource`` is the interface the refresher depends on, so tests and demo mode can
inject a fake. ``HttpxGitHubClient`` is the real implementation. Every response is parsed
with a Pydantic model; anything unexpected raises ``SourceMalformedError``.

"CI" (continuous integration) here means GitHub Actions workflow runs. External status
checks posted by other services are not read. That keeps the token to the "Actions",
"Contents", "Metadata" and "Pull requests" read permissions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, TypeVar
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from cop.http import get_json
from cop.observation import SourceMalformedError
from cop.settings import GITHUB_API_URL, GITHUB_OWNER, HTTP_TIMEOUT_SECONDS, PRODUCT_NAME

M = TypeVar("M")

_PER_PAGE = 100


class _Parsed(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class Commit(_Parsed):
    sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    html_url: str


class WorkflowRun(_Parsed):
    id: int
    workflow_id: int
    name: str | None = None
    status: str | None = None
    conclusion: str | None = None
    event: str
    head_sha: str
    head_branch: str | None = None
    created_at: datetime
    html_url: str


class _WorkflowRunList(_Parsed):
    total_count: int
    workflow_runs: list[WorkflowRun]


class Tag(_Parsed):
    name: str


class PullHead(_Parsed):
    sha: str


class Pull(_Parsed):
    number: int
    title: str
    html_url: str
    created_at: datetime
    draft: bool = False
    head: PullHead


class PullDetail(_Parsed):
    number: int
    # GitHub's values include clean, behind, blocked, dirty, draft, has_hooks, unstable and
    # unknown ("unknown" while GitHub is still computing mergeability).
    mergeable_state: str | None = None


_TAGS = TypeAdapter(list[Tag])
_PULLS = TypeAdapter(list[Pull])


def repo_api_url(repo: str) -> str:
    return f"{GITHUB_API_URL}/repos/{GITHUB_OWNER}/{quote(repo, safe='.-_')}"


def commit_url(repo: str, ref: str) -> str:
    return f"{repo_api_url(repo)}/commits/{quote(ref, safe='')}"


def runs_url(repo: str) -> str:
    return f"{repo_api_url(repo)}/actions/runs"


def tags_url(repo: str) -> str:
    return f"{repo_api_url(repo)}/tags"


def pulls_url(repo: str) -> str:
    return f"{repo_api_url(repo)}/pulls"


def pull_url(repo: str, number: int) -> str:
    return f"{pulls_url(repo)}/{number}"


class GitHubSource(Protocol):
    def commit(self, repo: str, ref: str) -> Commit: ...

    def runs_for_commit(self, repo: str, sha: str) -> list[WorkflowRun]: ...

    def tags(self, repo: str) -> list[Tag]: ...

    def open_pulls(self, repo: str) -> list[Pull]: ...

    def pull_detail(self, repo: str, number: int) -> PullDetail: ...

    def scheduled_runs(self, repo: str) -> list[WorkflowRun]: ...


def _parse(adapter: TypeAdapter[M], data: object, what: str) -> M:
    try:
        return adapter.validate_python(data)
    except ValidationError as exc:
        raise SourceMalformedError(
            f"Unexpected {what} shape ({exc.error_count()} problems)"
        ) from None


def _parse_run_list(data: object) -> list[WorkflowRun]:
    runs = _parse(TypeAdapter(_WorkflowRunList), data, "workflow run list")
    if runs.total_count > len(runs.workflow_runs):
        # A partial list could hide a failing run; refuse rather than show part of it.
        raise SourceMalformedError(
            f"Workflow run list is incomplete ({len(runs.workflow_runs)} of {runs.total_count})"
        )
    return runs.workflow_runs


class HttpxGitHubClient:
    """Reads the GitHub REST API. The token is sent only in the Authorization header."""

    def __init__(self, token: str | None, http: httpx.Client | None = None) -> None:
        self._http = http or httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": PRODUCT_NAME,
        }
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        self._headers = headers

    def __repr__(self) -> str:
        return f"HttpxGitHubClient(authenticated={'Authorization' in self._headers})"

    def _get(self, url: str, params: dict[str, str | int] | None = None) -> object:
        return get_json(self._http, url, params=params, headers=self._headers)

    def commit(self, repo: str, ref: str) -> Commit:
        return _parse(TypeAdapter(Commit), self._get(commit_url(repo, ref)), "commit")

    def runs_for_commit(self, repo: str, sha: str) -> list[WorkflowRun]:
        data = self._get(runs_url(repo), {"head_sha": sha, "per_page": _PER_PAGE})
        return _parse_run_list(data)

    def tags(self, repo: str) -> list[Tag]:
        return _parse(_TAGS, self._get(tags_url(repo), {"per_page": _PER_PAGE}), "tag list")

    def open_pulls(self, repo: str) -> list[Pull]:
        data = self._get(pulls_url(repo), {"state": "open", "per_page": _PER_PAGE})
        pulls = _parse(_PULLS, data, "pull request list")
        if len(pulls) >= _PER_PAGE:
            raise SourceMalformedError(
                f"More than {_PER_PAGE - 1} open pull requests; list truncated"
            )
        return pulls

    def pull_detail(self, repo: str, number: int) -> PullDetail:
        return _parse(TypeAdapter(PullDetail), self._get(pull_url(repo, number)), "pull request")

    def scheduled_runs(self, repo: str) -> list[WorkflowRun]:
        data = self._get(runs_url(repo), {"event": "schedule", "per_page": _PER_PAGE})
        runs = _parse(TypeAdapter(_WorkflowRunList), data, "workflow run list")
        # Runs arrive newest first and only the newest run per workflow is shown, so the
        # first page is read. Limit: a scheduled workflow whose newest run is not among the
        # newest 100 scheduled runs of the repository does not appear.
        return runs.workflow_runs
