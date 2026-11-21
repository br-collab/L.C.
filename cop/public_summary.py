"""Render the Phase A public summary that the pack already holds.

COP is the Common Operating Picture. The route reads public-summary.json beside the
golden-path pack. It does not recompute the counts. No run writes that file yet.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def load_public_summary(golden_path_file: Path | None) -> dict[str, Any] | None:
    """Read the pack's public summary. Absence is absence, not a second set of numbers."""

    if golden_path_file is None:
        return None
    path = golden_path_file.with_name("public-summary.json")
    if not path.is_file():
        return None
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        return None
    return document


def render_public_summary(document: Mapping[str, Any]) -> str:
    """Show the pack counts. The CAT record count is the file's cat_record_count."""

    dispositions = ", ".join(str(item) for item in document["surveillance_dispositions"])
    return "\n".join(
        (
            "Phase A public summary",
            f"wsp register status {document['wsp_register_status']}",
            f"wsp register version {document['wsp_register_version']}",
            f"3120 passed count {document['3120_passed_count']}",
            f"3120 exception count {document['3120_exception_count']}",
            f"3120 label {document['3120_label']}",
            f"cat record count {document['cat_record_count']}",
            f"cat feedback count {document['cat_feedback_count']}",
            f"aml alert count {document['aml_alert_count']}",
            f"surveillance dispositions {dispositions}",
        )
    )
