from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from echobot.channels.types import ChannelAddress
from echobot.commands.bindings import (
    CliCommandContext,
    GatewayCommandContext,
    dispatch_cli_command,
    dispatch_gateway_command,
)
from echobot.commands.dispatcher import (
    BoundTextCommand,
    CommandResult,
    dispatch_text_command,
)
from echobot.commands.help import parse_help_command
from echobot.commands.route_mode import (
    RouteModeCommand,
    execute_route_mode_command,
    parse_route_mode_command,
)
from echobot.commands.route_sessions import parse_route_session_command
from echobot.commands.runtime import (
    RuntimeCommand,
    execute_runtime_command,
    parse_runtime_command,
)
from echobot.commands.saved_sessions import parse_saved_session_command
from echobot.runtime.settings import (
    AppSettings,
    LLMSelection,
    RuntimeConfigSnapshot,
    RuntimeControls,
    SettingsService,
    SpeechSettings,
)
from echobot.runtime.sessions import Session


class SharedCommandDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_text_command_uses_first_matching_handler(self) -> None:
        executed: list[str] = []
        context = object()

        async def execute_first(received_context: object, _command: object) -> CommandResult:
            self.assertIs(context, received_context)
            executed.append("first")
            return CommandResult(text="first-result")

        async def execute_second(_context: object, _command: object) -> CommandResult:
            executed.append("second")
            return CommandResult(text="second-result")

        result = await dispatch_text_command(
            "/demo",
            context,
            [
                BoundTextCommand(
                    parse=lambda text: "first" if text == "/demo" else None,
                    execute=execute_first,
                ),
                BoundTextCommand(
                    parse=lambda text: "second" if text == "/demo" else None,
                    execute=execute_second,
                ),
            ],
        )

        assert result is not None
        self.assertEqual("first-result", result.text)
        self.assertEqual(["first"], executed)


class SessionCommandParsingTests(unittest.TestCase):
    def test_help_command_supports_bot_suffix(self) -> None:
        command = parse_help_command("/help@EchoBot")
        self.assertIsNotNone(command)

    def test_runtime_command_supports_get_set_and_list_forms(self) -> None:
        command = parse_runtime_command("/runtime")
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual("list", command.action)

        command = parse_runtime_command("/runtime set delegated_ack_enabled off")
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual("set", command.action)
        self.assertEqual("delegated_ack_enabled", command.key)
        self.assertEqual("off", command.value)

        command = parse_runtime_command("/runtime@EchoBot get delegated_ack_enabled")
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual("get", command.action)
        self.assertEqual("delegated_ack_enabled", command.key)

    def test_runtime_command_no_longer_supports_top_level_on_off_aliases(self) -> None:
        command = parse_runtime_command("/runtime off")
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual("help", command.action)

    def test_route_mode_command_supports_aliases(self) -> None:
        command = parse_route_mode_command("/route chat")
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual("set", command.action)
        self.assertEqual("chat_only", command.argument)

        command = parse_route_mode_command("/route@EchoBot force-agent")
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual("set", command.action)
        self.assertEqual("force_agent", command.argument)

    def test_route_session_command_supports_aliases(self) -> None:
        command = parse_route_session_command("/session switch 2")
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual("switch", command.action)
        self.assertEqual("2", command.argument)

        command = parse_route_session_command("/ls@EchoBot")
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual("list", command.action)

    def test_route_session_command_rejects_unknown_prefix(self) -> None:
        self.assertIsNone(parse_route_session_command("/newyork"))
        self.assertIsNone(parse_route_session_command("/switchboard"))

    def test_route_session_command_leaves_top_level_help_to_global_handler(self) -> None:
        self.assertIsNone(parse_route_session_command("/help"))

    def test_saved_session_command_maps_unknown_subcommand_to_help(self) -> None:
        command = parse_saved_session_command("/session nope")
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual("help", command.action)

    def test_saved_session_command_supports_rename_and_delete(self) -> None:
        command = parse_saved_session_command("/session rename renamed")
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual("rename", command.action)
        self.assertEqual("renamed", command.argument)

        command = parse_saved_session_command("/session delete")
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual("delete", command.action)

    def test_route_session_command_maps_unknown_subcommand_to_help(self) -> None:
        command = parse_route_session_command("/session nope")
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual("help", command.action)


class RuntimeCommandCoordinatorStub:
    def __init__(self, *, delegated_ack_enabled: bool = True) -> None:
        self._delegated_ack_enabled = delegated_ack_enabled

    @property
    def delegated_ack_enabled(self) -> bool:
        return self._delegated_ack_enabled

    def set_delegated_ack_enabled(self, enabled: bool) -> None:
        self._delegated_ack_enabled = bool(enabled)


class RouteModeCommandCoordinatorStub:
    def __init__(self, *, route_mode: str = "auto") -> None:
        self.route_mode = route_mode

    async def current_route_mode(self, _session_id: str) -> str:
        return self.route_mode

    async def set_session_route_mode(
        self,
        session_id: str,
        route_mode: str,
    ) -> Session:
        self.route_mode = route_mode
        return Session(
            id=session_id,
            title="Demo",
            history=[],
            agent_history=[],
            created_at="",
            updated_at="",
            metadata={"route_mode": route_mode},
        )


