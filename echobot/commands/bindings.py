from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from ..channels.types import ChannelAddress
from ..orchestration import ConversationCoordinator
from ..runtime.settings import SettingsService
from ..runtime.session_service import SessionLifecycleService
from .dispatcher import BoundTextCommand, CommandResult, dispatch_text_command
from .help import (
    HelpCommand,
    format_cli_help,
    format_gateway_help,
    parse_help_command,
)
from .role import RoleCommand, execute_role_command, parse_role_command
from .route_mode import (
    RouteModeCommand,
    execute_route_mode_command,
    parse_route_mode_command,
)
from .route_sessions import (
    RouteSessionCommand,
    execute_route_session_command,
    parse_route_session_command,
)
from .runtime import RuntimeCommand, execute_runtime_command, parse_runtime_command
from .saved_sessions import (
    SavedSessionCommand,
    execute_saved_session_command,
    parse_saved_session_command,
)

if TYPE_CHECKING:
    from ..gateway.session_service import GatewaySessionService


@dataclass(slots=True)
class CliCommandContext:
    coordinator: ConversationCoordinator
    settings_service: SettingsService
    session_service: SessionLifecycleService
    session_id: str


@dataclass(slots=True)
class GatewayCommandContext:
    coordinator: ConversationCoordinator
    settings_service: SettingsService
    session_service: GatewaySessionService
    route_key: str
    address: ChannelAddress
    metadata: dict[str, object]


async def dispatch_cli_command(
    context: CliCommandContext,
    text: str,
) -> CommandResult | None:
    return await dispatch_text_command(text, context, _CLI_COMMAND_HANDLERS)


async def dispatch_gateway_command(
    context: GatewayCommandContext,
    text: str,
) -> CommandResult | None:
    return await dispatch_text_command(text, context, _GATEWAY_COMMAND_HANDLERS)


async def _execute_cli_saved_session(
    context: CliCommandContext,
    command_obj: object,
) -> CommandResult:
    command = cast(SavedSessionCommand, command_obj)
    current_session = await context.coordinator.load_session(context.session_id)
    result = await execute_saved_session_command(
        session_service=context.session_service,
        current_session=current_session,
        command=command,
    )
    context.session_id = result.session.id
    return CommandResult.from_lines(result.lines)


async def _execute_cli_help(
    _context: CliCommandContext,
    command_obj: object,
) -> CommandResult:
    command = cast(HelpCommand, command_obj)
    del command
    return CommandResult(text=format_cli_help())


async def _execute_cli_runtime(
    context: CliCommandContext,
    command_obj: object,
) -> CommandResult:
    command = cast(RuntimeCommand, command_obj)
    return CommandResult(
        text=await execute_runtime_command(
            context.settings_service,
            command,
        )
    )


async def _execute_cli_role(
    context: CliCommandContext,
    command_obj: object,
) -> CommandResult:
    command = cast(RoleCommand, command_obj)
    try:
        text = await execute_role_command(
            context.coordinator,
            context.session_id,
            command,
        )
    except ValueError as exc:
        text = f"Role error: {exc}"
    return CommandResult(text=text)


async def _execute_cli_route_mode(
    context: CliCommandContext,
    command_obj: object,
) -> CommandResult:
    command = cast(RouteModeCommand, command_obj)
    return CommandResult(
        text=await execute_route_mode_command(
            context.coordinator,
            context.session_id,
            command,
        )
    )


async def _execute_gateway_role(
    context: GatewayCommandContext,
    command_obj: object,
) -> CommandResult:
    command = cast(RoleCommand, command_obj)
    session_id = await _gateway_session_id(context)
    try:
        text = await execute_role_command(
            context.coordinator,
            session_id,
            command,
        )
    except ValueError as exc:
        text = str(exc)
    return CommandResult(text=text)


async def _execute_gateway_help(
    _context: GatewayCommandContext,
    command_obj: object,
) -> CommandResult:
    command = cast(HelpCommand, command_obj)
    del command
    return CommandResult(text=format_gateway_help())


async def _execute_gateway_route_mode(
    context: GatewayCommandContext,
    command_obj: object,
) -> CommandResult:
    command = cast(RouteModeCommand, command_obj)
    session_id = await _gateway_session_id(context)
    return CommandResult(
        text=await execute_route_mode_command(
            context.coordinator,
            session_id,
            command,
        )
    )


async def _execute_gateway_runtime(
    context: GatewayCommandContext,
    command_obj: object,
) -> CommandResult:
    command = cast(RuntimeCommand, command_obj)
    await _gateway_session_id(context)
    return CommandResult(
        text=await execute_runtime_command(
            context.settings_service,
            command,
        )
    )


async def _execute_gateway_route_session(
    context: GatewayCommandContext,
    command_obj: object,
) -> CommandResult:
    command = cast(RouteSessionCommand, command_obj)
    return CommandResult(
        text=await execute_route_session_command(
            session_service=context.session_service,
            route_key=context.route_key,
            address=context.address,
            metadata=context.metadata,
            command=command,
        )
    )


async def _gateway_session_id(context: GatewayCommandContext) -> str:
    current = await context.session_service.current_routed_session(context.route_key)
    await context.session_service.remember_delivery_target(
        current.session_id,
        context.address,
        context.metadata,
    )
    return current.session_id


_CLI_COMMAND_HANDLERS: tuple[BoundTextCommand[CliCommandContext], ...] = (
    BoundTextCommand(
        parse=parse_help_command,
        execute=_execute_cli_help,
    ),
    BoundTextCommand(
        parse=parse_saved_session_command,
        execute=_execute_cli_saved_session,
    ),
    BoundTextCommand(
        parse=parse_runtime_command,
        execute=_execute_cli_runtime,
    ),
    BoundTextCommand(
        parse=parse_role_command,
        execute=_execute_cli_role,
    ),
    BoundTextCommand(
        parse=parse_route_mode_command,
        execute=_execute_cli_route_mode,
    ),
)


_GATEWAY_COMMAND_HANDLERS: tuple[BoundTextCommand[GatewayCommandContext], ...] = (
    BoundTextCommand(
        parse=parse_help_command,
        execute=_execute_gateway_help,
    ),
    BoundTextCommand(
        parse=parse_role_command,
        execute=_execute_gateway_role,
    ),
    BoundTextCommand(
        parse=parse_route_mode_command,
        execute=_execute_gateway_route_mode,
    ),
    BoundTextCommand(
        parse=parse_runtime_command,
        execute=_execute_gateway_runtime,
    ),
    BoundTextCommand(
        parse=parse_route_session_command,
        execute=_execute_gateway_route_session,
    ),
)
