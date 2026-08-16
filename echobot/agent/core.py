from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from typing import Any

from ..models import (
    ImageInput,
    LLMMessage,
    LLMResponse,
    ToolCall,
    build_user_message_content,
    message_content_to_text,
)
from ..providers.base import LLMProvider
from ..tools import ToolLoopControl, ToolResult
from .context import ContextPreparer, MemorySupport, SystemPromptValue
from .skills import prepare_skills
from .state import AgentRunState
from .tool_execution import ToolBatchExecutor
from .types import (
    AgentEvent,
    AgentEventHandler,
    AgentRequest,
    AgentRunResult,
    AgentRunStatus,
    CheckpointHandler,
)


logger = logging.getLogger(__name__)
_MAX_STEPS_FINAL_INSTRUCTION = (
    "The tool-step limit has been reached. Do not call more tools. "
    "Give the user the best concise answer you can from the work completed so far, "
    "and clearly mention anything that remains unfinished."
)


class AgentCore:
    """Canonical model/tool loop shared by every EchoBot agent entry point."""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        system_prompt: SystemPromptValue | None = None,
        memory_support: MemorySupport | None = None,
    ) -> None:
        self.provider = provider
        self._context = ContextPreparer(system_prompt, memory_support)
        self._tools = ToolBatchExecutor()

    async def run(
        self,
        request: AgentRequest,
        *,
        event_handler: AgentEventHandler | None = None,
        checkpoint_handler: CheckpointHandler | None = None,
    ) -> AgentRunResult:
        skills = prepare_skills(
            request.skill_registry,
            request.tool_registry,
            prompt=request.prompt,
            history=request.history,
            extra_system_messages=request.extra_system_messages,
        )
        user_message = LLMMessage(
            role="user",
            content=build_user_message_content(
                request.prompt,
                request.image_urls,
                request.file_attachments,
            ),
        )
        state = AgentRunState(
            persistent_messages=[
                *request.history,
                *skills.persistent_messages,
                user_message,
            ],
            new_messages=[*skills.persistent_messages, user_message],
            compressed_summary=request.compressed_summary,
        )
        registry = skills.tool_registry
        llm_tools = registry.to_llm_tools() if registry is not None else None

        for step in range(1, request.max_steps + 1):
            prepared = await self._context.prepare(
                state.persistent_messages,
                compressed_summary=state.compressed_summary,
                extra_system_messages=skills.extra_system_messages,
                transient_system_messages=request.transient_system_messages,
            )
            state.apply_prepared_context(prepared)
            response = await self.provider.generate(
                prepared.request_messages,
                tools=llm_tools,
                tool_choice=request.tool_choice,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
            )
            tool_calls = _response_tool_calls(response)
            response = _with_message_tool_calls(response, tool_calls)
            if tool_calls and registry is None:
                logger.warning("Provider returned tool calls when no tools were available")
                response = _without_tool_calls(response)
                tool_calls = []
            state.append(response.message)
            await _emit(
                event_handler,
                AgentEvent(
                    type="assistant_message",
                    step=step,
                    data={"message": _message_to_dict(response.message)},
                ),
            )

            if not tool_calls:
                return await self._complete(
                    state,
                    response,
                    step=step,
                    checkpoint_handler=checkpoint_handler,
                )

            assert registry is not None
            batch = await self._tools.execute(
                tool_calls,
                registry,
                arguments_truncated=response.finish_reason == "length",
                timeout_seconds=request.tool_timeout_seconds,
                max_parallel_tools=request.max_parallel_tools,
            )
            promoted_images, promoted_tool_names = await self._record_tool_results(
                state,
                batch.results,
                step=step,
                event_handler=event_handler,
            )

            promoted_message = _build_promoted_image_message(
                promoted_images,
                tool_names=promoted_tool_names,
            )
            if promoted_message is not None:
                state.append(promoted_message)
                await _emit(
                    event_handler,
                    AgentEvent(
                        type="tool_result_promotion",
                        step=step,
                        data={
                            "tool_names": list(dict.fromkeys(promoted_tool_names)),
                            "image_count": len(promoted_images),
                            "message": _message_to_dict(promoted_message),
                        },
                    ),
                )

            await _checkpoint(
                checkpoint_handler,
                state,
                step,
            )
            if batch.control is not None:
                return await self._complete_from_control(
                    state,
                    response,
                    control=batch.control,
                    step=step,
                    event_handler=event_handler,
                    checkpoint_handler=checkpoint_handler,
                )

        return await self._finish_after_max_steps(
            request,
            state=state,
            extra_system_messages=skills.extra_system_messages,
            event_handler=event_handler,
            checkpoint_handler=checkpoint_handler,
        )

    async def stream(self, request: AgentRequest) -> AsyncIterator[str]:
        """Stream one tool-free response using the same context preparation path."""
        if request.tool_registry is not None or request.skill_registry is not None:
            raise ValueError("AgentCore.stream only supports tool-free requests")

        user_message = LLMMessage(
            role="user",
            content=build_user_message_content(
                request.prompt,
                request.image_urls,
                request.file_attachments,
            ),
        )
        prepared = await self._context.prepare(
            [*request.history, user_message],
            compressed_summary=request.compressed_summary,
            extra_system_messages=request.extra_system_messages,
            transient_system_messages=request.transient_system_messages,
        )
        async for chunk in self.provider.stream_generate(
            prepared.request_messages,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        ):
            yield chunk

    async def _record_tool_results(
        self,
        state: AgentRunState,
        results: Sequence[ToolResult],
        *,
        step: int,
        event_handler: AgentEventHandler | None,
    ) -> tuple[list[dict[str, str]], list[str]]:
        promoted_images: list[dict[str, str]] = []
        promoted_tool_names: list[str] = []
        for result in results:
            message = result.to_message()
            state.append(message)
            await _emit(
                event_handler,
                AgentEvent(
                    type="tool_result",
                    step=step,
                    data={
                        "tool_name": result.tool_name,
                        "tool_call_id": result.call_id,
                        "is_error": result.is_error,
                        "message": _message_to_dict(message),
                    },
                ),
            )
            for trace_event in result.trace_events:
                await _emit(
                    event_handler,
                    AgentEvent(
                        type=trace_event.event,
                        step=step,
                        data={
                            "tool_name": result.tool_name,
                            "tool_call_id": result.call_id,
                            **trace_event.data,
                        },
                    ),
                )
            if result.is_error:
                continue
            if result.promoted_image_urls:
                promoted_images.extend(result.promoted_image_urls)
                promoted_tool_names.append(result.tool_name)
            state.outbound_content_blocks.extend(result.outbound_content_blocks)
        return promoted_images, promoted_tool_names

    async def _complete_from_control(
        self,
        state: AgentRunState,
        previous_response: LLMResponse,
        *,
        control: ToolLoopControl,
        step: int,
        event_handler: AgentEventHandler | None,
        checkpoint_handler: CheckpointHandler | None,
    ) -> AgentRunResult:
        response = LLMResponse(
            message=LLMMessage(role="assistant", content=control.response_content),
            model=previous_response.model,
            finish_reason="stop",
            usage=previous_response.usage,
        )
        state.append(response.message)
        status = _control_status(control.status)
        await _emit(
            event_handler,
            AgentEvent(
                type="assistant_message",
                step=step,
                data={
                    "message": _message_to_dict(response.message),
                    "source": "tool_control",
                    "status": status,
                },
            ),
        )
        return await self._complete(
            state,
            response,
            step=step,
            status=status,
            pending_user_input=dict(control.metadata),
            checkpoint_handler=checkpoint_handler,
        )

    async def _complete(
        self,
        state: AgentRunState,
        response: LLMResponse,
        *,
        step: int,
        status: AgentRunStatus = "completed",
        pending_user_input: dict[str, Any] | None = None,
        checkpoint_handler: CheckpointHandler | None,
    ) -> AgentRunResult:
        await self._context.remember(state.new_messages)
        await _checkpoint(checkpoint_handler, state, step)
        return state.result(
            response,
            steps=step,
            status=status,
            pending_user_input=pending_user_input,
        )

    async def _finish_after_max_steps(
        self,
        request: AgentRequest,
        *,
        state: AgentRunState,
        extra_system_messages: Sequence[str],
        event_handler: AgentEventHandler | None,
        checkpoint_handler: CheckpointHandler | None,
    ) -> AgentRunResult:
        final_step = request.max_steps + 1
        prepared = await self._context.prepare(
            state.persistent_messages,
            compressed_summary=state.compressed_summary,
            extra_system_messages=extra_system_messages,
            transient_system_messages=[
                *request.transient_system_messages,
                _MAX_STEPS_FINAL_INSTRUCTION,
            ],
        )
        response = await self.provider.generate(
            prepared.request_messages,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )
        if _response_tool_calls(response):
            response = _without_tool_calls(response)
        state.apply_prepared_context(prepared)
        state.append(response.message)
        await _emit(
            event_handler,
            AgentEvent(
                type="assistant_message",
                step=final_step,
                data={
                    "message": _message_to_dict(response.message),
                    "source": "max_steps_summary",
                    "status": "max_steps",
                },
            ),
        )
        return await self._complete(
            state,
            response,
            step=final_step,
            status="max_steps",
            checkpoint_handler=checkpoint_handler,
        )


