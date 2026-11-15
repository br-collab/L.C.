"""Read-only projection of the versioned Atreides golden-path evidence pack."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GoldenPath:
    run_id: str
    disposition: str
    account_status: str
    close_report: str
    evidence: tuple[dict[str, Any], ...]
    invariants: tuple[tuple[str, bool, bool], ...]
    exception_owner: str | None
    exception_age_seconds: int | None
    closing_cash: str
    closing_positions: dict[str, str]


def load_golden_path(path: Path | None) -> GoldenPath | None:
    """Return validated display data, or ``None`` when no readable source exists."""
    if path is None:
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        disposition = str(raw["disposition"])
        invariants = tuple(
            (str(name), bool(value), (bool(value) and disposition == "PASS"))
            for name, value in raw["invariants"].items()
        )
        statement = raw["statement"]
        return GoldenPath(
            run_id=path.parent.name,
            disposition=disposition,
            account_status=str(raw["account_status"]),
            close_report=str(raw["close_report"]),
            evidence=tuple(raw["evidence"]),
            invariants=invariants,
            exception_owner=raw.get("exception_owner"),
            exception_age_seconds=raw.get("exception_age_seconds"),
            closing_cash=str(statement["closing_cash"]),
            closing_positions={str(k): str(v) for k, v in statement["closing_positions"].items()},
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None
