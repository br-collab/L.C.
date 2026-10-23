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
from datetime import date, datetime
from decimal import Decimal
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
    "FiccLifecycleFact",
    "FiccNetPosition",
    "HttpxLifecycleClient",
    "Layer",
    "LayerReading",
    "LifecycleCell",
    "LifecycleRow",
    "build_row",
    "cell_for",
    "parse_document",
    "parse_ficc_document",
    "parse_oms_document",
    "parse_repo_document",
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
class FiccLifecycleFact:
    stage: str
    disposition: Disposition
    detail: str


@dataclass(frozen=True)
class FiccNetPosition:
    member_id: str
    cusip: str
    settlement_date: str
    net_quantity: Decimal
    net_settlement_amount: Decimal
    settlement_currency: str


@dataclass(frozen=True)
class LifecycleRow:
    """One lifecycle object, across every layer."""

    lifecycle_id: str
    cells: tuple[LifecycleCell, ...]
    taken_at: datetime | None = None
    clearing_method: str | None = None
    observation_seat: str | None = None
    observed_member_id: str | None = None
    ficc_facts: tuple[FiccLifecycleFact, ...] = ()
    net_positions: tuple[FiccNetPosition, ...] = ()
    repo_summary: tuple[str, ...] = ()

    @property
    def disposition(self) -> Disposition:
        if self.ficc_facts:
            return max((fact.disposition for fact in self.ficc_facts), key=lambda d: _SEVERITY[d])
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
SUPPORTED_FICC_SCHEMA_VERSION = "harness_c2.ficc_lifecycle_document/2"


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
    clearing_method: Literal["BILATERAL_GROSS"]
    stages: tuple[_StageRow, ...]


class _FiccFact(_WireModel):
    stage: Literal[
        "CLEARING_ACCEPTANCE",
        "NOVATION",
        "NETTING",
        "SETTLEMENT_INSTRUCTION",
        "SETTLEMENT_FINALITY",
    ]
    status: Literal["RECORDED", "NOT_RECORDED"]
    evidence_id: str | None = None
    reason: str | None = None


class _FiccPosition(_WireModel):
    member_id: str
    cusip: str
    settlement_date: date
    net_quantity: Decimal
    net_settlement_amount: Decimal
    settlement_currency: str
    source_obligation_ids: tuple[str, ...]
    source_trade_ids: tuple[str, ...]


class _FiccLifecycleDocument(_WireModel):
    schema_version: Literal["harness_c2.ficc_lifecycle_document/2"]
    taken_at: datetime
    scenario_id: ScenarioId
    lifecycle_id: LifecycleId
    synthetic: Literal[True]
    source_payload_digest: str
    clearing_agency: Literal["FICC_GSD"]
    clearing_method: Literal["FICC_GSD_NOVATED_NETTED"]
    observation_seat: Literal["FICC_MEMBER"]
    observed_member_id: str
    access_path: Literal["FICC_DIRECT", "FICC_SPONSORED", "FICC_AGENT"]
    rule_set_version: str
    processing_calendar_id: str
    facts: tuple[_FiccFact, ...]
    net_settlement_obligations: tuple[_FiccPosition, ...]


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
        lifecycle_id=str(document.lifecycle_id),
        cells=tuple(cells),
        taken_at=document.taken_at,
        clearing_method=document.clearing_method,
    )


