"""Local Consolidated Audit Trail record construction and validation.

Records are built only from sealed lifecycle evidence supplied as immutable event
facts. Nothing is submitted. The synthetic venue is an exchange, so L.C. does
not invent a firm execution event.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from typing import Any

SPEC_VERSION = "4.2.0 r4"
SPEC_DATE = date(2026, 10, 21)
SPEC_SOURCE = "CAT Industry Member Technical Specifications (2d) v4.2.0 r4"
SATURDAY_INDEX = 5


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


@dataclass(frozen=True, slots=True)
class CATFeedback:
    event_id: str
    accepted: bool
    errors: tuple[str, ...]
    correction_deadline: date | None


def derive_fdid(account_number: str, firm_salt: str) -> str:
    return "FDID-" + sha256(f"{firm_salt}:{account_number}".encode()).hexdigest()[:24]


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
                fields={
                    key: value
                    for key, value in fact.values.items()
                    if key not in {"account_number", "parent_event_id"}
                },
            )
        )
    return tuple(records)


def customer_field_basis() -> dict[str, dict[str, str]]:
    return {
        "fdid": {"action": "SENT", "basis": "SEC Rule 613 and CAT NMS Plan"},
        "account_number": {"action": "WITHHELD", "basis": "FDID requirement"},
        "name": {"action": "WITHHELD", "basis": "SEC Release 34-102386 and CAIS amendment"},
        "address": {"action": "WITHHELD", "basis": "SEC Release 34-102386 and CAIS amendment"},
        "year_of_birth": {
            "action": "WITHHELD",
            "basis": "SEC Release 34-102386 and CAIS amendment",
        },
    }


def _business_days_after(start: date, count: int) -> date:
    current = start
    added = 0
    while added < count:
        current += timedelta(days=1)
        if current.weekday() < SATURDAY_INDEX:
            added += 1
    return current


def validate(records: tuple[CATRecord, ...], *, reviewed_on: date) -> tuple[CATFeedback, ...]:
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
        if item.fdid == item.fields.get("account_number"):
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
