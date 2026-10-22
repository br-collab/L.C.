"""OMS-1 real emitter, COP parser and publication output."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from cop.lifecycle import parse_oms_document
from lc.oms_publication import build_synthetic_oms_publication

AT = datetime(2026, 10, 22, 14, tzinfo=UTC)


def test_oms_publication_replays_and_is_visible_to_cop() -> None:
    first = build_synthetic_oms_publication(AT)
    assert first == build_synthetic_oms_publication(AT)
    row = parse_oms_document(first)
    assert row.clearing_method == "OMS_1_ORDER_MANAGEMENT"
    assert [fact.stage for fact in row.ficc_facts] == [
        "ORDER_TERMS",
        "ORDER_STATES",
        "ALLOCATIONS",
        "FIRM_CONTROLS",
    ]


def test_oms_publication_is_written_with_the_slice() -> None:
    destination = os.environ.get("LIFECYCLE_PUBLICATION_DIR")
    if destination is None:
        return
    path = Path(destination)
    path.mkdir(parents=True, exist_ok=True)
    (path / "oms-orders-allocations.json").write_bytes(build_synthetic_oms_publication(AT))