def _response_tool_calls(response: LLMResponse) -> list[ToolCall]:
    return list(response.tool_calls or response.message.tool_calls)


def _with_message_tool_calls(
    response: LLMResponse,
    tool_calls: list[ToolCall],
) -> LLMResponse:
    if response.message.tool_calls == tool_calls:
        return response
    return replace(response, message=replace(response.message, tool_calls=tool_calls))


def _without_tool_calls(response: LLMResponse) -> LLMResponse:
    return replace(
        response,
        message=replace(response.message, tool_calls=[]),
        tool_calls=[],
        finish_reason="stop",
    )


def _control_status(value: str) -> AgentRunStatus:
    if value == "waiting_for_input":
        return "waiting_for_input"
    return "completed"


async def _emit(
    handler: AgentEventHandler | None,
    event: AgentEvent,
) -> None:
    if handler is None:
        return
    try:
        await handler(event)
    except Exception:
        logger.exception("Agent event handler failed for event '%s'", event.type)


async def _checkpoint(
    handler: CheckpointHandler | None,
    state: AgentRunState,
    step: int,
) -> None:
    if handler is None:
        return
    await handler(state.checkpoint(step))


def _message_to_dict(message: LLMMessage) -> dict[str, Any]:
    return {
        "role": message.role,
        "content": message.content,
        "content_text": message_content_to_text(message.content),
        "name": message.name,
        "tool_call_id": message.tool_call_id,
        "tool_calls": [
            {
                "id": call.id,
                "name": call.name,
                "arguments": call.arguments,
            }
            for call in message.tool_calls
        ],
    }


def _build_promoted_image_message(
    image_urls: Sequence[ImageInput],
    *,
    tool_names: Sequence[str],
) -> LLMMessage | None:
    if not image_urls:
        return None
    names = [name for name in dict.fromkeys(tool_names) if name]
    if len(names) == 1:
        text = (
            f"The previous tool '{names[0]}' produced image input. "
            "Use the attached image while continuing this request."
        )
    elif names:
        text = (
            f"The previous tools ({', '.join(names)}) produced image input. "
            "Use the attached images while continuing this request."
        )
    else:
        text = "The previous tool output included image input. Use the attached images."
    return LLMMessage(
        role="user",
        content=build_user_message_content(text, image_urls=image_urls),
    )
