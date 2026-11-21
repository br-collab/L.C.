"""PS-3D Part 6, Phase A: the COP shows the pack's public summary.

COP is the Common Operating Picture. The route reads public-summary.json. It does not
recompute the counts. The golden path does not write the file yet.
"""

from __future__ import annotations

import json
from pathlib import Path

from cop_fakes import GOOD_ENV, Rig, login

from cop.confidentiality import leaks_supervisory_secret, scan_confidentiality

CONTROL_PACK = "public summary pack count control"


def _summary() -> dict[str, object]:
    return {
        "phase": "A",
        "wsp_register_status": "EXCEPTIONS",
        "wsp_register_version": "1.2.0",
        "wsp_row_count": 18,
        "3120_passed_count": 11,
        "3120_exception_count": 41,
        "3120_label": "Phase A",
        "cat_record_count": 17,
        "cat_feedback_count": 5,
        "aml_alert_count": 2,
        "surveillance_dispositions": ["SURVEIL_CLEAR"],
    }


def test_cop_route_shows_public_summary_counts_from_pack(tmp_path: Path) -> None:
    publication = tmp_path / "publication"
    publication.mkdir()
    summary = _summary()
    (publication / "public-summary.json").write_text(
        json.dumps(summary, sort_keys=True),
        encoding="utf-8",
    )
    pack = publication / "golden-path.json"
    pack.write_text('{"phase": "A"}\n', encoding="utf-8")

    env = {**GOOD_ENV, "ATREIDES_GOLDEN_PATH_FILE": str(pack)}
    rig = Rig()
    rig.refresher.refresh_once()
    client = rig.app_client(env)
    login(client)
    body = client.get("/public-summary").get_data(as_text=True)
    assert f"cat record count {summary['cat_record_count']}" in body, CONTROL_PACK
    assert f"cat feedback count {summary['cat_feedback_count']}" in body
    assert f"3120 exception count {summary['3120_exception_count']}" in body
    assert f"3120 passed count {summary['3120_passed_count']}" in body
    assert f"wsp register status {summary['wsp_register_status']}" in body
    assert f"wsp register version {summary['wsp_register_version']}" in body
    assert f"aml alert count {summary['aml_alert_count']}" in body
    assert "SURVEIL_CLEAR" in body
    assert "Phase A" in body
    assert not leaks_supervisory_secret(body)

    failures = scan_confidentiality(publication, client)
    assert not any(item.endswith("public-summary.json") for item in failures)
    assert "/public-summary" not in failures or not leaks_supervisory_secret(body)
