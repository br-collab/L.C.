"""Acceptance tests for the synthetic advisory source and panel."""

from __future__ import annotations

from importlib import import_module

import pytest


@pytest.mark.xfail(strict=True, reason="WP-3 advisory reader is not implemented yet")
def test_valid_advisory_publication_parses_without_translation() -> None:
    module = import_module("cop.advisories")
    publication = module.parse_publication(module.example_publication_bytes())
    assert publication.schema_version == 1
    assert publication.synthetic is True
    assert {row.disposition.value for item in publication.scenarios for row in item.advisories} == {
        "PASS",
        "HOLD",
        "INDETERMINATE",
    }


@pytest.mark.xfail(strict=True, reason="WP-3 fail-closed parser is not implemented yet")
def test_bad_advisory_documents_fail_closed() -> None:
    module = import_module("cop.advisories")
    for mutation in module.example_invalid_publications():
        with pytest.raises(Exception):
            module.parse_publication(mutation)
