"""PS-3D Part 5, Phase A: publication files and COP routes stay free of case data.

COP is the Common Operating Picture. The scan is not called from a run yet. That wiring
is Phase B.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cop_fakes import GOOD_ENV, Rig, login

from cop.app import PANELS, SECTIONS
from cop.confidentiality import cop_get_routes, scan_confidentiality

CONTROL_SCAN = "SAR confidentiality scan control"


class _Body:
    def __init__(self, text: str) -> None:
        self._text = text

    def get_data(self, as_text: bool = False) -> str:
        return self._text


class _PoisonedExceptions:
    def get(self, path: str, **kwargs: Any) -> _Body:
        del kwargs
        if path == "/panel/exceptions":
            return _Body("CASE-ON-THE-PAGE")
        return _Body("clean")


def test_planted_case_identifier_fails_publication_and_cop_scan(tmp_path: Path) -> None:
    publication = tmp_path / "publication"
    publication.mkdir()
    (publication / "clean.json").write_text('{"phase": "A"}\n', encoding="utf-8")
    planted = publication / "planted-pack.json"
    planted.write_text('{"case_id": "CASE-ALERT-PLANTED"}\n', encoding="utf-8")

    rig = Rig()
    rig.refresher.refresh_once()
    client = rig.app_client(GOOD_ENV)
    login(client)
    failures = scan_confidentiality(publication, client)
    assert any(item.endswith("planted-pack.json") for item in failures), CONTROL_SCAN
    routes = cop_get_routes()
    assert not (set(failures) & set(routes))
    assert routes[0] == "/"
    assert "/healthz" in routes
    assert "/login" in routes
    assert {f"/section/{name}" for name in SECTIONS} <= set(routes)
    assert {f"/panel/{name}" for name in PANELS} <= set(routes)

    poisoned = scan_confidentiality(publication, _PoisonedExceptions())
    assert "/panel/exceptions" in poisoned
