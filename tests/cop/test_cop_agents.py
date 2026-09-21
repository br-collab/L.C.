"""The Agents panel: WP-A3 and WP-A4 on the surface, and A-T4 and A-T5 in the COP.

The acceptance tests in Project-Atreides assert what the record holds. These
assert what the surface does with it, which is the other half of the claim: a
contract that can express absence and a surface that cannot display it is still
a surface that lies (issue #36).

The rule under test throughout is WP-A3's: **the surface must refuse to render a
state the record does not hold.** No default-to-green, no blank identifier
printed as a value, no phase marked "recorded" when nothing was written.
"""

from __future__ import annotations

import json

import pytest
from cannae_kernel.absence import Absent, Recorded
from cannae_kernel.disposition import Disposition
from cop_fakes import (
    EXPECTED_ERROR_CLASS,
    FAILURE_MODES,
    Rig,
    activation_body,
    login,
    section,
)

from cop.agents import parse_snapshot
from cop.observation import (
    NOT_CONFIGURED,
    SourceFieldAbsentError,
    SourceMalformedError,
)
from cop.view import AgentRowView, PageView, build_page


def _page(rig: Rig) -> PageView:
    rig.refresher.refresh_once()
    return build_page(rig.refresher.snapshot, rig.clock.now)


def _row(page: PageView, agent_id: str) -> AgentRowView:
    return next(r for r in page.agents.rows if r.agent_id == agent_id)


# The source ------------------------------------------------------------------------------


class TestTheSourceFailsClosed:
    @pytest.mark.parametrize("mode", FAILURE_MODES)
    def test_every_transport_failure_is_typed_and_carries_no_value(self, mode: str) -> None:
        rig = Rig()
        rig.atreides.failure = mode
        rig.refresher.refresh_once()
        obs = rig.refresher.snapshot.agents.snapshot
        assert obs.error_class == EXPECTED_ERROR_CLASS[mode]
        assert obs.value is None, "a failed source must not carry a current value"

    def test_an_unknown_schema_version_is_refused_rather_than_read(self) -> None:
        """A renamed field read as absent would look like a fact about the agents
        instead of a fact about the reader."""
        raw = json.dumps(activation_body(schema_version=99)).encode()
        with pytest.raises(SourceMalformedError, match="schema version"):
            parse_snapshot(raw)

    def test_a_document_with_no_schema_version_is_refused(self) -> None:
        body = activation_body()
        del body["schema_version"]
        with pytest.raises(SourceFieldAbsentError, match="schema_version"):
            parse_snapshot(json.dumps(body).encode())

    def test_a_body_that_is_not_json_is_refused(self) -> None:
        with pytest.raises(SourceMalformedError, match="not valid JSON"):
            parse_snapshot(b"{not json")

    def test_a_body_that_is_not_an_object_is_refused(self) -> None:
        with pytest.raises(SourceMalformedError, match="not a JSON object"):
            parse_snapshot(b"[]")

    def test_absence_survives_the_wire_with_its_reason(self) -> None:
        """The whole reason absence is a type rather than a null."""
        snapshot = parse_snapshot(json.dumps(activation_body(stopped=True)).encode())
        stopped = next(a for a in snapshot.agents if a.agent_id == "fiat-operations-specialist")
        assert isinstance(stopped.last_summary, Absent)
        assert "the operator stopped this agent" in stopped.last_summary.reason
        running = next(a for a in snapshot.agents if a.agent_id == "settlement-operations-analyst")
        assert isinstance(running.last_summary, Recorded)


# Not configured --------------------------------------------------------------------------


