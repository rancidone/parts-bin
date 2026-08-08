"""Transport-neutral data types for Parts Bin agent runtimes."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


RuntimeName = Literal["codex", "openai", "local"]
EventKind = Literal[
    "user_message", "assistant_text", "tool_call", "tool_result",
    "approval_request", "approval_decision", "error", "completed",
]


@dataclass(frozen=True)
class ImageInput:
    """A user image. Data is forwarded only to the selected model transport."""

    media_type: str
    data_base64: str


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    call_id: str = ""


@dataclass(frozen=True)
class ModelTurn:
    """One model response, normalized across provider response formats."""

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    protocol_events: tuple[tuple[str, dict[str, Any]], ...] = ()


@dataclass(frozen=True)
class ConversationEvent:
    kind: EventKind
    thread_id: str
    runtime: RuntimeName
    data: dict[str, Any] = field(default_factory=dict)
    sequence: int | None = None

    def payload(self) -> dict[str, Any]:
        return {"kind": self.kind, "thread_id": self.thread_id, "runtime": self.runtime,
                "data": self.data, "sequence": self.sequence}


@dataclass(frozen=True)
class ApprovalResponse:
    request_id: str
    approved: bool


@dataclass(frozen=True)
class RuntimeResult:
    events: tuple[ConversationEvent, ...]
    status: Literal["completed", "awaiting_approval", "failed"]
