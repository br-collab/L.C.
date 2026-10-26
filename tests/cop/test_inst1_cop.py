"""INST-1 COP acceptance belongs to the COP-extra CI boundary."""

from __future__ import annotations

import pytest

from cop.app import SECTIONS
from cop.blindspots import CONTRACT_BLIND_SPOTS
from cop.observation import SourceMalformedError
from cop.traceability import parse_publication


def test_cop_has_cns_and_xrpl_tiles_and_names_cross_artifact_gaps() -> None:
    assert SECTIONS["cns"] == ("cns",)
    assert SECTIONS["xrpl"] == ("xrpl",)
    names = {spot.name.lower() for spot in CONTRACT_BLIND_SPOTS}
    assert sum("binding" in name for name in names) >= 4


def test_untested_requirement_requires_a_reason() -> None:
    raw = (
        b'{"schema_version":1,"synthetic":true,"enforcement_status":"ADVISORY_ONLY",'
        b'"claim_label":"EXPERIMENTAL","run_scope":"FULL",'
        b'"run_commit_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        b'"run_timestamp":"2026-10-26T12:00:00Z","requirements":['
        b'{"requirement_id":"R","status":"UNTESTED","test_node_ids":[]}],'
        b'"digest":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}'
    )
    with pytest.raises(SourceMalformedError):
        parse_publication(raw)
