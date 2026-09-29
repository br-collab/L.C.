from datetime import date

import pytest
from cannae_kernel.ids import LifecycleId, ScenarioId
from cannae_kernel.session import BusinessDate

from harness_c2.scenario import (
    ForeignLifecycleIdError,
    IdentifierKind,
    ScenarioRecord,
    start_scenario,
)

SCENARIO_ID = ScenarioId("scn_01K6C7FBR0R4JBQMT6HTZB79E1")
FOREIGN_ID = LifecycleId("lif_01K6C7FBR0R4JBQMT6HTZB79E2")
COMMITS = {
    "aureon": "a" * 40,
    "lc": "b" * 40,
    "atreides": "c" * 40,
}
BUSINESS_DATE = BusinessDate(
    value=date(2026, 9, 29),
    calendar="Fedwire Funds Service",
    established_by="rail published calendar",
)


def _scenario() -> ScenarioRecord:
    return start_scenario(
        scenario_id=SCENARIO_ID,
        seed=29,
        pinned_commits=COMMITS,
        policy_versions={"execution": "1.0", "settlement": "1.0"},
        business_date=BUSINESS_DATE,
    )


def test_same_scenario_and_seed_mint_the_same_single_lifecycle() -> None:
    assert _scenario().lifecycle_id == _scenario().lifecycle_id


def test_foreign_lifecycle_is_refused_by_name() -> None:
    with pytest.raises(ForeignLifecycleIdError, match="FOREIGN_LIFECYCLE_ID"):
        _scenario().require_lifecycle(FOREIGN_ID)


def test_domain_identifiers_link_without_collapsing_into_lifecycle() -> None:
    scenario = _scenario().link(IdentifierKind.INTENT, "int-domain-001")
    assert scenario.linked_identifiers[IdentifierKind.INTENT] == ("int-domain-001",)
    with pytest.raises(ValueError, match="never collapsed"):
        scenario.link(IdentifierKind.FILL, str(scenario.lifecycle_id))


def test_scenario_is_canonical_and_digest_stable() -> None:
    first = _scenario()
    second = _scenario()
    assert first.to_canonical_bytes() == second.to_canonical_bytes()
    assert first.canonical_digest == second.canonical_digest
    assert b'"established_by":"rail published calendar"' in first.to_canonical_bytes()