def parse_ficc_document(raw: bytes) -> LifecycleRow:
    """Read the strict FICC member-seat publication without inferring lifecycle cells."""

    try:
        data = json.loads(raw)
    except ValueError:
        raise SourceMalformedError("FICC lifecycle document is not valid JSON") from None
    if not isinstance(data, dict):
        raise SourceMalformedError("FICC lifecycle document is not a JSON object")
    version = data.get("schema_version")
    if version != SUPPORTED_FICC_SCHEMA_VERSION:
        raise SourceMalformedError(
            f"FICC lifecycle document is schema version {version!r}; this reader understands "
            f"only {SUPPORTED_FICC_SCHEMA_VERSION!r}"
        )
    try:
        document = _FiccLifecycleDocument.model_validate_json(raw)
    except ValidationError as exc:
        raise SourceMalformedError(
            f"Unexpected FICC lifecycle document shape ({exc.error_count()} problems)"
        ) from None
    expected_stages = (
        "CLEARING_ACCEPTANCE",
        "NOVATION",
        "NETTING",
        "SETTLEMENT_INSTRUCTION",
        "SETTLEMENT_FINALITY",
    )
    if tuple(fact.stage for fact in document.facts) != expected_stages:
        raise SourceMalformedError("FICC facts are absent, duplicated, or unordered")
    facts = tuple(
        FiccLifecycleFact(
            stage=fact.stage,
            disposition=(
                Disposition.PASS if fact.status == "RECORDED" else Disposition.INDETERMINATE
            ),
            detail=fact.evidence_id or fact.reason or "required evidence is absent",
        )
        for fact in document.facts
    )
    positions = tuple(
        FiccNetPosition(
            member_id=position.member_id,
            cusip=position.cusip,
            settlement_date=position.settlement_date.isoformat(),
            net_quantity=position.net_quantity,
            net_settlement_amount=position.net_settlement_amount,
            settlement_currency=position.settlement_currency,
        )
        for position in document.net_settlement_obligations
    )
    return LifecycleRow(
        lifecycle_id=str(document.lifecycle_id),
        cells=(),
        taken_at=document.taken_at,
        clearing_method=document.clearing_method,
        observation_seat=document.observation_seat,
        observed_member_id=document.observed_member_id,
        ficc_facts=facts,
        net_positions=positions,
    )


def parse_repo_document(raw: bytes) -> LifecycleRow:
    try:
        publication = json.loads(raw)
        if publication["schema_version"] != "harness_c2.ficc_repo_lifecycle/1":
            raise ValueError("unsupported schema")
        repo = publication["repo"]
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise SourceMalformedError("repo lifecycle publication is malformed") from exc
    legs = ", ".join(
        f"{leg['leg']} {leg['signed_par']} par / "
        f"{leg['signed_money']} {leg['currency']} via {leg['settlement_path']}"
        for leg in repo["legs"]
    )
    return LifecycleRow(
        lifecycle_id=str(publication["lifecycle_id"]),
        cells=(),
        taken_at=datetime.fromisoformat(publication["taken_at"]),
        clearing_method=repo["clearing_method"],
        observation_seat=repo["observation_seat"],
        observed_member_id=repo["observed_member_id"],
        repo_summary=(
            f"term={repo['term']}",
            f"rate={repo['repo_rate_percent']}%/{repo['day_count_basis']}",
            f"access={repo['access_path']}",
            f"legs={legs}",
            f"margin={repo['member_margin_method']}",
        ),
    )


def parse_equities_document(raw: bytes, name: str) -> LifecycleRow:
    """Render one EQ-1 publication without inferring anything absent from it."""
    try:
        document = json.loads(raw)
        if name in {"equities-account-capacity", "equities-locates"}:
            payload = json.loads(document["payload"])
            taken_at = datetime.fromisoformat(payload["as_of"])
            detail = (
                f"accounts={len(payload['accounts'])}; maximum_age_seconds="
                f"{payload['maximum_age_seconds']}"
                if name.endswith("capacity")
                else f"locates={len(payload['locates'])}; quantity limited and synthetic"
            )
        else:
            taken_at = datetime.fromisoformat(document["as_of"])
            if name == "equities-books":
                detail = (
                    f"balanced={document['balanced']}; sources="
                    f"{','.join(document['posting_sources'])}"
                )
            elif name == "equities-margin":
                detail = (
                    f"initial={document['initial']['disposition']}; price_move="
                    f"{document['after_price_move']['disposition']}"
                )
            elif name == "equities-market-access":
                decisions = document["decisions"]
                passed = sum(item["disposition"] == "PASS" for item in decisions)
                detail = (
                    f"decisions={len(decisions)}; passed={passed}; "
                    f"refused={len(decisions) - passed}"
                )
            elif name == "equities-protection-from-books":
                detail = "; ".join(
                    f"{key}={document[key]['disposition']}"
                    for key in ("reserve", "net_capital", "possession_or_control")
                )
            else:
                raise ValueError("unsupported equities document")
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise SourceMalformedError(f"{name} publication is malformed") from exc
    return LifecycleRow(
        lifecycle_id=name,
        cells=(),
        taken_at=taken_at,
        clearing_method="EQ1_EQUITIES_SELF_CLEARING",
        ficc_facts=(FiccLifecycleFact(stage=name, disposition=Disposition.PASS, detail=detail),),
    )


