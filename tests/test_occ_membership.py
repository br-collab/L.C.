"""SC-3 WP-6 acceptance, as amended by A5: OCC Rule 301(b)(1), and the common OCC table.

Acceptance criteria, mapped:

- Evidence below the requirement yields HOLD, citing the rule and the leg that binds:
  ``test_below_the_fixed_minimum_holds``, ``test_basic_standard_indebtedness_leg``,
  ``test_alternative_standard_debit_items_leg``.
- The requirement is the greater of the fixed minimum and the firm's standard's leg:
  ``test_the_requirement_is_the_greater_of_the_two_legs``.
- Missing input or source evidence is INDETERMINATE: ``test_missing_input_is_indeterminate``,
  ``test_a_missing_or_misread_table_item_is_indeterminate`` and every refused-item case below.
- PASS says only that this one rule was met: ``test_meeting_the_rule_passes_and_says_only_that``.
- Every figure comes from the cited, hash-pinned table, never a literal in code:
  ``test_no_occ_figure_is_a_literal_in_code``.
- The committed table pins Rule 301(b)(1) from the OCC Rules Bill saved:
  ``test_the_committed_table_pins_rule_301``, and, where ``OCC_SOURCES_DIR`` is set,
  ``test_every_committed_item_hashes_to_its_saved_source_file``.

Apart from the committed table, the tables below are SYNTHETIC: their URLs are
``example.invalid`` and their source files are written by the test. None of them is OCC's text.
"""

from __future__ import annotations

import hashlib
import json
import os
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
    DEBIT_ITEMS_ITEM,
    INDEBTEDNESS_CEILING_ITEM,
    MINIMUM_ITEM,
    FirmRegistration,
    NetCapitalEvidence,
    NetCapitalStandard,
    check_net_capital,
)
from lc.occ_rules import DEFAULT_TABLE_PATH, OccRuleTable, load_rule_table, verify_sources

D = Decimal
REPO = Path(__file__).resolve().parents[1]
SOURCE_TEXT = b"SYNTHETIC rules: the greater of (i) $10 million; (ii) 1500%; (iii) 2%."
SYNTHETIC_URL = "https://example.invalid/synthetic-rules"
BASIC, ALTERNATIVE = NetCapitalStandard.BASIC, NetCapitalStandard.ALTERNATIVE


def item(item_id: str = MINIMUM_ITEM, **changes: Any) -> dict[str, Any]:
    value, unit, verbatim = {
        MINIMUM_ITEM: ("10000000", "USD", "SYNTHETIC: the greater of (i) $10 million"),
        INDEBTEDNESS_CEILING_ITEM: (
            "1500",
            "percent of net capital",
            "SYNTHETIC: indebtedness cannot exceed 1500% of net capital",
        ),
        DEBIT_ITEMS_ITEM: (
            "2",
            "percent of aggregate debit items",
            "SYNTHETIC: 2% of its aggregate debit items",
        ),
    }[item_id]
    base: dict[str, Any] = {
        "item_id": item_id,
        "value": value,
        "unit": unit,
        "source": {
            "url": SYNTHETIC_URL,
            "source_kind": "RULES",
            "retrieved_dtg": "202610051200",
            "sha256": hashlib.sha256(SOURCE_TEXT).hexdigest(),
            "file_name": "synthetic-rules.pdf",
            "verbatim": verbatim,
        },
    }
    return base | changes


ALL_ITEMS = (MINIMUM_ITEM, INDEBTEDNESS_CEILING_ITEM, DEBIT_ITEMS_ITEM)


def table(tmp_path: Path, *items: dict[str, Any]) -> OccRuleTable:
    path = tmp_path / "table.json"
    chosen = items or tuple(item(i) for i in ALL_ITEMS)
    path.write_text(json.dumps({"table_version": "SYNTHETIC-1", "items": list(chosen)}))
    return load_rule_table(path)


def evidence(  # noqa: PLR0913 - each field of the evidence is set by name at the call site
    net_capital: str,
    *,
    standard: NetCapitalStandard = BASIC,
    indebtedness: str | None = "0",
    debit_items: str | None = None,
    currency: str = "USD",
    registration: FirmRegistration = FirmRegistration.BROKER_DEALER,
) -> NetCapitalEvidence:
    return NetCapitalEvidence(
        registration=registration,
        standard=standard,
        net_capital=D(net_capital),
        aggregate_indebtedness=None if indebtedness is None else D(indebtedness),
        aggregate_debit_items=None if debit_items is None else D(debit_items),
        currency=currency,
        source="SYNTHETIC net capital computation",
        as_of_dtg="202610051200",
        provenance=Provenance.POLICY_RESULT,
    )


def alternative(net_capital: str, debit_items: str) -> NetCapitalEvidence:
    return evidence(net_capital, standard=ALTERNATIVE, indebtedness=None, debit_items=debit_items)


# --- the rule ----------------------------------------------------------------------------------


