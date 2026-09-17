"""Operator login: constant-time key comparison and an in-memory failed-login limiter.

The limiter counts failures per client address and across all addresses. Behind a proxy
(Railway) every request may share one address, so the per-address limit can act as a
global limit. That locks the operator out for a while during an attack, which is the safe
direction for a read-only page.
"""

from __future__ import annotations

import hashlib
import hmac
import threading
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta

GLOBAL_KEY = "*"


def keys_match(supplied: str, expected: str) -> bool:
    """Compare in constant time. Both sides are hashed first so lengths do not leak."""
    supplied_digest = hashlib.sha256(supplied.encode("utf-8")).digest()
    expected_digest = hashlib.sha256(expected.encode("utf-8")).digest()
    return hmac.compare_digest(supplied_digest, expected_digest)


def key_fingerprint(operator_key: str, session_secret: str) -> str:
    """Bound into the session so that rotating the operator key ends existing sessions."""
    return hmac.new(
        session_secret.encode("utf-8"), operator_key.encode("utf-8"), hashlib.sha256
    ).hexdigest()


class LoginLimiter:
    def __init__(
        self,
        *,
        max_per_address: int,
        max_global: int,
        window: timedelta,
        clock: Callable[[], datetime],
    ) -> None:
        self._max_per_address = max_per_address
        self._max_global = max_global
        self._window = window
        self._clock = clock
        self._failures: dict[str, deque[datetime]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: datetime) -> deque[datetime]:
        failures = self._failures.setdefault(key, deque())
        while failures and now - failures[0] >= self._window:
            failures.popleft()
        return failures

    def retry_after(self, address: str) -> timedelta | None:
        """How long the caller must wait before trying again, or None if allowed now."""
        now = self._clock()
        with self._lock:
            waits = []
            for key, limit in ((address, self._max_per_address), (GLOBAL_KEY, self._max_global)):
                failures = self._prune(key, now)
                if len(failures) >= limit:
                    waits.append(failures[0] + self._window - now)
            return max(waits) if waits else None

    def record_failure(self, address: str) -> None:
        now = self._clock()
        with self._lock:
            for key in (address, GLOBAL_KEY):
                self._prune(key, now).append(now)
