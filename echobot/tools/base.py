from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from ..models import (
    LLMMessage,
    LLMTool,
    MessageContent,
    MessageContentBlock,
    ToolCall,
    message_content_blocks,
    normalize_image_input,
    normalize_message_content,
)


ToolPayload = str | int | float | bool | None | dict[str, Any] | list[Any]
ToolExecutionMode = Literal["sequential", "parallel"]


@dataclass(slots=True)
class ToolTraceEvent:
    event: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolLoopControl:
    action: str
    status: str = "completed"
    response_content: MessageContent = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolExecutionOutput:
    data: ToolPayload
    promoted_image_urls: list[dict[str, str]] = field(default_factory=list)
    outbound_content_blocks: list[MessageContentBlock] = field(default_factory=list)
    trace_events: list[ToolTraceEvent] = field(default_factory=list)
    control: ToolLoopControl | None = None


ToolOutput = ToolPayload | ToolExecutionOutput


@dataclass(slots=True)
class ToolResult:
    call_id: str
    tool_name: str
    content: str
    is_error: bool = False
    promoted_image_urls: list[dict[str, str]] = field(default_factory=list)
    outbound_content_blocks: list[MessageContentBlock] = field(default_factory=list)
    trace_events: list[ToolTraceEvent] = field(default_factory=list)
    control: ToolLoopControl | None = None

    def to_message(self) -> LLMMessage:
        return LLMMessage(
            role="tool",
            content=self.content,
            tool_call_id=self.call_id,
        )


class BaseTool(ABC):
    name: str
    description: str
    parameters: dict[str, Any]
    execution_mode: ToolExecutionMode = "sequential"

    def to_llm_tool(self) -> LLMTool:
        return LLMTool(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
        )

    @abstractmethod
    async def run(self, arguments: dict[str, Any]) -> ToolOutput:
        raise NotImplementedError


class ToolRegistry:
    def __init__(self, tools: Sequence[BaseTool] | None = None) -> None:
        self._tools: dict[str, BaseTool] = {}

        if tools:
            for tool in tools:
                self.register(tool)

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool name: {tool.name}")

        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def execution_mode(self, name: str) -> ToolExecutionMode:
        tool = self.get(name)
        return tool.execution_mode if tool is not None else "sequential"

    def copy(self) -> "ToolRegistry":
        return ToolRegistry(list(self._tools.values()))

    def register_many(self, tools: Sequence[BaseTool]) -> None:
        for tool in tools:
            self.register(tool)

    def to_llm_tools(self) -> list[LLMTool]:
        return [tool.to_llm_tool() for tool in self._tools.values()]

    async def execute(self, tool_call: ToolCall) -> ToolResult:
        tool = self.get(tool_call.name)
        if tool is None:
            return self.error_result(tool_call, f"Tool not found: {tool_call.name}")

        try:
            arguments = _parse_arguments(tool_call.arguments)
        except ValueError as exc:
            return self.error_result(tool_call, str(exc))

        try:
            output = await tool.run(arguments)
        except Exception as exc:
            return self.error_result(tool_call, str(exc))

        execution_output = _normalize_execution_output(output)
        return ToolResult(
            call_id=tool_call.id,
            tool_name=tool_call.name,
            content=_build_payload(execution_output.data),
            promoted_image_urls=execution_output.promoted_image_urls,
            outbound_content_blocks=execution_output.outbound_content_blocks,
            trace_events=execution_output.trace_events,
            control=execution_output.control,
        )

    def error_result(self, tool_call: ToolCall, message: str) -> ToolResult:
        return ToolResult(
            call_id=tool_call.id,
            tool_name=tool_call.name,
            content=_build_payload({"error": message}, is_error=True),
            is_error=True,
        )


def _parse_arguments(raw_arguments: str) -> dict[str, Any]:
    cleaned = raw_arguments.strip()
    if not cleaned:
        return {}

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON tool arguments: {exc.msg}") from exc

    if not isinstance(parsed, dict):
        raise ValueError("Tool arguments must be a JSON object")

    return parsed


def _normalize_execution_output(output: ToolOutput) -> ToolExecutionOutput:
    if isinstance(output, ToolExecutionOutput):
        return ToolExecutionOutput(
            data=output.data,
            promoted_image_urls=_normalize_promoted_images(output.promoted_image_urls),
            outbound_content_blocks=_normalize_outbound_content_blocks(
                output.outbound_content_blocks
            ),
            trace_events=_normalize_trace_events(output.trace_events),
            control=_normalize_tool_loop_control(output.control),
        )

    return ToolExecutionOutput(data=output)


def _normalize_promoted_images(
    values: list[dict[str, str]],
) -> list[dict[str, str]]:
    normalized_images: list[dict[str, str]] = []
    for value in values:
        normalized = normalize_image_input(value)
        if normalized is not None:
            normalized_images.append(normalized)
    return normalized_images


def _normalize_outbound_content_blocks(
    values: list[MessageContentBlock],
) -> list[MessageContentBlock]:
    normalized_blocks: list[MessageContentBlock] = []
    for block in values:
        normalized_blocks.extend(message_content_blocks([block]))
    return normalized_blocks


def _normalize_trace_events(
    values: list[ToolTraceEvent],
) -> list[ToolTraceEvent]:
    normalized_events: list[ToolTraceEvent] = []
    for value in values:
        event_name = str(value.event or "").strip()
        if not event_name:
            continue
        normalized_events.append(
            ToolTraceEvent(
                event=event_name,
                data=dict(value.data or {}),
            )
        )
    return normalized_events


def _normalize_tool_loop_control(
    value: ToolLoopControl | None,
) -> ToolLoopControl | None:
    if value is None:
        return None

    action = str(value.action or "").strip()
    if not action:
        return None

    return ToolLoopControl(
        action=action,
        status=str(value.status or "").strip() or "completed",
        response_content=normalize_message_content(value.response_content),
        metadata=dict(value.metadata or {}),
    )


def _build_payload(data: ToolPayload, *, is_error: bool = False) -> str:
    payload: dict[str, Any] = {
        "ok": not is_error,
        "result": data,
    }

    if is_error and isinstance(data, dict) and "error" in data:
        payload = {
            "ok": False,
            "error": data["error"],
        }

    return json.dumps(payload, ensure_ascii=False)