@pytest.mark.parametrize("net_capital", ["0", "9999999.99"])
def test_below_the_fixed_minimum_holds(tmp_path: Path, net_capital: str) -> None:
    check = check_net_capital(evidence(net_capital), table(tmp_path))
    assert check.disposition is Disposition.HOLD
    assert check.reason.endswith("Below on the fixed minimum")
    assert "SYNTHETIC: the greater of (i) $10 million" in check.reason
    assert SYNTHETIC_URL in check.reason
    assert check.requirement == D("10000000.00")


@pytest.mark.parametrize(
    ("net_capital", "indebtedness", "disposition"),
    [
        # 150,000,000 of indebtedness is exactly 1500% of 10,000,000.
        ("10000000", "150000000", Disposition.PASS),
        ("10000000", "150000001", Disposition.HOLD),
        # 160,000,001 / 15 = 10,666,666.7333...: the comparison is exact, not rounded.
        ("10666666.73", "160000001", Disposition.HOLD),
        ("10666666.74", "160000001", Disposition.PASS),
    ],
)
def test_basic_standard_indebtedness_leg(
    tmp_path: Path, net_capital: str, indebtedness: str, disposition: Disposition
) -> None:
    check = check_net_capital(evidence(net_capital, indebtedness=indebtedness), table(tmp_path))
    assert check.disposition is disposition
    assert "1500% of net capital" in check.reason
    if disposition is Disposition.HOLD:
        assert check.reason.endswith("Below on aggregate indebtedness")


@pytest.mark.parametrize(
    ("net_capital", "debit_items", "disposition", "requirement"),
    [
        ("10000000", "500000000", Disposition.PASS, "10000000.00"),
        ("15000000", "1000000000", Disposition.HOLD, "20000000.00"),
        ("20000000", "1000000000", Disposition.PASS, "20000000.00"),
    ],
)
def test_alternative_standard_debit_items_leg(
    tmp_path: Path, net_capital: str, debit_items: str, disposition: Disposition, requirement: str
) -> None:
    check = check_net_capital(alternative(net_capital, debit_items), table(tmp_path))
    assert check.disposition is disposition
    assert check.requirement == D(requirement)
    assert "2% of its aggregate debit items" in check.reason
    assert "1500%" not in check.reason


def test_the_requirement_is_the_greater_of_the_two_legs(tmp_path: Path) -> None:
    loaded = table(tmp_path)
    small_leg = check_net_capital(evidence("1", indebtedness="15"), loaded)
    large_leg = check_net_capital(evidence("1", indebtedness="300000000"), loaded)
    assert small_leg.requirement == D("10000000.00")
    assert large_leg.requirement == D("20000000.00")
    assert large_leg.reason.endswith("Below on the fixed minimum and aggregate indebtedness")


@pytest.mark.parametrize("net_capital", ["10000000", "250000000"])
def test_meeting_the_rule_passes_and_says_only_that(tmp_path: Path, net_capital: str) -> None:
    check = check_net_capital(evidence(net_capital), table(tmp_path))
    assert check.disposition is Disposition.PASS
    assert check.claim == CLAIM
    assert "not OCC membership or approval" in check.reason


@pytest.mark.parametrize(
    ("supplied", "said"),
    [
        (None, "no net capital evidence"),
        (
            evidence("50000000", registration=FirmRegistration.FUTURES_COMMISSION_MERCHANT),
            "covers broker-dealers",
        ),
        (evidence("50000000", indebtedness=None), "needs aggregate indebtedness"),
        (alternative("50000000", "1").model_copy(update={"aggregate_debit_items": None}), "needs"),
        (evidence("50000000", currency="EUR"), "evidence is in EUR"),
    ],
    ids=["no-evidence", "not-a-broker-dealer", "no-indebtedness", "no-debit-items", "currency"],
)
def test_missing_input_is_indeterminate(
    tmp_path: Path, supplied: NetCapitalEvidence | None, said: str
) -> None:
    check = check_net_capital(supplied, table(tmp_path))
    assert check.disposition is Disposition.INDETERMINATE
    assert said in check.reason
    assert check.requirement is None


def test_a_missing_or_misread_table_item_is_indeterminate(tmp_path: Path) -> None:
    without_leg = table(tmp_path, item(MINIMUM_ITEM), item(DEBIT_ITEMS_ITEM))
    check = check_net_capital(evidence("50000000"), without_leg)
    assert check.disposition is Disposition.INDETERMINATE
    assert f"has no item {INDEBTEDNESS_CEILING_ITEM}" in check.reason
    # The alternative standard does not need the missing item.
    assert check_net_capital(alternative("50000000", "1"), without_leg).disposition is (
        Disposition.PASS
    )
    misread = table(
        tmp_path, item(MINIMUM_ITEM), item(INDEBTEDNESS_CEILING_ITEM, unit="percent of debits")
    )
    assert "not 'percent of net capital'" in check_net_capital(evidence("1"), misread).reason


