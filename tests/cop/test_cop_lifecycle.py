"""COP-1 WP-1: the lifecycle board, and the acceptance tests the order names.

Most of the board is absent, and that is the principle being demonstrated
rather than a gap in the work. These tests are mostly about the absences.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from cannae_kernel.disposition import Disposition

from cop.app import PANELS, create_app
from cop.lifecycle import (
    CHECKPOINT_LAYER,
    CHECKPOINT_ORDER,
    NOT_BUILT_REASON,
    NOT_REACHED_REASON,
    Checkpoint,
    Layer,
    LayerReading,
    build_row,
    row_disposition,
)
from cop.settings import load_settings

AT = datetime(2026, 9, 21, 23, 0, tzinfo=UTC)
BUILT = frozenset({Layer.AUREON, Layer.ATREIDES})
ALL_BUILT = frozenset(Layer)


def reading(layer: Layer, disposition: Disposition, **overrides: object) -> LayerReading:
    fields: dict[str, object] = {
        "layer": layer,
        "built": True,
        "current": True,
        "detail": "something happened",
        "disposition": disposition,
        "stamped_at": AT,
        "provenance": "POLICY_RESULT",
    }
    fields.update(overrides)
    return LayerReading(**fields)  # type: ignore[arg-type]


def approved() -> dict[Checkpoint, LayerReading]:
    return {
        Checkpoint.APPROVED_INTENT: reading(
            Layer.AUREON, Disposition.PASS, provenance="HUMAN_JUDGMENT"
        )
    }


class TestARowIsNeverGreenerThanItsWeakestCell:
    """The order's first acceptance test."""

    def test_a_row_with_one_indeterminate_cell_never_renders_pass(self) -> None:
        row = build_row("lif_1", approved(), BUILT)
        assert any(c.disposition is Disposition.INDETERMINATE for c in row.cells)
        assert row.disposition is not Disposition.PASS

    def test_a_row_is_the_worst_of_its_cells(self) -> None:
        cells = build_row("lif_1", approved(), BUILT).cells
        assert row_disposition(cells) is Disposition.INDETERMINATE

    def test_a_block_outranks_an_indeterminate(self) -> None:
        readings = approved()
        readings[Checkpoint.OBLIGATION_ACCEPTANCE] = reading(Layer.ATREIDES, Disposition.BLOCK)
        assert build_row("lif_1", readings, BUILT).disposition is Disposition.BLOCK

    def test_indeterminate_outranks_hold(self) -> None:
        """ "We do not know" is worse to report than "we know, and it is waiting"."""
        readings = approved()
        readings[Checkpoint.OBLIGATION_ACCEPTANCE] = reading(Layer.ATREIDES, Disposition.HOLD)
        assert build_row("lif_1", readings, BUILT).disposition is Disposition.INDETERMINATE

    def test_a_fully_known_row_may_pass(self) -> None:
        """The guard against over-correcting: the rule must not make PASS unreachable."""
        readings = {
            checkpoint: reading(CHECKPOINT_LAYER[checkpoint], Disposition.PASS)
            for checkpoint in CHECKPOINT_ORDER
        }
        assert build_row("lif_1", readings, ALL_BUILT).disposition is Disposition.PASS

    def test_a_row_with_no_cells_is_indeterminate_not_pass(self) -> None:
        assert row_disposition(()) is Disposition.INDETERMINATE


class TestTheMiddleLayerRendersAbsent:
    """The order's third acceptance test: *"absent — layer not built", not empty
    and not PASS.*"""

    @pytest.mark.parametrize(
        "checkpoint",
        [Checkpoint.EXECUTION, Checkpoint.CLEARING, Checkpoint.SETTLEMENT_OBLIGATION],
    )
    def test_each_middle_column_says_layer_not_built(self, checkpoint: Checkpoint) -> None:
        row = build_row("lif_1", approved(), BUILT)
        cell = next(c for c in row.cells if c.checkpoint is checkpoint)
        assert cell.detail == NOT_BUILT_REASON
        assert cell.disposition is Disposition.INDETERMINATE

    def test_it_is_not_empty(self) -> None:
        for cell in build_row("lif_1", approved(), BUILT).cells:
            assert cell.detail.strip()

    def test_it_is_not_a_pass(self) -> None:
        row = build_row("lif_1", approved(), BUILT)
        middle = [c for c in row.cells if c.layer is Layer.LC]
        assert middle and all(c.disposition is not Disposition.PASS for c in middle)

    def test_all_three_middle_columns_belong_to_the_middle_layer(self) -> None:
        """Three, not the two the order names. The settlement obligation is
        formed by L.C. (JUM-D-02), so it is absent for the same reason —
        and Wave 4 turns on three columns by building one layer."""
        assert [c for c, layer in CHECKPOINT_LAYER.items() if layer is Layer.LC] == [
            Checkpoint.EXECUTION,
            Checkpoint.CLEARING,
            Checkpoint.SETTLEMENT_OBLIGATION,
        ]

    def test_an_absent_cell_carries_no_borrowed_timestamp(self) -> None:
        """Legate's observation time beside a thing that never happened would be
        a timestamp for an event, and there was no event."""
        row = build_row("lif_1", approved(), BUILT)
        for cell in row.cells:
            if cell.detail == NOT_BUILT_REASON:
                assert cell.stamped_at is None


