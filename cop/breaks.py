"""Synthetic break records for COP-1 panel 10.

No domain publishes break records yet. This source is deliberately local and
synthetic; the panel says so verbatim and panel 12 names the producer gap.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from cannae_kernel.disposition import Disposition

BREAKS_SOURCE_LABEL = "demo source: no break producer is published yet"


@dataclass(frozen=True)
class BreakRecord:
    object_id: str
    left_layer: str
    left_claim: str
    left_stamped_at: datetime
    right_layer: str
    right_claim: str
    right_stamped_at: datetime
    disposition: Disposition


class BreakSource(Protocol):
    def breaks(self) -> tuple[BreakRecord, ...]: ...