def test_the_check_records_the_table_it_read(tmp_path: Path) -> None:
    loaded = table(tmp_path)
    check = check_net_capital(evidence("1"), loaded)
    assert check.table_version == "SYNTHETIC-1"
    assert check.table_items_digest == loaded.items_digest
    assert {c.item_id for c in check.criteria} == {MINIMUM_ITEM, INDEBTEDNESS_CEILING_ITEM}


# --- the committed table -----------------------------------------------------------------------


def test_the_committed_table_pins_rule_301() -> None:
    committed = load_rule_table(DEFAULT_TABLE_PATH)
    assert committed.refused == ()
    rule_301 = [i for i in committed.items if i.item_id.startswith("occ.rule301.")]
    assert {i.item_id for i in rule_301} == set(ALL_ITEMS)
    for pinned in rule_301:
        assert pinned.source.file_name == "occ_rules.pdf"
        assert pinned.source.source_kind == "RULES"
    minimum = committed.item(MINIMUM_ITEM)
    assert minimum is not None and minimum.value == "10000000" and minimum.unit == "USD"
    assert "greater of: (i) $10 million" in minimum.source.verbatim
    below = check_net_capital(evidence("9000000"), committed)
    assert below.disposition is Disposition.HOLD
    assert "occ_rules.pdf" in below.reason


OCC_SOURCES = os.environ.get("OCC_SOURCES_DIR")


@pytest.mark.skipif(
    not OCC_SOURCES,
    reason="OCC's source files are not vendored; set OCC_SOURCES_DIR to the folder Bill saved "
    "them in to re-hash them. A skip is not a pass.",
)
def test_every_committed_item_hashes_to_its_saved_source_file() -> None:
    committed = load_rule_table(DEFAULT_TABLE_PATH)
    checked = verify_sources(committed, Path(str(OCC_SOURCES)))
    assert checked.refused == () and checked.items == committed.items


# --- the table fails closed --------------------------------------------------------------------


def minimum_with(**source: Any) -> dict[str, Any]:
    return item(source=item()["source"] | source)


@pytest.mark.parametrize(
    ("bad", "reason"),
    [
        (minimum_with(verbatim="the greater of (i) $1 million"), "does not state its value"),
        (item(source={k: v for k, v in item()["source"].items() if k != "sha256"}), "incomplete"),
        (minimum_with(url="http://example.invalid"), "incomplete"),
        (item(value="ten million"), "incomplete"),
        (item(value=10000000.0), "incomplete"),
    ],
    ids=["verbatim-states-another-value", "no-sha256", "not-https", "not-a-number", "a-float"],
)
def test_an_unsupported_item_is_refused_and_the_check_is_indeterminate(
    tmp_path: Path, bad: dict[str, Any], reason: str
) -> None:
    loaded = table(tmp_path, bad, item(INDEBTEDNESS_CEILING_ITEM))
    assert [i.item_id for i in loaded.items] == [INDEBTEDNESS_CEILING_ITEM]
    assert reason in loaded.refused[0].reason
    check = check_net_capital(evidence("50000000"), loaded)
    assert check.disposition is Disposition.INDETERMINATE
    assert "was refused" in check.reason


@pytest.mark.parametrize("verbatim", ["$10 million", "$10,000,000", "10000000", "$10,000,000.00"])
def test_the_loader_reads_each_way_a_source_writes_the_value(tmp_path: Path, verbatim: str) -> None:
    assert table(tmp_path, minimum_with(verbatim=f"a minimum of {verbatim}")).refused == ()


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
        (sources / "synthetic-rules.pdf").write_bytes(content)
    checked = verify_sources(table(tmp_path), sources)
    assert checked.items == () and reason in checked.refused[0].reason
    assert check_net_capital(evidence("50000000"), checked).disposition is (
        Disposition.INDETERMINATE
    )


def test_a_matching_source_file_keeps_the_item(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "synthetic-rules.pdf").write_bytes(SOURCE_TEXT)
    loaded = table(tmp_path)
    checked = verify_sources(loaded, sources)
    assert checked.items == loaded.items and checked.items_digest == loaded.items_digest


def test_the_items_digest_follows_the_items(tmp_path: Path) -> None:
    first = table(tmp_path, item())
    assert table(tmp_path, item()).items_digest == first.items_digest
    changed = table(tmp_path, minimum_with(retrieved_dtg="202610061200"))
    assert changed.items_digest != first.items_digest


# --- no figure in code -------------------------------------------------------------------------


def test_no_occ_figure_is_a_literal_in_code() -> None:
    for path in sorted((REPO / "lc").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert not re.search(r"10[_,]?000[_,]?000|\b1500\b", source), path.name


def test_the_modules_state_what_they_claim() -> None:
    membership = " ".join((membership_module.__doc__ or "").split())
    rules = " ".join((rules_module.__doc__ or "").split())
    assert "EXPERIMENTAL" in membership and "checks that one rule and nothing else" in membership
    assert "no distinction between an initial and an ongoing requirement" in membership
    assert "EXPERIMENTAL" in rules and "never a literal in code" in rules
