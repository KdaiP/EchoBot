from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from ..models import FileInput, ImageInput, LLMMessage, LLMResponse

if TYPE_CHECKING:
    from ..skill_support import SkillRegistry
    from ..tools import ToolRegistry


AgentRunStatus = Literal["completed", "waiting_for_input", "max_steps"]


@dataclass(slots=True)
class AgentRequest:
    """Everything needed to run one user request through the agent."""

    prompt: str
    history: Sequence[LLMMessage] = field(default_factory=tuple)
    image_urls: Sequence[ImageInput] = field(default_factory=tuple)
    file_attachments: Sequence[FileInput] = field(default_factory=tuple)
    compressed_summary: str = ""
    tool_registry: ToolRegistry | None = None
    skill_registry: SkillRegistry | None = None
    tool_choice: str | dict[str, Any] | None = None
    extra_system_messages: Sequence[str] = field(default_factory=tuple)
    transient_system_messages: Sequence[str] = field(default_factory=tuple)
    temperature: float | None = None
    max_tokens: int | None = None
    max_steps: int = 50
    tool_timeout_seconds: float | None = 120.0
    max_parallel_tools: int = 4

    def __post_init__(self) -> None:
        if self.max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        if self.tool_timeout_seconds is not None and self.tool_timeout_seconds <= 0:
            raise ValueError("tool_timeout_seconds must be greater than 0")
        if self.max_parallel_tools < 1:
            raise ValueError("max_parallel_tools must be at least 1")


@dataclass(slots=True)
class AgentRunResult:
    response: LLMResponse
    new_messages: list[LLMMessage]
    history: list[LLMMessage]
    steps: int
    compressed_summary: str = ""
    outbound_content_blocks: list[dict[str, Any]] = field(default_factory=list)
    status: AgentRunStatus = "completed"
    pending_user_input: dict[str, Any] | None = None


@dataclass(slots=True)
class AgentCheckpoint:
    """A protocol-complete snapshot that is safe to persist."""

    history: list[LLMMessage]
    compressed_summary: str
    step: int


@dataclass(slots=True)
class AgentEvent:
    type: str
    step: int
    data: dict[str, Any] = field(default_factory=dict)


AgentEventHandler = Callable[[AgentEvent], Awaitable[None]]
CheckpointHandler = Callable[[AgentCheckpoint], Awaitable[None]]
