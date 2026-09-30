"""The lifecycle board: one row per object, one cell per contract checkpoint (COP-1 WP-1).

COP = Common Operating Picture. C2 = Command and Control.

**The five frozen contracts are the spine, so the columns are already decided.**
`ApprovedIntentEnvelope` → `ExecutionEvent` → `ClearingTransformation` →
`SettlementObligationEnvelope` → `ObligationAcceptanceRecord`, then settled. This
module renders what those types carry and invents nothing.

The rule this panel exists to demonstrate
------------------------------------------
Absence is a recorded state rather than a gap in the rendering. A lifecycle
stopped at a hold carries the reason later checkpoints were not reached. A
surface that rendered those as blank would read as "nothing wrong here", and a
surface that rendered them as `PASS` would claim stages the transcript never
recorded.

Each layer's own clock
----------------------
Every cell carries **which layer's clock stamped it**, not merely a time. COP-0
shows `observed_at` — when *this page* saw a value — which is a different fact and
the one a break investigation does not need. Three clocks disagree routinely: a
venue's event time, the middle layer's processing time and a settlement rail's
business date are not the same instant and are not meant to be.

So a cell whose time came from Aureon says so, and the page **never reconciles
clocks silently**. Where this page's own observation is all there is, the cell says
that too, because "the time the picture looked" and "the time the layer stamped"
are different claims and only one of them is evidence about the lifecycle.

No cell is greener than its weakest input
------------------------------------------
A row's state is the **worst** of its cells, and a cell whose source is stale or
missing is `INDETERMINATE` — never carried forward from a previous refresh and
never softened to `HOLD`. That is COP-0's overall-state rule applied one level
down, and it is why a row with a single unknown cell cannot render `PASS`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol

import httpx
from cannae_kernel.absence import Absent, Recorded
from cannae_kernel.disposition import Disposition
from cannae_kernel.ids import LifecycleId, ScenarioId
from cannae_kernel.provenance import Provenance
from pydantic import BaseModel, ConfigDict, ValidationError

from cop.http import get_bytes
from cop.observation import SourceFieldAbsentError, SourceMalformedError
from cop.settings import HTTP_TIMEOUT_SECONDS, PRODUCT_NAME

__all__ = [
    "CHECKPOINT_LAYER",
    "CHECKPOINT_ORDER",
    "NOT_BUILT_REASON",
    "NOT_REACHED_REASON",
    "Checkpoint",
    "HttpxLifecycleClient",
    "Layer",
    "LayerReading",
    "LifecycleCell",
    "LifecycleRow",
    "build_row",
    "cell_for",
    "parse_document",
    "row_disposition",
]

#: What a cell says where the layer that would produce it does not exist yet.
#: One string, used everywhere, so the board cannot say it three slightly
#: different ways and invite a reader to think they mean three different things.
NOT_BUILT_REASON = "absent — layer not built"

#: What a cell says where the layer exists and has simply not reached this
#: checkpoint. Kept apart from :data:`NOT_BUILT_REASON` because they are
#: different facts with different responses: one waits for a missing layer, the other is
#: an ordinary lifecycle in flight. Collapsing them would have the board report
#: a built layer as unbuilt, which is the kind of false statement this panel
#: exists to prevent.
NOT_REACHED_REASON = "not reached — no record at this checkpoint"


class Layer(StrEnum):
    """Which domain stamps a checkpoint. Each keeps its own clock."""

    AUREON = "AUREON"
    """Pre-trade: governed, approved intent."""

    LC = "LC"
    """Legiones Cannenses: execution, clearing and obligation formation."""

    ATREIDES = "ATREIDES"
    """Post-trade: acceptance, settlement, finality."""


class Checkpoint(StrEnum):
    """One column. Named for the contract that crosses at that point."""

    APPROVED_INTENT = "APPROVED_INTENT"
    EXECUTION = "EXECUTION"
    CLEARING = "CLEARING"
    SETTLEMENT_OBLIGATION = "SETTLEMENT_OBLIGATION"
    OBLIGATION_ACCEPTANCE = "OBLIGATION_ACCEPTANCE"
    SETTLED = "SETTLED"


#: The columns, left to right, in the order a lifecycle passes through them.
CHECKPOINT_ORDER: tuple[Checkpoint, ...] = (
    Checkpoint.APPROVED_INTENT,
    Checkpoint.EXECUTION,
    Checkpoint.CLEARING,
    Checkpoint.SETTLEMENT_OBLIGATION,
    Checkpoint.OBLIGATION_ACCEPTANCE,
    Checkpoint.SETTLED,
)

#: Which layer owns each checkpoint — and therefore whose clock stamps it and
#: which unbuilt layer makes it absent.
#:
#: Three of the six belong to L.C., not the two the order names. The settlement
#: obligation is *formed* by the middle layer (JUM-D-02) and handed over, so it
#: shares ownership with execution and clearing. Deriving the board from ownership
#: rather than from a list keeps all three columns governed by one layer state.
CHECKPOINT_LAYER: dict[Checkpoint, Layer] = {
    Checkpoint.APPROVED_INTENT: Layer.AUREON,
    Checkpoint.EXECUTION: Layer.LC,
    Checkpoint.CLEARING: Layer.LC,
    Checkpoint.SETTLEMENT_OBLIGATION: Layer.LC,
    Checkpoint.OBLIGATION_ACCEPTANCE: Layer.ATREIDES,
    Checkpoint.SETTLED: Layer.ATREIDES,
}


@dataclass(frozen=True)
class LifecycleCell:
    """One checkpoint of one lifecycle object.

    ``stamped_by`` and ``stamped_at`` travel together and are the layer's own
    clock — not this page's. Where no layer has stamped anything, both are absent
    and the cell says why rather than borrowing this page's observation time.
    """

    checkpoint: Checkpoint
    layer: Layer
    disposition: Disposition
    detail: str
    """What this cell shows, or the reason it shows nothing. Never empty."""
    stamped_at: datetime | None
    """The **layer's** clock. ``None`` when that layer has stamped nothing."""
    provenance: str
    """What kind of claim the value is, in the kernel's vocabulary."""

    @property
    def is_absent(self) -> bool:
        return self.disposition is Disposition.INDETERMINATE and self.stamped_at is None


