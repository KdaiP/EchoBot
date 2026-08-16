from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models import LLMMessage, LLMResponse
from .context import PreparedContext
from .types import AgentCheckpoint, AgentRunResult, AgentRunStatus


@dataclass(slots=True)
class AgentRunState:
    """Mutable state owned by one invocation of ``AgentCore.run``."""

    persistent_messages: list[LLMMessage]
    new_messages: list[LLMMessage]
    compressed_summary: str
    outbound_content_blocks: list[dict[str, Any]] = field(default_factory=list)

    def apply_prepared_context(self, prepared: PreparedContext) -> None:
        self.persistent_messages = prepared.persistent_messages
        self.compressed_summary = prepared.compressed_summary

    def append(self, message: LLMMessage) -> None:
        self.persistent_messages.append(message)
        self.new_messages.append(message)

    def checkpoint(self, step: int) -> AgentCheckpoint:
        return AgentCheckpoint(
            history=list(self.persistent_messages),
            compressed_summary=self.compressed_summary,
            step=step,
        )

    def result(
        self,
        response: LLMResponse,
        *,
        steps: int,
        status: AgentRunStatus = "completed",
        pending_user_input: dict[str, Any] | None = None,
    ) -> AgentRunResult:
        return AgentRunResult(
            response=response,
            new_messages=list(self.new_messages),
            history=list(self.persistent_messages),
            steps=steps,
            compressed_summary=self.compressed_summary,
            outbound_content_blocks=list(self.outbound_content_blocks),
            status=status,
            pending_user_input=pending_user_input,
        )
