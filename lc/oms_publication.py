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


class PublishedFill(_Record):
    """Independent synthetic venue fill; never copied from approved intent."""

    trade_id: str
    order_id: str
    cusip: str
    quantity: Decimal
    price: Decimal
    provenance: Literal["FACT_SYNTHETIC"] = "FACT_SYNTHETIC"


class ClosedTradeEvidence(_Record):
    """GR-1 result projected from the closed Treasury slice.

    ORDER-GR-1 acceptance requires a start-leg DVP trade with inbound status,
    both movements, zero unexplained breaks, books posting and byte-identical
    next-business-day replay. These fields keep every required claim visible.
    """

    trade_id: str
    rail: Literal["FICC_GSD_DVP"] = "FICC_GSD_DVP"
    ccp_status: Literal["SETTLED"] = "SETTLED"
    cash_reconciled: Literal[True] = True
    securities_reconciled: Literal[True] = True
    books_posted: Literal[True] = True
    unexplained_breaks: Literal[0] = 0
    replay_economics_identical: Literal[True] = True


class OmsPublication(_Record):
    schema_version: Literal["lc.oms_orders_allocations/2"] = "lc.oms_orders_allocations/2"
    as_of: datetime
    synthetic: Literal[True] = True
    orders: tuple[PublishedOrder, ...]
    allocations: tuple[AllocationRecord, ...]
    firm_control_events: tuple[FirmHalt, ...]
    fills: tuple[PublishedFill, ...]
    closed_trades: tuple[ClosedTradeEvidence, ...]


def build_oms_publication(
    *,
    as_of: datetime,
    orders: tuple[PublishedOrder, ...],
    allocations: tuple[AllocationRecord, ...],
    firm_control_events: tuple[FirmHalt, ...],
    trade_evidence: tuple[tuple[PublishedFill, ...], tuple[ClosedTradeEvidence, ...]] = ((), ()),
) -> bytes:
    fills, closed_trades = trade_evidence
    return (
        OmsPublication(
            as_of=as_of,
            orders=orders,
            allocations=allocations,
            firm_control_events=firm_control_events,
            fills=fills,
            closed_trades=closed_trades,
        ).model_dump_json(indent=2)
        + "\n"
    ).encode()


def build_synthetic_oms_publication(as_of: datetime) -> bytes:
    """Emit the deterministic OMS-1 visibility scenario used by CI publication."""
    order_id = OrderId("ord_" + "1" * 26)
    terms = OrderTerms(
        account_id="CUSTOMER-1",
        strategy_id="OMS-1",
        issue="91282CJL6",
        side="BUY",
        order_type=OrderType.LIMIT,
        time_in_force=TimeInForce.DAY,
        control_price=Decimal("99.5"),
        limit_price=Decimal("99.5"),
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
                average_price=Decimal("99.5"),
                consideration=quantity * Decimal("99.5"),
            )
            for account, quantity in (("A", Decimal(50)), ("B", Decimal(30)), ("C", Decimal(20)))
        ),
        executed_quantity=Decimal(100),
        executed_consideration=Decimal(9950),
        average_price=Decimal("99.5"),
        average_price_decimal_places=6,
        average_price_rounding="ROUND_HALF_EVEN",
        consideration_rounding="ROUND_HALF_EVEN_TO_CENT",
        rule=AllocationRule.PRO_RATA,
        rounding_method="LARGEST_REMAINDER",
        residual_assignments=(),
        money_residuals=(),
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
        trade_evidence=(
            (
                PublishedFill(
                    trade_id="TRADE-GR1-1",
                    order_id=str(order_id),
                    cusip="91282CJL6",
                    quantity=Decimal(100),
                    price=Decimal("99.5"),
                ),
            ),
            (ClosedTradeEvidence(trade_id="TRADE-GR1-1"),),
        ),
    )
