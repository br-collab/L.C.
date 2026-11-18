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
    invariants: tuple[tuple[str, bool, str, bool], ...]
    engine_dispositions: dict[str, str]
    early_warning_notices: tuple[dict[str, str], ...]
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
        engine_dispositions = {
            str(name): str(value) for name, value in raw["engine_dispositions"].items()
        }
        engine_for_invariant = {
            "reserve": "reserve",
            "pab_reserve": "pab_reserve",
            "net_capital": "net_capital",
            "possession_or_control": "possession_or_control",
            "stock_record": "street_reconciliation",
            "street_reconciliation": "street_reconciliation",
            "settlement_two_sided": "street_reconciliation",
        }
        invariants = tuple(
            (
                str(name),
                bool(value),
                engine_dispositions.get(engine_for_invariant.get(str(name), ""), disposition),
                bool(value)
                and engine_dispositions.get(engine_for_invariant.get(str(name), ""), disposition)
                == "PASS",
            )
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
            engine_dispositions=engine_dispositions,
            early_warning_notices=tuple(raw.get("early_warning_notices", ())),
            exception_owner=raw.get("exception_owner"),
            exception_age_seconds=raw.get("exception_age_seconds"),
            closing_cash=str(statement["closing_cash"]),
            closing_positions={str(k): str(v) for k, v in statement["closing_positions"].items()},
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None
