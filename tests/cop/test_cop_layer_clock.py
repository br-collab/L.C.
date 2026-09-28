"""The clock strip consumes L.C.'s document without importing the domain package."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from cop_fakes import Rig, login

from cop.layer_clock import parse_layer_clock
from cop.observation import SourceMalformedError
from cop.settings import load_settings


def body(at: datetime) -> dict[str, object]:
    return {
        "schema_version": 1,
        "layer": "LC",
        "lifecycle_id": "lif_" + "1" * 26,
        "event_id": "evt_" + "2" * 26,
        "state": "HANDED_TO_ATREIDES",
        "times": {
            "event_time": at.isoformat(),
            "observation_time": at.isoformat(),
            "processing_time": at.isoformat(),
            "decision_time": at.isoformat(),
        },
        "provenance": "POLICY_RESULT",
        "event_digest": "sha256:" + "3" * 64,
    }


def test_reader_accepts_the_versioned_document() -> None:
    at = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
    assert parse_layer_clock(json.dumps(body(at)).encode()).times.event_time == at


def test_reader_refuses_an_unknown_schema_before_trusting_fields() -> None:
    document = body(datetime(2026, 9, 28, 14, 0, tzinfo=UTC))
    document["schema_version"] = 2
    with pytest.raises(SourceMalformedError, match="schema version 2"):
        parse_layer_clock(json.dumps(document).encode())


def test_clock_source_url_is_explicit_configuration() -> None:
    settings = load_settings(
        {
            "LEGATE_OPERATOR_KEY": "k" * 20,
            "LEGATE_SESSION_SECRET": "s" * 40,
            "LC_LAYER_CLOCK_URL": "https://lc.example.invalid/layer-clock.json",
        }
    )
    assert settings.lc_layer_clock_url == "https://lc.example.invalid/layer-clock.json"


def test_clock_strip_uses_event_time_and_ages_by_processing_time() -> None:
    rig = Rig(layer_clock_configured=True)
    rig.refresher.refresh_once()
    client = rig.app_client()
    login(client)
    html = client.get("/").get_data(as_text=True)
    assert "L.C. <b>17 Sep 2026 15:00:00 UTC</b>" in html

    rig.clock.advance(minutes=6)
    stale = client.get("/").get_data(as_text=True)
    assert 'L.C. <b class="clock-absent">Absent</b>' in stale
