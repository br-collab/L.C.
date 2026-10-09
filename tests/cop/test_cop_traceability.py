"""Acceptance tests for the synthetic requirement-coverage panel."""

from __future__ import annotations

import json

import pytest
from cop_fakes import Rig, login, section, traceability_body

from cop import app, view
from cop.observation import SourceMalformedError
from cop.traceability import parse_publication


def test_exact_published_shape_parses_and_preserves_statuses() -> None:
    publication = parse_publication(json.dumps(traceability_body()).encode())
    assert publication.run_scope == "FULL"
    assert [row.status.value for row in publication.requirements] == ["COVERED", "UNTESTED"]


@pytest.mark.parametrize(
    ("field", "value"),
    [("schema_version", 2), ("synthetic", False), ("run_scope", "PARTIAL"), ("requirements", [])],
)
def test_incomplete_or_unsupported_publication_fails_closed(field: str, value: object) -> None:
    body = traceability_body()
    body[field] = value
    with pytest.raises(SourceMalformedError):
        parse_publication(json.dumps(body).encode())


def test_duplicate_requirement_ids_fail_closed() -> None:
    body = traceability_body()
    requirements = body["requirements"]
    assert isinstance(requirements, list)
    requirements[1]["requirement_id"] = requirements[0]["requirement_id"]
    with pytest.raises(SourceMalformedError):
        parse_publication(json.dumps(body).encode())


def test_panel_is_read_only_and_lists_untested_first() -> None:
    assert app.PANELS["traceability"] == "Requirement coverage (synthetic)"
    assert "traceability" in view.PageView.__dataclass_fields__
    rig = Rig()
    rig.refresher.refresh_once()
    client = rig.app_client()
    login(client)
    body = section(client.get("/section/agents").get_data(as_text=True), "traceability")
    assert body.index("BR-02") < body.index("BR-01")
    assert "UNTESTED" in body
    assert "COVERED" in body
    assert "ADVISORY_ONLY" in body
    assert "EXPERIMENTAL" in body
    assert "SYNTHETIC" in body
    assert 'method="post"' not in body.lower()


def test_missing_url_is_indeterminate_and_never_an_empty_clean_state() -> None:
    rig = Rig(traceability_configured=False)
    rig.refresher.refresh_once()
    client = rig.app_client()
    login(client)
    body = section(client.get("/section/agents").get_data(as_text=True), "traceability")
    assert "INDETERMINATE" in body
    assert "NotConfigured" in body
    assert "No requirement rows are shown" in body
    assert "data-requirement=" not in body
