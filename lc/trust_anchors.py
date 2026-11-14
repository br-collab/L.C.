"""Build-fixed synthetic trust anchors for producer attestations."""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from threading import Lock
from types import MappingProxyType
from typing import Any

from cannae_kernel.signed_attestation import (
    PublicKeyRecord,
    PublicKeyRegistry,
    SignedAttestation,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import ValidationError

TRUST_ANCHOR_PATH = Path(__file__).with_name("trusted_producer_keys.json")
_runtime_lock = Lock()


class TrustAnchorRefusal(StrEnum):
    UNKNOWN_KEY = "TRUST_ANCHOR_SIGNING_KEY_UNKNOWN"
    WRONG_PRODUCER = "TRUST_ANCHOR_PRODUCER_MISMATCH"
    REVOKED = "TRUST_ANCHOR_SIGNING_KEY_REVOKED"
    OUTSIDE_VALIDITY = "TRUST_ANCHOR_SIGNING_KEY_OUTSIDE_VALIDITY"
    MALFORMED = "TRUST_ANCHOR_MALFORMED"


class TrustAnchorError(ValueError):
    def __init__(self, refusal: TrustAnchorRefusal) -> None:
        self.refusal = refusal
        super().__init__(refusal.value)


@dataclass(frozen=True)
class TrustAnchor:
    key_id: str
    producer: str
    public_key: Ed25519PublicKey
    valid_from: datetime
    valid_to: datetime
    revoked: bool


@dataclass
class _RuntimeTrustState:
    anchor: TrustAnchor | None = None
    installed: bool = False
    verification_started: bool = False


_runtime_state = _RuntimeTrustState()


@lru_cache(maxsize=1)
def load_trust_anchors() -> MappingProxyType[str, TrustAnchor]:
    document: dict[str, Any] = json.loads(TRUST_ANCHOR_PATH.read_text())
    if document.get("schema_version") != "lc.trusted_producer_keys/1":
        raise ValueError("unsupported trust anchor schema")
    anchors: dict[str, TrustAnchor] = {}
    for row in document["keys"]:
        key_id = str(row["key_id"])
        if key_id in anchors:
            raise ValueError("duplicate trust anchor key identifier")
        anchor = TrustAnchor(
            key_id=key_id,
            producer=str(row["producer"]),
            public_key=Ed25519PublicKey.from_public_bytes(
                base64.b64decode(str(row["public_key_b64"]), validate=True)
            ),
            valid_from=datetime.fromisoformat(str(row["valid_from"])),
            valid_to=datetime.fromisoformat(str(row["valid_to"])),
            revoked=bool(row["revoked"]),
        )
        if anchor.valid_from >= anchor.valid_to:
            raise ValueError("trust anchor validity window is empty")
        anchors[key_id] = anchor
    return MappingProxyType(anchors)


def install_runtime_trust_anchor(anchor: TrustAnchor) -> None:
    """Install one process-local producer key before the first verification."""

    with _runtime_lock:
        if _runtime_state.installed:
            raise RuntimeError("runtime trust anchor is set once")
        if _runtime_state.verification_started:
            raise RuntimeError("runtime trust anchor cannot change after verification")
        _runtime_state.anchor = anchor
        _runtime_state.installed = True


def resolve_pinned_anchor(
    wire: bytes,
    *,
    expected_producer: str,
) -> tuple[TrustAnchor, PublicKeyRegistry]:
    with _runtime_lock:
        _runtime_state.verification_started = True
    try:
        envelope = SignedAttestation.model_validate_json(wire)
    except (ValidationError, ValueError, binascii.Error) as exc:
        raise TrustAnchorError(TrustAnchorRefusal.MALFORMED) from exc
    deployment_anchor = load_trust_anchors().get(envelope.key_id)
    anchor = (
        _runtime_state.anchor
        if _runtime_state.anchor is not None and _runtime_state.anchor.key_id == envelope.key_id
        else deployment_anchor
    )
    if anchor is None:
        raise TrustAnchorError(TrustAnchorRefusal.UNKNOWN_KEY)
    if envelope.producer != expected_producer or anchor.producer != expected_producer:
        raise TrustAnchorError(TrustAnchorRefusal.WRONG_PRODUCER)
    if anchor.revoked:
        raise TrustAnchorError(TrustAnchorRefusal.REVOKED)
    registry = MappingProxyType(
        {anchor.key_id: PublicKeyRecord(anchor.producer, anchor.public_key)}
    )
    return anchor, registry


def require_anchor_valid_at(anchor: TrustAnchor, at: datetime) -> None:
    if at < anchor.valid_from or at > anchor.valid_to:
        raise TrustAnchorError(TrustAnchorRefusal.OUTSIDE_VALIDITY)
