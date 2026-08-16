from __future__ import annotations

import os
import platform
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from echobot.agent import AgentCore, AgentRequest
from echobot import build_default_system_prompt
from echobot.config import load_env_file
from echobot.memory import (
    MemoryPreparationResult,
    ReMeLightSettings,
    ReMeLightSupport,
    _configure_reme_internal_console_output,
    _agentscope_messages_to_llm,
    _llm_messages_to_agentscope,
)
from echobot.models import LLMMessage, LLMResponse, LLMTool, ToolCall
from echobot.providers.base import LLMProvider
from echobot.providers.openai_compatible import OpenAICompatibleSettings
from echobot.skill_support import SkillRegistry
from echobot.tools import BaseTool, ToolExecutionOutput, ToolRegistry


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


class FakeReMeLight:
    instances: list["FakeReMeLight"] = []

    def __init__(self, **kwargs) -> None:
        self.init_kwargs = kwargs
        self.compact_calls = []
        self.pre_reasoning_kwargs = {}
        self.summary_task_calls = []
        FakeReMeLight.instances.append(self)

    async def start(self) -> None:
        return None

    async def compact_tool_result(self, messages, **kwargs):
        self.compact_calls.append(
            {
                "messages": list(messages),
                "kwargs": kwargs,
            }
        )
        return list(messages)

    async def pre_reasoning_hook(self, **kwargs):
        self.pre_reasoning_kwargs = kwargs
        return kwargs["messages"], "summary"

    def add_async_summary_task(self, **kwargs) -> None:
        self.summary_task_calls.append(kwargs)

    async def await_summary_tasks(self) -> str:
        return ""

    async def close(self) -> bool:
        return True


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


class ImageEchoTool(BaseTool):
    name = "image_echo_tool"
    description = "Return an image for the next model call."
    parameters = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    async def run(self, arguments: dict[str, str]) -> ToolExecutionOutput:
        del arguments
        return ToolExecutionOutput(
            data={"status": "loaded"},
            promoted_image_urls=[
                {
                    "attachment_id": "img_demo",
                    "url": "attachment://img_demo",
                    "preview_url": "/api/attachments/img_demo/content",
                }
            ],
        )


class UserFileTool(BaseTool):
    name = "user_file_tool"
    description = "Return a file that should be sent to the user."
    parameters = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    async def run(self, arguments: dict[str, str]) -> ToolExecutionOutput:
        del arguments
        return ToolExecutionOutput(
            data={"status": "queued"},
            outbound_content_blocks=[
                {
                    "type": "file_attachment",
                    "file_attachment": {
                        "attachment_id": "file_demo",
                        "name": "report.txt",
                        "download_url": "/api/attachments/file_demo/content",
                        "workspace_path": "report.txt",
                        "content_type": "text/plain",
                        "size_bytes": 5,
                    },
                }
            ],
        )


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


class FakeImageToolProvider(LLMProvider):
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
                    id="call_image",
                    name="image_echo_tool",
                    arguments="{}",
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


