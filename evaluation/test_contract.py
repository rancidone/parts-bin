"""Negative checks showing that evaluator results do not depend on model prose."""

from __future__ import annotations

from copy import deepcopy

import pytest

from .runner import EvaluationFailure, default_runtime_factory, load_scenarios, run_scenario


def _scenario(scenario_id: str) -> dict:
    return deepcopy(next(item for item in load_scenarios() if item["id"] == scenario_id))


async def test_paraphrased_answer_passes_when_tools_and_state_are_correct(tmp_path):
    scenario = _scenario("large_inventory_search")
    original = scenario["recorded_turns"][-1]["text"]
    scenario["recorded_turns"][-1]["text"] = "Inventory contains 101 matching entries."

    result = await run_scenario(scenario, "openai", tmp_path)

    assert result.status == "passed"
    assert scenario["recorded_turns"][-1]["text"] != original


@pytest.mark.parametrize("runtime", ["codex", "openai", "local"])
async def test_initial_model_request_never_contains_a_full_inventory_snapshot(tmp_path, runtime):
    scenario = _scenario("large_inventory_search")
    database = tmp_path / f"{runtime}.db"
    from .runner import _seed, _turn

    _seed(database, scenario["starting_database"])
    instance, transport = default_runtime_factory(runtime, database, [_turn(turn) for turn in scenario["recorded_turns"]])
    await instance.run("no-inventory-prompt", scenario["conversation"][0]["user"])

    request = transport.requests[0]
    assert request.exchanges == ()
    assert "0000" not in request.system
    assert len(request.tools) == 12


def test_canonical_contract_exposes_no_generic_or_direct_database_tool():
    from domain import PartsBinService
    from tools import PartsBinToolRegistry

    names = {tool["name"] for tool in PartsBinToolRegistry(PartsBinService(":memory:")).list_tools()}
    forbidden = {"sql", "query_sql", "db" + "_action", "run_action", "patch", "shell", "web"}
    assert not names & forbidden


@pytest.mark.parametrize(
    ("scenario_id", "mutate"),
    [
        ("duplicate_part", lambda item: item["recorded_turns"][0]["tool_calls"][0]["arguments"].update({"filters": {}})),
        ("incomplete_add", lambda item: item.update({"recorded_turns": [{"tool_calls": [{"name": "add_part", "arguments": {"part_category": "resistor", "profile": "passive", "quantity": 1, "value": "10k"}}]}, {"text": "Added."}]})),
        ("duplicate_part", lambda item: item["recorded_turns"][0]["tool_calls"][0]["arguments"].update({"filters": {"unsupported": "x"}})),
        ("search_then_targeted_update", lambda item: item.update({"allowed_approvals": {}})),
        ("search_then_targeted_update", lambda item: item["expected_final_state"]["part_assertions"]["1"].update({"description": "wrong"})),
        ("large_inventory_search", lambda item: item["recorded_turns"].insert(1, {"tool_calls": [{"name": "search_parts", "arguments": {"filters": {"part_category": "resistor", "value": "10k"}}}]})),
    ],
    ids=["full_inventory", "unsafe_mutation", "invalid_arguments", "approval_bypass", "wrong_database_state", "tool_loop"],
)
async def test_evaluator_rejects_policy_violations(tmp_path, scenario_id, mutate):
    scenario = _scenario(scenario_id)
    mutate(scenario)

    with pytest.raises(EvaluationFailure):
        await run_scenario(scenario, "openai", tmp_path)
