"""Test composition root for process-local synthetic trust."""

from datetime import UTC, datetime

from lc.trust_anchors import TrustAnchor, install_runtime_trust_anchor
from test_fixtures.signed_approval import KEY_ID as APPROVAL_KEY_ID
from test_fixtures.signed_approval import PRIVATE_KEY as APPROVAL_PRIVATE_KEY
from test_fixtures.signed_status import KEY_ID, PRIVATE_KEY


def pytest_sessionstart() -> None:
    install_runtime_trust_anchor(
        TrustAnchor(
            key_id=KEY_ID,
            producer="atreides",
            public_key=PRIVATE_KEY.public_key(),
            valid_from=datetime(2026, 1, 1, tzinfo=UTC),
            valid_to=datetime(2035, 12, 31, 23, 59, 59, tzinfo=UTC),
            revoked=False,
        )
    )
    install_runtime_trust_anchor(
        TrustAnchor(
            key_id=APPROVAL_KEY_ID,
            producer="atreides-human-approval",
            public_key=APPROVAL_PRIVATE_KEY.public_key(),
            valid_from=datetime(2026, 1, 1, tzinfo=UTC),
            valid_to=datetime(2035, 12, 31, 23, 59, 59, tzinfo=UTC),
            revoked=False,
        )
    )
