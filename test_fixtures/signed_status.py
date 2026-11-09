"""Synthetic Atreides status signer and pinned L.C. public-key fixture."""

from cannae_kernel.canonical import canonical_bytes
from cannae_kernel.signed_attestation import (
    PublicKeyRecord,
    pinned_public_key_registry,
    sign_attestation,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import BaseModel

KEY_ID = "synthetic-atreides-key-1"
PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
PUBLIC_KEYS = pinned_public_key_registry(
    {KEY_ID: PublicKeyRecord("atreides", PRIVATE_KEY.public_key())}
)


def attest_status(document: BaseModel) -> bytes:
    return sign_attestation(
        canonical_bytes(document),
        producer="atreides",
        key_id=KEY_ID,
        private_key=PRIVATE_KEY,
    )
