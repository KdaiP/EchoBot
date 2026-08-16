from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..orchestration.route_modes import RouteMode, normalize_route_mode
from .parsing import split_action_argument, split_command_parts

if TYPE_CHECKING:
    from ..orchestration import ConversationCoordinator


@dataclass(slots=True)
class RouteModeCommand:
    action: str
    argument: str = ""


def parse_route_mode_command(text: str) -> RouteModeCommand | None:
    command_token, remainder = split_command_parts(text)
    if command_token != "/route":
        return None

    if not remainder:
        return RouteModeCommand(action="current")

    action, argument = split_action_argument(
        remainder,
        lowercase_argument=True,
    )

    if action in {"help", "current"}:
        return RouteModeCommand(action=action)

    direct_mode = parse_route_mode_argument(action)
    if direct_mode is not None:
        return RouteModeCommand(action="set", argument=direct_mode)

    if action == "set":
        return RouteModeCommand(action="set", argument=argument)

    return RouteModeCommand(action="help")


def format_route_mode_help() -> str:
    return "\n".join(
        [
            "Route mode commands:",
            "/route current - Show the current route mode for this session",
            "/route auto - Let the assistant choose chat or agent",
            "/route chat_only - Keep this session in chat-only mode",
            "/route force_agent - Always use the full agent in this session",
            "/route set <auto|chat_only|force_agent> - Explicit form",
            "Aliases: /route chat, /route agent",
        ]
    )


async def execute_route_mode_command(
    coordinator: "ConversationCoordinator",
    session_id: str,
    command: RouteModeCommand,
) -> str:
    if command.action == "help":
        return format_route_mode_help()

    if command.action == "current":
        route_mode = await coordinator.current_route_mode(session_id)
        return format_current_route_mode(route_mode)

    if command.action == "set":
        route_mode = parse_route_mode_argument(command.argument)
        if route_mode is None:
            return "Usage: /route <auto|chat_only|force_agent>"

        await coordinator.set_session_route_mode(session_id, route_mode)
        return f"Switched route mode to: {route_mode}"

    return format_route_mode_help()


def format_current_route_mode(route_mode: RouteMode) -> str:
    return f"Current route mode: {route_mode}"


def parse_route_mode_argument(value: str) -> RouteMode | None:
    cleaned = str(value or "").strip().lower()
    aliases = {
        "auto": "auto",
        "default": "auto",
        "chat": "chat_only",
        "chat_only": "chat_only",
        "chat-only": "chat_only",
        "agent": "force_agent",
        "force": "force_agent",
        "force_agent": "force_agent",
        "force-agent": "force_agent",
    }
    mapped = aliases.get(cleaned)
    if mapped is None:
        return None
    return normalize_route_mode(mapped)
