from datetime import UTC, date, datetime

from lc.cat import (
    CATRecord,
    EvidenceFact,
    build_records,
    customer_field_basis,
    public_summary,
    validate,
)

NOW = datetime(2026, 11, 18, 15, 30, 1, 123000, tzinfo=UTC)


def facts() -> tuple[EvidenceFact, ...]:
    common = {"account_number": "ACCT-123", "order_id": "ORDER-1"}
    return (
        EvidenceFact("E-NEW", "NEW_ORDER", NOW, common),
        EvidenceFact(
            "E-ROUTE",
            "ROUTE",
            NOW,
            common | {"parent_event_id": "E-NEW", "venue": "SYNTHETIC_EXCHANGE"},
        ),
        EvidenceFact(
            "E-ALLOC",
            "ALLOCATION",
            NOW,
            common | {"parent_event_id": "E-NEW", "allocation_id": "A-1"},
        ),
        EvidenceFact("E-EXEC", "EXCHANGE_EXECUTION", NOW, common),
    )


def test_cat_builder_reads_evidence_chain_not_request() -> None:
    records = build_records(facts(), firm_salt="SYNTHETIC-FIRM")
    assert {item.event_type for item in records} == {"NEW_ORDER", "ROUTE", "ALLOCATION"}
    assert {item.order_id for item in records} == {"ORDER-1"}


def test_cat_event_set_and_exchange_execution_boundary() -> None:
    records = build_records(facts(), firm_salt="SYNTHETIC-FIRM")
    assert len(records) == 3
    assert "EXCHANGE_EXECUTION" not in {item.event_type for item in records}


def test_cat_refuses_fdid_equal_to_account_number() -> None:
    record = CATRecord(
        "E",
        "NEW_ORDER",
        NOW.isoformat(timespec="milliseconds"),
        "O",
        "ACCT-123",
        None,
        {"account_number": "ACCT-123"},
    )
    feedback = validate((record,), reviewed_on=date(2026, 11, 18))
    assert feedback[0].errors == ("FDID must not equal the account number",)


def test_cat_customer_fields_have_reduced_cais_basis() -> None:
    basis = customer_field_basis()
    assert basis["fdid"]["action"] == "SENT"
    withheld = ("account_number", "name", "address", "year_of_birth")
    assert {basis[key]["action"] for key in withheld} == {"WITHHELD"}
    assert all(item["basis"] for item in basis.values())


def test_cat_validator_checks_schema_linkage_and_time_precision() -> None:
    records = build_records(facts(), firm_salt="SYNTHETIC-FIRM")
    assert all(item.accepted for item in validate(records, reviewed_on=date(2026, 11, 18)))
    broken = records[1].__class__(
        records[1].event_id,
        records[1].event_type,
        "2026-11-18T15:30:01+00:00",
        records[1].order_id,
        records[1].fdid,
        "MISSING",
        records[1].fields,
    )
    errors = validate((records[0], broken), reviewed_on=date(2026, 11, 18))[1].errors
    assert errors == (
        "timestamp must carry UTC millisecond precision",
        "parent new-order event is missing",
    )


def test_cat_route_without_parent_is_refused() -> None:
    records = build_records(facts(), firm_salt="SYNTHETIC-FIRM")
    feedback = validate((records[1],), reviewed_on=date(2026, 11, 18))
    assert "parent new-order event is missing" in feedback[0].errors


def test_cat_rejection_feedback_has_t_plus_3_deadline() -> None:
    records = build_records(facts(), firm_salt="SYNTHETIC-FIRM")
    feedback = validate((records[1],), reviewed_on=date(2026, 11, 18))
    assert feedback[0].correction_deadline == date(2026, 11, 23)


def test_cat_rejection_appears_on_cop_exceptions() -> None:
    records = build_records(facts(), firm_salt="SYNTHETIC-FIRM")
    feedback = validate((records[1],), reviewed_on=date(2026, 11, 18))
    summary = public_summary((records[1],), feedback)
    assert summary["exception_count"] == 1
    assert summary["exceptions"][0]["correction_deadline"] == "2026-11-23"
