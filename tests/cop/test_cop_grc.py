"""COP-2 WP-3 governance, controls/compliance and risk-limit panels."""

from __future__ import annotations

from cannae_kernel.disposition import Disposition
from cop_fakes import Rig, login, section

from cop.demo import DemoGrc


def test_production_grc_panels_are_absent_not_empty_or_green() -> None:
    rig = Rig()
    rig.refresher.refresh_once()
    client = rig.app_client()
    login(client)
    for name in ("controls", "risk"):
        html = client.get(f"/section/{name}").get_data(as_text=True)
        body = section(html, name)
        assert "Absent in production" in body
        assert "PASS" not in body
        assert "tone-absent" in html
    decisions = client.get("/section/decisions").get_data(as_text=True)
    assert "No published DSOR decision feed exists" in decisions
    assert "Partial coverage: governance Absent" in decisions


def test_demo_governance_carries_actor_time_doctrine_and_evidence() -> None:
    rig = Rig(grc_configured=True)
    rig.refresher.refresh_once()
    client = rig.app_client()
    login(client)
    body = section(client.get("/section/decisions").get_data(as_text=True), "governance")
    assert "Single operator under CAOM-001" in body
    assert "CATO-1.1" in body
    assert "Decision time" in body
    assert "demo://dsor/GOV-104" in body
    assert "authority record" in body


def test_control_without_test_evidence_is_indeterminate() -> None:
    rig = Rig(grc_configured=True)
    control = next(item for item in DemoGrc(rig.clock).controls() if item.control_id == "CTL-MAP")
    assert control.disposition is Disposition.PASS
    assert control.effective_disposition is Disposition.INDETERMINATE
    rig.refresher.refresh_once()
    client = rig.app_client()
    login(client)
    body = section(client.get("/section/controls").get_data(as_text=True), "controls")
    assert "CTL-MAP" in body
    assert "INDETERMINATE" in body
    assert "Absent — no test evidence recorded" in body


def test_risk_bands_use_recorded_exposure_and_limit() -> None:
    rig = Rig(grc_configured=True)
    risks = DemoGrc(rig.clock).risks()
    assert [risk.disposition for risk in risks] == [
        Disposition.HOLD,
        Disposition.HOLD,
        Disposition.BLOCK,
    ]
    rig.refresher.refresh_once()
    client = rig.app_client()
    login(client)
    body = section(client.get("/section/risk").get_data(as_text=True), "risk")
    assert "82% utilised" in body
    assert "90% utilised" in body
    assert "104% utilised" in body
    assert "producer clock" in body


def test_grc_pages_remain_read_only() -> None:
    rig = Rig(grc_configured=True)
    rig.refresher.refresh_once()
    client = rig.app_client()
    login(client)
    html = "".join(
        client.get(f"/section/{name}").get_data(as_text=True)
        for name in ("decisions", "controls", "risk")
    )
    assert "Assign" not in html
    assert "Approve" not in html
    assert "Release halt" not in html
