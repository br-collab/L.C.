"""PS-3D Part 3, Phase A: CAT records from the vendored golden-path pack.

Phase B wires these calls into a run. These tests call the functions directly.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from lc.cat import (
    SPEC_PATH,
    build_records_from_pack,
    builder_customer_actions,
    load_specification,
    write_cat_feedback,
)

PACK_PATH = (
    Path(__file__).resolve().parents[1]
    / "cop"
    / "vendor"
    / "atreides-golden-path"
    / "evidence-pack.json"
)
CAIS_PATH = Path(__file__).resolve().parents[1] / "lc" / "rules" / "cais-field-basis.json"
CONTROL_CHAIN = "CAT evidence-chain control"
CONTROL_CAIS = "CAIS field basis control"
CONTROL_CHECKSUM = "CAT specification checksum control"


def _pack() -> dict[str, object]:
    loaded = json.loads(PACK_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    return parsed.isoformat(timespec="milliseconds")


def test_cat_records_come_from_vendored_evidence_chain(tmp_path: Path) -> None:
    pack = _pack()
    records = build_records_from_pack(pack)
    evidence = pack["evidence"]
    assert isinstance(evidence, list)
    route_fact = next(item for item in evidence if item["summary"] == "MARKET_ACCESS_PASSED")
    route = next(item for item in records if item.event_type == "ROUTE")
    report = pack["venue_execution_report"]
    assert isinstance(report, dict)
    confirmation = pack["confirmation"]
    statement = pack["statement"]
    assert isinstance(confirmation, dict)
    assert isinstance(statement, dict)
    account = confirmation["account_id"]
    assert route.event_id == route_fact["event_id"], CONTROL_CHAIN
    assert route.order_id == report["order_id"], CONTROL_CHAIN
    assert route.event_timestamp == _timestamp(str(route_fact["recorded_at"])), CONTROL_CHAIN
    assert route.parent_event_id == route_fact["cause_event_id"], CONTROL_CHAIN
    assert route.raw_account_number == account, CONTROL_CHAIN
    assert account == statement["account_id"], CONTROL_CHAIN
    assert route.fdid != account, CONTROL_CHAIN
    assert {item.event_type for item in records} == {"NEW_ORDER", "ROUTE", "ALLOCATION"}
    assert "EXCHANGE_EXECUTION" not in {item.event_type for item in records}
    committed = json.loads(PACK_PATH.with_name("cat-feedback.json").read_text(encoding="utf-8"))
    fresh = write_cat_feedback(PACK_PATH, output_path=tmp_path / "cat-feedback.json")
    assert fresh == committed, CONTROL_CHAIN
    assert fresh["phase"] == "A", CONTROL_CHAIN
    assert fresh["reviewed_on"] == "2026-11-16", CONTROL_CHAIN
    assert fresh["record_count"] == 3, CONTROL_CHAIN
    assert fresh["exception_count"] == 0, CONTROL_CHAIN


def test_cais_field_bases_match_pinned_table() -> None:
    pin = json.loads(CAIS_PATH.read_text(encoding="utf-8"))["fields"]
    actions = builder_customer_actions()
    records = build_records_from_pack(_pack())
    for name, row in pin.items():
        assert row["citation"], CONTROL_CAIS
        assert actions[name] == row["action"], CONTROL_CAIS
    withheld = {name for name, row in pin.items() if row["action"] == "WITHHELD"}
    for record in records:
        overlap = withheld & set(record.fields)
        assert not overlap, CONTROL_CAIS


def test_cat_specification_checksum_matches_pin() -> None:
    document = load_specification()
    body = {key: value for key, value in document.items() if key != "sha256"}
    digest = sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert document["sha256"] == digest, CONTROL_CHECKSUM
    assert document["sha256"] == json.loads(SPEC_PATH.read_text(encoding="utf-8"))["sha256"]
