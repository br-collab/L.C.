"""Acceptance tests for the synthetic advisory source and panel."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from cop.advisories import parse_publication
from cop.observation import SourceMalformedError


def publication_body() -> dict[str, object]:
    digest = "sha256:" + "a" * 64
    advisory = {
        "schema_version": "0.1-draft",
        "claim_label": "EXPERIMENTAL",
        "enforcement_status": "ADVISORY_ONLY",
        "subject": "possession_or_control",
        "as_of": "2026-10-02",
        "disposition": "PASS",
        "reasons": [],
        "missing_rules": [],
        "missing_inputs": [],
        "rule_versions": [],
        "rule_table_version": "sc2-v1",
        "rule_table_digest": digest,
        "input_digest": digest,
        "result_digest": digest,
    }
    scenarios = []
    for disposition in ("PASS", "HOLD", "INDETERMINATE"):
        row = deepcopy(advisory)
        row["disposition"] = disposition
        scenarios.append(
            {
                "scenario_id": f"synthetic-{disposition.lower()}",
                "description": "Synthetic committed scenario.",
                "advisories": [row],
            }
        )
    return {
        "schema_version": 1,
        "synthetic": True,
        "taken_at": "2026-10-06T20:00:00Z",
        "scenarios": scenarios,
    }


def test_valid_advisory_publication_parses_without_translation() -> None:
    publication = parse_publication(json.dumps(publication_body()).encode())
    assert publication.schema_version == 1
    assert publication.synthetic is True
    assert {row.disposition.value for item in publication.scenarios for row in item.advisories} == {
        "PASS",
        "HOLD",
        "INDETERMINATE",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("synthetic", False),
        ("scenarios", []),
    ],
)
def test_bad_advisory_documents_fail_closed(field: str, value: object) -> None:
    body = publication_body()
    body[field] = value
    with pytest.raises(SourceMalformedError):
        parse_publication(json.dumps(body).encode())


def test_unknown_disposition_and_empty_advisories_fail_closed() -> None:
    body = publication_body()
    scenarios = body["scenarios"]
    assert isinstance(scenarios, list)
    scenarios[0]["advisories"][0]["disposition"] = "PROCEED"
    with pytest.raises(SourceMalformedError):
        parse_publication(json.dumps(body).encode())

    body = publication_body()
    scenarios = body["scenarios"]
    assert isinstance(scenarios, list)
    scenarios[0]["advisories"] = []
    with pytest.raises(SourceMalformedError):
        parse_publication(json.dumps(body).encode())


def test_malformed_json_fails_closed() -> None:
    with pytest.raises(SourceMalformedError, match="not valid JSON"):
        parse_publication(b"{not json")


@pytest.mark.xfail(strict=True, reason="WP-4 advisory panel is not implemented yet")
def test_advisory_panel_is_read_only_and_displays_required_labels() -> None:
    view = import_module("cop.view")
    app = import_module("cop.app")
    assert "advisories" in app.PANELS
    assert hasattr(view.PageView, "__dataclass_fields__")
    assert "advisories" in view.PageView.__dataclass_fields__
