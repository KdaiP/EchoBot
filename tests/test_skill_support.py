from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from echobot import AgentCore, LLMMessage, SkillRegistry, ToolRegistry
from echobot.models import LLMResponse, LLMTool, ToolCall
from echobot.providers.base import LLMProvider


def write_skill(
    directory: Path,
    *,
    name: str,
    description: str,
    body: str,
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        (
            "---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            "---\n\n"
            f"{body}\n"
        ),
        encoding="utf-8",
    )


def write_skill_resource(
    directory: Path,
    relative_path: str,
    content: str,
) -> None:
    target = directory / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


class RecordingProvider(LLMProvider):
    def __init__(self) -> None:
        self.last_messages: list[LLMMessage] = []
        self.last_tools: list[LLMTool] = []

    async def generate(
        self,
        messages,
        *,
        tools=None,
        tool_choice=None,
        temperature=None,
        max_tokens=None,
    ) -> LLMResponse:
        del tool_choice, temperature, max_tokens
        self.last_messages = list(messages)
        self.last_tools = list(tools or [])
        return LLMResponse(
            message=LLMMessage(role="assistant", content="ok"),
            model="fake-model",
            finish_reason="stop",
        )


class SkillToolProvider(LLMProvider):
    def __init__(self) -> None:
        self.calls: list[list[LLMMessage]] = []
        self.tools: list[list[LLMTool]] = []

    async def generate(
        self,
        messages,
        *,
        tools=None,
        tool_choice=None,
        temperature=None,
        max_tokens=None,
    ) -> LLMResponse:
        del tool_choice, temperature, max_tokens
        self.calls.append(list(messages))
        self.tools.append(list(tools or []))

        if len(self.calls) == 1:
            tool_calls = [
                ToolCall(
                    id="call_1",
                    name="activate_skill",
                    arguments='{"name": "demo-skill"}',
                )
            ]
            return LLMResponse(
                message=LLMMessage(role="assistant", content="", tool_calls=tool_calls),
                model="fake-model",
                finish_reason="tool_calls",
                tool_calls=tool_calls,
            )

        return LLMResponse(
            message=LLMMessage(role="assistant", content="done"),
            model="fake-model",
            finish_reason="stop",
        )


