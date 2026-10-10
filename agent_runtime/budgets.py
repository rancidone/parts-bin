"""Model context limits independent of authoritative conversation storage.

UTF-8 byte budgets avoid assuming a chars-per-token ratio for arbitrary text.
They bound text payloads, not image tokens or provider framing overhead.
"""

import json
from typing import Any

MAX_HISTORY_BYTES = 12_000
MAX_TOOL_RESULT_BYTES = 12_000
MAX_INPUT_BYTES = 64_000
MAX_OUTPUT_TOKENS = 8_192
HISTORY_NOTICE = "Earlier conversation turns were omitted to fit the context budget. Ask for clarification when a reference needs omitted context; do not invent it."


class ModelBudgetError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def encoded(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def size(value: Any) -> int:
    return len(encoded(value).encode("utf-8"))


def bounded_history(history: tuple[dict[str, str], ...]) -> tuple[dict[str, str], ...]:
    """Retain a contiguous suffix of whole user turns, never orphan replies."""
    omitted = any(item.get("role") == "developer" for item in history)
    turns: list[list[dict[str, str]]] = []
    for item in history:
        if item["role"] == "developer":
            continue
        if item["role"] == "user":
            turns.append([])
        if turns:
            turns[-1].append(item)
        else:
            omitted = True
    kept: list[dict[str, str]] = []
    notice = {"role": "developer", "text": HISTORY_NOTICE}
    for turn in reversed(turns):
        if size([notice, *turn, *kept]) > MAX_HISTORY_BYTES:
            omitted = True
            break
        kept = turn + kept
    return tuple(([notice] if omitted else []) + kept)


def model_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    """Keep saved/UI results intact; mark oversized model payloads explicitly."""
    if size(result) <= MAX_TOOL_RESULT_BYTES:
        return result
    return {
        "ok": result.get("ok", False),
        "result_omitted": True,
        "message": "Tool execution finished, but its result exceeds the model context budget. The full outcome is saved in conversation events. Do not infer any omitted facts or repeat mutations. For reads, narrow filters or use a smaller page limit; otherwise ask the user to inspect the saved result.",
    }
