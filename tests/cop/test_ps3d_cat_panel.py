"""PS-3D Part 3, Phase A: a planted CAT rejection renders on the exceptions panel.

The deadline text is the one ``lc.cat.validate`` already wrote into the feedback file.
The run call that writes that file during a golden path is Phase B.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from cop_fakes import GOOD_ENV, Rig, login

from lc.cat import write_cat_feedback

PACK_PATH = (
    Path(__file__).resolve().parents[2]
    / "cop"
    / "vendor"
    / "atreides-golden-path"
    / "evidence-pack.json"
)
CONTROL_PANEL = "CAT exceptions panel control"


def test_planted_invalid_cat_record_shows_on_cop_exceptions(tmp_path: Path) -> None:
    pack = json.loads(PACK_PATH.read_text(encoding="utf-8"))
    allocation = next(item for item in pack["evidence"] if item["summary"] == "allocation")
    assert allocation["cause_event_id"]
    allocation["cause_event_id"] = None
    folder = tmp_path / "planted-pack"
    folder.mkdir()
    path = folder / "evidence-pack.json"
    raw = json.dumps(pack).encode()
    path.write_bytes(raw)
    (folder / "source.json").write_text(
        json.dumps({"sha256": sha256(raw).hexdigest()}),
        encoding="utf-8",
    )
    written = write_cat_feedback(path)
    assert written["phase"] == "A"
    assert written["exception_count"] == 1
    rejection = written["rejections"][0]
    assert rejection["errors"] == ["parent new-order event is missing"]
    assert rejection["correction_deadline"] == "2026-11-19"
    rig = Rig()
    rig.refresher.refresh_once()
    client = rig.app_client({**GOOD_ENV, "ATREIDES_GOLDEN_PATH_FILE": str(path)})
    login(client)
    body = client.get("/panel/exceptions").get_data(as_text=True)
    expected = (
        f"Phase {written['phase']} CAT rejection {rejection['event_id']}"
        f" correction deadline {rejection['correction_deadline']}"
    )
    assert expected in body, CONTROL_PANEL
