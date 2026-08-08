from __future__ import annotations

import pytest

from .runner import load_scenarios, run_scenario


def _cases():
    return [(scenario["id"], runtime) for scenario in load_scenarios() for runtime in scenario.get("runtimes", ["codex", "openai", "local"])]


@pytest.mark.parametrize(("scenario_id", "runtime"), _cases())
async def test_recorded_scenarios(tmp_path, scenario_id, runtime):
    scenario = next(item for item in load_scenarios() if item["id"] == scenario_id)
    result = await run_scenario(scenario, runtime, tmp_path)
    assert result.status == "passed"
