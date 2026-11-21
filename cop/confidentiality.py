"""Phase A scan for case identifiers and suspicious activity report fields.

The golden path does not call this yet. That wiring is Phase B. COP is the Common
Operating Picture.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Protocol

from cop.app import PANELS, SECTIONS

_CASE_IDENTIFIER = "CASE-"
_SAR_KEY = re.compile(r'"(?:draft_)?sar"\s*:', re.IGNORECASE)
_SAR_VALUE = re.compile(r'"SAR"')


class PageClient(Protocol):
    def get(self, path: str, **kwargs: Any) -> Any: ...


def leaks_supervisory_secret(text: str) -> bool:
    """A case identifier or a suspicious activity report field is present."""

    return (
        _CASE_IDENTIFIER in text
        or _SAR_KEY.search(text) is not None
        or _SAR_VALUE.search(text) is not None
    )


def cop_get_routes() -> tuple[str, ...]:
    """Every COP GET route the confidentiality scan renders."""

    routes = ["/", "/healthz", "/login"]
    routes.extend(f"/section/{name}" for name in SECTIONS)
    routes.extend(f"/panel/{name}" for name in PANELS)
    return tuple(routes)


def scan_confidentiality(publication: Path, client: PageClient) -> tuple[str, ...]:
    """Walk a publication directory and the rendered body of every COP GET route.

    Phase A. A case identifier or a suspicious activity report field is a failure.
    """

    failures: list[str] = []
    files = tuple(sorted(path for path in publication.rglob("*") if path.is_file()))
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        if leaks_supervisory_secret(text):
            failures.append(str(path))
    for route in cop_get_routes():
        body = client.get(route).get_data(as_text=True)
        if leaks_supervisory_secret(body):
            failures.append(route)
    return tuple(failures)
