from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from ..channels.types import ChannelAddress
from .parsing import split_command_parts

if TYPE_CHECKING:
    from ..gateway.session_service import GatewaySessionService


@dataclass(slots=True)
class RouteSessionCommand:
    action: str
    argument: str = ""


class RouteSessionLike(Protocol):
    title: str

    @property
    def short_id(self) -> str: ...


def parse_route_session_command(text: str) -> RouteSessionCommand | None:
    command_token, remainder = split_command_parts(text)
    if not command_token.startswith("/"):
        return None

    if command_token == "/session":
        subcommand, subcommand_remainder = split_command_parts(remainder)
        mapping = {
            "help": "help",
            "list": "list",
            "ls": "list",
            "current": "current",
            "new": "new",
            "switch": "switch",
            "rename": "rename",
            "delete": "delete",
        }
        mapped = mapping.get(subcommand.lstrip("/"))
        if mapped is None:
            return RouteSessionCommand(action="help")
        return RouteSessionCommand(action=mapped, argument=subcommand_remainder)

    aliases = {
        "/new": "new",
        "/ls": "list",
        "/current": "current",
        "/switch": "switch",
        "/rename": "rename",
        "/delete": "delete",
    }
    action = aliases.get(command_token)
    if action is None:
        return None
    return RouteSessionCommand(action=action, argument=remainder)


async def execute_route_session_command(
    *,
    session_service: "GatewaySessionService",
    route_key: str,
    address: ChannelAddress,
    metadata: dict[str, object],
    command: RouteSessionCommand,
) -> str:
    if command.action == "help":
        current = await session_service.current_routed_session(route_key)
        await _remember_route_target(
            session_service,
            current.session_id,
            address,
            metadata,
        )
        return format_route_session_help()

    if command.action == "list":
        sessions = await session_service.list_routed_sessions(route_key)
        if not sessions:
            return "No sessions are available for this chat."
        await _remember_route_target(
            session_service,
            sessions[0].session_id,
            address,
            metadata,
        )
        return format_route_session_list(sessions)

    if command.action == "current":
        current = await session_service.current_routed_session(route_key)
        await _remember_route_target(
            session_service,
            current.session_id,
            address,
            metadata,
        )
        return format_current_route_session(current)

    if command.action == "new":
        created = await session_service.create_routed_session(
            route_key,
            title=(command.argument or None),
        )
        await _remember_route_target(
            session_service,
            created.session_id,
            address,
            metadata,
        )
        return f"Switched to a new session: {created.title} [{created.short_id}]"

    if command.action == "switch":
        if not command.argument:
            return "Usage: /switch <number>"
        try:
            index = int(command.argument)
        except ValueError:
            return "Session number must be an integer."
        try:
            selected = await session_service.switch_routed_session(
                route_key,
                index,
            )
        except ValueError as exc:
            return str(exc)
        await _remember_route_target(
            session_service,
            selected.session_id,
            address,
            metadata,
        )
        return f"Switched to session {index}: {selected.title} [{selected.short_id}]"

    if command.action == "rename":
        if not command.argument:
            return "Usage: /rename <title>"
        try:
            renamed = await session_service.rename_current_routed_session(
                route_key,
                command.argument,
            )
        except ValueError as exc:
            return str(exc)
        await _remember_route_target(
            session_service,
            renamed.session_id,
            address,
            metadata,
        )
        return (
            "Renamed current session to "
            f"{renamed.title} [{renamed.short_id}]"
        )

    if command.action == "delete":
        result = await session_service.delete_current_routed_session(route_key)
        await _remember_route_target(
            session_service,
            result.current.session_id,
            address,
            metadata,
        )
        if result.created_replacement:
            return (
                "Deleted the last session and created a fresh one: "
                f"{result.current.title} [{result.current.short_id}]"
            )
        return (
            "Deleted the current session. "
            f"Now using {result.current.title} [{result.current.short_id}]"
        )

    return format_route_session_help()


def format_route_session_help() -> str:
    return "\n".join(
        [
            "Session commands:",
            "/session help - Show the session command list",
            "/new [title] - Start a new session",
            "/ls - List sessions in this chat",
            "/switch <number> - Switch to a session",
            "/rename <title> - Rename the current session",
            "/delete - Delete the current session",
            "/current - Show the current session",
            "/route ... - Show or switch the route mode for this session",
            "/session ... - Alias for the same commands",
        ]
    )


def format_current_route_session(route_session: RouteSessionLike) -> str:
    return f"Current session: {route_session.title} [{route_session.short_id}]"


def format_route_session_list(sessions: list[RouteSessionLike]) -> str:
    lines = ["Sessions for this chat:"]
    for index, route_session in enumerate(sessions, start=1):
        marker = "*" if index == 1 else " "
        lines.append(
            f"{marker} {index}. {route_session.title} [{route_session.short_id}]"
        )
    lines.append("Use /switch <number> to change the current session.")
    return "\n".join(lines)


async def _remember_route_target(
    session_service: "GatewaySessionService",
    session_id: str,
    address: ChannelAddress,
    metadata: dict[str, object],
) -> None:
    await session_service.remember_delivery_target(
        session_id,
        address,
        metadata,
    )
