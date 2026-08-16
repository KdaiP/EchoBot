from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

from ..models import ToolCall
from ..tools import ToolLoopControl, ToolRegistry, ToolResult


@dataclass(slots=True)
class ToolBatchResult:
    results: list[ToolResult]
    control: ToolLoopControl | None = None


class ToolBatchExecutor:
    """Execute one model-ordered tool batch without breaking tool-call pairing."""

    async def execute(
        self,
        tool_calls: Sequence[ToolCall],
        registry: ToolRegistry,
        *,
        arguments_truncated: bool,
        timeout_seconds: float | None,
        max_parallel_tools: int,
    ) -> ToolBatchResult:
        if arguments_truncated:
            return ToolBatchResult(
                results=[
                    registry.error_result(
                        call,
                        "Tool call was not executed because the model response was truncated. "
                        "Call the tool again with complete arguments.",
                    )
                    for call in tool_calls
                ]
            )

        can_run_in_parallel = len(tool_calls) > 1 and all(
            registry.execution_mode(call.name) == "parallel"
            for call in tool_calls
        )
        if can_run_in_parallel:
            return await self._execute_parallel(
                tool_calls,
                registry,
                timeout_seconds=timeout_seconds,
                max_parallel_tools=max_parallel_tools,
            )
        return await self._execute_sequential(
            tool_calls,
            registry,
            timeout_seconds=timeout_seconds,
        )

    async def _execute_sequential(
        self,
        tool_calls: Sequence[ToolCall],
        registry: ToolRegistry,
        *,
        timeout_seconds: float | None,
    ) -> ToolBatchResult:
        results: list[ToolResult] = []
        control: ToolLoopControl | None = None
        for index, call in enumerate(tool_calls):
            result = await self._execute_one(call, registry, timeout_seconds)
            results.append(result)
            if result.control is None:
                continue

            control = result.control
            for skipped_call in tool_calls[index + 1 :]:
                results.append(
                    registry.error_result(
                        skipped_call,
                        f"Tool call skipped because '{call.name}' ended the agent loop.",
                    )
                )
            break
        return ToolBatchResult(results=results, control=control)

    async def _execute_parallel(
        self,
        tool_calls: Sequence[ToolCall],
        registry: ToolRegistry,
        *,
        timeout_seconds: float | None,
        max_parallel_tools: int,
    ) -> ToolBatchResult:
        semaphore = asyncio.Semaphore(max_parallel_tools)

        async def execute(call: ToolCall) -> ToolResult:
            async with semaphore:
                return await self._execute_one(call, registry, timeout_seconds)

        results = await asyncio.gather(*(execute(call) for call in tool_calls))
        control = next((result.control for result in results if result.control), None)
        return ToolBatchResult(results=list(results), control=control)

    async def _execute_one(
        self,
        call: ToolCall,
        registry: ToolRegistry,
        timeout_seconds: float | None,
    ) -> ToolResult:
        try:
            async with asyncio.timeout(timeout_seconds):
                return await registry.execute(call)
        except TimeoutError:
            return registry.error_result(
                call,
                f"Tool call timed out after {timeout_seconds} seconds.",
            )
