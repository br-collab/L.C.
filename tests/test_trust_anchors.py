import hashlib
import inspect
import tomllib

from lc.lifecycle import accept_intent, split_parent
from lc.market_access import MarketAccessGate
from lc.oms import replace_order
from lc.recommendation_profile import enforce_recommendation_profile, read_account_status_document
from lc.trust_anchors import TRUST_ANCHOR_PATH

EXPECTED_CHECKSUM = "966f3c87705e9d0f86d4bde4a246248c17272b039bba12581e5e10ccf09847fd"


def test_trust_anchor_file_checksum() -> None:
    assert hashlib.sha256(TRUST_ANCHOR_PATH.read_bytes()).hexdigest() == EXPECTED_CHECKSUM
    with (TRUST_ANCHOR_PATH.parents[1] / "pyproject.toml").open("rb") as handle:
        package_data = tomllib.load(handle)["tool"]["setuptools"]["package-data"]["lc"]
    assert TRUST_ANCHOR_PATH.name in package_data


def test_public_verifiers_accept_no_registry_argument() -> None:
    public_entries = (
        accept_intent,
        split_parent,
        replace_order,
        enforce_recommendation_profile,
        read_account_status_document,
        MarketAccessGate,
    )
    for entry in public_entries:
        assert all("registry" not in name for name in inspect.signature(entry).parameters)