class MinimalCommandCoordinatorStub:
    delegated_ack_enabled = True


def _settings_service(
    workspace: Path,
    coordinator: RuntimeCommandCoordinatorStub,
    controls: RuntimeControls,
) -> SettingsService:
    service = SettingsService(
        workspace,
        defaults=AppSettings(
            revision=0,
            runtime=RuntimeConfigSnapshot(),
            llm=LLMSelection(active_provider="default"),
            speech=SpeechSettings(asr_provider="fake-asr"),
        ),
    )
    service.bind_runtime(coordinator, controls)
    return service


class CommandExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_cli_command_returns_global_help_for_help_alias(self) -> None:
        result = await dispatch_cli_command(
            CliCommandContext(
                coordinator=MinimalCommandCoordinatorStub(),
                settings_service=object(),
                session_service=object(),
                session_id="demo",
            ),
            "/help",
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn("Available commands:", result.text)
        self.assertIn("/session list", result.text)
        self.assertIn("/role current", result.text)
        self.assertIn("/runtime set <name> <value>", result.text)

    async def test_dispatch_gateway_command_returns_global_help_for_help_alias(self) -> None:
        result = await dispatch_gateway_command(
            GatewayCommandContext(
                coordinator=MinimalCommandCoordinatorStub(),
                settings_service=object(),
                session_service=object(),
                route_key="demo-route",
                address=ChannelAddress(channel="telegram", chat_id="123"),
                metadata={},
            ),
            "/help",
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn("Available commands:", result.text)
        self.assertIn("/new [title] - Start a new session", result.text)
        self.assertIn("/route auto", result.text)
        self.assertIn("/runtime get <name>", result.text)

    async def test_execute_runtime_command_updates_coordinator_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            coordinator = RuntimeCommandCoordinatorStub(
                delegated_ack_enabled=True,
            )
            controls = RuntimeControls()
            settings_service = _settings_service(workspace, coordinator, controls)

            response = await execute_runtime_command(
                settings_service,
                RuntimeCommand(
                    action="set",
                    key="delegated_ack_enabled",
                    value="off",
                ),
            )

            self.assertFalse(coordinator.delegated_ack_enabled)
            self.assertEqual(
                "Updated runtime setting: delegated_ack_enabled = off",
                response,
            )

            settings_path = workspace / ".echobot" / "settings.json"
            self.assertTrue(settings_path.exists())
            payload = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertFalse(payload["runtime"]["delegated_ack_enabled"])

            current = await execute_runtime_command(
                settings_service,
                RuntimeCommand(action="get", key="delegated_ack_enabled"),
            )
            self.assertEqual(
                "delegated_ack_enabled = off",
                current,
            )

            listing = await execute_runtime_command(
                settings_service,
                RuntimeCommand(action="list"),
            )
            self.assertIn("Runtime settings:", listing)
            self.assertIn("delegated_ack_enabled = off", listing)

    async def test_execute_runtime_command_updates_shell_safety_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            coordinator = RuntimeCommandCoordinatorStub()
            controls = RuntimeControls()
            settings_service = _settings_service(workspace, coordinator, controls)

            response = await execute_runtime_command(
                settings_service,
                RuntimeCommand(
                    action="set",
                    key="shell_safety_mode",
                    value="read-only",
                ),
            )

            self.assertEqual("read-only", controls.shell_safety_mode)
            self.assertEqual(
                "Updated runtime setting: shell_safety_mode = read-only",
                response,
            )

            settings_path = workspace / ".echobot" / "settings.json"
            payload = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual("read-only", payload["runtime"]["shell_safety_mode"])

    async def test_execute_runtime_command_updates_tool_safety_switches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            coordinator = RuntimeCommandCoordinatorStub()
            controls = RuntimeControls()
            settings_service = _settings_service(workspace, coordinator, controls)

            response = await execute_runtime_command(
                settings_service,
                RuntimeCommand(
                    action="set",
                    key="file_write_enabled",
                    value="off",
                ),
            )

            self.assertFalse(controls.file_write_enabled)
            self.assertEqual(
                "Updated runtime setting: file_write_enabled = off",
                response,
            )

            response = await execute_runtime_command(
                settings_service,
                RuntimeCommand(
                    action="set",
                    key="web_private_network_enabled",
                    value="on",
                ),
            )

            self.assertTrue(controls.web_private_network_enabled)
            self.assertEqual(
                "Updated runtime setting: web_private_network_enabled = on",
                response,
            )

            settings_path = workspace / ".echobot" / "settings.json"
            payload = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertFalse(payload["runtime"]["file_write_enabled"])
            self.assertTrue(payload["runtime"]["web_private_network_enabled"])


class RouteModeCommandExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_execute_route_mode_command_updates_session_route_mode(self) -> None:
        coordinator = RouteModeCommandCoordinatorStub(route_mode="auto")

        current = await execute_route_mode_command(
            coordinator,
            "demo",
            RouteModeCommand(action="current"),
        )
        self.assertEqual("Current route mode: auto", current)

        switched = await execute_route_mode_command(
            coordinator,
            "demo",
            RouteModeCommand(action="set", argument="agent"),
        )
        self.assertEqual("Switched route mode to: force_agent", switched)
        self.assertEqual("force_agent", coordinator.route_mode)
