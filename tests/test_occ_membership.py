"""SC-3 WP-6 acceptance: one published OCC membership criterion, and the common OCC table.

Acceptance criteria, mapped:

- Below-criterion evidence yields HOLD with the cited criterion:
  ``test_evidence_below_the_criterion_holds_and_cites_it``.
- Missing input or source evidence is INDETERMINATE: ``test_missing_evidence_is_indeterminate``,
  ``test_the_committed_table_has_no_criterion_yet_so_the_check_is_indeterminate`` and every
  refused-item case below.
- PASS says only that this one published criterion was met:
  ``test_evidence_meeting_the_criterion_passes_and_says_only_that``.
- The criterion comes from the cited, hash-pinned table, never a literal in code:
  ``test_no_occ_figure_is_a_literal_in_code``.

The tables below are SYNTHETIC: their URLs are ``example.invalid`` and their source files are
written by the test. None of them is OCC's text.
"""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from cannae_kernel.disposition import Disposition
from cannae_kernel.provenance import Provenance

import lc.occ_membership as membership_module
import lc.occ_rules as rules_module
from lc.occ_membership import (
    CLAIM,
    CRITERION_ITEM,
    InitialNetCapitalEvidence,
    check_initial_net_capital,
)
from lc.occ_rules import DEFAULT_TABLE_PATH, OccRuleTable, load_rule_table, verify_sources

D = Decimal
REPO = Path(__file__).resolve().parents[1]
SOURCE_TEXT = b"SYNTHETIC page: applicants must have a minimum initial net capital of $10,000,000."


def item(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "item_id": CRITERION_ITEM,
        "value": "10000000",
        "unit": "USD",
        "source": {
            "url": "https://example.invalid/synthetic-membership",
            "source_kind": "WEB_PAGE",
            "retrieved_dtg": "202610051200",
            "sha256": hashlib.sha256(SOURCE_TEXT).hexdigest(),
            "file_name": "synthetic-membership.html",
            "verbatim": "a minimum initial net capital of $10,000,000",
        },
    }
    return base | changes


def table(tmp_path: Path, *items: dict[str, Any]) -> OccRuleTable:
    path = tmp_path / "table.json"
    path.write_text(json.dumps({"table_version": "SYNTHETIC-1", "items": list(items)}))
    return load_rule_table(path)


def evidence(amount: str, currency: str = "USD") -> InitialNetCapitalEvidence:
    return InitialNetCapitalEvidence(
        amount=D(amount),
        currency=currency,
        source="SYNTHETIC net capital computation",
        as_of_dtg="202610051200",
        provenance=Provenance.POLICY_RESULT,
    )


# --- the criterion -----------------------------------------------------------------------------


@pytest.mark.parametrize("amount", ["0", "9999999.99"])
def test_evidence_below_the_criterion_holds_and_cites_it(tmp_path: Path, amount: str) -> None:
    check = check_initial_net_capital(evidence(amount), table(tmp_path, item()))
    assert check.disposition is Disposition.HOLD
    assert "a minimum initial net capital of $10,000,000" in check.reason
    assert "https://example.invalid/synthetic-membership" in check.reason
    assert check.criterion is not None and check.criterion.value == "10000000"
    assert "refused" not in check.reason


@pytest.mark.parametrize("amount", ["10000000", "250000000"])
def test_evidence_meeting_the_criterion_passes_and_says_only_that(
    tmp_path: Path, amount: str
) -> None:
    check = check_initial_net_capital(evidence(amount), table(tmp_path, item()))
    assert check.disposition is Disposition.PASS
    assert check.claim == CLAIM
    assert "not OCC membership or approval" in check.reason


def test_missing_evidence_is_indeterminate(tmp_path: Path) -> None:
    check = check_initial_net_capital(None, table(tmp_path, item()))
    assert check.disposition is Disposition.INDETERMINATE
    assert "no initial net capital evidence" in check.reason


def test_evidence_in_another_currency_is_indeterminate(tmp_path: Path) -> None:
    check = check_initial_net_capital(evidence("20000000", "EUR"), table(tmp_path, item()))
    assert check.disposition is Disposition.INDETERMINATE


