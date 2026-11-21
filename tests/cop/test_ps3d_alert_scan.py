"""PS-3D Phase A: an alert identifier is a case identifier.

CASE-{alert_id} means an ALERT- token names a case. The scan also flags the anti-money
laundering monitoring scenario names. The golden path does not call this yet.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cop_fakes import GOOD_ENV, Rig, login

from cop.confidentiality import scan_confidentiality

CONTROL_ALERT = "alert identifier confidentiality control"
CONTROL_SCENARIO = "monitoring scenario confidentiality control"


class _Body:
    def __init__(self, text: str) -> None:
        self._text = text

    def get_data(self, as_text: bool = False) -> str:
        return self._text


class _Poison:
    def __init__(self, text: str) -> None:
        self._text = text

    def get(self, path: str, **kwargs: Any) -> _Body:
        del kwargs
        if path == "/panel/exceptions":
            return _Body(self._text)
        return _Body("clean")


def test_planted_alert_identifier_fails_publication_and_cop_scan(tmp_path: Path) -> None:
    publication = tmp_path / "publication"
    publication.mkdir()
    (publication / "clean.json").write_text('{"phase": "A"}\n', encoding="utf-8")
    planted = publication / "planted-alert.json"
    planted.write_text('{"token": "ALERT-PLANTED-1"}\n', encoding="utf-8")

    rig = Rig()
    rig.refresher.refresh_once()
    client = rig.app_client(GOOD_ENV)
    login(client)
    failures = scan_confidentiality(publication, client)
    assert any(item.endswith("planted-alert.json") for item in failures), CONTROL_ALERT

    poisoned = scan_confidentiality(publication, _Poison("ALERT-ON-THE-PAGE"))
    assert "/panel/exceptions" in poisoned, CONTROL_ALERT


def test_planted_monitoring_scenario_name_fails_scan(tmp_path: Path) -> None:
    publication = tmp_path / "publication"
    publication.mkdir()
    planted = publication / "planted-scenario.json"
    planted.write_text('{"scenario": "DEPOSIT_WITHDRAWAL_NO_TRADING"}\n', encoding="utf-8")
    failures = scan_confidentiality(publication, _Poison("clean"))
    assert any(item.endswith("planted-scenario.json") for item in failures), CONTROL_SCENARIO
    poisoned = scan_confidentiality(publication, _Poison("UNRELATED_ACCOUNT_TRANSFER"))
    assert "/panel/exceptions" in poisoned, CONTROL_SCENARIO
