"""The L.C. (Legiones Cannenses) lifecycle export: executions read from a sealed register.

Downstream surveillance in aureon reads this export instead of fills a caller assembles.
Every row is an executed order event in the lifecycle register, joined to exactly one venue
execution report whose digest the kernel `ExecutionEvent` carries. Provenance is the venue
event's own (`FACT_EXTERNAL` or `FACT_SYNTHETIC`), per row, never a label the caller picks.

The export holds only what L.C. records: order, account, issue, side, quantity, price, venue
and event time. Close price, session volume and beneficial owner are not L.C. facts; the
reader takes them from their own named sources.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from cannae_kernel.absence import require_recorded
from cannae_kernel.canonical import digest
from cannae_kernel.envelopes import ExecutionEvent
from cannae_kernel.ids import OrderId
from cannae_kernel.provenance import Provenance

from lc.events import LifecycleState, OrderTerms
from lc.lifecycle import LifecycleRegister, OrderSnapshot, replay
from lc.policy import ExecutionReport, execution_recorded_reason

__all__ = ["LIFECYCLE_EXPORT_SCHEMA", "export_executions", "write_lifecycle_export"]

LIFECYCLE_EXPORT_SCHEMA = "lc.lifecycle_export/1"
_EXECUTED = frozenset({LifecycleState.PARTIALLY_EXECUTED, LifecycleState.EXECUTED})
_OBSERVED = frozenset({Provenance.FACT_EXTERNAL, Provenance.FACT_SYNTHETIC})


def _terms(snapshot_orders: Mapping[OrderId, OrderSnapshot], order_id: OrderId) -> OrderTerms:
    """The order's sealed terms, or its parent's when the child carries none."""

    order: OrderSnapshot | None = snapshot_orders.get(order_id)
    while order is not None:
        if order.terms is not None:
            return order.terms
        parent = order.parent_order_id
        order = snapshot_orders.get(parent) if parent is not None else None
    raise ValueError(f"LIFECYCLE_EXPORT_TERMS_MISSING: {order_id}")


def export_executions(
    register: LifecycleRegister,
    executions: Iterable[tuple[ExecutionEvent, ExecutionReport]],
) -> dict[str, Any]:
    """Export every executed order event, each joined to exactly one venue execution.

    Refused, by name: a venue report whose digest is not its event's payload digest; a
    venue event for another lifecycle; a provenance that is not an observation; an executed
    order event with no venue report; a venue report no executed event recorded.
    """

    snapshot = replay(register.events)  # verifies the sealed chain
    by_reason: dict[str, tuple[ExecutionEvent, ExecutionReport]] = {}
    for venue_event, report in executions:
        if venue_event.payload_digest != digest(report):
            raise ValueError(f"LIFECYCLE_EXPORT_DIGEST_MISMATCH: {report.execution_id}")
        if venue_event.lifecycle_id != register.lifecycle_id:
            raise ValueError(f"LIFECYCLE_EXPORT_FOREIGN_EXECUTION: {report.execution_id}")
        if venue_event.provenance not in _OBSERVED:
            raise ValueError(f"LIFECYCLE_EXPORT_PROVENANCE: {report.execution_id}")
        reason = execution_recorded_reason(report.execution_id)
        if reason in by_reason:
            raise ValueError(f"LIFECYCLE_EXPORT_DUPLICATE_EXECUTION: {report.execution_id}")
        by_reason[reason] = (venue_event, report)

    rows: list[dict[str, str]] = []
    used: set[str] = set()
    for event in register.events:
        payload = event.payload
        if payload.state not in _EXECUTED:
            continue
        joined = by_reason.get(payload.reason)
        if joined is None:
            raise ValueError(f"LIFECYCLE_EXPORT_UNREPORTED_FILL: {payload.order_id}")
        venue_event, report = joined
        quantity = require_recorded(payload.quantity)
        if report.order_id != payload.order_id or report.quantity != quantity:
            raise ValueError(f"LIFECYCLE_EXPORT_FILL_MISMATCH: {report.execution_id}")
        used.add(payload.reason)
        terms = _terms(snapshot.orders, payload.order_id)
        rows.append(
            {
                "order_id": str(payload.order_id),
                "parent_order_id": str(payload.parent_order_id or ""),
                "account_id": terms.account_id,
                "symbol": terms.issue,
                "side": terms.side,
                "quantity": str(quantity),
                "price": str(report.price),
                "venue": report.venue,
                "filled_at": event.times.event_time.isoformat(),
                "execution_id": report.execution_id,
                "event_digest": str(event.envelope_digest),
                "provenance": venue_event.provenance.value,
            }
        )
    unrecorded = sorted(set(by_reason) - used)
    if unrecorded:
        raise ValueError(f"LIFECYCLE_EXPORT_UNRECORDED_EXECUTION: {unrecorded}")
    return {
        "schema": LIFECYCLE_EXPORT_SCHEMA,
        "lifecycle_id": str(register.lifecycle_id),
        "last_event_digest": str(snapshot.last_event_digest),
        "executions": rows,
    }


def write_lifecycle_export(
    register: LifecycleRegister,
    executions: Iterable[tuple[ExecutionEvent, ExecutionReport]],
    path: Path,
) -> Path:
    """Write the export as canonical JSON and return its path."""

    document = export_executions(register, executions)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
