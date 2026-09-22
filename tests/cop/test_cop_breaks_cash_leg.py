"""COP-1 WP-3: synthetic breaks and the live cash-leg document."""

from __future__ import annotations

import json

import pytest
from cop_fakes import Rig, login, section

from cop.cash_leg import parse_cash_leg
from cop.observation import SourceMalformedError
from cop.view import build_page


def test_cash_leg_reader_extracts_only_endpoint_claims() -> None:
    rig = Rig()
    leg = parse_cash_leg(json.dumps(rig.cash_leg.body).encode())
    assert leg.funding_disposition == "will_queue"
    assert leg.rail == "fedwire"
    assert leg.finality_class == "GROSS_FINAL"
    assert leg.net_debit_cap_headroom == "49250000"


def test_cash_leg_reader_fails_closed_when_a_required_stage_changes() -> None:
    rig = Rig()
    del rig.cash_leg.body["stages"][1]["detail"]["recommended_rail"]
    with pytest.raises(SourceMalformedError, match="Unexpected cash-leg document shape"):
        parse_cash_leg(json.dumps(rig.cash_leg.body).encode())


def test_break_panel_labels_the_synthetic_source_exactly() -> None:
    rig = Rig()
    rig.refresher.refresh_once()
    client = rig.app_client()
    login(client)
    html = section(client.get("/panel/breaks").get_data(as_text=True), "breaks")
    assert "demo source: no break producer is published yet" in html
    assert "Aureon clock" in html
    assert "Atreides clock" in html


def test_cash_leg_panel_does_not_invent_unpublished_cutoff_or_clocks() -> None:
    rig = Rig()
    rig.refresher.refresh_once()
    client = rig.app_client()
    login(client)
    html = section(client.get("/panel/cashleg").get_data(as_text=True), "cashleg")
    assert "will_queue" in html
    assert "fedwire" in html
    assert "GROSS_FINAL" in html
    assert "absent — endpoint does not publish cutoff headroom" in html
    assert "absent — endpoint does not publish a rail clock" in html
    assert "absent — endpoint does not publish a trading-session clock" in html


def test_panel_twelve_names_the_missing_break_producer_and_remedy() -> None:
    rig = Rig()
    page = rig.refresher.refresh_once()
    spots = build_page(page, rig.clock()).blind_spots
    gap = next(spot for spot in spots if spot.name == "Break records across layers")
    assert gap.remedy == "change a contract / add a producer"