def test_the_check_records_the_table_it_read(tmp_path: Path) -> None:
    loaded = table(tmp_path, item())
    check = check_initial_net_capital(evidence("1"), loaded)
    assert check.table_version == "SYNTHETIC-1"
    assert check.table_items_digest == loaded.items_digest


def test_the_committed_table_has_no_criterion_yet_so_the_check_is_indeterminate() -> None:
    """OCC's site refused retrieval from the build environment on 5 October 2026, so no OCC
    source is pinned yet. This test changes when one is: it records the current state."""
    committed = load_rule_table(DEFAULT_TABLE_PATH)
    assert committed.items == ()
    check = check_initial_net_capital(evidence("50000000"), committed)
    assert check.disposition is Disposition.INDETERMINATE
    assert f"has no item {CRITERION_ITEM}" in check.reason


# --- the table fails closed --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("bad", "reason"),
    [
        (
            item(
                source=item()["source"]
                | {"verbatim": "a minimum initial net capital of $1,000,000"}
            ),
            "does not state its value",
        ),
        (item(source={k: v for k, v in item()["source"].items() if k != "sha256"}), "incomplete"),
        (item(source=item()["source"] | {"url": "http://example.invalid"}), "incomplete"),
        (item(value="ten million"), "incomplete"),
        (item(value=10000000.0), "incomplete"),
    ],
    ids=["verbatim-states-another-value", "no-sha256", "not-https", "not-a-number", "a-float"],
)
def test_an_unsupported_item_is_refused_and_the_check_is_indeterminate(
    tmp_path: Path, bad: dict[str, Any], reason: str
) -> None:
    loaded = table(tmp_path, bad)
    assert loaded.items == ()
    assert reason in loaded.refused[0].reason
    check = check_initial_net_capital(evidence("50000000"), loaded)
    assert check.disposition is Disposition.INDETERMINATE
    assert "was refused" in check.reason


def test_a_duplicate_item_is_refused(tmp_path: Path) -> None:
    loaded = table(tmp_path, item(), item())
    assert len(loaded.items) == 1
    assert loaded.refused[0].reason == "the item appears twice"


@pytest.mark.parametrize(
    ("content", "reason"),
    [(None, "is missing"), (SOURCE_TEXT + b" altered", "does not hash")],
    ids=["missing-file", "altered-file"],
)
def test_mismatched_source_evidence_is_refused(
    tmp_path: Path, content: bytes | None, reason: str
) -> None:
    sources = tmp_path / "sources"
    sources.mkdir()
    if content is not None:
        (sources / "synthetic-membership.html").write_bytes(content)
    checked = verify_sources(table(tmp_path, item()), sources)
    assert checked.items == () and reason in checked.refused[0].reason
    assert check_initial_net_capital(evidence("50000000"), checked).disposition is (
        Disposition.INDETERMINATE
    )


def test_a_matching_source_file_keeps_the_item(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "synthetic-membership.html").write_bytes(SOURCE_TEXT)
    loaded = table(tmp_path, item())
    checked = verify_sources(loaded, sources)
    assert checked.items == loaded.items and checked.items_digest == loaded.items_digest


def test_the_items_digest_follows_the_items(tmp_path: Path) -> None:
    first = table(tmp_path, item())
    assert table(tmp_path, item()).items_digest == first.items_digest
    changed = table(tmp_path, item(source=item()["source"] | {"retrieved_dtg": "202610061200"}))
    assert changed.items_digest != first.items_digest


# --- no figure in code -------------------------------------------------------------------------


def test_no_occ_figure_is_a_literal_in_code() -> None:
    for path in sorted((REPO / "lc").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert not re.search(r"10[_,]?000[_,]?000", source), path.name


def test_the_modules_state_what_they_claim() -> None:
    membership = " ".join((membership_module.__doc__ or "").split())
    rules = " ".join((rules_module.__doc__ or "").split())
    assert "EXPERIMENTAL" in membership and "says only that this one published" in membership
    assert "EXPERIMENTAL" in rules and "never a literal in code" in rules
