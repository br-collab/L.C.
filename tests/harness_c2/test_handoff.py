"""Stop 3: C2 grants a handoff; it never becomes the thing that prevents one.

The asymmetry is the whole design, so most of this module is about keeping it.
If everything in ``harness_c2.handoff`` stopped working, every agent would
refuse every lateral input — the behaviour the system had before C2 existed.
The failure mode is *nothing moves*, and these tests hold it that way.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import pytest
from cannae_kernel.actor import ActorKind, ActorRef
from cannae_kernel.authority import AuthorityRecord
from cannae_kernel.canonical import digest
from cannae_kernel.disposition import Disposition
from cannae_kernel.ids import ActorId
from lineage_fixtures import LIFECYCLE, OTHER_LIFECYCLE, operator, service
from pydantic import ValidationError

import harness_c2.handoff as handoff_module
from harness_c2.handoff import (
    HANDOFF_DECISION_TYPE,
    HandoffAuthorization,
    HandoffRefusedError,
    HandoffRequest,
    HandoffStage,
    admits,
    issue_handoff,
)

AT = datetime(2026, 9, 21, 14, 30, tzinfo=UTC)
LATER = AT + timedelta(minutes=30)


def authority(**overrides: object) -> AuthorityRecord:
    fields: dict[str, object] = {
        "decision_type": HANDOFF_DECISION_TYPE,
        "authorizing_actor": operator(),
        "timestamp": AT,
        "rationale": "the operator admitted the investigation agent to this break",
        "quorum_refs": (),
        "independence_asserted": False,
    }
    fields.update(overrides)
    return AuthorityRecord(**fields)  # type: ignore[arg-type]


def request(**overrides: object) -> HandoffRequest:
    fields: dict[str, object] = {
        "handoff_id": "c2h-0001",
        "lifecycle_id": LIFECYCLE,
        "from_agent": "settlement-operations-analyst",
        "to_agent": "settlement-investigation-analyst",
        "stage": HandoffStage.INVESTIGATE,
        "issued_at": AT,
        "not_after": LATER,
    }
    fields.update(overrides)
    return HandoffRequest(**fields)  # type: ignore[arg-type]


class TestIssuanceRecordsAnAuthorityItDidNotCreate:
    """C2 is a harness, not an authority. It records that an entitled actor said yes."""

    def test_a_handoff_carries_the_authority_that_granted_it(self) -> None:
        issued = issue_handoff(request(), authority())
        assert issued.authorization.authority_digest == digest(authority())
        assert issued.authorization.authorized_by == operator()

    def test_who_authorized_this_is_answerable_from_the_handoff_alone(self) -> None:
        issued = issue_handoff(request(), authority())
        who = issued.authorization.authorized_by
        assert who.actor_kind is ActorKind.HUMAN
        assert who.authenticated

    def test_a_deterministic_service_may_also_authorize(self) -> None:
        issued = issue_handoff(request(), authority(authorizing_actor=service()))
        assert issued.authorization.authorized_by.actor_kind is ActorKind.DETERMINISTIC_SERVICE

    def test_the_digest_returned_is_the_one_a_consumer_will_check(self) -> None:
        """Returned rather than left for callers to recompute, so the bytes in a
        journal and the bytes a consumer checks are the same bytes."""
        issued = issue_handoff(request(), authority())
        assert issued.authorization_digest == digest(issued.authorization)


class TestNoAgentAuthorizesAnything:
    """JUM-D-07. The kernel refuses it and this module refuses it again."""

    @pytest.mark.parametrize("kind", [ActorKind.AGENT_R, ActorKind.AGENT_J, ActorKind.AGENT_H])
    def test_an_agent_cannot_be_the_authorizing_actor(self, kind: ActorKind) -> None:
        agent = ActorRef(
            actor_id=ActorId("act_01M2P20SY00000000000000009"),
            actor_kind=kind,
            role="an agent",
            entitlement_refs=("CAOM-001:advisory",),
            authenticated=True,
        )
        with pytest.raises(ValidationError, match="may not authorize"):
            authority(authorizing_actor=agent)

    def test_an_unauthenticated_authority_is_refused_by_the_kernel(self) -> None:
        unauthenticated = operator().model_copy(update={"authenticated": False})
        with pytest.raises(ValidationError, match="must be authenticated"):
            authority(authorizing_actor=unauthenticated)


class TestACredentialForOneThingIsNotACredentialForEverything:
    def test_an_authority_record_for_another_decision_is_refused(self) -> None:
        with pytest.raises(HandoffRefusedError, match="not a C2 handoff"):
            issue_handoff(request(), authority(decision_type="INTENT_APPROVAL"))

    def test_the_refusal_carries_the_kernel_disposition(self) -> None:
        with pytest.raises(HandoffRefusedError) as raised:
            issue_handoff(request(), authority(decision_type="INTENT_APPROVAL"))
        assert raised.value.disposition is Disposition.BLOCK

    def test_an_authority_dated_after_the_handoff_is_refused(self) -> None:
        """An authorization cannot post-date the thing it authorizes."""
        with pytest.raises(HandoffRefusedError, match="cannot post-date"):
            issue_handoff(request(), authority(timestamp=AT + timedelta(hours=1)))

    def test_an_authority_dated_before_the_handoff_is_fine(self) -> None:
        """The guard against over-correcting: approving first is the normal order."""
        issued = issue_handoff(request(), authority(timestamp=AT - timedelta(hours=1)))
        assert issued.authorization.handoff_id == "c2h-0001"


class TestAHandoffIsBoundedRatherThanGeneralPurpose:
    """A credential admitting any agent to any object would satisfy the
    consumer's check and destroy the reason for it."""

    def test_it_does_not_admit_another_lifecycle(self) -> None:
        a = issue_handoff(request(), authority()).authorization
        assert not admits(
            a,
            lifecycle_id=OTHER_LIFECYCLE,
            to_agent="settlement-investigation-analyst",
            stage=HandoffStage.INVESTIGATE,
            when=AT,
        )

    def test_it_does_not_admit_another_agent(self) -> None:
        a = issue_handoff(request(), authority()).authorization
        assert not admits(
            a,
            lifecycle_id=LIFECYCLE,
            to_agent="fiat-operations-specialist",
            stage=HandoffStage.INVESTIGATE,
            when=AT,
        )

    def test_it_does_not_admit_another_stage(self) -> None:
        """A handoff issued for reconciliation must not admit an agent to
        instruction preparation."""
        a = issue_handoff(request(), authority()).authorization
        assert not admits(
            a,
            lifecycle_id=LIFECYCLE,
            to_agent="settlement-investigation-analyst",
            stage=HandoffStage.PREPARE_INSTRUCTION,
            when=AT,
        )

    def test_it_admits_exactly_what_it_names(self) -> None:
        a = issue_handoff(request(), authority()).authorization
        assert admits(
            a,
            lifecycle_id=LIFECYCLE,
            to_agent="settlement-investigation-analyst",
            stage=HandoffStage.INVESTIGATE,
            when=AT,
        )


