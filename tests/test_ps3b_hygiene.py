"""PS-3B workflow pin checks."""

from pathlib import Path


def test_ci_workflows_pin_aureon_and_use_correct_pr_label() -> None:
    strict = Path(".github/workflows/strict-typing.yml").read_text(encoding="utf-8")
    assert "ref: 58698b418ef1c72110efaf854b96b962941c42c8" in strict
    ci = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "MVP-5B closure: Aureon #146" in ci
