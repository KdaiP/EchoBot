from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ..runtime.settings import (
    RUNTIME_SETTING_DEFINITIONS,
    SettingsService,
    format_runtime_setting_value,
    parse_text_runtime_setting_value,
)
from .parsing import split_action_argument, split_command_parts

@dataclass(slots=True)
class RuntimeCommand:
    action: str
    key: str = ""
    value: str = ""


def parse_runtime_command(text: str) -> RuntimeCommand | None:
    command_token, remainder = split_command_parts(text)
    if command_token != "/runtime":
        return None

    if not remainder:
        return RuntimeCommand(action="list")

    action, argument = split_action_argument(
        remainder,
    )

    if action in {"help", "list"}:
        return RuntimeCommand(action=action)
    if action == "get":
        key, _unused = split_action_argument(argument)
        return RuntimeCommand(action="get", key=key.lower())
    if action == "set":
        key, value = split_action_argument(argument)
        return RuntimeCommand(
            action="set",
            key=key.lower(),
            value=value,
        )

    return RuntimeCommand(action="help")


def format_runtime_help() -> str:
    return "\n".join(
        [
            "Runtime commands:",
            "/runtime list - List runtime settings and current values",
            "/runtime get <name> - Show one runtime setting",
            "/runtime set <name> <value> - Update one runtime setting",
            "",
            "Available runtime settings:",
            *[
                (
                    f"{definition.name} <{definition.value_hint}> - "
                    f"{definition.description}"
                )
                for definition in RUNTIME_SETTING_DEFINITIONS.values()
            ],
        ]
    )


async def execute_runtime_command(
    settings_service: SettingsService,
    command: RuntimeCommand,
) -> str:
    if command.action == "help":
        return format_runtime_help()

    if command.action == "list":
        return _format_runtime_settings_list(settings_service)

    if command.action == "get":
        if not command.key:
            return "Usage: /runtime get <name>"
        if command.key not in RUNTIME_SETTING_DEFINITIONS:
            return _format_unknown_runtime_setting(command.key)
        return _format_runtime_setting_line(
            command.key,
            settings_service.get_runtime_value(command.key),
        )

    if command.action == "set":
        if not command.key or not command.value:
            return "Usage: /runtime set <name> <value>"
        if command.key not in RUNTIME_SETTING_DEFINITIONS:
            return _format_unknown_runtime_setting(command.key)
        try:
            parsed_value = parse_text_runtime_setting_value(command.key, command.value)
        except ValueError as exc:
            return str(exc)
        try:
            snapshot = await asyncio.to_thread(
                settings_service.apply_runtime_updates,
                {command.key: parsed_value},
            )
        except Exception as exc:
            return f"Failed to save runtime settings: {exc}"
        return (
            "Updated runtime setting: "
            + _format_runtime_setting_line(
                command.key,
                snapshot[command.key],
            )
        )

    return format_runtime_help()


def _format_runtime_settings_list(
    settings_service: SettingsService,
) -> str:
    snapshot = settings_service.runtime_snapshot()
    lines = ["Runtime settings:"]
    for name in RUNTIME_SETTING_DEFINITIONS:
        lines.append(
            _format_runtime_setting_line(
                name,
                snapshot[name],
            )
        )
    return "\n".join(lines)


def _format_runtime_setting_line(name: str, value: object) -> str:
    definition = RUNTIME_SETTING_DEFINITIONS[name]
    return f"{definition.name} = {format_runtime_setting_value(name, value)}"


def _format_unknown_runtime_setting(name: str) -> str:
    known_names = ", ".join(RUNTIME_SETTING_DEFINITIONS)
    return f"Unknown runtime setting: {name}. Available settings: {known_names}"
