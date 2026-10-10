"""Negative checks showing that evaluator results do not depend on model prose."""

from __future__ import annotations

from copy import deepcopy

import pytest

from evaluation.runner import EvaluationFailure, default_runtime_factory, load_scenarios, run_scenario


def _scenario(scenario_id: str) -> dict:
    return deepcopy(next(item for item in load_scenarios() if item["id"] == scenario_id))


async def test_paraphrased_answer_passes_when_tools_and_state_are_correct(tmp_path):
    scenario = _scenario("large_inventory_search")
    original = scenario["recorded_turns"][-1]["text"]
    scenario["recorded_turns"][-1]["text"] = "Inventory contains 101 matching entries."

    result = await run_scenario(scenario, "openai", tmp_path)

    assert result.status == "passed"
    assert scenario["recorded_turns"][-1]["text"] != original


async def test_lookup_scenario_rejects_wrong_search_matches(tmp_path):
    scenario = _scenario("inventory_lookup_value_notation")
    scenario["tool_constraints"]["expected_search_results"][0]["part_ids"] = [2]

    with pytest.raises(EvaluationFailure, match="search 1 result differs"):
        await run_scenario(scenario, "openai", tmp_path)


async def test_lookup_scenario_requires_every_clarification_cue(tmp_path):
    scenario = _scenario("inventory_lookup_ambiguous_package")
    scenario["recorded_turns"][-1]["text"] = "Which package do you need: 0402?"

    with pytest.raises(EvaluationFailure, match="required semantic cue"):
        await run_scenario(scenario, "openai", tmp_path)


async def test_scenarios_seed_execute_and_inspect_injected_repositories(tmp_path):
    from unittest.mock import Mock
    from db.conversations import SQLiteConversationRepository
    from db.repository import SQLitePartsBinRepository
    from evaluation.runner import EvaluationStorage

    repository = SQLitePartsBinRepository(tmp_path / "injected-inventory.db")
    inventory = Mock(wraps=repository.inventory)
    repository.inventory = inventory
    conversations = SQLiteConversationRepository(tmp_path / "injected-events.db")
    storage = EvaluationStorage(repository, conversations)
    storage_factory = Mock(return_value=storage)

    def factory(runtime, supplied_repository, supplied_conversations, turns):
        assert supplied_repository is repository
        assert supplied_conversations is conversations
        return default_runtime_factory(runtime, supplied_repository, supplied_conversations, turns)

    result = await run_scenario(_scenario("pending_review_resolution"), "openai", tmp_path,
                                factory=factory, storage_factory=storage_factory)
    assert result.status == "passed"
    storage_factory.assert_called_once_with(tmp_path, "pending_review_resolution-openai")
    inventory.insert.assert_called_once()
    inventory.save_pending_review.assert_called_once()
    # Both the agent's provenance tool and the final state check use this port.
    assert inventory.list_provenance.call_count >= 2
    inventory.list_provenance.assert_called_with(1)
    assert inventory.search.call_count >= 1
    assert any(event.kind == "approval_decision" for event in conversations.events("eval-pending_review_resolution"))
    assert len(list(tmp_path.glob("*.db"))) == 2


@pytest.mark.parametrize("runtime", ["openai"])
async def test_initial_model_request_never_contains_a_full_inventory_snapshot(tmp_path, runtime):
    scenario = _scenario("large_inventory_search")
    from evaluation.runner import _seed, _turn, sqlite_evaluation_storage

    storage = sqlite_evaluation_storage(tmp_path, runtime)
    _seed(storage.repository, scenario["starting_database"])
    instance, transport = default_runtime_factory(runtime, storage.repository, storage.conversations,
                                                   [_turn(turn) for turn in scenario["recorded_turns"]])
    await instance.run("no-inventory-prompt", scenario["conversation"][0]["user"])

    request = transport.requests[0]
    assert request.exchanges == ()
    assert "0000" not in request.system
    assert len(request.tools) == 17


def test_canonical_contract_exposes_no_generic_or_direct_database_tool():
    from domain import PartsBinService
    from tools import PartsBinToolRegistry

    from db.repository import SQLitePartsBinRepository
    names = {tool["name"] for tool in PartsBinToolRegistry(PartsBinService(SQLitePartsBinRepository(":memory:"))).list_tools()}
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
