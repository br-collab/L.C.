"""COP-1 WP-1: the lifecycle board, and the acceptance tests the order names.

Most of the board is absent, and that is the principle being demonstrated
rather than a gap in the work. These tests are mostly about the absences.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from cannae_kernel.disposition import Disposition
from cop_fakes import Rig, login, section

from cop.app import PANELS, create_app
from cop.lifecycle import (
    CHECKPOINT_LAYER,
    CHECKPOINT_ORDER,
    NOT_BUILT_REASON,
    NOT_REACHED_REASON,
    Checkpoint,
    HttpxLifecycleClient,
    Layer,
    LayerReading,
    build_row,
    parse_document,
    parse_equities_document,
    parse_ficc_document,
    row_disposition,
)
from cop.observation import SourceMalformedError
from cop.settings import load_settings
from cop.view import build_page

AT = datetime(2026, 9, 21, 23, 0, tzinfo=UTC)
BUILT = frozenset({Layer.AUREON, Layer.ATREIDES})
ALL_BUILT = frozenset(Layer)


def _wire_document(
    *, funded: bool = True, version: str = "harness_c2.lifecycle_document/1"
) -> bytes:
    stages = []
    for checkpoint in CHECKPOINT_ORDER:
        if checkpoint is Checkpoint.SETTLED and not funded:
            evidence: dict[str, object] = {
                "state": "absent",
                "kind": "NOTHING_RECORDED",
                "reason": "NOT_REACHED: CASH_GATE_HOLD:UNFUNDED_AT_SETTLEMENT_INSTANT",
            }
        else:
            disposition = (
                "HOLD"
                if not funded
                and checkpoint
                in {
                    Checkpoint.SETTLEMENT_OBLIGATION,
                    Checkpoint.OBLIGATION_ACCEPTANCE,
                }
                else "PASS"
            )
            reason = (
                "CASH_GATE_HOLD:UNFUNDED_AT_SETTLEMENT_INSTANT"
                if checkpoint is Checkpoint.OBLIGATION_ACCEPTANCE and not funded
                else f"recorded {checkpoint.value.lower()}"
            )
            evidence = {
                "state": "recorded",
                "value": {
                    "disposition": disposition,
                    "reason": reason,
                    "stamped_at": "2026-09-29T15:00:00Z",
                    "provenance": "FACT_SYNTHETIC",
                },
            }
        stages.append({"stage": checkpoint.value, "evidence": evidence})
    return json.dumps(
        {
            "schema_version": version,
            "taken_at": "2026-09-29T15:00:00Z",
            "scenario_id": "scn_00000000000000000000000001",
            "lifecycle_id": "lif_03J0YY19FSPXWRSJ8ES4ZMMHSR",
            "synthetic": True,
            "clearing_method": "BILATERAL_GROSS",
            "stages": stages,
        }
    ).encode()


def _ficc_wire_document() -> bytes:
    return json.dumps(
        {
            "schema_version": "harness_c2.ficc_lifecycle_document/2",
            "taken_at": "2026-10-21T21:00:00Z",
            "scenario_id": "scn_00000000000000000000000015",
            "lifecycle_id": "lif_03J0YY19FSPXWRSJ8ES4ZMMR15",
            "synthetic": True,
            "source_payload_digest": "sha256:" + "a" * 64,
            "clearing_agency": "FICC_GSD",
            "clearing_method": "FICC_GSD_NOVATED_NETTED",
            "observation_seat": "FICC_MEMBER",
            "observed_member_id": "member-a",
            "access_path": "FICC_DIRECT",
            "rule_set_version": "synthetic-r15b-rule-set",
            "processing_calendar_id": "synthetic-r15b-calendar",
            "facts": [
                {
                    "stage": stage,
                    "status": "RECORDED",
                    "evidence_id": f"evidence-{stage}",
                    "reason": None,
                }
                for stage in (
                    "CLEARING_ACCEPTANCE",
                    "NOVATION",
                    "NETTING",
                    "SETTLEMENT_INSTRUCTION",
                    "SETTLEMENT_FINALITY",
                )
            ],
            "net_settlement_obligations": [
                {
                    "member_id": "member-a",
                    "cusip": "91282CJL6",
                    "settlement_date": "2026-10-22",
                    "net_quantity": "65",
                    "net_settlement_amount": "-64.35",
                    "settlement_currency": "USD",
                    "source_obligation_ids": ["obligation-1", "obligation-2"],
                    "source_trade_ids": ["trade-1", "trade-2"],
                }
            ],
        }
    ).encode()


def _repo_wire_document(method: str) -> bytes:
    path = "DVP_NETTING" if method == "FICC_GSD_DVP_REPO" else "GCF_TRIPARTY"
    return json.dumps(
        {
            "schema_version": "harness_c2.ficc_repo_lifecycle/1",
            "taken_at": "2026-10-21T23:30:00+00:00",
            "scenario_id": "scn_00000000000000000000000016",
            "lifecycle_id": "lif_03J0YY19FSPXWRSJ8ES4ZMMR16",
            "synthetic": True,
            "source_payload_digest": "sha256:" + "a" * 64,
            "cato_evidence": None,
            "repo": {
                "schema_version": "atreides.ficc_repo/1",
                "clearing_agency": "FICC_GSD",
                "clearing_method": method,
                "observation_seat": "FICC_MEMBER",
                "observed_member_id": "member-a",
                "access_path": "FICC_DIRECT",
                "sponsored_execution": None,
                "repo_rate_percent": "5",
                "day_count_basis": 360,
                "term": "TERM",
                "haircut_percent": "1",
                "member_margin_approximation": "0.25",
                "member_margin_method": "SYNTHETIC_APPROXIMATION_NOT_FICC_VAR",
                "legs": [
                    {
                        "leg": "START",
                        "signed_par": "100",
                        "signed_money": "-99",
                        "currency": "USD",
                        "settlement_path": path,
                    },
                    {
                        "leg": "CLOSE",
                        "signed_par": "-100",
                        "signed_money": "99.01375",
                        "currency": "USD",
                        "settlement_path": path,
                    },
                ],
            },
        }
    ).encode()


def _equities_wire_document(name: str) -> bytes:
    if name == "equities-account-capacity":
        payload = json.dumps(
            {"as_of": "2026-10-22T14:00:00+00:00", "maximum_age_seconds": 300, "accounts": []}
        )
        return json.dumps({"payload": payload}).encode()
    if name == "equities-locates":
        payload = json.dumps({"as_of": "2026-10-22T14:00:00+00:00", "locates": []})
        return json.dumps({"payload": payload}).encode()
    base: dict[str, object] = {"as_of": "2026-10-22T14:00:00+00:00"}
    if name == "equities-books":
        base.update({"balanced": True, "posting_sources": ["CNS_SETTLEMENT"]})
    elif name == "equities-margin":
        base.update(
            {"initial": {"disposition": "PASS"}, "after_price_move": {"disposition": "HOLD"}}
        )
    elif name == "equities-market-access":
        base["decisions"] = [{"disposition": "PASS"}, {"disposition": "HOLD"}]
    else:
        base.update(
            {
                "reserve": {"disposition": "HOLD"},
                "net_capital": {"disposition": "PASS"},
                "possession_or_control": {"disposition": "PASS"},
            }
        )
    return json.dumps(base).encode()


def _oms_wire_document() -> bytes:
    return json.dumps(
        {
            "schema_version": "lc.oms_orders_allocations/2",
            "as_of": "2026-10-22T14:00:00+00:00",
            "synthetic": True,
            "orders": [
                {
                    "order_id": "O1",
                    "terms": {"order_type": "LIMIT", "time_in_force": "DAY"},
                    "states": ["PARENT_ACTIVE", "MARKET_ACCESS_PASSED", "EXECUTED"],
                    "gate_decisions": [],
                }
            ],
            "allocations": [
                {
                    "allocation_id": "A1",
                    "average_price": "101",
                    "rule": "PRO_RATA",
                    "entries": [{"account": "A", "quantity": "100"}],
                }
            ],
            "firm_control_events": [],
            "fills": [
                {
                    "trade_id": "T1",
                    "order_id": "O1",
                    "cusip": "91282CJL6",
                    "quantity": "100",
                    "price": "99.5",
                    "provenance": "FACT_SYNTHETIC",
                }
            ],
            "closed_trades": [
                {
                    "trade_id": "T1",
                    "rail": "FICC_GSD_DVP",
                    "ccp_status": "SETTLED",
                    "cash_reconciled": True,
                    "securities_reconciled": True,
                    "books_posted": True,
                    "unexplained_breaks": 0,
                    "replay_economics_identical": True,
                }
            ],
        }
    ).encode()


class TestPublishedLifecycleReader:
    def test_ficc_document_exposes_the_member_seat_and_both_legs(self) -> None:
        row = parse_ficc_document(_ficc_wire_document())
        assert row.disposition is Disposition.PASS
        assert row.observation_seat == "FICC_MEMBER"
        assert row.observed_member_id == "member-a"
        assert row.net_positions[0].net_quantity == 65
        assert row.net_positions[0].net_settlement_amount == Decimal("-64.35")
        assert row.net_positions[0].settlement_currency == "USD"

    def test_funded_and_unfunded_documents_render_without_inference(self) -> None:
        funded = parse_document(_wire_document())
        unfunded = parse_document(_wire_document(funded=False))
        assert funded.disposition is Disposition.PASS
        assert funded.clearing_method == "BILATERAL_GROSS"
        assert unfunded.disposition is Disposition.INDETERMINATE
        acceptance = next(
            cell for cell in unfunded.cells if cell.checkpoint is Checkpoint.OBLIGATION_ACCEPTANCE
        )
        settled = next(cell for cell in unfunded.cells if cell.checkpoint is Checkpoint.SETTLED)
        assert acceptance.disposition is Disposition.HOLD
        assert acceptance.detail == "CASH_GATE_HOLD:UNFUNDED_AT_SETTLEMENT_INSTANT"
        assert settled.is_absent and settled.stamped_at is None
        assert "NOT_REACHED" in settled.detail

    def test_unknown_schema_is_refused_before_fields_are_read(self) -> None:
        with pytest.raises(SourceMalformedError, match="understands only"):
            parse_document(_wire_document(version="harness_c2.lifecycle_document/2"))

    def test_http_client_reads_both_stable_instances(self) -> None:
        requested: list[str] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            if request.url.path.endswith("ficc-netted.json"):
                return httpx.Response(200, content=_ficc_wire_document())
            if request.url.path.endswith("ficc-repo-dvp.json"):
                return httpx.Response(200, content=_repo_wire_document("FICC_GSD_DVP_REPO"))
            if request.url.path.endswith("ficc-repo-gcf.json"):
                return httpx.Response(200, content=_repo_wire_document("FICC_GCF_REPO"))
            stem = request.url.path.rsplit("/", 1)[-1].removesuffix(".json")
            if stem.startswith("equities-"):
                return httpx.Response(200, content=_equities_wire_document(stem))
            if stem == "oms-orders-allocations":
                return httpx.Response(200, content=_oms_wire_document())
            return httpx.Response(
                200,
                content=_wire_document(
                    funded=request.url.path.endswith("funded.json")
                    and not request.url.path.endswith("unfunded.json")
                ),
            )

        client = HttpxLifecycleClient(
            "https://example.invalid/lifecycle/",
            http=httpx.Client(transport=httpx.MockTransport(respond)),
        )
        rows = client.rows()
        assert len(rows) == 12
        assert requested == [
            "https://example.invalid/lifecycle/funded.json",
            "https://example.invalid/lifecycle/unfunded.json",
            "https://example.invalid/lifecycle/ficc-netted.json",
            "https://example.invalid/lifecycle/ficc-repo-dvp.json",
            "https://example.invalid/lifecycle/ficc-repo-gcf.json",
            "https://example.invalid/lifecycle/equities-account-capacity.json",
            "https://example.invalid/lifecycle/equities-locates.json",
            "https://example.invalid/lifecycle/equities-books.json",
            "https://example.invalid/lifecycle/equities-margin.json",
            "https://example.invalid/lifecycle/equities-market-access.json",
            "https://example.invalid/lifecycle/equities-protection-from-books.json",
            "https://example.invalid/lifecycle/oms-orders-allocations.json",
        ]

    def test_equities_publications_are_person_visible_rows(self) -> None:
        for name in (
            "equities-account-capacity",
            "equities-locates",
            "equities-books",
            "equities-margin",
            "equities-market-access",
            "equities-protection-from-books",
        ):
            row = parse_equities_document(_equities_wire_document(name), name)
            assert row.disposition is Disposition.PASS
            assert row.clearing_method == "EQ1_EQUITIES_SELF_CLEARING"

    def test_unset_configuration_names_the_variable(self) -> None:
        settings = load_settings({"LEGATE_OPERATOR_KEY": "k", "LEGATE_SESSION_SECRET": "s"})
        assert settings.lifecycle_base_url is None

    def test_reading_age_is_visible_whenever_rows_are_shown(self) -> None:
        rig = Rig()
        rig.refresher.refresh_once()
        client = rig.app_client()
        login(client)
        html = section(client.get("/section/trades").get_data(as_text=True), "lifecycles")
        assert "Reading age:" in html and "0 s" in html

    def test_cop_labels_the_ficc_seat_and_both_net_legs(self) -> None:
        rig = Rig()
        rig.refresher.refresh_once()
        client = rig.app_client()
        login(client)
        html = section(client.get("/section/trades").get_data(as_text=True), "lifecycles")
        assert "FICC_MEMBER" in html
        assert "Member member-a: par 65; money -64.35 USD" in html

    def test_past_lifecycle_cadence_holds_the_board_and_names_the_source(self) -> None:
        rig = Rig()
        snapshot = rig.refresher.refresh_once()
        rig.clock.advance(hours=27)
        page = build_page(snapshot, rig.clock.now)
        assert not page.lifecycles.tile.current
        assert page.lifecycles.rows == ()
        assert page.lifecycles.tile.badge.code == Disposition.INDETERMINATE.value
        assert any("Lifecycle board" in reason.text for reason in page.banner.reasons)


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
    """The generic unbuilt-layer state remains explicit, non-empty and non-PASS."""

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
        """The settlement obligation is formed by L.C. (JUM-D-02), so the same
        layer state governs it together with execution and clearing."""
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
        """A built layer reports not reached rather than falsely claiming unbuilt."""
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
        """One waits for a missing layer; the other asks somebody to inspect a source."""
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
