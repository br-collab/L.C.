"""MVP-5 operator views from the versioned golden-path pack."""

from __future__ import annotations

import json
from pathlib import Path

from cop_fakes import GOOD_ENV, Rig, login

from cop.golden_path import load_golden_path


def _pack(tmp_path: Path, *, disposition: str = "PASS", invariant: bool = True) -> Path:
    folder = tmp_path / "mvp4-client-reporting-2026-11-15-v2"
    folder.mkdir()
    path = folder / "evidence-pack.json"
    path.write_text(
        json.dumps(
            {
                "disposition": disposition,
                "account_status": "ACTIVE",
                "close_report": "NORMAL_CLOSE",
                "invariants": {"stock_record": invariant},
                "exception_owner": None,
                "exception_age_seconds": None,
                "evidence": [
                    {
                        "event_id": "evt_1",
                        "producer": "Atreides",
                        "signature_status": "VERIFIED",
                        "summary": "account approved",
                    }
                ],
                "statement": {
                    "closing_cash": "10000.00",
                    "closing_positions": {"SYN-EQUITY": "10"},
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_missing_or_malformed_source_is_absent(tmp_path: Path) -> None:
    assert load_golden_path(None) is None
    assert load_golden_path(tmp_path / "missing.json") is None


def test_pack_supplies_run_timeline_cash_and_controls(tmp_path: Path) -> None:
    view = load_golden_path(_pack(tmp_path))
    assert view is not None
    assert view.run_id == "mvp4-client-reporting-2026-11-15-v2"
    assert view.evidence[0]["producer"] == "Atreides"
    assert view.closing_cash == "10000.00"
    assert view.invariants == (("stock_record", True, True),)


def test_engine_and_invariant_disagreement_is_a_failure(tmp_path: Path) -> None:
    view = load_golden_path(_pack(tmp_path, disposition="BLOCK", invariant=True))
    assert view is not None
    assert view.invariants == (("stock_record", True, False),)


def test_operator_sections_render_pack_and_absence(tmp_path: Path) -> None:
    path = _pack(tmp_path)
    rig = Rig()
    rig.refresher.refresh_once()
    env = {**GOOD_ENV, "ATREIDES_GOLDEN_PATH_FILE": str(path)}
    client = rig.app_client(env)
    login(client)
    expected = {
        "trades": "Golden customer timeline",
        "exceptions": "draft early warning",
        "cash": "Closing cash: 10000.00",
        "controls": "Engine disposition",
    }
    for section_name, text in expected.items():
        body = client.get(f"/section/{section_name}").get_data(as_text=True)
        assert "mvp4-client-reporting-2026-11-15-v2" in body
        assert text in body

    absent = rig.app_client(GOOD_ENV)
    login(absent)
    body = absent.get("/section/trades").get_data(as_text=True)
    assert "Run identifier: <code>ABSENT</code>" in body
    assert "No readable golden-path evidence pack is configured" in body
