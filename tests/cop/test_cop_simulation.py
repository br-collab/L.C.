from __future__ import annotations

from cop_fakes import Rig, login

from cop.app import PANELS, SECTIONS


def test_simulation_is_a_reachable_cop_panel() -> None:
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
    assert "Not tested:" in body
