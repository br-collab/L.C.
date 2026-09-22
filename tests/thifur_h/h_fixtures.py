"""Projections and outcomes for the Phase C.1 tests. Every value is invented."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from cannae_kernel.ids import LifecycleId

from thifur_h.projection import FundingProjection, RealisedOutcome

AT = datetime(2026, 9, 21, 22, 0, tzinfo=UTC)


def lifecycle(index: int) -> LifecycleId:
    return LifecycleId(f"lif_01M2P20SY0000000000000{index:04d}")


def projection(index: int = 1, **overrides: Any) -> FundingProjection:
    fields: dict[str, Any] = {
        "lifecycle_id": lifecycle(index),
        "projected_at": AT,
        "currency": "USD",
        "obligation_amount": Decimal("1000000"),
        "committed_position": Decimal("250000"),
        "window_closes_in_seconds": 7200,
        "expected_inflow_amount": Decimal("750000"),
        "expected_inflow_in_seconds": 5400,
        "gross_final": True,
        "approved_paths": ("fedwire", "chips"),
    }
    fields.update(overrides)
    return FundingProjection(**fields)


def outcome(index: int = 1, **overrides: Any) -> RealisedOutcome:
    fields: dict[str, Any] = {
        "lifecycle_id": lifecycle(index),
        "outcome": "will_queue",
        "settled_within_window": True,
        "path_taken": "fedwire",
    }
    fields.update(overrides)
    return RealisedOutcome(**fields)


HARMFUL = frozenset({"will_fail"})
