"""Process-local synthetic approval signer for L.C. tests."""

from datetime import UTC, datetime

from cannae_kernel.canonical import canonical_bytes
from cannae_kernel.envelopes import ApprovedIntentEnvelope
from cannae_kernel.signed_attestation import sign_attestation
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from lc.approval_attestation import APPROVAL_PRODUCER, ApprovalAttestation

KEY_ID = "synthetic-approval-run-key-1"
PRIVATE_KEY = Ed25519PrivateKey.generate()


def attest_approval(envelope: ApprovedIntentEnvelope) -> bytes:
    approval = ApprovalAttestation(
        approval_id="synthetic-test-approval",
        decision_digest=envelope.payload_digest,
        approver=envelope.approved_by,
        approved_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    return sign_attestation(
        canonical_bytes(approval),
        producer=APPROVAL_PRODUCER,
        key_id=KEY_ID,
        private_key=PRIVATE_KEY,
    )
