from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .base import ToolOutput
from .filesystem import WorkspaceTool, _read_positive_int
from .shell import _decode_command_output


class GitStatusTool(WorkspaceTool):
    name = "git_status"
    execution_mode = "parallel"
    description = "Show the current git status for the workspace."
    parameters = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    async def run(self, arguments: dict[str, Any]) -> ToolOutput:
        del arguments
        await _ensure_git_repository(self.workspace.resolve())
        command_result = await _run_git_command(
            self.workspace.resolve(),
            "status",
            "--short",
            "--branch",
        )
        return {
            "workspace": str(self.workspace.resolve()),
            "text": command_result["stdout"],
            "lines": [
                line
                for line in str(command_result["stdout"]).splitlines()
                if line.strip()
            ],
        }


class GitDiffTool(WorkspaceTool):
    name = "git_diff"
    execution_mode = "parallel"
    description = "Show a git diff for the workspace or one file."
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Optional relative file or directory path inside the workspace.",
                "default": "",
            },
            "staged": {
                "type": "boolean",
                "description": "Show the staged diff instead of the working tree diff.",
                "default": False,
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum number of characters to return.",
                "default": 12000,
            },
        },
        "additionalProperties": False,
    }

    async def run(self, arguments: dict[str, Any]) -> ToolOutput:
        await _ensure_git_repository(self.workspace.resolve())

        relative_path = str(arguments.get("path", "")).strip()
        staged = bool(arguments.get("staged", False))
        max_chars = _read_positive_int(arguments.get("max_chars", 12000), name="max_chars")

        command = ["--no-pager", "diff"]
        if staged:
            command.append("--cached")

        normalized_path = ""
        if relative_path:
            target = self._resolve_workspace_path(relative_path)
            normalized_path = self._to_relative_path(target)
            command.extend(["--", normalized_path])

        command_result = await _run_git_command(
            self.workspace.resolve(),
            *command,
        )
        diff_text = str(command_result["stdout"])
        return {
            "workspace": str(self.workspace.resolve()),
            "path": normalized_path,
            "staged": staged,
            "diff": diff_text[:max_chars],
            "total_chars": len(diff_text),
            "truncated": len(diff_text) > max_chars,
        }


async def _ensure_git_repository(workspace: Path) -> None:
    result = await _run_git_command(
        workspace,
        "rev-parse",
        "--show-toplevel",
        allow_failure=True,
    )
    if result["return_code"] == 0:
        return

    stderr = str(result["stderr"]).strip()
    if stderr:
        raise ValueError(stderr)
    raise ValueError(f"Workspace is not inside a git repository: {workspace}")


async def _run_git_command(
    workspace: Path,
    *args: str,
    allow_failure: bool = False,
) -> dict[str, Any]:
    process = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(workspace),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await process.communicate()
    except asyncio.CancelledError:
        process.kill()
        await process.communicate()
        raise
    stdout = _decode_command_output(stdout_bytes)
    stderr = _decode_command_output(stderr_bytes)

    if process.returncode != 0 and not allow_failure:
        message = stderr.strip() or stdout.strip() or f"git {' '.join(args)} failed"
        raise ValueError(message)

    return {
        "return_code": process.returncode,
        "stdout": stdout,
        "stderr": stderr,
    }
