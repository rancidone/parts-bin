"""OpenAI Responses API transport."""

from __future__ import annotations

import json
from typing import Any

import httpx

from .models import ModelTurn, ToolCall
from .budgets import (MAX_INPUT_BYTES, MAX_OUTPUT_TOKENS, ModelBudgetError,
                      bounded_history, model_tool_result, size)
from .runtime import ModelRequest


class OpenAIResponsesTransport:
    def __init__(self, *, api_key: str, model: str, base_url: str = "https://api.openai.com/v1", client: httpx.AsyncClient | None = None):
        self.api_key, self.model, self.base_url = api_key, model, base_url.rstrip("/")
        self.client = client or httpx.AsyncClient(timeout=60.0)

    async def complete(self, request: ModelRequest) -> ModelTurn:
        content: list[dict[str, Any]] = [{"type": "input_text", "text": request.user_text}]
        if request.image is not None and not request.exchanges:
            content.append({"type": "input_image", "image_url": f"data:{request.image.media_type};base64,{request.image.data_base64}"})
        input_items: list[dict[str, Any]] = [
            {"role": item["role"], "content": item["text"]} for item in bounded_history(request.history)
        ] + [{"role": "user", "content": content}]
        call_ids: set[str] = set()
        for exchange in request.exchanges:
            if exchange["type"] == "model_output":
                input_items.extend(exchange["items"])
                call_ids.update(item["call_id"] for item in exchange["items"] if item.get("type") == "function_call")
            elif exchange["type"] == "approval_denied":
                input_items.append({"role": "user", "content": "The user declined the pending operation. Do not execute it."})
            elif exchange["type"] == "tool_result":
                # Runtime checkpoints normally retain the original function item.
                # A caller supplying only a tool outcome must provide its call too.
                if exchange["call_id"] not in call_ids:
                    input_items.append({"type": "function_call", "call_id": exchange["call_id"],
                                        "name": exchange["name"], "arguments": json.dumps(exchange.get("arguments", {}))})
                input_items.append({"type": "function_call_output", "call_id": exchange["call_id"],
                                    "output": json.dumps(model_tool_result(exchange["result"]), separators=(",", ":"))})
        # Images have their own preprocessing bound; base64 is not text tokens.
        text_input = [{**item, "content": [part for part in item["content"] if part.get("type") != "input_image"]}
                      if isinstance(item.get("content"), list) else item for item in input_items]
        if size({"instructions": request.system, "input": text_input, "tools": request.tools}) > MAX_INPUT_BYTES:
            raise ModelBudgetError("context_budget_exceeded", "This request exceeds the model input budget. Saved outcomes remain available; start a narrower request without repeating completed mutations.")
        response = await self.client.post(f"{self.base_url}/responses", headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "instructions": request.system, "input": input_items,
                  "tools": list(request.tools), "tool_choice": "auto", "store": False,
                  "parallel_tool_calls": False, "max_output_tokens": MAX_OUTPUT_TOKENS})
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") in {"incomplete", "failed", "cancelled"}:
            raise ModelBudgetError("model_response_incomplete", "The model did not complete its response. No partial tool calls were executed; inspect saved outcomes before making a new request.")
        text: list[str] = []
        calls: list[ToolCall] = []
        for item in payload.get("output", []):
            if item.get("type") == "function_call":
                try:
                    arguments = json.loads(item.get("arguments", "{}"))
                except json.JSONDecodeError:
                    arguments = {}
                calls.append(ToolCall(item.get("name", ""), arguments, item.get("call_id", "")))
            elif item.get("type") == "message":
                text.extend(part.get("text", "") for part in item.get("content", []) if part.get("type") == "output_text")
        return ModelTurn("".join(text), tuple(calls), tuple(payload.get("output", [])))

    async def close(self) -> None:
        await self.client.aclose()
