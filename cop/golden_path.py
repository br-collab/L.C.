"""Read-only projection of the versioned Atreides golden-path evidence pack."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
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
    reconciliation_calendar: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class GoldenPathLoad:
    value: GoldenPath | None
    reason: str | None


@dataclass(frozen=True)
class CatFeedbackView:
    """Phase A feedback already written by ``lc.cat.validate``. The panel does not recompute it."""

    phase: str
    record_count: int
    exception_count: int
    rejections: tuple[dict[str, Any], ...]


def golden_path_health(path: Path | None) -> dict[str, str]:
    """Report whether the configured pack is readable and digest-pinned."""
    base = {"variable": "ATREIDES_GOLDEN_PATH_FILE"}
    if path is None:
        return {"state": "not_configured", **base}
    loaded = load_golden_path_result(path)
    if loaded.value is not None:
        return {"state": "configured", **base}
    return {"state": "not_configured", "reason": loaded.reason or "unreadable", **base}


def load_golden_path(path: Path | None) -> GoldenPath | None:
    """Return validated display data, or ``None`` when no readable source exists."""
    return load_golden_path_result(path).value


def load_golden_path_result(path: Path | None) -> GoldenPathLoad:
    """Return validated display data plus a named fail-closed reason."""
    if path is None:
        return GoldenPathLoad(None, "ATREIDES_GOLDEN_PATH_FILE is not set")
    try:
        pack_bytes = path.read_bytes()
        source = json.loads(path.with_name("source.json").read_text(encoding="utf-8"))
        expected = str(source["sha256"])
        actual = sha256(pack_bytes).hexdigest()
        if actual != expected:
            return GoldenPathLoad(None, f"sha256 mismatch: expected {expected}, observed {actual}")
        raw = json.loads(pack_bytes)
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
        return GoldenPathLoad(
            GoldenPath(
                run_id=str(raw["run_id"]),
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
                closing_positions={
                    str(k): str(v) for k, v in statement["closing_positions"].items()
                },
                reconciliation_calendar=tuple(raw.get("reconciliation_calendar", ())),
            ),
            None,
        )
    except FileNotFoundError:
        return GoldenPathLoad(None, "pack or source.json is missing")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return GoldenPathLoad(None, f"unreadable pack metadata: {type(exc).__name__}")


def load_cat_feedback(path: Path | None) -> CatFeedbackView | None:
    """Read pinned Phase A feedback for this pack. A hash mismatch is absence, not a zero."""

    if path is None or not path.is_file():
        return None
    feedback_path = path.with_name("cat-feedback.json")
    if not feedback_path.is_file():
        return None
    pack_sha = sha256(path.read_bytes()).hexdigest()
    document = json.loads(feedback_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("pack_sha256") != pack_sha:
        return None
    raw_rejections = document["rejections"]
    if not isinstance(raw_rejections, list):
        return None
    rejections = tuple(item for item in raw_rejections if isinstance(item, dict))
    return CatFeedbackView(
        phase=str(document["phase"]),
        record_count=int(document["record_count"]),
        exception_count=int(document["exception_count"]),
        rejections=rejections,
    )
