"""Process-local synthetic Atreides status signer."""

from cannae_kernel.canonical import canonical_bytes
from cannae_kernel.signed_attestation import sign_attestation
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import BaseModel

KEY_ID = "synthetic-atreides-run-key-1"
PRIVATE_KEY = Ed25519PrivateKey.generate()


def attest_status(document: BaseModel) -> bytes:
    return sign_attestation(
        canonical_bytes(document),
        producer="atreides",
        key_id=KEY_ID,
        private_key=PRIVATE_KEY,
    )
