from __future__ import annotations

import os
import platform
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from echobot import build_default_system_prompt
from echobot.agent import AgentCore, AgentRunResult
from echobot.config import load_env_file
from echobot.memory import (
    MemoryPreparationResult,
    ReMeLightSettings,
    _configure_reme_internal_console_output,
    _agentscope_messages_to_llm,
    _llm_messages_to_agentscope,
)
from echobot.models import LLMMessage, LLMResponse, LLMTool, LLMUsage, ToolCall
from echobot.providers.base import LLMProvider
from echobot.providers.openai_compatible import (
    OpenAICompatibleProvider,
    OpenAICompatibleSettings,
)
from echobot.runtime.session_runner import SessionAgentRunner
from echobot.runtime.sessions import SessionStore
from echobot.runtime.turns import run_agent_turn
from echobot.tools import BaseTool, ToolRegistry


class FakeProvider(LLMProvider):
    def __init__(self) -> None:
        self.last_messages: list[LLMMessage] = []
        self.last_tools: list[LLMTool] | None = None

    async def generate(
        self,
        messages,
        *,
        tools=None,
        tool_choice=None,
        temperature=None,
        max_tokens=None,
    ) -> LLMResponse:
        self.last_messages = list(messages)
        self.last_tools = list(tools) if tools else None
        return LLMResponse(
            message=LLMMessage(role="assistant", content="ok"),
            model="fake-model",
        )


class FakeMemorySupport:
    def __init__(self) -> None:
        self.prepare_count = 0
        self.remembered_messages: list[LLMMessage] = []

    async def compact_history(
        self,
        messages,
        *,
        system_prompt: str,
        compressed_summary: str,
    ) -> MemoryPreparationResult:
        del system_prompt, compressed_summary
        self.prepare_count += 1
        kept_messages = list(messages[-2:])
        return MemoryPreparationResult(
            messages=kept_messages,
            compressed_summary=f"summary-{self.prepare_count}",
        )

    async def remember_turn(self, messages) -> None:
        self.remembered_messages = list(messages)

    def build_summary_message(self, compressed_summary: str) -> str:
        return f"summary::{compressed_summary}" if compressed_summary else ""


class EchoTool(BaseTool):
    name = "echo_tool"
    description = "Return the same text."
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
        },
        "required": ["text"],
        "additionalProperties": False,
    }

    async def run(self, arguments: dict[str, str]) -> dict[str, str]:
        return {"echo": arguments["text"]}


class FakeToolProvider(LLMProvider):
    def __init__(self) -> None:
        self.calls = 0
        self.seen_messages: list[list[LLMMessage]] = []

    async def generate(
        self,
        messages,
        *,
        tools=None,
        tool_choice=None,
        temperature=None,
        max_tokens=None,
    ) -> LLMResponse:
        del tools, tool_choice, temperature, max_tokens
        self.calls += 1
        self.seen_messages.append(list(messages))
        if self.calls == 1:
            tool_calls = [
                ToolCall(
                    id="call_1",
                    name="echo_tool",
                    arguments='{"text": "hello"}',
                )
            ]
            return LLMResponse(
                message=LLMMessage(
                    role="assistant",
                    content="",
                    tool_calls=tool_calls,
                ),
                model="fake-model",
                finish_reason="tool_calls",
                tool_calls=tool_calls,
            )

        return LLMResponse(
            message=LLMMessage(role="assistant", content="done"),
            model="fake-model",
            finish_reason="stop",
        )


class MaxStepsRecordingAgent(AgentCore):
    def __init__(self) -> None:
        super().__init__(FakeProvider())
        self.max_steps_seen: int | None = None

    async def ask_with_tools(
        self,
        user_input: str,
        *,
        tool_registry: ToolRegistry,
        image_urls=None,
        history=None,
        compressed_summary: str = "",
        tool_choice=None,
        extra_system_messages=None,
        transient_system_messages=None,
        temperature=None,
        max_tokens=None,
        max_steps: int = 50,
        trace_callback=None,
    ) -> AgentRunResult:
        del (
            tool_registry,
            image_urls,
            history,
            tool_choice,
            extra_system_messages,
            transient_system_messages,
            temperature,
            max_tokens,
            trace_callback,
        )
        self.max_steps_seen = max_steps
        user_message = LLMMessage(role="user", content=user_input)
        response = LLMResponse(
            message=LLMMessage(role="assistant", content="ok"),
            model="fake-model",
        )
        return AgentRunResult(
            response=response,
            new_messages=[user_message, response.message],
            history=[user_message, response.message],
            steps=1,
            compressed_summary=compressed_summary,
        )


class AgentCoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_ask_reuses_same_core_message_building(self) -> None:
        provider = FakeProvider()
        agent = AgentCore(provider, system_prompt="You are helpful.")
        history = [LLMMessage(role="assistant", content="hello")]

        response = await agent.ask("world", history=history)

        self.assertEqual("ok", response.message.content)
        self.assertEqual(3, len(provider.last_messages))
        self.assertEqual("system", provider.last_messages[0].role)
        self.assertEqual("assistant", provider.last_messages[1].role)
        self.assertEqual("user", provider.last_messages[2].role)

    async def test_ask_supports_text_and_image_user_content(self) -> None:
        provider = FakeProvider()
        agent = AgentCore(provider, system_prompt="You are helpful.")

        await agent.ask(
            "describe this",
            image_urls=["data:image/png;base64,AAAA"],
        )

        user_message = provider.last_messages[-1]
        self.assertEqual("user", user_message.role)
        self.assertIsInstance(user_message.content, list)
        self.assertEqual("text", user_message.content[0]["type"])
        self.assertEqual("describe this", user_message.content[0]["text"])
        self.assertEqual("image_url", user_message.content[1]["type"])
        self.assertEqual(
            "data:image/png;base64,AAAA",
            user_message.content[1]["image_url"]["url"],
        )

    async def test_ask_with_memory_uses_compacted_history_and_summary(self) -> None:
        provider = FakeProvider()
        memory_support = FakeMemorySupport()
        agent = AgentCore(
            provider,
            system_prompt="You are helpful.",
            memory_support=memory_support,
        )
        history = [
            LLMMessage(role="assistant", content="first"),
            LLMMessage(role="assistant", content="second"),
        ]

        result = await agent.ask_with_memory("world", history=history)

        self.assertEqual("ok", result.response.message.content)
        self.assertEqual("summary-1", result.compressed_summary)
        self.assertEqual(1, memory_support.prepare_count)
        self.assertEqual(3, len(result.history))
        self.assertEqual(4, len(provider.last_messages))
        self.assertEqual("system", provider.last_messages[0].role)
        self.assertEqual("system", provider.last_messages[1].role)
        self.assertEqual("summary::summary-1", provider.last_messages[1].content)
        self.assertEqual("assistant", provider.last_messages[2].role)
        self.assertEqual("user", provider.last_messages[3].role)
        self.assertEqual("world", memory_support.remembered_messages[0].content)
        self.assertEqual("ok", memory_support.remembered_messages[1].content)

    async def test_ask_with_tools_compacts_before_each_model_call(self) -> None:
        provider = FakeToolProvider()
        memory_support = FakeMemorySupport()
        agent = AgentCore(provider, memory_support=memory_support)
        registry = ToolRegistry([EchoTool()])

        result = await agent.ask_with_tools(
            "test",
            tool_registry=registry,
            history=[LLMMessage(role="assistant", content="older")],
        )

        self.assertEqual("done", result.response.message.content)
        self.assertEqual(2, memory_support.prepare_count)
        self.assertEqual("summary-2", result.compressed_summary)
        self.assertEqual("test", memory_support.remembered_messages[0].content)
        self.assertEqual("done", memory_support.remembered_messages[-1].content)

    async def test_ask_places_transient_system_messages_before_history(self) -> None:
        provider = FakeProvider()
        agent = AgentCore(provider, system_prompt="You are helpful.")
        history = [LLMMessage(role="assistant", content="older")]

        await agent.ask(
            "world",
            history=history,
            transient_system_messages=["handoff"],
        )

        self.assertEqual(
            ["system", "system", "assistant", "user"],
            [message.role for message in provider.last_messages],
        )
        self.assertEqual("handoff", provider.last_messages[1].content)

    async def test_ask_with_tools_reuses_transient_system_messages_during_tool_loop(self) -> None:
        provider = FakeToolProvider()
        agent = AgentCore(provider)
        registry = ToolRegistry([EchoTool()])

        await agent.ask_with_tools(
            "test",
            tool_registry=registry,
            history=[LLMMessage(role="assistant", content="older")],
            transient_system_messages=["handoff"],
        )

        self.assertEqual(2, len(provider.seen_messages))
        first_call = provider.seen_messages[0]
        second_call = provider.seen_messages[1]

        self.assertEqual(
            ["system", "assistant", "user"],
            [message.role for message in first_call],
        )
        self.assertEqual("handoff", first_call[0].content)
        self.assertEqual(
            ["system", "assistant", "user", "assistant", "tool"],
            [message.role for message in second_call],
        )
        self.assertEqual("handoff", second_call[0].content)


class RunAgentTurnTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_agent_turn_uses_default_max_steps_of_50(self) -> None:
        agent = MaxStepsRecordingAgent()

        result = await run_agent_turn(
            agent,
            "hello",
            [],
            compressed_summary="",
            skill_registry=None,
            tool_registry=ToolRegistry(),
            temperature=None,
            max_tokens=None,
        )

        self.assertEqual("ok", result.response.message.content)
        self.assertEqual(50, agent.max_steps_seen)


class SessionAgentRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_prompt_uses_configured_default_max_steps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_store = SessionStore(Path(temp_dir) / "sessions")
            agent = MaxStepsRecordingAgent()
            runner = SessionAgentRunner(
                agent,
                session_store,
                tool_registry_factory=lambda *_args: ToolRegistry(),
                default_max_steps=77,
            )

            await runner.run_prompt("demo", "hello")

            self.assertEqual(77, agent.max_steps_seen)


class SystemPromptTests(unittest.TestCase):
    def test_build_default_system_prompt_includes_environment_workspace_and_agents(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "AGENTS.md").write_text(
                "# AGENTS.md\n\nProject-specific instructions.\n",
                encoding="utf-8",
            )

            prompt = build_default_system_prompt(workspace)

            self.assertIn("# EchoBot", prompt)
            self.assertIn("## Environment", prompt)
            self.assertIn(f"- Workspace: {workspace.resolve()}", prompt)
            self.assertIn(
                f"- Session store: {workspace.resolve() / '.echobot' / 'sessions'}",
                prompt,
            )
            self.assertIn(platform.system(), prompt)
            self.assertIn(platform.python_version(), prompt)
            self.assertIn("Project-specific instructions.", prompt)

    def test_build_default_system_prompt_can_include_memory_section(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir).resolve()

            prompt = build_default_system_prompt(
                workspace,
                enable_project_memory=True,
            )

            self.assertIn("## Memory", prompt)
            self.assertIn(str(workspace / ".echobot" / "reme"), prompt)
            self.assertIn(str(workspace / ".echobot" / "reme" / "MEMORY.md"), prompt)
            self.assertIn("memory_search", prompt)

    def test_build_default_system_prompt_can_use_custom_memory_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir).resolve()
            memory_workspace = workspace / ".custom-memory"

            prompt = build_default_system_prompt(
                workspace,
                enable_project_memory=True,
                memory_workspace=memory_workspace,
            )

            self.assertIn(str(memory_workspace), prompt)
            self.assertIn(str(memory_workspace / "MEMORY.md"), prompt)


class ReMeLightSettingsTests(unittest.TestCase):
    def test_from_provider_settings_uses_hidden_runtime_directory_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            provider_settings = OpenAICompatibleSettings(
                api_key="test-key",
                model="test-model",
                base_url="https://example.com/v1",
            )

            with patch.dict(os.environ, {}, clear=True):
                settings = ReMeLightSettings.from_provider_settings(
                    workspace,
                    provider_settings,
                )

            self.assertEqual(
                workspace.resolve() / ".echobot" / "reme",
                settings.working_dir,
            )

    def test_from_provider_settings_resolves_relative_memory_workspace_from_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            provider_settings = OpenAICompatibleSettings(
                api_key="test-key",
                model="test-model",
                base_url="https://example.com/v1",
            )

            with patch.dict(
                os.environ,
                {"REME_WORKING_DIR": ".state/reme"},
                clear=True,
            ):
                settings = ReMeLightSettings.from_provider_settings(
                    workspace,
                    provider_settings,
                )

            self.assertEqual(
                workspace.resolve() / ".state" / "reme",
                settings.working_dir,
            )

    def test_from_provider_settings_disables_reme_console_output_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            provider_settings = OpenAICompatibleSettings(
                api_key="test-key",
                model="test-model",
                base_url="https://example.com/v1",
            )

            with patch.dict(os.environ, {}, clear=True):
                settings = ReMeLightSettings.from_provider_settings(
                    workspace,
                    provider_settings,
                )

            self.assertFalse(settings.console_output_enabled)

    def test_from_provider_settings_can_enable_reme_console_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            provider_settings = OpenAICompatibleSettings(
                api_key="test-key",
                model="test-model",
                base_url="https://example.com/v1",
            )

            with patch.dict(
                os.environ,
                {"REME_CONSOLE_OUTPUT": "true"},
                clear=True,
            ):
                settings = ReMeLightSettings.from_provider_settings(
                    workspace,
                    provider_settings,
                )

            self.assertTrue(settings.console_output_enabled)


