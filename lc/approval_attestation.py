"""Verification of signed approval evidence at the L.C. acceptance boundary."""

from datetime import datetime

from cannae_kernel.actor import ActorRef
from cannae_kernel.signed_attestation import AttestationVerificationError, verify_attestation
from pydantic import BaseModel, ConfigDict, Field

from lc.trust_anchors import TrustAnchorError, require_anchor_valid_at, resolve_pinned_anchor

APPROVAL_PRODUCER = "atreides-human-approval"


class ApprovalAttestation(BaseModel):
    """Consumer contract for an approval signed by a trusted producer."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    approval_id: str = Field(min_length=1)
    decision_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    approver: ActorRef
    approved_at: datetime


def verify_approval_attestation(
    wire: bytes,
    *,
    decision_digest: str,
    approver: ActorRef,
    evaluated_at: datetime,
) -> ApprovalAttestation:
    """Verify pinned trust, signature, validity time, digest, and approving actor."""

    try:
        anchor, registry = resolve_pinned_anchor(wire, expected_producer=APPROVAL_PRODUCER)
        payload = verify_attestation(wire, expected_producer=APPROVAL_PRODUCER, registry=registry)
        approval = ApprovalAttestation.model_validate_json(payload)
        require_anchor_valid_at(anchor, evaluated_at)
    except (TrustAnchorError, AttestationVerificationError, ValueError) as exc:
        raise ValueError("APPROVAL_ATTESTATION_INVALID") from exc
    if approval.decision_digest != decision_digest or approval.approver != approver:
        raise ValueError("APPROVAL_ATTESTATION_BINDING_INVALID")
    return approval
