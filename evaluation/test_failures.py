from __future__ import annotations

import json

from agent_runtime import ConversationEvent

from .failures import capture_failure, promote_failure


def test_capture_failure_redacts_messages_images_arguments_and_results(tmp_path):
    artifact = tmp_path / "failure.json"
    events = [
        ConversationEvent("user_message", "secret-thread", "openai", {"text": "my secret", "image": {"data_base64": "AA=="}}),
        ConversationEvent("tool_call", "secret-thread", "openai", {"name": "search_parts", "arguments": {"filters": {"part_number": "secret"}}}),
        ConversationEvent("tool_result", "secret-thread", "openai", {"name": "search_parts", "result": {"error": {"code": "part_not_found", "message": "secret"}}}),
    ]
    capture_failure(artifact, runtime="openai", failure_code="assertion_failed", events=events, scenario_id="case")
    content = artifact.read_text()
    assert "my secret" not in content and "data_base64" not in content and "part_number" not in content
    captured = json.loads(content)
    assert captured["events"] == [{"kind": "user_message"}, {"kind": "tool_call", "tool": "search_parts"}, {"kind": "tool_result", "tool": "search_parts", "error_code": "part_not_found"}]


def test_approved_failure_promotes_reviewed_candidate(tmp_path):
    capture = tmp_path / "failure.json"
    candidate = tmp_path / "candidate.json"
    scenarios = tmp_path / "scenarios.json"
    capture_failure(capture, runtime="local", failure_code="tool_loop", events=[])
    candidate.write_text(json.dumps({"id": "reviewed_failure", "starting_database": {"parts": []}, "conversation": [{"user": "synthetic"}], "tool_constraints": {}, "expected_final_state": {}, "recorded_turns": [{"text": "done"}]}))
    scenarios.write_text(json.dumps({"version": 1, "scenarios": []}))
    assert promote_failure(capture, candidate, approved_by="operator", scenarios_path=scenarios) == "reviewed_failure"
    assert json.loads(scenarios.read_text())["scenarios"][0]["id"] == "reviewed_failure"
    assert json.loads(capture.read_text())["promotion"]["approved_by"] == "operator"
