from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from ..attachments import AttachmentStore
from ..scheduling.cron import CronService
from .base import BaseTool, ToolOutput, ToolRegistry
from .cron import CronTool
from .filesystem import (
    EditTextFileTool,
    ListDirectoryTool,
    ReadTextFileTool,
    SearchFilesTool,
    SearchTextInFilesTool,
    WriteTextFileTool,
)
from .git import GitDiffTool, GitStatusTool
from .memory import MemorySearchTool
from .media import SendFileToUserTool, SendImageToUserTool, ViewImageTool
from .planning import RequestUserInputTool, UpdatePlanTool
from .shell import CommandExecutionTool, _decode_command_output, locale
from .web import WebRequestTool


class CurrentTimeTool(BaseTool):
    name = "get_current_time"
    execution_mode = "parallel"
    description = "Get the current local time."
    parameters = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    async def run(self, arguments: dict[str, Any]) -> ToolOutput:
        del arguments
        now = datetime.now().astimezone()
        return {
            "current_time": now.isoformat(timespec="seconds"),
            "timezone": str(now.tzinfo),
        }


def create_basic_tool_registry(
    workspace: str | Path = ".",
    *,
    attachment_store: AttachmentStore | None = None,
    supports_image_input: bool = True,
    memory_support: Any | None = None,
    cron_service: CronService | None = None,
    session_id: str = "heartbeat",
    allow_file_writes: bool = True,
    allow_cron_mutations: bool = True,
    allow_private_network: bool = False,
    shell_safety_mode: str = "danger-full-access",
) -> ToolRegistry:
    tools: list[BaseTool] = [
        CurrentTimeTool(),
        ListDirectoryTool(workspace),
        SearchFilesTool(workspace),
        SearchTextInFilesTool(workspace),
        ReadTextFileTool(workspace),
        WriteTextFileTool(workspace, writes_enabled=allow_file_writes),
        EditTextFileTool(workspace, writes_enabled=allow_file_writes),
        GitStatusTool(workspace),
        GitDiffTool(workspace),
        UpdatePlanTool(),
        RequestUserInputTool(),
        WebRequestTool(allow_private_network=allow_private_network),
        CommandExecutionTool(
            workspace,
            shell_safety_mode=shell_safety_mode,
            workspace_write_enabled=allow_file_writes,
        ),
    ]
    if attachment_store is not None:
        if supports_image_input:
            tools.append(ViewImageTool(workspace, attachment_store=attachment_store))
        tools.append(SendImageToUserTool(workspace, attachment_store=attachment_store))
        tools.append(SendFileToUserTool(workspace, attachment_store=attachment_store))
    if memory_support is not None:
        tools.append(MemorySearchTool(memory_support))
    if cron_service is not None:
        tools.append(
            CronTool(
                cron_service,
                session_id=session_id,
                allow_mutations=allow_cron_mutations,
            )
        )

    return ToolRegistry(tools)


__all__ = [
    "CommandExecutionTool",
    "CronTool",
    "CurrentTimeTool",
    "EditTextFileTool",
    "GitDiffTool",
    "GitStatusTool",
    "ListDirectoryTool",
    "MemorySearchTool",
    "ReadTextFileTool",
    "RequestUserInputTool",
    "SearchFilesTool",
    "SearchTextInFilesTool",
    "UpdatePlanTool",
    "WebRequestTool",
    "WriteTextFileTool",
    "_decode_command_output",
    "create_basic_tool_registry",
    "locale",
]
