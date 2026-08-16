from __future__ import annotations

import asyncio
from dataclasses import dataclass
import locale
import os
from pathlib import Path
import re
import shlex
from typing import Any

from .base import ToolOutput
from .filesystem import WorkspaceTool


SHELL_SAFETY_MODES = {
    "read-only",
    "workspace-write",
    "danger-full-access",
}

_DANGEROUS_COMMAND_PATTERNS = [
    (r"\bgit\s+reset\b", "git reset rewrites repository state"),
    (r"\bgit\s+clean\b", "git clean removes files"),
    (r"\bgit\s+checkout\s+--\b", "git checkout -- discards changes"),
    (r"\bgit\s+restore\b", "git restore can discard changes"),
    (r"\bremove-item\b", "Remove-Item can delete files"),
    (r"\brm\b", "rm can delete files"),
    (r"\bdel\b", "del can delete files"),
    (r"\brmdir\b", "rmdir can delete directories"),
    (r"\bformat\b", "format is a destructive system command"),
    (r"\bshutdown\b", "shutdown changes system state"),
    (r"\brestart-computer\b", "Restart-Computer changes system state"),
    (r"\bstop-computer\b", "Stop-Computer changes system state"),
    (r"\breg\s+(add|delete)\b", "registry edits are high risk"),
    (
        r"\b(choco|winget|apt|apt-get|yum|dnf|brew)\b",
        "system package management changes the machine",
    ),
    (
        r"\b(pip|uv|poetry|npm|pnpm|yarn|bun|cargo)\b\s+"
        r"(install|add|remove|uninstall|update)\b",
        "package installation changes the environment",
    ),
    (
        r"\b(invoke-webrequest|invoke-restmethod|curl|wget|scp|sftp|ssh|ftp|telnet|nc|ncat)\b",
        "network commands should use dedicated tools or full-access mode",
    ),
]

_WRITE_COMMAND_PATTERNS = [
    (r"\bset-content\b", "Set-Content writes files"),
    (r"\badd-content\b", "Add-Content writes files"),
    (r"\bout-file\b", "Out-File writes files"),
    (r"\bnew-item\b", "New-Item creates files or directories"),
    (r"\bcopy-item\b", "Copy-Item writes files"),
    (r"\bmove-item\b", "Move-Item changes files"),
    (r"\brename-item\b", "Rename-Item changes files"),
    (r"\bmkdir\b", "mkdir creates directories"),
    (r"\bmd\b", "md creates directories"),
    (r"\bcopy\b", "copy writes files"),
    (r"\bmove\b", "move changes files"),
    (r"\bren\b", "ren renames files"),
    (r"\bcp\b", "cp writes files"),
    (r"\bmv\b", "mv changes files"),
    (r"\btouch\b", "touch writes files"),
    (
        r"\bgit\s+(commit|apply|am|cherry-pick|merge|rebase|stash|switch|checkout)\b",
        "git command changes repository state",
    ),
    (r"(?:^|[^>])>>?(?:[^>]|$)", "shell redirection writes files"),
]

_READ_ONLY_COMMANDS = frozenset(
    {
        "cat",
        "dir",
        "echo",
        "findstr",
        "get-childitem",
        "get-content",
        "get-filehash",
        "get-item",
        "get-location",
        "ls",
        "pwd",
        "resolve-path",
        "rg",
        "select-string",
        "test-path",
        "type",
        "where",
        "which",
    }
)

_WORKSPACE_WRITE_COMMANDS = frozenset(
    {
        "add-content",
        "cp",
        "copy",
        "copy-item",
        "md",
        "mkdir",
        "mv",
        "move",
        "move-item",
        "new-item",
        "out-file",
        "ren",
        "rename-item",
        "set-content",
        "touch",
    }
)

_READ_ONLY_GIT_SUBCOMMANDS = frozenset(
    {
        "branch",
        "diff",
        "log",
        "ls-files",
        "rev-parse",
        "show",
        "status",
    }
)

_INTERPRETER_COMMANDS = frozenset(
    {
        "bash",
        "bun",
        "cmd",
        "cscript",
        "deno",
        "fish",
        "lua",
        "mshta",
        "node",
        "perl",
        "php",
        "powershell",
        "pwsh",
        "py",
        "python",
        "pythonw",
        "ruby",
        "sh",
        "tclsh",
        "uv",
        "wscript",
        "zsh",
    }
)