class SkillRegistryTests(unittest.TestCase):
    def test_discover_prefers_project_skill_when_names_collide(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            write_skill(
                project_root / "skills" / "demo-skill",
                name="demo-skill",
                description="project copy",
                body="project body",
            )
            write_skill(
                project_root / "echobot" / "skills" / "demo-skill",
                name="demo-skill",
                description="built-in copy",
                body="built-in body",
            )

            registry = SkillRegistry.discover(
                project_root=project_root,
                include_user_roots=False,
            )

            skill = registry.get("demo-skill")
            self.assertIsNotNone(skill)
            assert skill is not None
            self.assertEqual("project copy", skill.description)
            self.assertTrue(any("Duplicate skill ignored" in item for item in registry.warnings))
            self.assertTrue(
                any("already loaded from" in item for item in registry.warnings)
            )

    def test_explicit_activation_detects_skill_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            write_skill(
                project_root / "skills" / "demo-skill",
                name="demo-skill",
                description="demo description",
                body="demo body",
            )

            registry = SkillRegistry.discover(
                project_root=project_root,
                include_user_roots=False,
            )

            names = registry.explicit_skill_names(
                "Please use /demo-skill now and $demo-skill again."
            )
            messages = registry.build_explicit_activation_messages("Use /demo-skill")

            self.assertEqual(["demo-skill"], names)
            self.assertEqual(1, len(messages))
            self.assertIn('<active_skill name="demo-skill">', messages[0])
            self.assertIn("demo body", messages[0])

    def test_explicit_activation_skips_already_active_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            write_skill(
                project_root / "skills" / "demo-skill",
                name="demo-skill",
                description="demo description",
                body="demo body",
            )
            registry = SkillRegistry.discover(
                project_root=project_root,
                include_user_roots=False,
            )

            messages = registry.build_explicit_activation_messages(
                "Use /demo-skill",
                active_skill_names=["demo-skill"],
            )

            self.assertEqual([], messages)

    def test_discover_supports_utf8_bom_skill_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            skill_dir = project_root / "echobot" / "skills" / "weather"
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                (
                    "---\n"
                    "name: weather\n"
                    "description: Weather skill\n"
                    "---\n\n"
                    "Use wttr.in for quick forecasts.\n"
                ),
                encoding="utf-8-sig",
            )

            registry = SkillRegistry.discover(
                project_root=project_root,
                include_user_roots=False,
            )

            self.assertIn("weather", registry.names())
            self.assertEqual([], registry.warnings)

    def test_discover_supports_multiline_description(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            skill_dir = project_root / "skills" / "weather"
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                (
                    "---\n"
                    "name: weather\n"
                    "description: >\n"
                    "  Get weather forecasts.\n"
                    "  Trigger when the user asks about rain or temperature.\n"
                    "---\n\n"
                    "Use wttr.in first.\n"
                ),
                encoding="utf-8",
            )

            registry = SkillRegistry.discover(
                project_root=project_root,
                include_user_roots=False,
            )

            skill = registry.get("weather")
            self.assertIsNotNone(skill)
            assert skill is not None
            self.assertEqual(
                "Get weather forecasts. Trigger when the user asks about rain or temperature.",
                skill.description,
            )

    def test_discover_rejects_multiline_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            skill_dir = project_root / "skills" / "weather"
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                (
                    "---\n"
                    "name: >\n"
                    "  weather\n"
                    "description: Weather skill\n"
                    "---\n\n"
                    "Use wttr.in first.\n"
                ),
                encoding="utf-8",
            )

            registry = SkillRegistry.discover(
                project_root=project_root,
                include_user_roots=False,
            )

            self.assertEqual([], registry.names())
            self.assertTrue(
                any(
                    "name must be a single-line value" in item
                    for item in registry.warnings
                )
            )

    def test_active_skill_names_from_history_supports_new_and_legacy_messages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            write_skill(
                project_root / "skills" / "demo-skill",
                name="demo-skill",
                description="demo description",
                body="demo body",
            )
            registry = SkillRegistry.discover(
                project_root=project_root,
                include_user_roots=False,
            )
            history = [
                LLMMessage(
                    role="system",
                    content=(
                        "The user explicitly activated this skill.\n"
                        '<active_skill name="demo-skill">\n'
                        "demo body\n"
                        "</active_skill>"
                    ),
                ),
                LLMMessage(
                    role="tool",
                    content=json.dumps(
                        {
                            "ok": True,
                            "result": {
                                "name": "demo-skill",
                                "directory": "skills/demo-skill",
                                "content": "Skill name: demo-skill",
                            },
                            "ensure_ascii": False,
                        },
                        ensure_ascii=False,
                    ),
                ),
            ]

            active_skill_names = registry.active_skill_names_from_history(history)

            self.assertEqual(["demo-skill"], active_skill_names)

    def test_discover_finds_skills_via_meipass(self) -> None:
        with tempfile.TemporaryDirectory() as meipass_dir, \
                tempfile.TemporaryDirectory() as project_dir:
            meipass = Path(meipass_dir)
            project_root = Path(project_dir)
            write_skill(
                meipass / "echobot" / "skills" / "bundled-skill",
                name="bundled-skill",
                description="from meipass",
                body="bundled body",
            )

            original = getattr(sys, "_MEIPASS", None)
            sys._MEIPASS = str(meipass)
            try:
                registry = SkillRegistry.discover(
                    project_root=project_root,
                    include_user_roots=False,
                )
            finally:
                if original is None:
                    del sys._MEIPASS
                else:
                    sys._MEIPASS = original

            self.assertIn("bundled-skill", registry.names())
            skill = registry.get("bundled-skill")
            assert skill is not None
            self.assertEqual("from meipass", skill.description)


class SkillToolRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_skill_tools_load_resources_lazily(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            skill_dir = project_root / "skills" / "demo-skill"
            write_skill(
                skill_dir,
                name="demo-skill",
                description="demo description",
                body="demo body",
            )
            write_skill_resource(
                skill_dir,
                "references/guide.md",
                "guide content",
            )
            write_skill_resource(
                skill_dir,
                "scripts/run.py",
                "print('demo')\n",
            )
            registry = SkillRegistry.discover(
                project_root=project_root,
                include_user_roots=False,
            )
            tool_registry = ToolRegistry(registry.create_tools())

            before_activation = await tool_registry.execute(
                ToolCall(
                    id="call_1",
                    name="read_skill_resource",
                    arguments='{"name": "demo-skill", "path": "references/guide.md"}',
                )
            )
            activation = await tool_registry.execute(
                ToolCall(
                    id="call_2",
                    name="activate_skill",
                    arguments='{"name": "demo-skill"}',
                )
            )
            listed = await tool_registry.execute(
                ToolCall(
                    id="call_3",
                    name="list_skill_resources",
                    arguments='{"name": "demo-skill", "folder": "references"}',
                )
            )
            resource = await tool_registry.execute(
                ToolCall(
                    id="call_4",
                    name="read_skill_resource",
                    arguments='{"name": "demo-skill", "path": "references/guide.md"}',
                )
            )

            before_payload = json.loads(before_activation.content)
            activation_payload = json.loads(activation.content)
            list_payload = json.loads(listed.content)
            resource_payload = json.loads(resource.content)

            self.assertFalse(before_payload["ok"])
            self.assertIn("Activate it first", before_payload["error"])
            self.assertTrue(activation_payload["ok"])
            self.assertEqual("skill_activation", activation_payload["result"]["kind"])
            self.assertIn("references: 1 file", activation_payload["result"]["resource_summary"])
            self.assertNotIn("references/guide.md", activation_payload["result"]["content"])
            self.assertEqual(["references/guide.md"], list_payload["result"]["entries"])
            self.assertEqual("guide content", resource_payload["result"]["content"])


class SkillAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_ask_with_skills_adds_catalog_and_lazy_skill_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            skill_dir = project_root / "skills" / "demo-skill"
            write_skill(
                skill_dir,
                name="demo-skill",
                description="demo description",
                body="demo body",
            )
            write_skill_resource(
                skill_dir,
                "references/guide.md",
                "guide content",
            )
            registry = SkillRegistry.discover(
                project_root=project_root,
                include_user_roots=False,
            )

            provider = SkillToolProvider()
            agent = AgentCore(provider)

            result = await agent.ask_with_skills("help me", skill_registry=registry)

            self.assertEqual("done", result.response.message.content)
            self.assertEqual(2, len(provider.calls))
            self.assertEqual("system", provider.calls[0][0].role)
            self.assertIn("<available_skills>", provider.calls[0][0].content)
            self.assertIn("demo-skill", provider.calls[0][0].content)
            tool_names = [tool.name for tool in provider.tools[0]]
            self.assertEqual(
                ["activate_skill", "list_skill_resources", "read_skill_resource"],
                tool_names,
            )
            tool_payload = json.loads(provider.calls[1][-1].content)
            self.assertEqual("demo-skill", tool_payload["result"]["name"])
            self.assertEqual("skill_activation", tool_payload["result"]["kind"])
            self.assertIn("demo body", tool_payload["result"]["content"])
            self.assertNotIn("references/guide.md", tool_payload["result"]["content"])

    async def test_ask_with_skills_activates_explicit_skill_without_tool_call(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            skill_dir = project_root / "skills" / "demo-skill"
            write_skill(
                skill_dir,
                name="demo-skill",
                description="demo description",
                body="demo body",
            )
            write_skill_resource(
                skill_dir,
                "references/guide.md",
                "guide content",
            )
            registry = SkillRegistry.discover(
                project_root=project_root,
                include_user_roots=False,
            )

            provider = RecordingProvider()
            agent = AgentCore(provider)

            response = await agent.ask_with_skills(
                "Please follow /demo-skill for this task.",
                skill_registry=registry,
            )

            self.assertEqual("ok", response.response.message.content)
            system_messages = [
                item.content for item in provider.last_messages if item.role == "system"
            ]
            self.assertTrue(
                any("The user explicitly activated this skill." in item for item in system_messages)
            )
            self.assertTrue(
                any('<active_skill name="demo-skill">' in item for item in system_messages)
            )
            self.assertTrue(any("demo body" in item for item in system_messages))
            self.assertFalse(any("references/guide.md" in item for item in system_messages))
