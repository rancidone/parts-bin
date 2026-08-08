"""The single server-side approval coordinator used by every runtime."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from tools import ApprovalReceipt


def _fingerprint(tool_name: str, arguments: dict[str, Any]) -> str:
    encoded = json.dumps({"tool": tool_name, "arguments": arguments}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


@dataclass(frozen=True)
class ApprovalRequest:
    request_id: str
    thread_id: str
    tool_name: str
    arguments: dict[str, Any]


class ApprovalEngine:
    """Issues and consumes typed approval receipts; model transports cannot forge one."""

    def __init__(self) -> None:
        self._pending: dict[str, ApprovalRequest] = {}
        self._approved: set[tuple[str, str]] = set()

    def request(self, thread_id: str, tool_name: str, arguments: dict[str, Any]) -> ApprovalRequest:
        request_id = _fingerprint(tool_name, arguments)
        request = ApprovalRequest(request_id, thread_id, tool_name, dict(arguments))
        self._pending[request_id] = request
        return request

    def decide(self, thread_id: str, request_id: str, approved: bool) -> ApprovalRequest:
        request = self._pending.get(request_id)
        if request is None or request.thread_id != thread_id:
            raise ValueError("Unknown approval request")
        del self._pending[request_id]
        if approved:
            self._approved.add((thread_id, request_id))
        return request

    def receipt_for(self, thread_id: str, tool_name: str, arguments: dict[str, Any]) -> ApprovalReceipt | None:
        request_id = _fingerprint(tool_name, arguments)
        if (thread_id, request_id) not in self._approved:
            return None
        return ApprovalReceipt.issue(tool_name, arguments)

    async def checker(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        """Registry callback; consumes the one typed approval it validates."""
        request_id = _fingerprint(tool_name, arguments)
        matching = next((entry for entry in self._approved if entry[1] == request_id), None)
        if matching is None:
            return False
        self._approved.remove(matching)
        return True
