"""Handoff issuance: C2 grants what the consumer already checks for (Stop 3).

C2 = Command and Control. CAOM = Consolidated Authority Operating Mode.

The asymmetry this module must not break
----------------------------------------
Stop 3 — *no agent acts on a lifecycle object without a recorded C2 handoff
authorization* — **is already satisfied at the consumer**, by WP-A2 in
``atreides.activation.handoff``: each agent's own type check refuses input that
did not arrive with a recorded handoff, and refuses it whether or not anything
upstream is running.

This module adds the ability to *grant* a handoff. It does not become the thing
that prevents one. That asymmetry is the whole design, and it is what makes
adding C2 safe rather than load-bearing on arrival: if everything here stopped
working tomorrow, every agent would refuse every lateral input, which is the
behaviour the system had before C2 existed. The failure mode of this module is
*nothing moves*, and that is the correct direction to fail in.

Nothing here weakens the consumer's check, and nothing here is permitted to run
inside an agent. An agent calling this to authorize its own input is the exact
fragile pattern Wave 2 condemned — a filter one layer up, relocated into the
thing it was supposed to constrain.

C2 does not authorize. It records that an authority did.
---------------------------------------------------------
The kernel is unambiguous: only an authenticated ``HUMAN`` or
``DETERMINISTIC_SERVICE`` may authorize, and no agent class may
(:data:`~cannae_kernel.authority.AUTHORIZING_KINDS`, JUM-D-07). C2 is a harness,
not an authority. So a handoff is issued **against an**
:class:`~cannae_kernel.authority.AuthorityRecord` that some entitled actor
already made — C2 assembles, binds and records it, and the kernel's own
validator refuses the record if the actor was not entitled or not authenticated.

That is why there is no ``issue_handoff(actor=...)`` taking a bare actor. The
authority has to exist as a record before a handoff can reference it, which
means the question "who authorized this?" is answerable from the handoff alone.

Bound to one lifecycle, one direction, one purpose
--------------------------------------------------
A handoff names the lifecycle, the agent handing over, the agent receiving, and
the stage of work. A general-purpose credential that admitted any agent to any
object would satisfy the consumer's check and destroy the reason for it — the
consumer would be verifying that *somebody, once, authorized something*.

Expiry, and why it is required rather than optional
---------------------------------------------------
Every handoff carries ``not_after``. An authorization that never expires is
indistinguishable from a standing entitlement, and a standing entitlement to act
on a lifecycle object is the thing CAOM-001 does not grant to any agent. There is
no "no expiry" value to pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Self

from cannae_kernel._model import KernelModel, NonEmptyStr, UtcDatetime
from cannae_kernel.actor import ActorKind, ActorRef
from cannae_kernel.authority import AuthorityRecord
from cannae_kernel.canonical import digest
from cannae_kernel.disposition import Disposition
from cannae_kernel.ids import LifecycleId
from pydantic import model_validator

__all__ = [
    "HANDOFF_DECISION_TYPE",
    "HandoffAuthorization",
    "HandoffRefusedError",
    "HandoffRequest",
    "HandoffStage",
    "IssuedHandoff",
    "admits",
    "issue_handoff",
]


class HandoffStage(StrEnum):
    """What the receiving agent is being admitted to do.

    Named rather than free text so that two handoffs for the same lifecycle are
    comparable, and so a handoff issued for reconciliation cannot silently admit
    an agent to instruction preparation.
    """

    VALIDATE = "VALIDATE"
    """Run gates over a lifecycle object and report what they said."""

    INVESTIGATE = "INVESTIGATE"
    """Assemble evidence about an object. Reads only."""

    PREPARE_INSTRUCTION = "PREPARE_INSTRUCTION"
    """Prepare an artefact for an entitled member to submit under their own
    credentials. Never submission: Atreides never submits to a rail and never
    holds submission credentials."""

    RECONCILE = "RECONCILE"
    """Compare what was prepared against what a venue reported."""


class HandoffAuthorization(KernelModel):
    """A recorded C2 handoff authorization, as the consumer's check will see it.

    Frozen, closed and strict, like every kernel-shaped record. The identifier
    a consumer stores as its handoff basis is :attr:`handoff_id`; everything
    else here is what makes that identifier answerable.
    """

    handoff_id: NonEmptyStr
    lifecycle_id: LifecycleId
    from_agent: NonEmptyStr
    to_agent: NonEmptyStr
    stage: HandoffStage
    issued_at: UtcDatetime
    not_after: UtcDatetime
    """When this authorization stops being one. Required: see the module docstring."""
    authority_digest: NonEmptyStr
    """``digest`` of the :class:`AuthorityRecord` this handoff was issued against.

    The digest, not a copy of the record: one owner per field is what stops two
    parts of the system disagreeing about who authorized what.
    """
    authorized_by: ActorRef
    """The entitled actor named on that authority record, carried so that "who
    authorized this?" is answerable from the handoff alone."""

    @model_validator(mode="after")
    def _expiry_follows_issue(self) -> Self:
        if self.not_after <= self.issued_at:
            raise ValueError(
                "a handoff that expires at or before it was issued authorizes nothing; "
                "it would be refused by the consumer at the moment it was written"
            )
        return self

    @model_validator(mode="after")
    def _a_handoff_is_between_two_agents(self) -> Self:
        """An agent cannot hand work to itself.

        A self-handoff is how an agent would manufacture its own authorization:
        the consumer's check would find a recorded handoff and pass, and nothing
        outside the agent would have decided anything.
        """
        if self.from_agent == self.to_agent:
            raise ValueError(
                f"{self.from_agent!r} cannot hand off to itself: a self-handoff is an "
                "agent authorizing its own input"
            )
        return self

    def is_live_at(self, when: datetime) -> bool:
        """True while this authorization is in force. Boundaries are exclusive at expiry."""
        return self.issued_at <= when < self.not_after