class TestEveryHandoffExpires:
    """A handoff that never expires is a standing entitlement, and CAOM-001
    grants no agent a standing entitlement to act on a lifecycle object."""

    def test_there_is_no_way_to_ask_for_one_that_does_not_expire(self) -> None:
        with pytest.raises(TypeError):
            HandoffRequest(  # type: ignore[call-arg]
                handoff_id="c2h-0002",
                lifecycle_id=LIFECYCLE,
                from_agent="a",
                to_agent="b",
                stage=HandoffStage.VALIDATE,
                issued_at=AT,
            )

    def test_a_handoff_expiring_before_it_was_issued_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="authorizes nothing"):
            issue_handoff(request(not_after=AT - timedelta(minutes=1)), authority())

    def test_a_handoff_expiring_exactly_when_issued_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="authorizes nothing"):
            issue_handoff(request(not_after=AT), authority())

    def test_it_stops_admitting_at_expiry(self) -> None:
        a = issue_handoff(request(), authority()).authorization
        assert a.is_live_at(LATER - timedelta(seconds=1))
        assert not a.is_live_at(LATER), "expiry is exclusive; a handoff is dead at not_after"
        assert not a.is_live_at(LATER + timedelta(hours=1))

    def test_it_does_not_admit_before_it_was_issued(self) -> None:
        a = issue_handoff(request(), authority()).authorization
        assert not a.is_live_at(AT - timedelta(seconds=1))


class TestAnAgentCannotManufactureItsOwnAuthorization:
    def test_a_self_handoff_is_refused(self) -> None:
        """The consumer's check would find a recorded handoff and pass, and
        nothing outside the agent would have decided anything."""
        with pytest.raises(ValidationError, match="cannot hand off to itself"):
            issue_handoff(request(to_agent="settlement-operations-analyst"), authority())


class TestTheAsymmetryIsNotInverted:
    """C2 adds the ability to grant. It never becomes the thing that prevents."""

    def test_issuance_exposes_no_way_to_refuse_a_consumer_its_own_check(self) -> None:
        surface = set(handoff_module.__all__)
        forbidden = {"bypass", "override", "force", "trust", "skip_check", "admit_any"}
        assert surface.isdisjoint(forbidden), (
            f"the issuance module has grown a way to override the consumer: {surface & forbidden}"
        )

    def test_admits_is_a_convenience_and_says_so(self) -> None:
        """It must not be mistaken for the refusal, which lives at the agent."""
        doc = inspect.getdoc(handoff_module.admits) or ""
        assert "not a replacement" in doc
        assert "atreides.activation.handoff" in doc

    def test_the_module_cannot_act(self) -> None:
        """Stop 1 again, for this module specifically."""
        source = inspect.getsource(handoff_module)
        for forbidden in ("import httpx", "import requests", "sqlite3", "open(", "urllib"):
            assert forbidden not in source, f"handoff issuance reaches for {forbidden!r}"


class TestTheAuthorizationRecordIsClosedAndStrict:
    def test_an_unknown_field_is_refused(self) -> None:
        a = issue_handoff(request(), authority()).authorization
        payload = a.model_dump()
        payload["admit_everything"] = True
        with pytest.raises(ValidationError):
            HandoffAuthorization.model_validate(payload)

    def test_it_round_trips_through_json(self) -> None:
        a = issue_handoff(request(), authority()).authorization
        assert HandoffAuthorization.model_validate_json(a.model_dump_json()) == a
