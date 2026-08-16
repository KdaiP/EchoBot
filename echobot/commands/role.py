from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .parsing import split_action_argument, split_command_parts

if TYPE_CHECKING:
    from ..orchestration import ConversationCoordinator


@dataclass(slots=True)
class RoleCommand:
    action: str
    argument: str = ""


def parse_role_command(text: str) -> RoleCommand | None:
    command_token, remainder = split_command_parts(text)
    if command_token != "/role":
        return None

    if not remainder:
        return RoleCommand(action="current")

    action, argument = split_action_argument(remainder)

    if action in {"help", "list", "current", "set"}:
        return RoleCommand(action=action, argument=argument)
    return RoleCommand(action="help")


def format_role_help() -> str:
    return "\n".join(
        [
            "Role commands:",
            "/role current - Show the current role card",
            "/role list - List available role cards",
            "/role set <name> - Switch to a role card",
        ]
    )


def format_role_list(role_names: list[str], *, current_role_name: str) -> str:
    if not role_names:
        return "No role cards are available."

    lines = ["Available roles:"]
    for name in role_names:
        marker = "*" if name == current_role_name else " "
        lines.append(f"{marker} {name}")
    return "\n".join(lines)


async def execute_role_command(
    coordinator: "ConversationCoordinator",
    session_id: str,
    command: RoleCommand,
) -> str:
    if command.action == "help":
        return format_role_help()

    if command.action == "list":
        current_role = await coordinator.current_role_name(session_id)
        return format_role_list(
            coordinator.available_roles(),
            current_role_name=current_role,
        )

    if command.action == "current":
        current_role = await coordinator.current_role_name(session_id)
        return f"Current role: {current_role}"

    if command.action == "set":
        if not command.argument:
            return "Usage: /role set <name>"
        session = await coordinator.set_session_role(
            session_id,
            command.argument,
        )
        role_name = session.metadata.get("role_name", "default")
        return f"Switched role to: {role_name}"

    return format_role_help()
