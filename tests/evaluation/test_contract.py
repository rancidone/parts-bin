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


async def test_category_lookup_requires_discovery_before_search(tmp_path):
    scenario = _scenario("inventory_lookup_category_discovery")
    scenario['recorded_turns'].pop(0)
    with pytest.raises(EvaluationFailure, match='required tool sequence'):
        await run_scenario(scenario, 'openai', tmp_path)


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
    assert len(request.tools) == 20


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


@pytest.mark.parametrize('scenario_id,key,field', [
    ('inventory_lookup_partial_marking', 'expected_candidate_results', 'candidate_ids'),
    ('inventory_lookup_electrical_evidence', 'expected_specification_results', 'match_ids'),
])
async def test_lookup_evaluation_rejects_wrong_candidates_and_evidence_matches(tmp_path, scenario_id, key, field):
    scenario = _scenario(scenario_id)
    scenario['tool_constraints'][key][0][field] = [4]
    with pytest.raises(EvaluationFailure, match='result differs'):
        await run_scenario(scenario, 'openai', tmp_path)


async def test_lookup_evaluation_rejects_staging_facts_even_without_changing_stock(tmp_path):
    scenario = _scenario('inventory_lookup_value_notation')
    scenario['recorded_turns'].insert(0, {'tool_calls': [{
        'name': 'stage_specification_review', 'arguments': {'part_id': 1, 'facts': [{
            'name': 'rated_voltage', 'value': '50 V', 'basis': 'rated', 'conditions': {},
            'evidence': {'kind': 'user_assertion', 'excerpt': 'Invented assertion'},
        }]},
    }]})
    with pytest.raises(EvaluationFailure, match='mutation was not allowed'):
        await run_scenario(scenario, 'openai', tmp_path)


async def test_lookup_snapshot_detects_changes_to_electrical_reviews(tmp_path):
    from domain import PartsBinService
    from evaluation.runner import _seed, _snapshot, sqlite_evaluation_storage
    scenario = _scenario('inventory_lookup_electrical_evidence')
    storage = sqlite_evaluation_storage(tmp_path, 'snapshot')
    _seed(storage.repository, scenario['starting_database'])
    before = _snapshot(storage.repository)
    service = PartsBinService(storage.repository)
    service.reject_specification_review(2)
    after = _snapshot(storage.repository)
    assert before['parts'] == after['parts']
    assert before['specifications'] == after['specifications']
    assert before != after


@pytest.mark.parametrize('scenario_id,text', [
    ('inventory_lookup_ambiguous_units', 'What value do you mean: 100 pF, 100 nF, or 100 µF?'),
    ('inventory_lookup_partial_marking', 'Candidate PBSS5350T,215 and PBSS5350T,115 stock; confirm the exact part before merging.'),
    ('inventory_lookup_insufficient_stock', 'You don’t have four 10 kΩ resistors in a single package: 2 in 0402 and 3 in 0603.'),
])
async def test_live_clarification_paraphrases_preserve_required_meaning(tmp_path, scenario_id, text):
    scenario = _scenario(scenario_id)
    scenario['recorded_turns'][-1]['text'] = text
    assert (await run_scenario(scenario, 'openai', tmp_path)).status == 'passed'


async def test_electrical_lookup_rejects_plain_inventory_results_instead_of_crashing(tmp_path):
    scenario = _scenario('inventory_lookup_electrical_evidence')
    scenario['recorded_turns'][2]['tool_calls'][0]['arguments'].pop('requirements')
    with pytest.raises(EvaluationFailure, match='result shape differs'):
        await run_scenario(scenario, 'openai', tmp_path)
