"""Cross-repository workflow pin checks."""

from pathlib import Path


def test_ci_workflows_pin_aureon_and_use_correct_pr_label() -> None:
    strict = Path(".github/workflows/strict-typing.yml").read_text(encoding="utf-8")
    assert "ref: 1c95875178d8819446b798991ef8483c9a49a029" in strict
    ci = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "PS-3C Part 6: Aureon #158" in ci