_RESTRICTED_SYNTAX_PATTERNS = [
    (r"[\r\n;]", "multiple shell statements are not allowed"),
    (r"(?:\|\||&&)", "conditional shell operators are not allowed"),
    (r"(?<!\|)\|(?!\|)", "pipelines are not allowed"),
    (r"`", "shell escape syntax is not allowed"),
    (r"\$\(", "shell subexpressions are not allowed"),
    (r"@['\"({]", "complex PowerShell literals are not allowed"),
    (r"[<>]", "shell redirection is not allowed"),
]

_EXECUTABLE_SUFFIXES = (".bat", ".cmd", ".exe", ".ps1", ".sh")


@dataclass(slots=True)
class ShellSafetyAssessment:
    allowed: bool
    level: str
    reason: str
    error: str = ""


class CommandExecutionTool(WorkspaceTool):
    name = "run_shell_command"
    description = (
        "Run a shell command in the workspace and return stdout and stderr. "
        "In read-only and workspace-write modes, only simple allowlisted commands are accepted."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to run.",
            },
            "workdir": {
                "type": "string",
                "description": "Relative working directory inside the workspace.",
                "default": ".",
            },
            "timeout": {
                "type": "number",
                "description": "Command timeout in seconds.",
                "default": 20,
            },
            "max_output_chars": {
                "type": "integer",
                "description": "Maximum characters kept for stdout and stderr.",
                "default": 4000,
            },
        },
        "required": ["command"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        workspace: str | Path = ".",
        *,
        shell_safety_mode: str = "danger-full-access",
        workspace_write_enabled: bool = True,
    ) -> None:
        super().__init__(workspace)
        self.shell_safety_mode = normalize_shell_safety_mode(shell_safety_mode)
        self._workspace_write_enabled = bool(workspace_write_enabled)

    async def run(self, arguments: dict[str, Any]) -> ToolOutput:
        command = str(arguments.get("command", "")).strip()
        if not command:
            raise ValueError("command is required")

        relative_workdir = str(arguments.get("workdir", ".")).strip() or "."
        timeout = _read_positive_float(arguments.get("timeout", 20), name="timeout")
        max_output_chars = _read_positive_int(
            arguments.get("max_output_chars", 4000),
            name="max_output_chars",
        )

        workdir = self._resolve_workspace_path(relative_workdir)
        if not workdir.exists():
            raise ValueError(f"Path does not exist: {relative_workdir}")
        if not workdir.is_dir():
            raise ValueError(f"Path is not a directory: {relative_workdir}")

        assessment = ShellCommandPolicy(
            self.workspace.resolve(),
            shell_safety_mode=self.shell_safety_mode,
            workspace_write_enabled=self._workspace_write_enabled,
        ).assess(command)
        if not assessment.allowed:
            raise ValueError(assessment.error)

        return await self._run_command(
            command,
            workdir,
            relative_workdir,
            timeout,
            max_output_chars,
            assessment,
        )

    async def _run_command(
        self,
        command: str,
        workdir: Path,
        relative_workdir: str,
        timeout: float,
        max_output_chars: int,
        assessment: ShellSafetyAssessment,
    ) -> dict[str, Any]:
        shell_command = _build_shell_command(command)
        process = await asyncio.create_subprocess_exec(
            *shell_command,
            cwd=str(workdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )
        except asyncio.CancelledError:
            process.kill()
            await process.communicate()
            raise
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise RuntimeError(f"Command timed out after {timeout} seconds") from exc

        stdout_text = _decode_command_output(stdout_bytes)
        stderr_text = _decode_command_output(stderr_bytes)
        stdout, stdout_truncated = _truncate_text(stdout_text, max_output_chars)
        stderr, stderr_truncated = _truncate_text(stderr_text, max_output_chars)

        return {
            "command": command,
            "workdir": relative_workdir,
            "return_code": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "shell_safety_mode": self.shell_safety_mode,
            "safety_level": assessment.level,
            "safety_reason": assessment.reason,
        }


class ShellCommandPolicy:
    def __init__(
        self,
        workspace_root: Path,
        *,
        shell_safety_mode: str,
        workspace_write_enabled: bool,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.shell_safety_mode = normalize_shell_safety_mode(shell_safety_mode)
        self.workspace_write_enabled = bool(workspace_write_enabled)

    def assess(self, command: str) -> ShellSafetyAssessment:
        normalized_command = str(command or "").strip()
        if self.shell_safety_mode == "danger-full-access":
            return _classify_full_access_command(normalized_command)
        return self._assess_restricted_command(normalized_command)

    def _assess_restricted_command(self, command: str) -> ShellSafetyAssessment:
        external_path_reason = _external_path_reason(command)
        if external_path_reason is not None:
            return _blocked_assessment(
                shell_safety_mode=self.shell_safety_mode,
                level="dangerous",
                reason=external_path_reason,
                required_mode="danger-full-access",
            )

        restricted_syntax_reason = _restricted_syntax_reason(command)
        if restricted_syntax_reason is not None:
            return _blocked_assessment(
                shell_safety_mode=self.shell_safety_mode,
                level="dangerous",
                reason=restricted_syntax_reason,
                required_mode="danger-full-access",
            )

        tokens = _tokenize_simple_command(command)
        if not tokens:
            return _blocked_assessment(
                shell_safety_mode=self.shell_safety_mode,
                level="read_only",
                reason="Command is empty after parsing",
                required_mode="read-only",
            )

        interpreter_name = _first_interpreter_name(tokens)
        if interpreter_name:
            return _blocked_assessment(
                shell_safety_mode=self.shell_safety_mode,
                level="dangerous",
                reason=(
                    f"interpreter command '{interpreter_name}' can execute arbitrary code"
                ),
                required_mode="danger-full-access",
            )

        primary_command = _command_name(tokens[0])
        if not primary_command:
            return _blocked_assessment(
                shell_safety_mode=self.shell_safety_mode,
                level="read_only",
                reason="Unable to determine the command name",
                required_mode="read-only",
            )

        if primary_command == "git":
            return self._assess_restricted_git_command(tokens)

        if primary_command in _READ_ONLY_COMMANDS:
            return ShellSafetyAssessment(
                allowed=True,
                level="read_only",
                reason=f"'{primary_command}' is allowed in restricted read-only mode",
            )

        if primary_command in _WORKSPACE_WRITE_COMMANDS:
            if self.shell_safety_mode != "workspace-write":
                return _blocked_assessment(
                    shell_safety_mode=self.shell_safety_mode,
                    level="workspace_write",
                    reason=f"'{primary_command}' writes files inside the workspace",
                    required_mode="workspace-write",
                )
            if not self.workspace_write_enabled:
                return ShellSafetyAssessment(
                    allowed=False,
                    level="workspace_write",
                    reason="workspace file writes are disabled by runtime settings",
                    error="Command blocked because workspace file writes are disabled by runtime settings.",
                )
            return ShellSafetyAssessment(
                allowed=True,
                level="workspace_write",
                reason=f"'{primary_command}' is allowed in workspace-write mode",
            )

        return _blocked_assessment(
            shell_safety_mode=self.shell_safety_mode,
            level="dangerous",
            reason=(
                f"'{primary_command}' is not in the restricted shell allowlist"
            ),
            required_mode="danger-full-access",
        )

    def _assess_restricted_git_command(
        self,
        tokens: list[str],
    ) -> ShellSafetyAssessment:
        git_subcommand = _git_subcommand(tokens)
        if not git_subcommand:
            return _blocked_assessment(
                shell_safety_mode=self.shell_safety_mode,
                level="read_only",
                reason="git requires an explicit subcommand in restricted modes",
                required_mode="read-only",
            )

        if git_subcommand not in _READ_ONLY_GIT_SUBCOMMANDS:
            return _blocked_assessment(
                shell_safety_mode=self.shell_safety_mode,
                level="dangerous",
                reason=f"git subcommand '{git_subcommand}' is not read-only",
                required_mode="danger-full-access",
            )

        return ShellSafetyAssessment(
            allowed=True,
            level="read_only",
            reason=f"git {git_subcommand} is allowed in restricted read-only mode",
        )


def _read_positive_int(value: Any, *, name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc

    if number <= 0:
        raise ValueError(f"{name} must be greater than 0")

    return number


def _read_positive_float(value: Any, *, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc

    if number <= 0:
        raise ValueError(f"{name} must be greater than 0")

    return number


def _truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False

    return text[:max_chars], True


def _decode_command_output(raw_bytes: bytes) -> str:
    if not raw_bytes:
        return ""

    preferred_encoding = locale.getpreferredencoding(False) or "utf-8"
    candidate_encodings = ["utf-8"]
    if preferred_encoding.lower() not in {"utf-8", "utf_8"}:
        candidate_encodings.append(preferred_encoding)

    for encoding in candidate_encodings:
        try:
            return raw_bytes.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue

    try:
        return raw_bytes.decode(preferred_encoding, errors="replace")
    except LookupError:
        return raw_bytes.decode("utf-8", errors="replace")


def _build_shell_command(command: str) -> list[str]:
    if os.name == "nt":
        return ["powershell.exe", "-NoProfile", "-Command", command]

    return ["/bin/sh", "-lc", command]


def normalize_shell_safety_mode(value: str | None) -> str:
    cleaned = str(value or "").strip().lower()
    if cleaned not in SHELL_SAFETY_MODES:
        raise ValueError(
            "shell_safety_mode must be one of: "
            + ", ".join(sorted(SHELL_SAFETY_MODES))
        )
    return cleaned


def _classify_full_access_command(command: str) -> ShellSafetyAssessment:
    lower_command = command.lower()
    external_path_reason = _external_path_reason(command)
    if external_path_reason is not None:
        return ShellSafetyAssessment(
            allowed=True,
            level="dangerous",
            reason=external_path_reason,
        )

    try:
        tokens = _tokenize_simple_command(command)
    except ValueError:
        tokens = []

    interpreter_name = _first_interpreter_name(tokens)
    if interpreter_name:
        return ShellSafetyAssessment(
            allowed=True,
            level="dangerous",
            reason=f"interpreter command '{interpreter_name}' can execute arbitrary code",
        )

    for pattern, reason in _DANGEROUS_COMMAND_PATTERNS:
        if re.search(pattern, lower_command):
            return ShellSafetyAssessment(
                allowed=True,
                level="dangerous",
                reason=reason,
            )

    for pattern, reason in _WRITE_COMMAND_PATTERNS:
        if re.search(pattern, lower_command):
            return ShellSafetyAssessment(
                allowed=True,
                level="workspace_write",
                reason=reason,
            )

    return ShellSafetyAssessment(
        allowed=True,
        level="read_only",
        reason="Command looks read-only",
    )


def _external_path_reason(command: str) -> str | None:
    if re.search(r"(?:^|[\s'\"=])\.\.(?:[\\/]|$)", command):
        return "command references a path outside the workspace"
    if os.name == "nt":
        if re.search(r"(?:^|[\s'\"=])[a-z]:[\\/]", command, flags=re.IGNORECASE):
            return "command references a path outside the workspace"
        if re.search(r"(?:^|[\s'\"=])\\\\", command):
            return "command references a path outside the workspace"
    else:
        if re.search(r"(?:^|[\s'\"=])/", command):
            return "command references a path outside the workspace"
    return None


def _restricted_syntax_reason(command: str) -> str | None:
    for pattern, reason in _RESTRICTED_SYNTAX_PATTERNS:
        if re.search(pattern, command):
            return reason
    return None


def _tokenize_simple_command(command: str) -> list[str]:
    if not command.strip():
        return []
    try:
        tokens = shlex.split(command, posix=os.name != "nt")
    except ValueError as exc:
        raise ValueError(f"Unable to parse shell command: {exc}") from exc

    return [_strip_wrapping_quotes(token) for token in tokens if token.strip()]


def _strip_wrapping_quotes(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {"'", '"'}:
        return token[1:-1]
    return token


def _first_interpreter_name(tokens: list[str]) -> str:
    for token in tokens:
        command_name = _command_name(token)
        if command_name in _INTERPRETER_COMMANDS:
            return command_name
    return ""


def _command_name(token: str) -> str:
    cleaned = str(token or "").strip()
    if not cleaned:
        return ""

    name = Path(cleaned).name.lower()
    for suffix in _EXECUTABLE_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _git_subcommand(tokens: list[str]) -> str:
    for token in tokens[1:]:
        if not token or token.startswith("-"):
            continue
        return _command_name(token)
    return ""


def _blocked_assessment(
    *,
    shell_safety_mode: str,
    level: str,
    reason: str,
    required_mode: str,
) -> ShellSafetyAssessment:
    return ShellSafetyAssessment(
        allowed=False,
        level=level,
        reason=reason,
        error=(
            f"Command blocked by shell safety mode '{shell_safety_mode}'. "
            f"Detected level: {level}. Reason: {reason}. "
            f"Required mode: {required_mode}."
        ),
    )
