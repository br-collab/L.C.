"""Acceptance tests for the synthetic advisory source and panel."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from cop_fakes import Rig, advisory_body, login, section

from cop import app, view
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


def test_advisory_panel_is_read_only_and_displays_required_labels() -> None:
    assert app.PANELS["advisories"] == "Engine advisories (synthetic)"
    assert "advisories" in view.PageView.__dataclass_fields__

    rig = Rig()
    rig.advisories.body = advisory_body(dispositions=("PASS", "HOLD", "INDETERMINATE"))
    rig.refresher.refresh_once()
    client = rig.app_client()
    login(client)
    body = section(client.get("/section/agents").get_data(as_text=True), "advisories")
    assert "ADVISORY_ONLY" in body
    assert "EXPERIMENTAL" in body
    assert "SYNTHETIC" in body
    assert "PASS" in body
    assert "HOLD" in body
    assert "INDETERMINATE" in body
    assert "no issue found within the supplied inputs" in body
    for forbidden in ("compliant", "approved", "cleared", "certified"):
        assert forbidden not in body.lower()
    for verb in ("acknowledge", "authorize", "release", "submit"):
        assert f'href="/{verb}' not in body


def test_unread_advisory_source_never_renders_an_empty_clean_state() -> None:
    rig = Rig(advisories_configured=False)
    rig.refresher.refresh_once()
    client = rig.app_client()
    login(client)
    body = section(client.get("/section/agents").get_data(as_text=True), "advisories")
    assert "INDETERMINATE" in body
    assert "NotConfigured" in body
    assert "No advisory rows are shown" in body
    assert "PASS" not in body


def test_cross_domain_job_pins_and_exercises_the_advisory_producer() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text()
    seam = Path("tests/test_atreides_handoff.py").read_text()
    assert "1e5f4eb368980d5d1bdb204e92eab97e7e62f56e" in workflow
    assert "publication_bytes" in seam
    assert "parse_publication" in seam
