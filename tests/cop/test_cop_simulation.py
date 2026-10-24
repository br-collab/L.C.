from __future__ import annotations

from cop_fakes import Rig, login

from cop.app import PANELS, SECTIONS


def test_simulation_is_a_reachable_cop_panel() -> None:
    """CLOSE-1 WP4.4: list every artifact, window and source commit."""
    assert PANELS["simulation"] == "Market and settlement simulation"
    assert "simulation" in SECTIONS["programme"]
    rig = Rig()
    rig.refresher.refresh_once()
    client = rig.app_client()
    login(client)
    response = client.get("/panel/simulation")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "SYNTHETIC SIMULATION:" in body
    notice = (
        "SIM-1 v1 invariants are not yet independent checks; green results are not evidence "
        "(see SIM-1B)."
    )
    assert notice in body
    assert "simulation-snapshot/daily-close.json" in body
    assert "simulation-snapshot/run-summary.json" in body
    assert "simulation-snapshot/trend.json" in body
    assert "simulation-snapshot/spike-2019/SUMMARY.md" in body
    assert "simulation-snapshot/spike-2019/run-summary.json" in body
    assert "simulation-snapshot/stress-2020-03/SUMMARY.md" in body
    assert "simulation-snapshot/stress-2020-03/run-summary.json" in body
    assert "simulation-snapshot/stress-2023-03/SUMMARY.md" in body
    assert "simulation-snapshot/stress-2023-03/run-summary.json" in body
    assert "2019-08-19" in body and "2019-09-17" in body
    assert "2020-02-10" in body and "2020-03-31" in body
    assert "2023-02-09" in body and "2023-03-24" in body
    source_commit = "21348fea502dcc8a87e33e70365203c6404efe0d"
    assert body.count(f"/commit/{source_commit}") == 3
    assert body.count(f"<code>{source_commit}</code>") == 3
    assert "Rolling synthetic close" in body
    assert "Dated published artifacts" in body
    assert "does not replace the latest synthetic close" in body
    assert "Not tested:" in body
