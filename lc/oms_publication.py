"""Human-visible OMS-1 order, control and allocation publication."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from cannae_kernel.actor import ActorKind, ActorRef
from cannae_kernel.disposition import Disposition
from cannae_kernel.ids import ActorId, AllocationId, OrderId
from pydantic import BaseModel, ConfigDict

from lc.events import LifecycleState, OrderTerms, OrderType, TimeInForce
from lc.market_access import FirmHalt, HaltScope, MarketAccessDecision
from lc.trade import AllocationEntry, AllocationRecord, AllocationRule


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class PublishedOrder(_Record):
    order_id: str
    terms: OrderTerms
    states: tuple[LifecycleState, ...]
    gate_decisions: tuple[MarketAccessDecision, ...]


class OmsPublication(_Record):
    schema_version: Literal["lc.oms_orders_allocations/1"] = "lc.oms_orders_allocations/1"
    as_of: datetime
    synthetic: Literal[True] = True
    orders: tuple[PublishedOrder, ...]
    allocations: tuple[AllocationRecord, ...]
    firm_control_events: tuple[FirmHalt, ...]


def build_oms_publication(
    *,
    as_of: datetime,
    orders: tuple[PublishedOrder, ...],
    allocations: tuple[AllocationRecord, ...],
    firm_control_events: tuple[FirmHalt, ...],
) -> bytes:
    return (
        OmsPublication(
            as_of=as_of,
            orders=orders,
            allocations=allocations,
            firm_control_events=firm_control_events,
        ).model_dump_json(indent=2)
        + "\n"
    ).encode()


def build_synthetic_oms_publication(as_of: datetime) -> bytes:
    """Emit the deterministic OMS-1 visibility scenario used by CI publication."""
    order_id = OrderId("ord_" + "1" * 26)
    terms = OrderTerms(
        account_id="CUSTOMER-1",
        strategy_id="OMS-1",
        issue="ACME",
        side="BUY",
        order_type=OrderType.LIMIT,
        time_in_force=TimeInForce.DAY,
        control_price=Decimal(100),
        limit_price=Decimal(100),
    )
    decision = MarketAccessDecision(
        order_id=order_id,
        order_event_digest="sha256:" + "1" * 64,
        disposition=Disposition.PASS,
        terminal=False,
        checks=(),
        limits_version="OMS-1",
        limits_digest="sha256:" + "2" * 64,
        capacity_digest="sha256:" + "3" * 64,
        capacity_journal_digest="sha256:" + "4" * 64,
        capacity_margin_result_digest="sha256:" + "5" * 64,
        locates_digest="sha256:" + "6" * 64,
    )
    allocation = AllocationRecord(
        allocation_id=AllocationId("alc_" + "1" * 26),
        trade_ids=("T1", "T2"),
        entries=tuple(
            AllocationEntry(
                account=account,
                quantity=quantity,
                average_price=Decimal(101),
                consideration=quantity * Decimal(101),
            )
            for account, quantity in (("A", Decimal(50)), ("B", Decimal(30)), ("C", Decimal(20)))
        ),
        executed_quantity=Decimal(100),
        executed_consideration=Decimal(10100),
        average_price=Decimal(101),
        rule=AllocationRule.PRO_RATA,
        rounding_method="LARGEST_REMAINDER",
        residual_assignments=(),
        allocation_plan_digest="sha256:" + "7" * 64,
    )
    actor = ActorRef(
        actor_id=ActorId("act_" + "1" * 26),
        actor_kind=ActorKind.HUMAN,
        role="firm-control-authority",
        entitlement_refs=("OMS_KILL_SWITCH",),
        authenticated=True,
    )
    halt = FirmHalt(
        halt_id="HALT-OMS-1",
        scope=HaltScope.FIRM,
        scope_id=None,
        authority=actor,
        recorded_at=as_of,
    )
    return build_oms_publication(
        as_of=as_of,
        orders=(
            PublishedOrder(
                order_id=str(order_id),
                terms=terms,
                states=(
                    LifecycleState.PARENT_ACTIVE,
                    LifecycleState.MARKET_ACCESS_PASSED,
                    LifecycleState.PARTIALLY_EXECUTED,
                    LifecycleState.EXECUTED,
                    LifecycleState.ALLOCATED,
                ),
                gate_decisions=(decision,),
            ),
        ),
        allocations=(allocation,),
        firm_control_events=(halt, halt.model_copy(update={"released_at": as_of})),
    )