class HandoffRefusedError(PermissionError):
    """Raised when a handoff cannot be issued. Carries the reason as data.

    A ``PermissionError``: refusing to issue is control flow, and the record of
    the refusal is the message plus the :attr:`disposition`, which is the kernel
    vocabulary rather than a locally invented one.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.disposition = Disposition.BLOCK


@dataclass(frozen=True)
class IssuedHandoff:
    """What issuance produced: the authorization, and the digest binding it.

    The digest is returned rather than recomputed by callers, so that the value
    written into a journal and the value the consumer checks are the same bytes.
    """

    authorization: HandoffAuthorization
    authorization_digest: str


#: The decision type an authority record must carry to authorize a handoff.
#:
#: Checked rather than assumed: an authority record made to approve an intent is
#: not a record authorizing an agent handoff, and accepting any record as any
#: authorization is how a credential for one thing becomes a credential for
#: everything.
HANDOFF_DECISION_TYPE = "C2_HANDOFF"


@dataclass(frozen=True)
class HandoffRequest:
    """What is being asked for, kept apart from the authority granting it.

    Two values rather than eight arguments, and the split is the point: a
    request is a description of work, and an :class:`AuthorityRecord` is
    somebody entitled saying yes to it. Collapsing them into one call signature
    invites a caller to build both in the same breath, which is how a request
    comes to carry its own approval.
    """

    handoff_id: str
    lifecycle_id: LifecycleId
    from_agent: str
    to_agent: str
    stage: HandoffStage
    issued_at: datetime
    not_after: datetime


def issue_handoff(request: HandoffRequest, authority: AuthorityRecord) -> IssuedHandoff:
    """Record a handoff that an entitled authority granted, or refuse to.

    C2 does not authorize. ``authority`` is a record some entitled actor already
    made, and the kernel has already refused it at construction if that actor
    was not a ``HUMAN`` or a ``DETERMINISTIC_SERVICE``, or was not
    authenticated. What this function adds is the checks the kernel cannot make,
    because they need to know what the record is *for*:

    - the record must be a handoff authorization, not some other decision that
      happens to exist;
    - it must not be older than the handoff it authorizes;
    - no agent class may appear as the authorizing actor, which the kernel
      already refuses and which is asserted again here because it is the one
      failure that would make every other check pointless.

    Raises :class:`HandoffRefusedError` rather than returning a refusal: there is
    no useful partial result, and a caller that ignored a returned refusal would
    hold something that looks like an authorization.
    """
    if authority.decision_type != HANDOFF_DECISION_TYPE:
        raise HandoffRefusedError(
            f"the authority record authorizes {authority.decision_type!r}, not a C2 "
            f"handoff. A record made for one decision is not a credential for another"
        )
    if authority.authorizing_actor.actor_kind not in {
        ActorKind.HUMAN,
        ActorKind.DETERMINISTIC_SERVICE,
    }:
        # The kernel refuses this at AuthorityRecord construction. Asserted again
        # because it is the failure that would make every other check here
        # pointless, and a second check costs nothing.
        raise HandoffRefusedError(
            f"{authority.authorizing_actor.actor_kind.value} may not authorize a handoff; "
            f"no agent class authorizes anything (JUM-D-07)"
        )
    if authority.timestamp > request.issued_at:
        raise HandoffRefusedError(
            f"the authority record is dated {authority.timestamp.isoformat()}, after the "
            f"handoff it would authorize ({request.issued_at.isoformat()}). An "
            f"authorization cannot post-date the thing it authorizes"
        )
    authorization = HandoffAuthorization(
        handoff_id=request.handoff_id,
        lifecycle_id=request.lifecycle_id,
        from_agent=request.from_agent,
        to_agent=request.to_agent,
        stage=request.stage,
        issued_at=request.issued_at,
        not_after=request.not_after,
        authority_digest=digest(authority),
        authorized_by=authority.authorizing_actor,
    )
    return IssuedHandoff(
        authorization=authorization,
        authorization_digest=digest(authorization),
    )


def admits(
    authorization: HandoffAuthorization,
    *,
    lifecycle_id: LifecycleId,
    to_agent: str,
    stage: HandoffStage,
    when: datetime,
) -> bool:
    """Whether this authorization admits *this* agent to *this* work, now.

    Provided so that a consumer checking a handoff asks one question rather than
    four, and so the four are written once. It is a **convenience for the
    consumer, not a replacement for it**: the refusal that matters still lives in
    ``atreides.activation.handoff.admit``, at the receiving agent, and nothing
    here is permitted to become the only thing standing between a lateral input
    and an agent acting on it.
    """
    return (
        authorization.lifecycle_id == lifecycle_id
        and authorization.to_agent == to_agent
        and authorization.stage is stage
        and authorization.is_live_at(when)
    )