@dataclass(frozen=True)
class LifecycleRow:
    """One lifecycle object, across every layer."""

    lifecycle_id: str
    cells: tuple[LifecycleCell, ...]
    taken_at: datetime | None = None

    @property
    def disposition(self) -> Disposition:
        return row_disposition(self.cells)

    @property
    def unknown_cells(self) -> tuple[LifecycleCell, ...]:
        return tuple(c for c in self.cells if c.disposition is Disposition.INDETERMINATE)


#: Worst-first. INDETERMINATE outranks HOLD deliberately: "we do not know" is a
#: worse thing to report to an operator than "we know, and it is waiting".
_SEVERITY: dict[Disposition, int] = {
    Disposition.PASS: 0,
    Disposition.HOLD: 1,
    Disposition.INDETERMINATE: 2,
    Disposition.BLOCK: 3,
}


def row_disposition(cells: tuple[LifecycleCell, ...]) -> Disposition:
    """The worst of the cells. **A row is never greener than its weakest cell.**

    An empty row is ``INDETERMINATE`` rather than ``PASS``: a lifecycle with no
    checkpoints at all is not a clean one, it is one nothing is known about.
    """
    if not cells:
        return Disposition.INDETERMINATE
    return max((c.disposition for c in cells), key=lambda d: _SEVERITY[d])


@dataclass(frozen=True)
class LayerReading:
    """What one layer reported about one lifecycle object, if anything.

    ``built`` is the distinction the whole board turns on. A layer that does not
    exist yet and a layer that exists and is unreachable are different facts, and
    a reader who cannot tell them apart will chase the wrong one.
    """

    layer: Layer
    built: bool
    current: bool
    """Whether the reading is current. A stale reading is never carried forward
    as though it were fresh — COP-0's rule, applied per cell."""
    detail: str
    disposition: Disposition
    stamped_at: datetime | None
    provenance: str
    stale_reason: str | None = None


def cell_for(checkpoint: Checkpoint, reading: LayerReading) -> LifecycleCell:
    """One cell, from one layer's reading. Three cases, kept apart on purpose.

    **Not built** — the layer does not exist yet. ``INDETERMINATE``, with the
    reason, and no time at all: borrowing this page's observation time here
    would put a timestamp beside a thing that never happened.

    **Not current** — the layer exists and the reading is stale or failed.
    ``INDETERMINATE``, and the detail says which, because the response is
    different: one waits for a missing layer, the other asks somebody to look at a source.

    **Current** — whatever the layer said, carried through with its own clock.
    """
    layer = CHECKPOINT_LAYER[checkpoint]
    if not reading.built:
        return LifecycleCell(
            checkpoint=checkpoint,
            layer=layer,
            disposition=Disposition.INDETERMINATE,
            detail=NOT_BUILT_REASON,
            stamped_at=None,
            provenance="—",
        )
    if not reading.current:
        return LifecycleCell(
            checkpoint=checkpoint,
            layer=layer,
            disposition=Disposition.INDETERMINATE,
            detail=(f"not current: {reading.stale_reason or 'the source did not answer'}"),
            stamped_at=None,
            provenance=reading.provenance,
        )
    return LifecycleCell(
        checkpoint=checkpoint,
        layer=layer,
        disposition=reading.disposition,
        detail=reading.detail,
        stamped_at=reading.stamped_at,
        provenance=reading.provenance,
    )


