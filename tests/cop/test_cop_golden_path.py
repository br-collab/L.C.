"""MVP-5 operator views from the versioned golden-path pack."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from cop_fakes import GOOD_ENV, Rig, login

from cop.golden_path import load_golden_path


def _pack(tmp_path: Path, *, disposition: str = "PASS", invariant: bool = True) -> Path:
    folder = tmp_path / "mvp4-client-reporting-2026-11-15-v2"
    folder.mkdir()
    path = folder / "evidence-pack.json"
    pack = json.dumps(
        {
            "run_id": "PS1-RUN-001",
            "disposition": disposition,
            "account_status": "ACTIVE",
            "close_report": "NORMAL_CLOSE",
            "invariants": {"stock_record": invariant},
            "engine_dispositions": {"street_reconciliation": disposition},
            "early_warning_notices": [{"paragraph": "17a-11", "status": "DRAFT"}],
            "exception_owner": None,
            "exception_age_seconds": None,
            "evidence": [
                {
                    "event_id": "evt_1",
                    "producer": "Atreides",
                    "signature_status": "VERIFIED",
                    "actor_kind": "HUMAN",
                    "summary": "account approved",
                }
            ],
            "statement": {
                "closing_cash": "10000.00",
                "closing_positions": {"SYN-EQUITY": "10"},
            },
            "reconciliation_calendar": [
                {"item_id": item_id, "status": "RAN", "break_count": 0}
                for item_id in (
                    "stock-record-to-street",
                    "ftr-to-cns-fail-statement",
                    "stock-loan-to-contract-register",
                    "cash-to-bank",
                    "reserve-bank-to-books",
                    "securities-count",
                )
            ],
        }
    )
    path.write_text(pack, encoding="utf-8")
    (folder / "source.json").write_text(
        json.dumps({"sha256": sha256(pack.encode()).hexdigest()}),
        encoding="utf-8",
    )
    return path


def test_missing_or_malformed_source_is_absent(tmp_path: Path) -> None:
    assert load_golden_path(None) is None
    assert load_golden_path(tmp_path / "missing.json") is None


def test_pack_supplies_run_timeline_cash_and_controls(tmp_path: Path) -> None:
    view = load_golden_path(_pack(tmp_path))
    assert view is not None
    assert view.run_id == "PS1-RUN-001"
    assert view.evidence[0]["producer"] == "Atreides"
    assert view.closing_cash == "10000.00"
    assert view.invariants == (("stock_record", True, "PASS", True),)
    assert len(view.reconciliation_calendar) == 6


def test_engine_and_invariant_disagreement_is_a_failure(tmp_path: Path) -> None:
    view = load_golden_path(_pack(tmp_path, disposition="PASS", invariant=False))
    assert view is not None
    assert view.invariants == (("stock_record", False, "PASS", False),)


def test_one_byte_pack_change_fails_closed_with_named_health_reason(tmp_path: Path) -> None:
    path = _pack(tmp_path)
    path.write_bytes(path.read_bytes() + b" ")
    rig = Rig()
    rig.refresher.refresh_once()
    health = (
        rig.app_client({**GOOD_ENV, "ATREIDES_GOLDEN_PATH_FILE": str(path)})
        .get("/healthz")
        .get_json()["sources"]["golden-path"]
    )
    assert health["state"] == "not_configured"
    assert health["reason"].startswith("sha256 mismatch:")


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
        assert "PS1-RUN-001" in body
        assert text in body

    dashboard = client.get("/").get_data(as_text=True)
    for section_name in expected:
        assert f'href="/section/{section_name}"' in dashboard
        section = dashboard.split(f'href="/section/{section_name}"', 1)[1].split("</a>", 1)[0]
        assert "ABSENT" not in section
    controls = client.get("/section/controls").get_data(as_text=True)
    for item_id in (
        "stock-record-to-street",
        "ftr-to-cns-fail-statement",
        "stock-loan-to-contract-register",
        "cash-to-bank",
        "reserve-bank-to-books",
        "securities-count",
    ):
        assert item_id in controls

    absent = rig.app_client(GOOD_ENV)
    login(absent)
    body = absent.get("/section/trades").get_data(as_text=True)
    assert "Run identifier: <code>ABSENT</code>" in body
    assert "No readable golden-path evidence pack is configured" in body
