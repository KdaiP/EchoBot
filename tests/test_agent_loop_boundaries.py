from __future__ import annotations

import asyncio
import json
import unittest

from echobot import AgentCore, AgentRequest, LLMMessage, ToolCall
from echobot.models import LLMResponse
from echobot.providers.base import LLMProvider
from echobot.tools import BaseTool, RequestUserInputTool, ToolRegistry


class CountingTool(BaseTool):
    name = "count"
    description = "Record one execution."
    parameters = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    def __init__(self) -> None:
        self.calls = 0

    async def run(self, arguments):
        del arguments
        self.calls += 1
        return {"calls": self.calls}


class ParallelProbeTool(BaseTool):
    name = "parallel_probe"
    description = "Measure bounded parallel execution."
    execution_mode = "parallel"
    parameters = {
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
        "additionalProperties": False,
    }

    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    async def run(self, arguments):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01)
            return {"value": arguments["value"]}
        finally:
            self.active -= 1


def tool_response(
    *calls: ToolCall,
    finish_reason: str = "tool_calls",
) -> LLMResponse:
    tool_calls = list(calls)
    return LLMResponse(
        message=LLMMessage(role="assistant", tool_calls=tool_calls),
        model="fake-model",
        finish_reason=finish_reason,
        tool_calls=tool_calls,
    )


class TruncatedThenDoneProvider(LLMProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, messages, **kwargs) -> LLMResponse:
        del messages, kwargs
        self.calls += 1
        if self.calls == 1:
            return tool_response(
                ToolCall(id="truncated", name="count", arguments="{}"),
                finish_reason="length",
            )
        return LLMResponse(
            message=LLMMessage(role="assistant", content="recovered"),
            model="fake-model",
            finish_reason="stop",
        )


class AlwaysToolProvider(LLMProvider):
    def __init__(self) -> None:
        self.calls = 0
        self.final_request_had_tools: bool | None = None

    async def generate(self, messages, *, tools=None, **kwargs) -> LLMResponse:
        del messages, kwargs
        self.calls += 1
        if tools:
            return tool_response(
                ToolCall(id=f"call_{self.calls}", name="count", arguments="{}")
            )
        self.final_request_had_tools = bool(tools)
        return LLMResponse(
            message=LLMMessage(role="assistant", content="partial summary"),
            model="fake-model",
            finish_reason="stop",
        )


class ControlBatchProvider(LLMProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, messages, **kwargs) -> LLMResponse:
        del messages, kwargs
        self.calls += 1
        return tool_response(
            ToolCall(
                id="ask",
                name="request_user_input",
                arguments='{"prompt":"Which file?"}',
            ),
            ToolCall(id="must_skip", name="count", arguments="{}"),
        )


class ParallelThenDoneProvider(LLMProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, messages, **kwargs) -> LLMResponse:
        del messages, kwargs
        self.calls += 1
        if self.calls == 1:
            return tool_response(
                ToolCall(
                    id="first",
                    name="parallel_probe",
                    arguments='{"value":1}',
                ),
                ToolCall(
                    id="second",
                    name="parallel_probe",
                    arguments='{"value":2}',
                ),
            )
        return LLMResponse(
            message=LLMMessage(role="assistant", content="done"),
            model="fake-model",
        )


class AgentLoopBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_truncated_tool_arguments_are_never_executed(self) -> None:
        provider = TruncatedThenDoneProvider()
        tool = CountingTool()
        result = await AgentCore(provider).run(
            AgentRequest(
                prompt="run it",
                tool_registry=ToolRegistry([tool]),
            )
        )

        self.assertEqual(0, tool.calls)
        self.assertEqual("recovered", result.response.message.content)
        error_payload = json.loads(result.history[-2].content)
        self.assertFalse(error_payload["ok"])
        self.assertIn("truncated", error_payload["error"])

    async def test_max_steps_returns_final_tool_free_summary(self) -> None:
        provider = AlwaysToolProvider()
        tool = CountingTool()
        result = await AgentCore(provider).run(
            AgentRequest(
                prompt="keep working",
                tool_registry=ToolRegistry([tool]),
                max_steps=2,
            )
        )

        self.assertEqual("max_steps", result.status)
        self.assertEqual(3, result.steps)
        self.assertEqual(2, tool.calls)
        self.assertEqual("partial summary", result.response.message.content)
        self.assertFalse(provider.final_request_had_tools)

    async def test_control_tool_adds_results_for_every_declared_call(self) -> None:
        provider = ControlBatchProvider()
        counting_tool = CountingTool()
        result = await AgentCore(provider).run(
            AgentRequest(
                prompt="edit something",
                tool_registry=ToolRegistry(
                    [RequestUserInputTool(), counting_tool]
                ),
            )
        )

        self.assertEqual("waiting_for_input", result.status)
        self.assertEqual(0, counting_tool.calls)
        assistant_call = next(
            message for message in result.history if message.tool_calls
        )
        tool_results = [
            message
            for message in result.history
            if message.role == "tool"
        ]
        self.assertEqual(2, len(assistant_call.tool_calls))
        self.assertEqual(["ask", "must_skip"], [item.tool_call_id for item in tool_results])
        skipped_payload = json.loads(tool_results[-1].content)
        self.assertFalse(skipped_payload["ok"])
        self.assertIn("skipped", skipped_payload["error"])

    async def test_parallel_safe_tools_run_concurrently_but_keep_model_order(self) -> None:
        tool = ParallelProbeTool()
        result = await AgentCore(ParallelThenDoneProvider()).run(
            AgentRequest(
                prompt="inspect both",
                tool_registry=ToolRegistry([tool]),
                max_parallel_tools=2,
            )
        )

        tool_results = [message for message in result.history if message.role == "tool"]
        self.assertEqual(2, tool.max_active)
        self.assertEqual(
            ["first", "second"],
            [message.tool_call_id for message in tool_results],
        )
