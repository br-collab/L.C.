"""Observations: one fetched or computed value, with its provenance and failure state.

Fail-closed rule (COP-0 data rule 2): an observation either holds a current value, or it
holds an error class and, separately, the last good value with the time it was observed.
A last good value is never placed in ``value``, so it cannot be shown as current.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Generic, TypeVar

from cannae_kernel.provenance import Provenance

T = TypeVar("T")


class SourceError(Exception):
    """A source could not provide a usable value. ``error_class`` is shown on the page."""

    error_class = "SourceError"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class SourceTimeoutError(SourceError):
    error_class = "Timeout"


class SourceConnectionError(SourceError):
    error_class = "ConnectionError"


class SourceHttpError(SourceError):
    error_class = "HttpError"

    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP status {status_code}")
        self.status_code = status_code


class SourceRateLimitedError(SourceError):
    error_class = "RateLimited"


class SourceMalformedError(SourceError):
    error_class = "MalformedResponse"


class SourceFieldAbsentError(SourceError):
    error_class = "FieldAbsent"


class InputUnavailableError(SourceError):
    """A computed value could not be computed because an input is not current."""

    error_class = "InputUnavailable"


class ProgramFileError(SourceError):
    error_class = "InvalidProgramFile"


NOT_YET_REFRESHED = "NotYetRefreshed"
UNEXPECTED_ERROR = "UnexpectedError"


@dataclass(frozen=True)
class Observation(Generic[T]):
    """A value from one source at one time, or the reason there is none."""

    key: str
    source_url: str
    provenance: Provenance
    attempted_at: datetime | None
    value: T | None = None
    observed_at: datetime | None = None
    error_class: str | None = None
    error_detail: str | None = None
    last_good_value: T | None = None
    last_good_at: datetime | None = None
    # None means the value does not age (for example the packaged program file).
    stale_after: timedelta | None = None

    @property
    def ok(self) -> bool:
        return self.error_class is None and self.observed_at is not None

    def is_current(self, now: datetime) -> bool:
        """True only for a successful value that is not older than ``stale_after``."""
        if not self.ok or self.observed_at is None:
            return False
        if self.stale_after is None:
            return True
        return now - self.observed_at <= self.stale_after

    def good_value(self) -> T | None:
        """The newest good value, whether current or not (for last-good carry-over)."""
        return self.value if self.ok else self.last_good_value

    def good_at(self) -> datetime | None:
        return self.observed_at if self.ok else self.last_good_at


def pending(
    key: str, source_url: str, provenance: Provenance, stale_after: timedelta | None
) -> Observation[T]:
    """The placeholder before the first refresh: indeterminate, never green."""
    return Observation(
        key=key,
        source_url=source_url,
        provenance=provenance,
        attempted_at=None,
        error_class=NOT_YET_REFRESHED,
        error_detail="No refresh has completed yet",
        stale_after=stale_after,
    )