class TestAnExplicitlyUnbuiltReadingIsStillAbsent:
    """A source may report a checkpoint and say the layer behind it is not built.

    That path is separate from a checkpoint with no reading at all, and it was
    untested until a mutation showed it: swapping the not-built branch to carry
    `reading.stamped_at` changed nothing, because nothing exercised it.
    """

    def _cell(self) -> object:
        readings = approved()
        readings[Checkpoint.EXECUTION] = reading(
            Layer.LC,
            Disposition.PASS,
            built=False,
            detail="executed at 14:02",
            stamped_at=AT,
        )
        return next(
            c
            for c in build_row("lif_1", readings, BUILT).cells
            if c.checkpoint is Checkpoint.EXECUTION
        )

    def test_it_says_layer_not_built(self) -> None:
        assert self._cell().detail == NOT_BUILT_REASON  # type: ignore[attr-defined]

    def test_it_carries_no_timestamp_even_though_one_was_supplied(self) -> None:
        """A time beside a thing that never happened is a timestamp for an event,
        and there was no event. The supplied one is dropped on purpose."""
        assert self._cell().stamped_at is None  # type: ignore[attr-defined]

    def test_it_does_not_carry_the_supplied_detail_forward(self) -> None:
        assert "executed at 14:02" not in self._cell().detail  # type: ignore[attr-defined]

    def test_it_is_indeterminate_whatever_disposition_was_supplied(self) -> None:
        assert self._cell().disposition is Disposition.INDETERMINATE  # type: ignore[attr-defined]


class TestNotBuiltIsNotTheSameAsNotReached:
    def test_a_built_layer_with_no_record_says_not_reached(self) -> None:
        """Saying "layer not built" about Atreides because a trade has not
        settled would be false on the page."""
        row = build_row("lif_1", approved(), BUILT)
        settled = next(c for c in row.cells if c.checkpoint is Checkpoint.SETTLED)
        assert settled.detail == NOT_REACHED_REASON
        assert settled.detail != NOT_BUILT_REASON

    def test_an_unbuilt_layer_says_not_built(self) -> None:
        row = build_row("lif_1", approved(), BUILT)
        execution = next(c for c in row.cells if c.checkpoint is Checkpoint.EXECUTION)
        assert execution.detail == NOT_BUILT_REASON

    def test_both_are_indeterminate_because_neither_is_evidence(self) -> None:
        row = build_row("lif_1", approved(), BUILT)
        for checkpoint in (Checkpoint.EXECUTION, Checkpoint.SETTLED):
            cell = next(c for c in row.cells if c.checkpoint is checkpoint)
            assert cell.disposition is Disposition.INDETERMINATE

    def test_when_the_middle_layer_is_built_the_columns_stop_saying_not_built(self) -> None:
        """What Wave 4 changes, asserted now so it is visible when it lands."""
        row = build_row("lif_1", approved(), ALL_BUILT)
        middle = [c for c in row.cells if c.layer is Layer.LC]
        assert all(c.detail == NOT_REACHED_REASON for c in middle)