def parse_oms_document(raw: bytes) -> LifecycleRow:
    """Expose sealed order terms, states, gates, allocation and firm controls."""
    try:
        document = json.loads(raw)
        if document["schema_version"] != "lc.oms_orders_allocations/2":
            raise ValueError("unsupported schema")
        orders = document["orders"]
        allocations = document["allocations"]
        controls = document["firm_control_events"]
        fills = document["fills"]
        closed = document["closed_trades"]
        terms = ", ".join(
            f"{item['order_id']}={item['terms']['order_type']}/{item['terms']['time_in_force']}"
            for item in orders
        )
        states = ", ".join(f"{item['order_id']}:{'→'.join(item['states'])}" for item in orders)
        allocation = ", ".join(
            f"{item['allocation_id']} avg={item['average_price']} rule={item['rule']} "
            f"entries={len(item['entries'])}"
            for item in allocations
        )
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise SourceMalformedError("OMS order and allocation publication is malformed") from exc
    return LifecycleRow(
        lifecycle_id="oms-orders-allocations",
        cells=(),
        taken_at=datetime.fromisoformat(document["as_of"]),
        clearing_method="OMS_1_ORDER_MANAGEMENT",
        ficc_facts=(
            FiccLifecycleFact(stage="ORDER_TERMS", disposition=Disposition.PASS, detail=terms),
            FiccLifecycleFact(stage="ORDER_STATES", disposition=Disposition.PASS, detail=states),
            FiccLifecycleFact(stage="ALLOCATIONS", disposition=Disposition.PASS, detail=allocation),
            FiccLifecycleFact(
                stage="FIRM_CONTROLS",
                disposition=Disposition.PASS,
                detail=f"events={len(controls)}",
            ),
            FiccLifecycleFact(
                stage="FILLS",
                disposition=Disposition.PASS,
                detail=", ".join(
                    f"{item['trade_id']} {item['cusip']} qty={item['quantity']} "
                    f"price={item['price']} {item['provenance']}"
                    for item in fills
                ),
            ),
            FiccLifecycleFact(
                stage="CLOSED_TRADE",
                disposition=Disposition.PASS,
                detail=", ".join(
                    f"{item['trade_id']} {item['rail']} {item['ccp_status']} "
                    f"cash={item['cash_reconciled']} securities={item['securities_reconciled']} "
                    f"books={item['books_posted']} breaks={item['unexplained_breaks']} "
                    f"replay={item['replay_economics_identical']}"
                    for item in closed
                ),
            ),
        ),
    )


class HttpxLifecycleClient:
    """Read the stable bilateral and FICC publication instances over HTTPS."""

    def __init__(self, base_url: str, http: httpx.Client | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._http = http or httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)

    def rows(self) -> tuple[LifecycleRow, ...]:
        headers = {"Accept": "application/json", "User-Agent": PRODUCT_NAME}
        bilateral = tuple(
            parse_document(get_bytes(self._http, f"{self._base_url}/{name}.json", headers=headers))
            for name in ("funded", "unfunded")
        )
        ficc = parse_ficc_document(
            get_bytes(self._http, f"{self._base_url}/ficc-netted.json", headers=headers)
        )
        repo_rows = tuple(
            parse_repo_document(
                get_bytes(self._http, f"{self._base_url}/{name}.json", headers=headers)
            )
            for name in ("ficc-repo-dvp", "ficc-repo-gcf")
        )
        equities_rows = tuple(
            parse_equities_document(
                get_bytes(self._http, f"{self._base_url}/{name}.json", headers=headers), name
            )
            for name in (
                "equities-account-capacity",
                "equities-locates",
                "equities-books",
                "equities-margin",
                "equities-market-access",
                "equities-protection-from-books",
            )
        )
        oms = parse_oms_document(
            get_bytes(self._http, f"{self._base_url}/oms-orders-allocations.json", headers=headers)
        )
        return (*bilateral, ficc, *repo_rows, *equities_rows, oms)
