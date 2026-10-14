"""OpenAI Responses API transport."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

import httpx

from .models import ModelTurn, ToolCall
from .budgets import (MAX_INPUT_BYTES, MAX_OUTPUT_TOKENS, MAX_REASONING_STATE_BYTES,
                      ModelBudgetError, bounded_history, encoded, model_tool_result, size)
from .runtime import ModelRequest
from .telemetry import AgentTelemetry


class OpenAIResponsesTransport:
    def __init__(self, *, api_key: str, model: str, base_url: str = "https://api.openai.com/v1", client: httpx.AsyncClient | None = None,
                 telemetry: AgentTelemetry | None = None):
        self.api_key, self.model, self.base_url = api_key, model, base_url.rstrip("/")
        self.client = client or httpx.AsyncClient(timeout=60.0)
        self.telemetry = telemetry or AgentTelemetry()

    async def complete(self, request: ModelRequest) -> ModelTurn:
        response = await self.client.post(f"{self.base_url}/responses", headers=self._headers(),
                                          json=self._request_body(request))
        response.raise_for_status()
        return self._turn(response.json())

    async def start(self, request: ModelRequest) -> str:
        """Submit without waiting for generation; retain the existing store policy."""
        body = {**self._request_body(request), "background": True}
        response = await self.client.post(f"{self.base_url}/responses", headers=self._headers(), json=body)
        response.raise_for_status()
        payload = response.json()
        response_id = payload.get("id")
        if not isinstance(response_id, str) or not re.fullmatch(r"resp_[A-Za-z0-9_-]+", response_id):
            raise ValueError("Background submission did not return a valid response ID")
        return response_id

    async def retrieve(self, response_id: str) -> ModelTurn | None:
        response = await self.client.get(f"{self.base_url}/responses/{quote(response_id, safe='')}",
                                         headers=self._headers())
        if response.status_code in {404, 410}:
            raise ModelBudgetError("model_response_unavailable",
                "The submitted model response is unavailable or expired. It will not be submitted again; inspect provider usage before starting a new request.")
        response.raise_for_status()
        payload = response.json()
        if payload.get("id") != response_id:
            raise ModelBudgetError("model_response_invalid", "The provider returned a different response identity")
        if payload.get("status") in {"queued", "in_progress"}:
            return None
        if payload.get("status") not in {"completed", "incomplete", "failed", "cancelled"}:
            raise ModelBudgetError("model_response_invalid", "The provider returned an unknown response status")
        return self._turn(payload)

    async def cancel(self, response_id: str) -> str:
        """Idempotent cleanup of a known background response; discard all output."""
        response = await self.client.post(f"{self.base_url}/responses/{quote(response_id, safe='')}/cancel",
                                          headers=self._headers())
        if response.status_code in {404, 410}:
            return "unavailable"
        response.raise_for_status()
        payload = response.json()
        status = payload.get("status")
        if payload.get("id") != response_id or status not in {"completed", "cancelled", "failed", "incomplete"}:
            raise ValueError("Provider cancellation did not confirm a terminal response")
        return status

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def _request_body(self, request: ModelRequest) -> dict[str, Any]:
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
                                        "name": exchange["name"], "arguments": encoded(exchange.get("arguments", {}))})
                input_items.append({"type": "function_call_output", "call_id": exchange["call_id"],
                                    "output": encoded(model_tool_result(exchange["result"], tool=exchange["name"]))})
        # Images have their own preprocessing bound; base64 is not text tokens.
        text_input = [{**item, "content": [part for part in item["content"] if part.get("type") != "input_image"]}
                      if isinstance(item.get("content"), list) else item for item in input_items]
        reasoning_bytes = sum(len(item.get("encrypted_content", "").encode("utf-8"))
                              for item in input_items if item.get("type") == "reasoning"
                              and item.get("encrypted_content"))
        text_input = [{key: value for key, value in item.items() if key != "encrypted_content"}
                      if item.get("type") == "reasoning" else item for item in text_input]
        input_bytes = size({"instructions": request.system, "input": text_input, "tools": request.tools})
        self.telemetry.model_request(request.thread_id, input_bytes=input_bytes,
            reasoning_bytes=reasoning_bytes, tool_count=len(request.tools),
            exchange_count=len(request.exchanges), omitted_results=sum(
                bool(json.loads(item["output"]).get("result_omitted"))
                for item in input_items if item.get("type") == "function_call_output"))
        if reasoning_bytes > MAX_REASONING_STATE_BYTES:
            raise ModelBudgetError("context_budget_exceeded", "This request exceeds the saved reasoning budget. Saved outcomes remain available; start a narrower request without repeating completed mutations.")
        if input_bytes > MAX_INPUT_BYTES:
            raise ModelBudgetError("context_budget_exceeded", "This request exceeds the model input budget. Saved outcomes remain available; start a narrower request without repeating completed mutations.")
        return {"model": self.model, "instructions": request.system, "input": input_items,
                "tools": list(request.tools), "tool_choice": "auto", "store": False,
                "parallel_tool_calls": False, "max_output_tokens": MAX_OUTPUT_TOKENS}

    @staticmethod
    def _turn(payload: dict[str, Any]) -> ModelTurn:
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
