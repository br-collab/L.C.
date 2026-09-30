"""BB2 plans are first-class inputs to the complete scenario runner."""

from test_runner import _run

from harness_c2.chaos import generate_plan
from harness_c2.transcript import CrossingArtifactEnvelope


def test_scenario_runner_records_the_plan_and_replays_it_exactly() -> None:
    plan = generate_plan(29)
    first_scenario, first = _run(funded=True, chaos_plan=plan)
    second_scenario, second = _run(funded=True, chaos_plan=plan)
    assert first_scenario == second_scenario
    assert first.to_bytes() == second.to_bytes()
    conditions = first.crossings[: len(plan.conditions)]
    artifact_ids = []
    for crossing in conditions:
        assert isinstance(crossing.envelope, CrossingArtifactEnvelope)
        assert crossing.envelope.artifact_kind == "injection_condition"
        artifact_ids.append(crossing.envelope.artifact_id)
    assert tuple(artifact_ids) == tuple(condition.condition_id for condition in plan.conditions)
