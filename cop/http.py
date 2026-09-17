"""One HTTP GET helper that turns every failure into a typed ``SourceError``.

Error details name the failure only. They never contain request headers, so an
``Authorization`` token cannot reach the page or a log line through an error message.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from http import HTTPStatus

import httpx

from cop.observation import (
    SourceConnectionError,
    SourceHttpError,
    SourceMalformedError,
    SourceRateLimitedError,
    SourceTimeoutError,
)

_RATE_LIMIT_STATUSES = frozenset({HTTPStatus.FORBIDDEN, HTTPStatus.TOO_MANY_REQUESTS})


def _rate_limit_detail(response: httpx.Response) -> str | None:
    """Return a description if ``response`` is a rate-limit refusal, else None."""
    if response.status_code not in _RATE_LIMIT_STATUSES:
        return None
    headers = response.headers
    reset = headers.get("x-ratelimit-reset")
    reset_text = ""
    if reset is not None and reset.isdigit():
        reset_at = datetime.fromtimestamp(int(reset), tz=UTC)
        reset_text = f"; resets at {reset_at:%H:%M} UTC (Coordinated Universal Time)"
    if headers.get("x-ratelimit-remaining") == "0":
        return f"Rate limit exhausted (HTTP {response.status_code}){reset_text}"
    if "retry-after" in headers:
        return f"Rate limited (HTTP {response.status_code}); retry after {headers['retry-after']} s"
    if response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
        return "Rate limited (HTTP 429)"
    # GitHub's secondary rate limit can arrive as a 403 with only a message in the body.
    if "rate limit" in response.text[:500].lower():
        return f"Rate limited (HTTP {response.status_code})"
    return None


def get_json(
    client: httpx.Client,
    url: str,
    *,
    params: Mapping[str, str | int] | None = None,
    headers: Mapping[str, str] | None = None,
) -> object:
    """GET ``url`` and return the decoded JSON body, or raise a ``SourceError``."""
    try:
        response = client.get(url, params=params, headers=headers)
    except httpx.TimeoutException as exc:
        raise SourceTimeoutError(f"No response in time ({type(exc).__name__})") from None
    except httpx.HTTPError as exc:
        raise SourceConnectionError(type(exc).__name__) from None

    limited = _rate_limit_detail(response)
    if limited is not None:
        raise SourceRateLimitedError(limited)
    if response.status_code != HTTPStatus.OK:
        raise SourceHttpError(response.status_code)
    try:
        return response.json()
    except ValueError:
        raise SourceMalformedError("Response body is not valid JSON") from None