def _missing(checkpoint: Checkpoint, built_layers: frozenset[Layer]) -> LifecycleCell:
    """A checkpoint with no reading. **Which kind of nothing depends on the layer.**

    An unbuilt layer has nothing to report until that layer exists. A built
    layer with no record at this checkpoint is an ordinary lifecycle in flight.
    Saying "layer not built" about Atreides because a trade has not settled yet
    would be false on the page, and false in the direction that makes the
    programme look less finished than it is.

    Both are ``INDETERMINATE`` — neither is evidence of anything — but the
    detail distinguishes them, because the two want different responses.
    """
    layer = CHECKPOINT_LAYER[checkpoint]
    return LifecycleCell(
        checkpoint=checkpoint,
        layer=layer,
        disposition=Disposition.INDETERMINATE,
        detail=NOT_BUILT_REASON if layer not in built_layers else NOT_REACHED_REASON,
        stamped_at=None,
        provenance="—",
    )


def build_row(
    lifecycle_id: str,
    readings: dict[Checkpoint, LayerReading],
    built_layers: frozenset[Layer],
) -> LifecycleRow:
    """One row, in column order.

    Rendering whatever it is given rather than raising is deliberate: a page that
    threw because one source was quiet would take the other five columns with it.
    """
    cells = tuple(
        cell_for(checkpoint, readings[checkpoint])
        if checkpoint in readings
        else _missing(checkpoint, built_layers)
        for checkpoint in CHECKPOINT_ORDER
    )
    return LifecycleRow(lifecycle_id=lifecycle_id, cells=cells)


SUPPORTED_SCHEMA_VERSION = "harness_c2.lifecycle_document/1"


class _WireModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class _StageEvidence(_WireModel):
    disposition: Disposition
    reason: str
    stamped_at: datetime
    provenance: Provenance


class _StageRow(_WireModel):
    stage: Checkpoint
    evidence: Recorded[_StageEvidence] | Absent


class _LifecycleDocument(_WireModel):
    schema_version: Literal["harness_c2.lifecycle_document/1"]
    taken_at: datetime
    scenario_id: ScenarioId
    lifecycle_id: LifecycleId
    synthetic: Literal[True]
    stages: tuple[_StageRow, ...]


class LifecycleSource(Protocol):
    def rows(self) -> tuple[LifecycleRow, ...]: ...


def parse_document(raw: bytes) -> LifecycleRow:
    """Read one strict publication instance without importing its producer."""
    try:
        data = json.loads(raw)
    except ValueError:
        raise SourceMalformedError("Lifecycle document is not valid JSON") from None
    if not isinstance(data, dict):
        raise SourceMalformedError("Lifecycle document is not a JSON object")
    version = data.get("schema_version")
    if version is None:
        raise SourceFieldAbsentError("Lifecycle document has no schema_version")
    if version != SUPPORTED_SCHEMA_VERSION:
        raise SourceMalformedError(
            f"Lifecycle document is schema version {version!r}; this reader understands "
            f"only {SUPPORTED_SCHEMA_VERSION!r}"
        )
    try:
        document = _LifecycleDocument.model_validate_json(raw)
    except ValidationError as exc:
        raise SourceMalformedError(
            f"Unexpected lifecycle document shape ({exc.error_count()} problems)"
        ) from None
    if tuple(stage.stage for stage in document.stages) != CHECKPOINT_ORDER:
        raise SourceMalformedError("Lifecycle document stages are absent, duplicated, or unordered")
    cells = []
    for stage in document.stages:
        layer = CHECKPOINT_LAYER[stage.stage]
        if isinstance(stage.evidence, Absent):
            cells.append(
                LifecycleCell(
                    checkpoint=stage.stage,
                    layer=layer,
                    disposition=Disposition.INDETERMINATE,
                    detail=stage.evidence.reason,
                    stamped_at=None,
                    provenance="FACT_SYNTHETIC",
                )
            )
        else:
            value = stage.evidence.value
            cells.append(
                LifecycleCell(
                    checkpoint=stage.stage,
                    layer=layer,
                    disposition=value.disposition,
                    detail=value.reason,
                    stamped_at=value.stamped_at,
                    provenance=value.provenance.value,
                )
            )
    return LifecycleRow(
        lifecycle_id=str(document.lifecycle_id), cells=tuple(cells), taken_at=document.taken_at
    )


class HttpxLifecycleClient:
    """Read the funded and unfunded stable publication instances over HTTPS."""

    def __init__(self, base_url: str, http: httpx.Client | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._http = http or httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)

    def rows(self) -> tuple[LifecycleRow, ...]:
        headers = {"Accept": "application/json", "User-Agent": PRODUCT_NAME}
        return tuple(
            parse_document(get_bytes(self._http, f"{self._base_url}/{name}.json", headers=headers))
            for name in ("funded", "unfunded")
        )
