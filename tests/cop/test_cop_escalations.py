"""COP-1 WP-2: the escalation queue and panel 12.

Panel 12's acceptance test is the one that matters most: *never empty, and lists
each unconnected source by name.*
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from cop_fakes import Rig, login

from cop.blindspots import (
    CONTRACT_BLIND_SPOTS,
    BlindSpotKind,
    LayerStatus,
    SourceStatus,
    blind_spots,
)
from cop.escalations import (
    FORBIDDEN_FIELDS,
    parse_queue,
)
from cop.observation import SourceFieldAbsentError, SourceMalformedError
from cop.view import build_page

AT = datetime(2026, 9, 21, 23, 30, tzinfo=UTC)


def document(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "schema_version": 1,
        "taken_at": AT.isoformat().replace("+00:00", "Z"),
        "packets": [
            {
                "packet_id": "ESC-0002",
                "lifecycle_id": "lif_2",
                "trigger": "LINEAGE_HOLE",
                "raised_at": (AT - timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
                "disposition": "BLOCK",
                "summary": "the clearing transformation is missing",
                "findings": ["CLEARING — MISSING"],
                "unknowns": [],
                "requires_human_authority": True,
            },
            {
                "packet_id": "ESC-0001",
                "lifecycle_id": "lif_1",
                "trigger": "HANDOFF_REFUSED",
                "raised_at": (AT - timedelta(hours=9)).isoformat().replace("+00:00", "Z"),
                "disposition": "BLOCK",
                "summary": "a lateral input was refused",
                "findings": [],
                "unknowns": [{"what": "who would authorize", "why": "no authority record"}],
                "requires_human_authority": True,
            },
        ],
    }
    base.update(overrides)
    return base


class TestTheQueueIsOldestFirst:
    def test_the_longest_waiting_is_first(self) -> None:
        """The one most likely to have been forgotten belongs where somebody
        trips over it."""
        queue = parse_queue(json.dumps(document()).encode())
        assert [p.packet_id for p in queue.oldest_first] == ["ESC-0001", "ESC-0002"]

    def test_the_document_order_is_not_trusted(self) -> None:
        queue = parse_queue(json.dumps(document()).encode())
        assert [p.packet_id for p in queue.packets] == ["ESC-0002", "ESC-0001"]
        assert queue.oldest_first != queue.packets


class TestAPacketMayNotResolveWhatItEscalates:
    """C2 does not interpret doctrine. The reader refuses at the boundary."""

    @pytest.mark.parametrize("field", sorted(FORBIDDEN_FIELDS))
    def test_a_deciding_field_is_refused(self, field: str) -> None:
        doc = document()
        doc["packets"][0][field] = "whatever"  # type: ignore[index]
        with pytest.raises(SourceMalformedError, match="which C2 may not decide"):
            parse_queue(json.dumps(doc).encode())

    def test_the_refusal_names_the_offending_field(self) -> None:
        doc = document()
        doc["packets"][0]["recipient"] = "ops@example.invalid"  # type: ignore[index]
        with pytest.raises(SourceMalformedError, match="recipient"):
            parse_queue(json.dumps(doc).encode())

    def test_an_unknown_field_is_refused_rather_than_ignored(self) -> None:
        """`extra="forbid"` here, unlike the other readers. The fields that must
        not appear are the point of the contract."""
        doc = document()
        doc["packets"][0]["something_new"] = 1  # type: ignore[index]
        with pytest.raises(SourceMalformedError):
            parse_queue(json.dumps(doc).encode())


class TestTheSourceFailsClosed:
    def test_an_unknown_schema_version_is_refused(self) -> None:
        with pytest.raises(SourceMalformedError, match="schema version"):
            parse_queue(json.dumps(document(schema_version=99)).encode())

    def test_a_document_with_no_schema_version_is_refused(self) -> None:
        doc = document()
        del doc["schema_version"]
        with pytest.raises(SourceFieldAbsentError):
            parse_queue(json.dumps(doc).encode())

    def test_a_body_that_is_not_json_is_refused(self) -> None:
        with pytest.raises(SourceMalformedError, match="not valid JSON"):
            parse_queue(b"{nope")

    def test_an_empty_queue_parses(self) -> None:
        """Empty is a real answer and different from a source that failed."""
        queue = parse_queue(json.dumps(document(packets=[])).encode())
        assert queue.packets == ()


class TestPanelTwelveIsNeverEmpty:
    """The order's fourth acceptance test."""

    def test_it_is_not_empty_with_everything_connected_and_built(self) -> None:
        """The case that would tempt an empty panel. It still is not empty."""
        spots = blind_spots((), ())
        assert spots
        assert spots == CONTRACT_BLIND_SPOTS

    def test_it_lists_each_unconnected_source_by_name(self) -> None:
        spots = blind_spots(
            (
                SourceStatus("Atreides activation snapshot", configured=False, current=False),
                SourceStatus("C2 escalation queue", configured=False, current=False),
            ),
            (),
        )
        named = {s.name for s in spots if s.kind is BlindSpotKind.SOURCE_NOT_CONNECTED}
        assert named == {"Atreides activation snapshot", "C2 escalation queue"}

    def test_not_connected_and_not_answering_are_different_entries(self) -> None:
        """One asks somebody to configure a thing; the other asks somebody to
        look at it. A reader who cannot tell them apart chases the wrong one."""
        spots = blind_spots(
            (
                SourceStatus("A", configured=False, current=False),
                SourceStatus("B", configured=True, current=False, error="Timeout"),
            ),
            (),
        )
        kinds = {s.name: s.kind for s in spots if s.name in {"A", "B"}}
        assert kinds["A"] is BlindSpotKind.SOURCE_NOT_CONNECTED
        assert kinds["B"] is BlindSpotKind.SOURCE_NOT_ANSWERING

    def test_a_healthy_source_produces_no_entry(self) -> None:
        spots = blind_spots((SourceStatus("A", configured=True, current=True),), ())
        assert not any(s.name == "A" for s in spots)

    def test_an_unbuilt_layer_is_named_with_what_it_hides(self) -> None:
        spots = blind_spots(
            (), (LayerStatus("Legiones Cannenses", built=False, covers="execution and clearing"),)
        )
        layer = next(s for s in spots if s.kind is BlindSpotKind.LAYER_NOT_BUILT)
        assert "execution and clearing" in layer.detail
        assert "Wave 4" in layer.remedy

    def test_every_entry_says_what_would_change_it(self) -> None:
        """A remedy a reader cannot act on makes the list longer and the page no
        better."""
        spots = blind_spots(
            (SourceStatus("A", configured=False, current=False),),
            (LayerStatus("L", built=False, covers="things"),),
        )
        for spot in spots:
            assert spot.remedy.strip()
            assert spot.remedy.lower() != "investigate"

    def test_the_largest_blind_spot_is_named_and_cannot_be_closed_by_code(self) -> None:
        human = next(s for s in CONTRACT_BLIND_SPOTS if "outside this system" in s.name)
        assert "cannot be closed by code" in human.detail


class TestThePanelsRenderOnThePage:
    def test_panel_twelve_is_never_empty_on_a_rendered_page(self) -> None:
        rig = Rig()
        rig.refresher.refresh_once()
        page = build_page(rig.refresher.snapshot, rig.clock.now)
        assert page.blind_spots

    def test_an_unconfigured_escalation_source_appears_in_panel_twelve(self) -> None:
        rig = Rig(escalations_configured=False)
        rig.refresher.refresh_once()
        page = build_page(rig.refresher.snapshot, rig.clock.now)
        names = {s.name for s in page.blind_spots}
        assert "C2 escalation queue" in names

    def test_the_queue_panel_is_reachable(self) -> None:
        rig = Rig()
        rig.refresher.refresh_once()
        client = rig.app_client()
        login(client)
        assert client.get("/panel/escalations").status_code == 200
        assert client.get("/panel/blindspots").status_code == 200