class TestAnUnconfiguredSourceIsNotAFailedOne:
    def test_it_reports_not_configured_and_names_the_variable(self) -> None:
        rig = Rig(agents_configured=False)
        rig.refresher.refresh_once()
        obs = rig.refresher.snapshot.agents.snapshot
        assert obs.error_class == NOT_CONFIGURED
        assert obs.error_detail is not None
        assert "ATREIDES_AGENTS_URL" in obs.error_detail

    def test_it_does_not_spoil_a_clean_refresh(self) -> None:
        """Nothing is wrong with a source nobody set up. Marking it failed would
        blank the clean-refresh time for everybody and train them to ignore it."""
        rig = Rig(agents_configured=False)
        rig.github.nightly_conclusion = "success"
        rig.refresher.refresh_once()
        assert rig.refresher.snapshot.last_clean_refresh_at == rig.clock.now

    def test_it_is_not_listed_as_a_stale_source(self) -> None:
        rig = Rig(agents_configured=False)
        page = _page(rig)
        assert not any("ATREIDES" in s or "activation" in s for s in page.banner.stale_sources)

    def test_it_still_makes_the_overall_state_not_confirmed(self) -> None:
        """A picture that cannot see the agents may not report that they are fine."""
        rig = Rig(agents_configured=False)
        page = _page(rig)
        assert page.banner.overall.code != Disposition.PASS
        assert any("agent activation" in r.text for r in page.banner.reasons)

    def test_the_panel_shows_no_rows_at_all(self) -> None:
        rig = Rig(agents_configured=False)
        page = _page(rig)
        assert page.agents.rows == ()
        assert page.agents.tile.badge.code == Disposition.INDETERMINATE


# A-T4 on the surface ---------------------------------------------------------------------


class TestAT4StoppedAgentRendersAbsentWithAReason:
    """With an agent stopped, the COP renders it absent-with-reason. The rendered
    string is not "pass", not "recorded", and not empty."""

    AGENT = "fiat-operations-specialist"

    def _stopped_page(self) -> PageView:
        rig = Rig()
        rig.atreides.body = activation_body(stopped=True)
        return _page(rig)

    def test_the_rendered_row_names_the_absence_and_its_reason(self) -> None:
        row = _row(self._stopped_page(), self.AGENT)
        assert row.summary.strip()
        assert "pass" not in row.summary.lower()
        assert "the operator stopped this agent" in row.summary

    def test_no_field_of_a_stopped_agent_renders_empty_or_as_a_dash(self) -> None:
        """A dash is a blank with a costume on. Every field states its absence."""
        row = _row(self._stopped_page(), self.AGENT)
        for field in (row.summary, row.observed_at, row.provenance, row.handoff_basis, row.refusal):
            assert field.strip() not in {"", "-", "—", "None", "null"}
            assert "nothing recorded" in field.lower()

    def test_the_badge_is_indeterminate_and_says_stopped(self) -> None:
        row = _row(self._stopped_page(), self.AGENT)
        assert row.badge.code == Disposition.INDETERMINATE
        assert row.badge.label == "Stopped"
        assert row.up is False

    def test_the_rendered_html_shows_the_reason_rather_than_a_blank(self) -> None:
        """End to end, through the template, which is where #36 actually happened."""
        rig = Rig()
        rig.atreides.body = activation_body(stopped=True)
        rig.refresher.refresh_once()
        client = rig.app_client()
        login(client)
        html = client.get("/panel/agents").get_data(as_text=True)
        panel = section(html, "agents")
        assert self.AGENT in panel
        assert "agent stopped: the operator stopped this agent" in panel

    def test_a_running_agent_still_shows_its_value(self) -> None:
        """The guard against over-correcting: absence rendering must not swallow
        the values that are there."""
        row = _row(self._stopped_page(), "settlement-operations-analyst")
        assert "Pre-routing gates clear" in row.summary
        assert row.badge.code == Disposition.PASS


# A-T5 on the surface ---------------------------------------------------------------------


