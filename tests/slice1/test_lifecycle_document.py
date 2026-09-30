"""The published lifecycle document is exactly a view of slice transcript evidence."""

from __future__ import annotations

import os
from pathlib import Path

from cannae_kernel.absence import Absent, Recorded
from cannae_kernel.disposition import Disposition
from test_runner import AT, _run

from harness_c2.lifecycle_document import (
    LIFECYCLE_DOCUMENT_SCHEMA_VERSION,
    LifecycleDocument,
    LifecycleStage,
    build_lifecycle_document,
)


def _document(*, funded: bool) -> LifecycleDocument:
    scenario, transcript = _run(funded=funded)
    return build_lifecycle_document(scenario, transcript, taken_at=AT)


def test_document_round_trips_and_accepts_only_its_declared_schema() -> None:
    document = _document(funded=True)
    raw = document.model_dump_json().encode()
    assert document.schema_version == LIFECYCLE_DOCUMENT_SCHEMA_VERSION
    assert LifecycleDocument.model_validate_json(raw, strict=True) == document
    changed = raw.replace(LIFECYCLE_DOCUMENT_SCHEMA_VERSION.encode(), b"unknown/2")
    try:
        LifecycleDocument.model_validate_json(changed, strict=True)
    except ValueError:
        pass
    else:
        raise AssertionError("an unknown schema version was accepted")


def test_unfunded_document_holds_and_records_later_stages_as_not_reached() -> None:
    document = _document(funded=False)
    acceptance = next(s for s in document.stages if s.stage is LifecycleStage.OBLIGATION_ACCEPTANCE)
    settled = next(s for s in document.stages if s.stage is LifecycleStage.SETTLED)
    assert isinstance(acceptance.evidence, Recorded)
    assert acceptance.evidence.value.disposition is Disposition.HOLD
    assert acceptance.evidence.value.reason == "CASH_GATE_HOLD:UNFUNDED_AT_SETTLEMENT_INSTANT"
    assert isinstance(settled.evidence, Absent)
    assert settled.evidence.reason == (
        "NOT_REACHED: CASH_GATE_HOLD:UNFUNDED_AT_SETTLEMENT_INSTANT"
    )


def test_every_published_value_comes_from_the_transcript() -> None:
    scenario, transcript = _run(funded=True)
    document = build_lifecycle_document(scenario, transcript, taken_at=AT)
    assert document.scenario_id == scenario.scenario_id
    assert document.lifecycle_id == scenario.lifecycle_id
    assert len(document.stages) == 6
    assert all(isinstance(stage.evidence, Recorded) for stage in document.stages)


def test_ci_publication_instances_are_written_when_requested() -> None:
    destination = os.environ.get("LIFECYCLE_PUBLICATION_DIR")
    if destination is None:
        return
    path = Path(destination)
    path.mkdir(parents=True, exist_ok=True)
    for name, funded in (("funded", True), ("unfunded", False)):
        document = _document(funded=funded).model_dump_json(indent=2)
        (path / f"{name}.json").write_text(document + "\n")
