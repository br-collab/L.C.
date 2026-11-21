"""Local Consolidated Audit Trail record construction and validation.

Records are built only from sealed lifecycle evidence supplied as immutable event
facts. Nothing is submitted. The synthetic venue is an exchange, so L.C. does
not invent a firm execution event.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Any

SPEC_VERSION = "4.2.0 r4"
SPEC_DATE = date(2026, 10, 21)
SPEC_SOURCE = "CAT Industry Member Technical Specifications (2d) v4.2.0 r4"
SPEC_PATH = Path(__file__).with_name("rules") / "CAT-IM-2d-v4.2.0-r4.meta.json"
CAIS_PATH = Path(__file__).with_name("rules") / "cais-field-basis.json"
PHASE_A = "A"
ROUTE_SUMMARY = "MARKET_ACCESS_PASSED"
NEW_ORDER_SUMMARY = "PARENT_ACTIVE"
ALLOCATION_SUMMARY = "allocation"
_SUMMARY_EVENT = {
    NEW_ORDER_SUMMARY: "NEW_ORDER",
    ROUTE_SUMMARY: "ROUTE",
    ALLOCATION_SUMMARY: "ALLOCATION",
}
SATURDAY_INDEX = 5
_CALENDAR_FOLDER = Path(__file__).with_name("vendor") / "atreides-calendar"
_SKIPPED_FIELD_KEYS = frozenset({"parent_event_id", "cause_event_id"})


class CalendarIndeterminateError(ValueError):
    """The vendored Atreides calendar does not cover the date."""


class SpecificationChecksumError(ValueError):
    """The pinned CAT specification file does not match its recorded SHA-256."""


@lru_cache(maxsize=1)
def us_equities_holidays() -> dict[int, frozenset[date]]:
    """Holidays from Atreides' committed US equities calendar, checksum-verified on load.

    L.C. keeps no holiday list of its own; ``source.json`` names the Atreides commit and
    SHA-256 of the vendored file.
    """

    source = json.loads((_CALENDAR_FOLDER / "source.json").read_text(encoding="utf-8"))
    raw = (_CALENDAR_FOLDER / "us_equities.json").read_bytes()
    if sha256(raw).hexdigest() != source["sha256"]:
        raise CalendarIndeterminateError("vendored calendar does not match its pinned checksum")
    document = json.loads(raw)
    return {
        int(year): frozenset(date.fromisoformat(day) for day in days)
        for year, days in document["holidays"].items()
    }


@dataclass(frozen=True, slots=True)
class EvidenceFact:
    event_id: str
    kind: str
    occurred_at: datetime
    values: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CATRecord:
    event_id: str
    event_type: str
    event_timestamp: str
    order_id: str
    fdid: str
    parent_event_id: str | None
    fields: dict[str, Any]
    specification: str = SPEC_VERSION
    raw_account_number: str | None = None


@dataclass(frozen=True, slots=True)
class CATFeedback:
    event_id: str
    accepted: bool
    errors: tuple[str, ...]
    correction_deadline: date | None


def _spec_body_bytes(document: Mapping[str, Any]) -> bytes:
    body = {key: value for key, value in document.items() if key != "sha256"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def load_specification() -> dict[str, Any]:
    """Return the pinned specification, after its SHA-256 matches the recorded digest."""

    document = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise SpecificationChecksumError("CAT specification checksum control")
    recorded = document.get("sha256")
    actual = sha256(_spec_body_bytes(document)).hexdigest()
    if not isinstance(recorded, str) or recorded != actual:
        raise SpecificationChecksumError("CAT specification checksum control")
    return document


def synthetic_firm_salt() -> str:
    """The synthetic salt is a pinned specification field, not a caller argument."""

    salt = load_specification().get("synthetic_firm_salt")
    if not isinstance(salt, str) or not salt:
        raise ValueError("CAT specification is missing the synthetic firm salt")
    return salt


def _cais_fields() -> dict[str, dict[str, str]]:
    document = json.loads(CAIS_PATH.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("fields"), dict):
        raise ValueError("CAIS field basis table is missing")
    fields: dict[str, dict[str, str]] = {}
    for name, row in document["fields"].items():
        if not isinstance(row, dict):
            raise ValueError("CAIS field basis row is missing")
        action = row.get("action")
        citation = row.get("citation")
        if not isinstance(action, str) or not isinstance(citation, str) or not citation:
            raise ValueError("CAIS field basis row is missing a citation")
        fields[str(name)] = {"action": action, "citation": citation}
    return fields


def builder_customer_actions() -> dict[str, str]:
    """Sent or withheld action the builder applies. The pin is the other side of the check."""

    actions = {name: row["action"] for name, row in _cais_fields().items()}
    return actions


def derive_fdid(account_number: str, firm_salt: str) -> str:
    return "FDID-" + sha256(f"{firm_salt}:{account_number}".encode()).hexdigest()[:24]


def _transmitted_fields(values: Mapping[str, Any]) -> dict[str, Any]:
    withheld = {name for name, action in builder_customer_actions().items() if action == "WITHHELD"}
    skipped = withheld | _SKIPPED_FIELD_KEYS
    return {key: value for key, value in values.items() if key not in skipped}


def build_records(
    evidence_chain: tuple[EvidenceFact, ...], *, firm_salt: str
) -> tuple[CATRecord, ...]:
    records = []
    for fact in evidence_chain:
        if fact.kind not in {"NEW_ORDER", "ROUTE", "ALLOCATION"}:
            continue
        account = str(fact.values["account_number"])
        fdid = derive_fdid(account, firm_salt)
        records.append(
            CATRecord(
                event_id=fact.event_id,
                event_type=fact.kind,
                event_timestamp=fact.occurred_at.astimezone(UTC).isoformat(timespec="milliseconds"),
                order_id=str(fact.values["order_id"]),
                fdid=fdid,
                parent_event_id=(
                    None
                    if fact.values.get("parent_event_id") is None
                    else str(fact.values["parent_event_id"])
                ),
                fields=_transmitted_fields(fact.values),
                raw_account_number=account,
            )
        )
    return tuple(records)


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"CAT pack {label} is not an object")
    return value


def _evidence_facts(pack: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    evidence = pack.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("CAT pack is missing its evidence chain")
    facts: list[dict[str, Any]] = []
    for item in evidence:
        facts.append(_mapping(item, "evidence fact"))
    return tuple(facts)


def _account_number(pack: Mapping[str, Any]) -> str:
    confirmation = str(_mapping(pack.get("confirmation"), "confirmation")["account_id"])
    statement = str(_mapping(pack.get("statement"), "statement")["account_id"])
    if confirmation != statement:
        raise ValueError("CAT account number disagrees inside the pack")
    return confirmation


def _order_id(pack: Mapping[str, Any]) -> str:
    report = _mapping(pack.get("venue_execution_report"), "venue execution report")
    return str(report["order_id"])


def _event_timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("CAT evidence timestamp is not timezone-aware")
    return parsed.astimezone(UTC).isoformat(timespec="milliseconds")


def _parent_new_order(
    fact: Mapping[str, Any],
    new_order_id: str | None,
    by_id: Mapping[str, Mapping[str, Any]],
) -> str | None:
    if new_order_id is None:
        return None
    parent = fact.get("cause_event_id")
    seen: set[str] = set()
    while isinstance(parent, str) and parent not in seen:
        if parent == new_order_id:
            return parent
        seen.add(parent)
        ancestor = by_id.get(parent)
        if ancestor is None:
            return None
        parent = ancestor.get("cause_event_id")
    return None


def _close_date(pack: Mapping[str, Any]) -> date:
    calendar = pack.get("reconciliation_calendar")
    if not isinstance(calendar, list) or not calendar:
        raise ValueError("CAT pack is missing its close date")
    dates: set[date] = set()
    for row in calendar:
        item = _mapping(row, "reconciliation row")
        if "close_date" not in item:
            raise ValueError("CAT pack close date is missing")
        dates.add(date.fromisoformat(str(item["close_date"])))
    if len(dates) != 1:
        raise ValueError("CAT pack close date is not unique")
    return dates.pop()


def build_records_from_pack(pack: Mapping[str, Any]) -> tuple[CATRecord, ...]:
    """Build new-order, route and allocation records from a golden-path evidence pack.

    Phase A. ``run_golden_path`` does not call this. Phase B wires that call.
    """

    evidence = _evidence_facts(pack)
    by_id = {str(item["event_id"]): item for item in evidence}
    route_facts = [item for item in evidence if item.get("summary") == ROUTE_SUMMARY]
    if len(route_facts) != 1:
        raise ValueError("CAT evidence chain has no single route fact")
    route_fact = route_facts[0]
    cause = route_fact.get("cause_event_id")
    new_order_id = cause if isinstance(cause, str) else None
    new_order = by_id.get(new_order_id) if new_order_id is not None else None
    if new_order is None or new_order.get("summary") != NEW_ORDER_SUMMARY:
        new_order = None
    allocation_facts = [item for item in evidence if item.get("summary") == ALLOCATION_SUMMARY]
    if len(allocation_facts) != 1:
        raise ValueError("CAT evidence chain has no single allocation fact")
    account = _account_number(pack)
    order_id = _order_id(pack)
    fdid = derive_fdid(account, synthetic_firm_salt())
    selected: list[tuple[dict[str, Any], str | None]] = []
    if new_order is not None and new_order_id is not None:
        selected.append((new_order, None))
    selected.append((route_fact, new_order_id if new_order is not None else None))
    allocation_parent = _parent_new_order(
        allocation_facts[0],
        new_order_id if new_order is not None else None,
        by_id,
    )
    selected.append((allocation_facts[0], allocation_parent))
    venue = str(_mapping(pack.get("venue_execution_report"), "venue execution report")["venue"])
    records: list[CATRecord] = []
    for fact, parent_id in selected:
        summary = str(fact["summary"])
        event_type = _SUMMARY_EVENT[summary]
        fields: dict[str, Any] = {}
        if event_type == "ROUTE":
            fields["venue"] = venue
        if event_type == "ALLOCATION":
            fields["allocation_id"] = str(fact["event_id"])
        records.append(
            CATRecord(
                event_id=str(fact["event_id"]),
                event_type=event_type,
                event_timestamp=_event_timestamp(str(fact["recorded_at"])),
                order_id=order_id,
                fdid=fdid,
                parent_event_id=parent_id,
                fields=_transmitted_fields(fields),
                raw_account_number=account,
            )
        )
    return tuple(records)


def customer_field_basis() -> dict[str, dict[str, str]]:
    actions = builder_customer_actions()
    return {
        name: {"action": actions[name], "basis": row["citation"]}
        for name, row in _cais_fields().items()
    }


def _business_days_after(start: date, count: int) -> date:
    holidays = us_equities_holidays()
    current = start
    added = 0
    while added < count:
        current += timedelta(days=1)
        if current.year not in holidays:
            raise CalendarIndeterminateError(f"no US equities calendar for {current.year}")
        if current.weekday() < SATURDAY_INDEX and current not in holidays[current.year]:
            added += 1
    return current


def validate(records: tuple[CATRecord, ...], *, reviewed_on: date) -> tuple[CATFeedback, ...]:
    load_specification()
    ids = {item.event_id for item in records}
    feedback = []
    for item in records:
        errors = []
        if item.event_type not in {"NEW_ORDER", "ROUTE", "ALLOCATION"}:
            errors.append("unsupported event type")
        if not item.event_id or not item.order_id or not item.fdid:
            errors.append("missing required field")
        if not item.event_timestamp.endswith("+00:00") or "." not in item.event_timestamp:
            errors.append("timestamp must carry UTC millisecond precision")
        raw_account = item.raw_account_number or item.fields.get("account_number")
        if item.fdid == raw_account:
            errors.append("FDID must not equal the account number")
        if item.event_type in {"ROUTE", "ALLOCATION"} and item.parent_event_id not in ids:
            errors.append("parent new-order event is missing")
        feedback.append(
            CATFeedback(
                item.event_id,
                not errors,
                tuple(errors),
                None if not errors else _business_days_after(reviewed_on, 3),
            )
        )
    return tuple(feedback)


def write_cat_feedback(pack_path: Path, output_path: Path | None = None) -> dict[str, Any]:
    """Write validator feedback for a pack. Phase A: a caller invokes this; a run does not."""

    pack_bytes = pack_path.read_bytes()
    pack = json.loads(pack_bytes)
    if not isinstance(pack, dict):
        raise ValueError("CAT pack is not an object")
    records = build_records_from_pack(pack)
    reviewed = _close_date(pack)
    feedback = validate(records, reviewed_on=reviewed)
    rejections = [
        {
            "event_id": item.event_id,
            "errors": list(item.errors),
            "correction_deadline": (
                None if item.correction_deadline is None else item.correction_deadline.isoformat()
            ),
        }
        for item in feedback
        if not item.accepted
    ]
    document: dict[str, Any] = {
        "phase": PHASE_A,
        "pack_sha256": sha256(pack_bytes).hexdigest(),
        "reviewed_on": reviewed.isoformat(),
        "record_count": len(records),
        "exception_count": len(rejections),
        "rejections": rejections,
    }
    destination = pack_path.with_name("cat-feedback.json") if output_path is None else output_path
    destination.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return document


def public_summary(
    records: tuple[CATRecord, ...], feedback: tuple[CATFeedback, ...]
) -> dict[str, Any]:
    return {
        "record_count": len(records),
        "validated_count": sum(item.accepted for item in feedback),
        "exception_count": sum(not item.accepted for item in feedback),
        "exceptions": tuple(
            {
                "event_id": item.event_id,
                "errors": item.errors,
                "correction_deadline": item.correction_deadline.isoformat()
                if item.correction_deadline
                else None,
            }
            for item in feedback
            if not item.accepted
        ),
    }