class TestAT5TheCopReportsHaltedOnlyFromServerState:
    """The COP reports halted **only** when the server-side state is halted."""

    def test_a_halted_document_is_reported_as_halted_and_blocks(self) -> None:
        rig = Rig()
        rig.atreides.body = activation_body(halted=True)
        page = _page(rig)
        assert page.agents.halted is True
        assert "Tier 0 emergency halt" in page.agents.halt_text
        assert page.agents.tile.badge.label.startswith("Halted")
        assert page.banner.overall.code == Disposition.BLOCK

    def test_an_unhalted_document_is_never_reported_as_halted(self) -> None:
        page = _page(Rig())
        assert page.agents.halted is False
        assert "no halt covering Atreides" in page.agents.halt_text

    def test_a_stale_snapshot_does_not_report_halted_either_way(self) -> None:
        """The state the control could lie about. With no current snapshot the COP
        knows nothing about the halt, and says so rather than guessing."""
        rig = Rig()
        rig.atreides.body = activation_body(halted=True)
        rig.refresher.refresh_once()
        rig.clock.advance(minutes=30)
        page = build_page(rig.refresher.snapshot, rig.clock.now)
        assert page.agents.halted is False
        assert "Not confirmed" in page.agents.halt_text
        assert page.agents.rows == ()


# The probe reads the right way round -----------------------------------------------------


class TestTheProbeIsReadInverted:
    PROBE = "lateral-handoff-probe"

    def test_a_refused_probe_is_healthy_and_says_why(self) -> None:
        row = _row(_page(Rig()), self.PROBE)
        assert row.expects_refusal
        assert row.badge.code == Disposition.PASS
        assert "Refusal held" in row.badge.label

    def test_a_refused_probe_does_not_hold_the_whole_picture_red(self) -> None:
        """A board that is permanently red hides a real failure as well as one
        that is permanently green."""
        rig = Rig()
        rig.github.nightly_conclusion = "success"
        page = _page(rig)
        assert not any(self.PROBE in r.text for r in page.banner.reasons)

    def test_an_admitted_probe_takes_the_whole_picture_to_block(self) -> None:
        """The alarm: WP-A2's refusal at the consumer has stopped working."""
        rig = Rig()
        rig.atreides.body = activation_body(probe_admitted=True)
        page = _page(rig)
        row = _row(page, self.PROBE)
        assert row.badge.code == Disposition.BLOCK
        assert "REFUSAL BROKEN" in row.badge.label
        assert page.banner.overall.code == Disposition.BLOCK
        assert any("lateral input was admitted" in r.text for r in page.banner.reasons)


# The panel as a whole --------------------------------------------------------------------


class TestThePanel:
    def test_it_is_registered_and_reachable(self) -> None:
        rig = Rig()
        rig.refresher.refresh_once()
        client = rig.app_client()
        login(client)
        assert client.get("/panel/agents").status_code == 200

    def test_it_declares_the_flow_synthetic(self) -> None:
        """So no reader mistakes these for venue facts."""
        rig = Rig()
        rig.refresher.refresh_once()
        client = rig.app_client()
        login(client)
        panel = section(client.get("/panel/agents").get_data(as_text=True), "agents")
        assert "SYNTHETIC FLOW" in panel

    def test_it_states_that_agents_recommend_only(self) -> None:
        rig = Rig()
        rig.refresher.refresh_once()
        client = rig.app_client()
        login(client)
        panel = section(client.get("/panel/agents").get_data(as_text=True), "agents")
        assert "recommend only" in panel
        assert "explicit operator action" in panel

    def test_the_handoff_basis_is_shown_for_every_agent(self) -> None:
        """AMD1 section 3b reaches the surface: every row states what it arrived
        under, and "operator-direct under CAOM-001" is a value, not a blank."""
        page = _page(Rig())
        for row in page.agents.rows:
            assert "operator-direct under CAOM-001" in row.handoff_basis

    def test_the_panel_requires_a_login(self) -> None:
        rig = Rig()
        rig.refresher.refresh_once()
        response = rig.app_client().get("/panel/agents")
        assert response.status_code == 302
