from __future__ import annotations

from pathlib import Path

ATREIDES = "75199dc67d59f5c58a6953031ac0d39ba680c5cf"
AUREON = "1c95875178d8819446b798991ef8483c9a49a029"


def test_cross_repository_workflows_use_current_main_pins() -> None:
    ci = Path(".github/workflows/ci.yml").read_text()
    typing = Path(".github/workflows/strict-typing.yml").read_text()

    assert ci.count(f"ref: {ATREIDES}") == 2
    assert ci.count(f"ref: {AUREON}") == 2
    assert f"ref: {ATREIDES}" in typing
    assert f"ref: {AUREON}" in typing
    assert "MVP-5B closure: Aureon #146" not in ci
    assert "PS-1B closure: Atreides #194" not in ci