class FakeUserFileToolProvider(LLMProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def generate(
        self,
        messages,
        *,
        tools=None,
        tool_choice=None,
        temperature=None,
        max_tokens=None,
    ) -> LLMResponse:
        del messages, tools, tool_choice, temperature, max_tokens
        self.calls += 1
        if self.calls == 1:
            tool_calls = [
                ToolCall(
                    id="call_file",
                    name="user_file_tool",
                    arguments="{}",
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


class FakeUserInputToolProvider(LLMProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def generate(
        self,
        messages,
        *,
        tools=None,
        tool_choice=None,
        temperature=None,
        max_tokens=None,
    ) -> LLMResponse:
        del messages, tools, tool_choice, temperature, max_tokens
        self.calls += 1
        tool_calls = [
            ToolCall(
                id="call_user_input",
                name="request_user_input",
                arguments=(
                    '{"prompt":"请确认要修改哪个文件。",'
                    '"choices":["修改 src/app.py","修改 src/api.py"]}'
                ),
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


class AgentCoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_builds_one_standard_request_context(self) -> None:
        provider = FakeProvider()
        agent = AgentCore(provider, system_prompt="You are helpful.")
        history = [LLMMessage(role="assistant", content="hello")]

        result = await agent.run(AgentRequest(prompt="world", history=history))

        self.assertEqual("ok", result.response.message.content)
        self.assertEqual(3, len(provider.last_messages))
        self.assertEqual("system", provider.last_messages[0].role)
        self.assertEqual("assistant", provider.last_messages[1].role)
        self.assertEqual("user", provider.last_messages[2].role)

    async def test_run_supports_text_and_image_user_content(self) -> None:
        provider = FakeProvider()
        agent = AgentCore(provider, system_prompt="You are helpful.")

        await agent.run(
            AgentRequest(
                prompt="describe this",
                image_urls=[
                {
                    "attachment_id": "img_demo",
                    "url": "attachment://img_demo",
                    "preview_url": "/api/attachments/img_demo/content",
                }
                ],
            )
        )

        user_message = provider.last_messages[-1]
        self.assertEqual("user", user_message.role)
        self.assertIsInstance(user_message.content, list)
        self.assertEqual("text", user_message.content[0]["type"])
        self.assertEqual("describe this", user_message.content[0]["text"])
        self.assertEqual("image_url", user_message.content[1]["type"])
        self.assertEqual(
            "attachment://img_demo",
            user_message.content[1]["image_url"]["url"],
        )
        self.assertEqual(
            "/api/attachments/img_demo/content",
            user_message.content[1]["image_url"]["preview_url"],
        )

    async def test_run_uses_compacted_history_and_summary(self) -> None:
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

        result = await agent.run(AgentRequest(prompt="world", history=history))

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

    async def test_run_compacts_before_each_model_call(self) -> None:
        provider = FakeToolProvider()
        memory_support = FakeMemorySupport()
        agent = AgentCore(provider, memory_support=memory_support)
        registry = ToolRegistry([EchoTool()])

        result = await agent.run(
            AgentRequest(
                prompt="test",
                tool_registry=registry,
                history=[LLMMessage(role="assistant", content="older")],
            )
        )

        self.assertEqual("done", result.response.message.content)
        self.assertEqual(2, memory_support.prepare_count)
        self.assertEqual("summary-2", result.compressed_summary)
        self.assertEqual("test", memory_support.remembered_messages[0].content)
        self.assertEqual("done", memory_support.remembered_messages[-1].content)

    async def test_run_places_transient_system_messages_before_history(self) -> None:
        provider = FakeProvider()
        agent = AgentCore(provider, system_prompt="You are helpful.")
        history = [LLMMessage(role="assistant", content="older")]

        await agent.run(
            AgentRequest(
                prompt="world",
                history=history,
                transient_system_messages=["handoff"],
            )
        )

        self.assertEqual(
            ["system", "system", "assistant", "user"],
            [message.role for message in provider.last_messages],
        )
        self.assertEqual("handoff", provider.last_messages[1].content)

    async def test_run_reuses_transient_system_messages_during_tool_loop(self) -> None:
        provider = FakeToolProvider()
        agent = AgentCore(provider)
        registry = ToolRegistry([EchoTool()])

        await agent.run(
            AgentRequest(
                prompt="test",
                tool_registry=registry,
                history=[LLMMessage(role="assistant", content="older")],
                transient_system_messages=["handoff"],
            )
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

    async def test_run_promotes_tool_images_into_next_user_message(self) -> None:
        provider = FakeImageToolProvider()
        agent = AgentCore(provider)
        registry = ToolRegistry([ImageEchoTool()])

        result = await agent.run(
            AgentRequest(prompt="inspect this", tool_registry=registry)
        )

        self.assertEqual("done", result.response.message.content)
        self.assertEqual(2, provider.calls)
        second_call = provider.seen_messages[1]
        self.assertEqual(
            ["user", "assistant", "tool", "user"],
            [message.role for message in second_call],
        )
        promoted_user_message = second_call[-1]
        self.assertIsInstance(promoted_user_message.content, list)
        self.assertEqual("text", promoted_user_message.content[0]["type"])
        self.assertIn("image_echo_tool", promoted_user_message.content[0]["text"])
        self.assertEqual("image_url", promoted_user_message.content[1]["type"])
        self.assertEqual(
            "attachment://img_demo",
            promoted_user_message.content[1]["image_url"]["url"],
        )
        self.assertEqual(
            "/api/attachments/img_demo/content",
            promoted_user_message.content[1]["image_url"]["preview_url"],
        )

    async def test_run_collects_outbound_content_blocks(self) -> None:
        provider = FakeUserFileToolProvider()
        agent = AgentCore(provider)
        registry = ToolRegistry([UserFileTool()])

        result = await agent.run(
            AgentRequest(prompt="send the file", tool_registry=registry)
        )

        self.assertEqual("done", result.response.message.content)
        self.assertEqual(1, len(result.outbound_content_blocks))
        self.assertEqual("file_attachment", result.outbound_content_blocks[0]["type"])
        self.assertEqual(
            "report.txt",
            result.outbound_content_blocks[0]["file_attachment"]["name"],
        )

    async def test_run_combines_skills_and_regular_tools(self) -> None:
        provider = FakeUserFileToolProvider()
        agent = AgentCore(provider)
        skill_registry = SkillRegistry()
        tool_registry = ToolRegistry([UserFileTool()])

        result = await agent.run(
            AgentRequest(
                prompt="send the file",
                skill_registry=skill_registry,
                tool_registry=tool_registry,
            )
        )

        self.assertEqual("done", result.response.message.content)
        self.assertEqual(1, len(result.outbound_content_blocks))
        self.assertEqual("file_attachment", result.outbound_content_blocks[0]["type"])
        self.assertEqual(
            "report.txt",
            result.outbound_content_blocks[0]["file_attachment"]["name"],
        )

    async def test_request_user_input_stops_tool_loop_and_sets_waiting_status(self) -> None:
        from echobot.tools import RequestUserInputTool

        provider = FakeUserInputToolProvider()
        agent = AgentCore(provider)
        registry = ToolRegistry([RequestUserInputTool()])

        result = await agent.run(
            AgentRequest(
                prompt="帮我继续改代码",
                tool_registry=registry,
            )
        )

        self.assertEqual(1, provider.calls)
        self.assertEqual("waiting_for_input", result.status)
        self.assertEqual(
            "请确认要修改哪个文件。",
            result.pending_user_input["prompt"],
        )
        self.assertIn("请确认要修改哪个文件。", result.response.message.content)

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
            workspace = Path(temp_dir)

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
            workspace = Path(temp_dir)
            memory_workspace = workspace / ".custom-memory"

            prompt = build_default_system_prompt(
                workspace,
                enable_project_memory=True,
                memory_workspace=memory_workspace,
            )

            self.assertIn(str(memory_workspace), prompt)
            self.assertIn(str(memory_workspace / "MEMORY.md"), prompt)

    def test_build_default_system_prompt_omits_view_image_when_vision_is_disabled(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)

            prompt = build_default_system_prompt(
                workspace,
                supports_image_input=False,
            )

            self.assertNotIn("`view_image`", prompt)
            self.assertIn("`send_image_to_user`", prompt)


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


class ReMeLightSupportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        FakeReMeLight.instances.clear()

    async def test_ensure_started_uses_latest_reme_light_constructor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._build_settings(Path(temp_dir))

            with patch("echobot.memory.support.ReMeLight", FakeReMeLight):
                support = ReMeLightSupport(settings)
                await support.ensure_started()

            init_kwargs = FakeReMeLight.instances[0].init_kwargs
            self.assertNotIn("tool_result_threshold", init_kwargs)
            self.assertNotIn("retention_days", init_kwargs)
            self.assertEqual(str(settings.working_dir), init_kwargs["working_dir"])

    async def test_compact_history_passes_tool_result_settings_to_reme(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._build_settings(
                Path(temp_dir),
                tool_result_threshold=1234,
                retention_days=9,
                tool_result_compact_keep_n=1,
            )

            with patch("echobot.memory.support.ReMeLight", FakeReMeLight):
                support = ReMeLightSupport(settings)
                result = await support.compact_history(
                    [
                        LLMMessage(role="user", content="one"),
                        LLMMessage(role="assistant", content="two"),
                        LLMMessage(role="user", content="three"),
                    ],
                    system_prompt="system",
                    compressed_summary="",
                )

            reme = FakeReMeLight.instances[0]
            self.assertEqual("summary", result.compressed_summary)
            self.assertEqual(1, len(reme.compact_calls))
            self.assertEqual(2, len(reme.compact_calls[0]["messages"]))
            self.assertEqual(
                {
                    "old_max_bytes": 1234,
                    "recent_max_bytes": 1234,
                    "retention_days": 9,
                    "recent_n": 0,
                },
                reme.compact_calls[0]["kwargs"],
            )
            self.assertFalse(
                reme.pre_reasoning_kwargs["enable_tool_result_compact"],
            )

    @staticmethod
    def _build_settings(
        working_dir: Path,
        **overrides,
    ) -> ReMeLightSettings:
        values = {
            "working_dir": working_dir,
            "llm_api_key": "test-key",
            "llm_base_url": "https://example.com/v1",
            "llm_model": "test-model",
        }
        values.update(overrides)
        return ReMeLightSettings(**values)


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
                reasoning_content="I need to search memory first.",
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
        self.assertEqual(
            "I need to search memory first.",
            round_tripped[0].reasoning_content,
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