class ReMeConsoleOutputPatchTests(unittest.TestCase):
    def test_patch_disables_console_output_for_reme_agents_only(self) -> None:
        class FakeReActAgent:
            def __init__(self, name: str) -> None:
                self.name = name
                self.console_output_enabled = True

            def set_console_output_enabled(self, enabled: bool) -> None:
                self.console_output_enabled = enabled

        _configure_reme_internal_console_output(False, react_agent_cls=FakeReActAgent)

        reme_agent = FakeReActAgent("reme_summarizer")
        normal_agent = FakeReActAgent("assistant")

        self.assertFalse(reme_agent.console_output_enabled)
        self.assertTrue(normal_agent.console_output_enabled)

    def test_patch_can_reenable_console_output_when_requested(self) -> None:
        class FakeReActAgent:
            def __init__(self, name: str) -> None:
                self.name = name
                self.console_output_enabled = True

            def set_console_output_enabled(self, enabled: bool) -> None:
                self.console_output_enabled = enabled

        _configure_reme_internal_console_output(False, react_agent_cls=FakeReActAgent)
        _configure_reme_internal_console_output(True, react_agent_cls=FakeReActAgent)

        reme_agent = FakeReActAgent("reme_compactor")

        self.assertTrue(reme_agent.console_output_enabled)


class ReMeLightMessageConversionTests(unittest.TestCase):
    def test_tool_result_round_trip_preserves_original_json_payload(self) -> None:
        messages = [
            LLMMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        name="memory_search",
                        arguments='{"query":"prior work"}',
                    )
                ],
            ),
            LLMMessage(
                role="tool",
                content=(
                    '{"ok":true,"result":{"query":"prior work","results":['
                    '{"path":"MEMORY.md","content":"saved note"}]}}'
                ),
                tool_call_id="call_1",
            ),
        ]

        converted = _llm_messages_to_agentscope(messages)
        self.assertEqual(
            messages[1].content,
            converted[1].content[0]["output"],
        )

        round_tripped = _agentscope_messages_to_llm(converted)
        self.assertEqual(messages[1].content, round_tripped[1].content)


class OpenAICompatibleProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        settings = OpenAICompatibleSettings(
            api_key="test-key",
            model="test-model",
            base_url="https://example.com/v1",
        )
        self.provider = OpenAICompatibleProvider(settings)

    def test_build_payload_keeps_optional_fields_simple(self) -> None:
        payload = self.provider._build_payload(
            messages=[LLMMessage(role="user", content="hi")],
            tools=[
                LLMTool(
                    name="search_weather",
                    description="Search weather",
                    parameters={"type": "object", "properties": {}},
                )
            ],
            tool_choice="auto",
            temperature=0.3,
            max_tokens=200,
        )

        self.assertEqual("test-model", payload["model"])
        self.assertEqual("hi", payload["messages"][0]["content"])
        self.assertEqual("search_weather", payload["tools"][0]["function"]["name"])
        self.assertEqual("auto", payload["tool_choice"])
        self.assertEqual(0.3, payload["temperature"])
        self.assertEqual(200, payload["max_tokens"])

    def test_parse_response_supports_tool_calls(self) -> None:
        response = self.provider._parse_response(
            {
                "model": "test-model",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "function": {
                                        "name": "search_weather",
                                        "arguments": '{"city":"Shanghai"}',
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "prompt_cache_hit_tokens": 6,
                    "prompt_cache_miss_tokens": 4,
                },
            }
        )

        self.assertEqual("assistant", response.message.role)
        self.assertEqual("tool_calls", response.finish_reason)
        self.assertEqual(1, len(response.tool_calls))
        self.assertEqual("search_weather", response.tool_calls[0].name)
        self.assertEqual(15, response.usage.total_tokens)
        self.assertEqual(6, response.usage.prompt_cache_hit_tokens)
        self.assertEqual(4, response.usage.prompt_cache_miss_tokens)

    def test_llm_usage_to_dict_keeps_cache_metrics(self) -> None:
        usage = LLMUsage(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            prompt_cache_hit_tokens=6,
            prompt_cache_miss_tokens=4,
        )

        self.assertEqual(
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "prompt_cache_hit_tokens": 6,
                "prompt_cache_miss_tokens": 4,
                "prompt_cache_hit_rate_percent": 60.0,
            },
            usage.to_dict(),
        )

    def test_llm_usage_hit_rate_percent_handles_zero_prompt_tokens(self) -> None:
        usage = LLMUsage()

        self.assertIsNone(usage.prompt_cache_hit_rate_percent())

    def test_llm_usage_from_openai_prompt_tokens_details(self) -> None:
        usage = LLMUsage.from_dict(
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "prompt_tokens_details": {
                    "cached_tokens": 6,
                },
            }
        )

        self.assertEqual(10, usage.prompt_tokens)
        self.assertEqual(5, usage.completion_tokens)
        self.assertEqual(15, usage.total_tokens)
        self.assertEqual(6, usage.prompt_cache_hit_tokens)
        self.assertEqual(4, usage.prompt_cache_miss_tokens)
        self.assertEqual(60.0, usage.prompt_cache_hit_rate_percent())

    def test_llm_usage_from_input_output_tokens_and_cached_tokens(self) -> None:
        usage = LLMUsage.from_dict(
            {
                "input_tokens": 12,
                "output_tokens": 3,
                "input_tokens_details": {
                    "cached_tokens": 8,
                },
            }
        )

        self.assertEqual(12, usage.prompt_tokens)
        self.assertEqual(3, usage.completion_tokens)
        self.assertEqual(15, usage.total_tokens)
        self.assertEqual(8, usage.prompt_cache_hit_tokens)
        self.assertEqual(4, usage.prompt_cache_miss_tokens)
        self.assertEqual(66.67, usage.prompt_cache_hit_rate_percent())

    def test_parse_stream_chunk_returns_delta_content(self) -> None:
        chunk = self.provider._parse_stream_chunk(
            '{"choices":[{"delta":{"content":"hello"}}]}'
        )

        self.assertEqual("hello", chunk)

    def test_parse_stream_chunk_ignores_non_content_events(self) -> None:
        chunk = self.provider._parse_stream_chunk(
            '{"choices":[{"delta":{"role":"assistant"},"finish_reason":null}]}'
        )

        self.assertEqual("", chunk)


class OpenAICompatibleSettingsTests(unittest.TestCase):
    def test_from_env_reads_required_values(self) -> None:
        settings = OpenAICompatibleSettings.from_env(
            env={
                "LLM_API_KEY": "test-key",
                "LLM_MODEL": "test-model",
                "LLM_BASE_URL": "https://example.com/v1",
                "LLM_TIMEOUT": "30",
            }
        )

        self.assertEqual("test-key", settings.api_key)
        self.assertEqual("test-model", settings.model)
        self.assertEqual("https://example.com/v1", settings.base_url)
        self.assertEqual(30.0, settings.timeout)

    def test_from_env_requires_api_key_and_model(self) -> None:
        with self.assertRaisesRegex(ValueError, "LLM_API_KEY"):
            OpenAICompatibleSettings.from_env(env={"LLM_MODEL": "test-model"})

    def test_from_env_validates_timeout(self) -> None:
        with self.assertRaisesRegex(ValueError, "LLM_TIMEOUT"):
            OpenAICompatibleSettings.from_env(
                env={
                    "LLM_API_KEY": "test-key",
                    "LLM_MODEL": "test-model",
                    "LLM_TIMEOUT": "not-a-number",
                }
            )


class LoadEnvFileTests(unittest.TestCase):
    def test_load_env_file_reads_key_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                'LLM_API_KEY=test-key\nLLM_MODEL="test-model"\n',
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                load_env_file(env_path)

                self.assertEqual("test-key", os.environ["LLM_API_KEY"])
                self.assertEqual("test-model", os.environ["LLM_MODEL"])

    def test_load_env_file_keeps_existing_value_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text("LLM_MODEL=new-model\n", encoding="utf-8")

            with patch.dict(os.environ, {"LLM_MODEL": "old-model"}, clear=True):
                load_env_file(env_path)

                self.assertEqual("old-model", os.environ["LLM_MODEL"])