class TestAStaleSourceIsNeverCurrent:
    """The order's second acceptance test."""

    def test_a_stale_reading_is_indeterminate(self) -> None:
        readings = approved()
        readings[Checkpoint.OBLIGATION_ACCEPTANCE] = reading(
            Layer.ATREIDES,
            Disposition.PASS,
            current=False,
            stale_reason="the activation snapshot is 40 minutes old",
        )
        cell = next(
            c
            for c in build_row("lif_1", readings, BUILT).cells
            if c.checkpoint is Checkpoint.OBLIGATION_ACCEPTANCE
        )
        assert cell.disposition is Disposition.INDETERMINATE

    def test_a_stale_reading_says_which_kind_of_nothing_it_is(self) -> None:
        """One waits for Wave 4; the other asks somebody to look at a source."""
        readings = approved()
        readings[Checkpoint.SETTLED] = reading(
            Layer.ATREIDES, Disposition.PASS, current=False, stale_reason="Timeout"
        )
        cell = next(
            c
            for c in build_row("lif_1", readings, BUILT).cells
            if c.checkpoint is Checkpoint.SETTLED
        )
        assert "not current" in cell.detail
        assert "Timeout" in cell.detail
        assert cell.detail != NOT_BUILT_REASON

    def test_a_stale_reading_does_not_carry_its_value_forward_as_current(self) -> None:
        readings = approved()
        readings[Checkpoint.SETTLED] = reading(
            Layer.ATREIDES,
            Disposition.PASS,
            current=False,
            detail="settled at 14:02",
            stale_reason="ConnectionError",
        )
        cell = next(
            c
            for c in build_row("lif_1", readings, BUILT).cells
            if c.checkpoint is Checkpoint.SETTLED
        )
        assert "settled at 14:02" not in cell.detail
        assert cell.stamped_at is None


class TestEachLayerKeepsItsOwnClock:
    def test_a_cell_names_the_layer_that_stamped_it(self) -> None:
        row = build_row("lif_1", approved(), BUILT)
        intent = next(c for c in row.cells if c.checkpoint is Checkpoint.APPROVED_INTENT)
        assert intent.layer is Layer.AUREON
        assert intent.stamped_at == AT

    def test_every_checkpoint_has_exactly_one_owning_layer(self) -> None:
        assert set(CHECKPOINT_LAYER) == set(CHECKPOINT_ORDER)
        assert len(CHECKPOINT_LAYER) == len(CHECKPOINT_ORDER)

    def test_the_columns_are_in_lifecycle_order(self) -> None:
        """The five frozen contracts are the spine; the order is not a choice."""
        assert CHECKPOINT_ORDER == (
            Checkpoint.APPROVED_INTENT,
            Checkpoint.EXECUTION,
            Checkpoint.CLEARING,
            Checkpoint.SETTLEMENT_OBLIGATION,
            Checkpoint.OBLIGATION_ACCEPTANCE,
            Checkpoint.SETTLED,
        )


class TestTheCopIsReadOnly:
    """The order's fifth acceptance test, and COP-1 rule 4.

    *The COP shows; the domains decide.* Escalations are displayed for the human;
    acting on them happens where authority lives.
    """

    ALLOWED_WRITES = frozenset({"login_submit", "logout"})

    def test_no_route_accepts_a_write_method(self) -> None:
        app = create_app(
            load_settings({"LEGATE_OPERATOR_KEY": "k" * 20, "LEGATE_SESSION_SECRET": "s" * 40}),
            start_refresher=False,
        )
        offenders = []
        for rule in app.url_map.iter_rules():
            methods = (rule.methods or set()) & {"POST", "PUT", "PATCH", "DELETE"}
            if methods and rule.endpoint not in self.ALLOWED_WRITES:
                offenders.append(f"{rule.endpoint} {sorted(methods)} {rule.rule}")
        assert offenders == [], f"the COP has acquired a write route: {offenders}"

    def test_the_only_writes_are_signing_in_and_out(self) -> None:
        """Named explicitly so that adding a third is a decision, not a line."""
        app = create_app(
            load_settings({"LEGATE_OPERATOR_KEY": "k" * 20, "LEGATE_SESSION_SECRET": "s" * 40}),
            start_refresher=False,
        )
        writes = {
            rule.endpoint
            for rule in app.url_map.iter_rules()
            if (rule.methods or set()) & {"POST", "PUT", "PATCH", "DELETE"}
        }
        assert writes == self.ALLOWED_WRITES

    def test_no_route_is_named_like_a_control(self) -> None:
        """No approve, release or resolve. The halt control arrives in COP-1's
        successor, and naming one here would be the first step to building it."""
        forbidden = {"approve", "release", "resolve", "halt", "submit", "execute"}
        assert {p for p in PANELS if p in forbidden} == set()
