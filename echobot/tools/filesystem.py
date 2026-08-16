from __future__ import annotations

import asyncio
import fnmatch
import re
from pathlib import Path
from typing import Any

from .base import BaseTool, ToolOutput


class WorkspaceTool(BaseTool):
    def __init__(self, workspace: str | Path = ".") -> None:
        self.workspace = Path(workspace)

    def _resolve_workspace_path(self, relative_path: str) -> Path:
        workspace_root = self.workspace.resolve()
        target = (workspace_root / relative_path).resolve()

        try:
            target.relative_to(workspace_root)
        except ValueError as exc:
            raise ValueError(f"Path is outside the workspace: {relative_path}") from exc

        return target

    def _to_relative_path(self, target: Path) -> str:
        return str(target.resolve().relative_to(self.workspace.resolve())).replace("\\", "/")


class WritableWorkspaceTool(WorkspaceTool):
    def __init__(
        self,
        workspace: str | Path = ".",
        *,
        writes_enabled: bool = True,
    ) -> None:
        super().__init__(workspace)
        self._writes_enabled = bool(writes_enabled)

    def _require_writes_enabled(self) -> None:
        if self._writes_enabled:
            return
        raise ValueError("当前运行时已禁用文件写入工具")


class ListDirectoryTool(WorkspaceTool):
    name = "list_directory"
    execution_mode = "parallel"
    description = "List files and folders under the workspace."
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path inside the workspace.",
                "default": ".",
            }
        },
        "additionalProperties": False,
    }

    async def run(self, arguments: dict[str, Any]) -> ToolOutput:
        relative_path = str(arguments.get("path", ".")).strip() or "."
        return await asyncio.to_thread(self._list_directory, relative_path)

    def _list_directory(self, relative_path: str) -> dict[str, Any]:
        target = self._resolve_workspace_path(relative_path)
        if not target.exists():
            raise ValueError(f"Path does not exist: {relative_path}")
        if not target.is_dir():
            raise ValueError(f"Path is not a directory: {relative_path}")

        entries = []
        for child in sorted(target.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
            entries.append(
                {
                    "name": child.name,
                    "type": "file" if child.is_file() else "directory",
                }
            )

        return {
            "path": self._to_relative_path(target),
            "entries": entries[:200],
            "truncated": len(entries) > 200,
        }


class ReadTextFileTool(WorkspaceTool):
    name = "read_text_file"
    execution_mode = "parallel"
    description = "Read a UTF-8 text file from the workspace."
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative file path inside the workspace.",
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum number of characters to return.",
                "default": 4000,
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    async def run(self, arguments: dict[str, Any]) -> ToolOutput:
        relative_path = str(arguments.get("path", "")).strip()
        if not relative_path:
            raise ValueError("path is required")

        max_chars = _read_positive_int(arguments.get("max_chars", 4000), name="max_chars")
        return await asyncio.to_thread(self._read_text_file, relative_path, max_chars)

    def _read_text_file(self, relative_path: str, max_chars: int) -> dict[str, Any]:
        target = self._resolve_workspace_path(relative_path)
        if not target.exists():
            raise ValueError(f"File does not exist: {relative_path}")
        if not target.is_file():
            raise ValueError(f"Path is not a file: {relative_path}")

        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Only UTF-8 text files are supported") from exc

        return {
            "path": self._to_relative_path(target),
            "content": content[:max_chars],
            "total_chars": len(content),
            "truncated": len(content) > max_chars,
        }


class WriteTextFileTool(WritableWorkspaceTool):
    name = "write_text_file"
    description = "Write a UTF-8 text file inside the workspace."
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative file path inside the workspace.",
            },
            "content": {
                "type": "string",
                "description": "Text content to write.",
            },
            "overwrite": {
                "type": "boolean",
                "description": "Overwrite the file if it already exists.",
                "default": False,
            },
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        workspace: str | Path = ".",
        *,
        writes_enabled: bool = True,
    ) -> None:
        super().__init__(workspace, writes_enabled=writes_enabled)

    async def run(self, arguments: dict[str, Any]) -> ToolOutput:
        self._require_writes_enabled()
        relative_path = str(arguments.get("path", "")).strip()
        if not relative_path:
            raise ValueError("path is required")

        content = str(arguments.get("content", ""))
        overwrite = bool(arguments.get("overwrite", False))
        return await asyncio.to_thread(
            self._write_text_file,
            relative_path,
            content,
            overwrite,
        )

    def _write_text_file(
        self,
        relative_path: str,
        content: str,
        overwrite: bool,
    ) -> dict[str, Any]:
        target = self._resolve_workspace_path(relative_path)
        file_existed = target.exists()
        if file_existed and not overwrite:
            raise ValueError(f"File already exists: {relative_path}")

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

        return {
            "path": self._to_relative_path(target),
            "written_chars": len(content),
            "overwritten": file_existed and overwrite,
        }

class SearchFilesTool(WorkspaceTool):
    name = "search_files"
    execution_mode = "parallel"
    description = "Find files and folders in the workspace using a glob-style pattern."
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative directory inside the workspace.",
                "default": ".",
            },
            "pattern": {
                "type": "string",
                "description": "Glob-style pattern, for example '*.py' or 'src/**/*.js'.",
                "default": "*",
            },
            "include_directories": {
                "type": "boolean",
                "description": "Include directories in the results.",
                "default": False,
            },
            "include_hidden": {
                "type": "boolean",
                "description": "Include hidden files and directories.",
                "default": False,
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of matches to return.",
                "default": 200,
            },
        },
        "additionalProperties": False,
    }

    async def run(self, arguments: dict[str, Any]) -> ToolOutput:
        relative_path = str(arguments.get("path", ".")).strip() or "."
        pattern = str(arguments.get("pattern", "*")).strip() or "*"
        include_directories = bool(arguments.get("include_directories", False))
        include_hidden = bool(arguments.get("include_hidden", False))
        max_results = _read_positive_int(
            arguments.get("max_results", 200),
            name="max_results",
        )
        return await asyncio.to_thread(
            self._search_files,
            relative_path,
            pattern,
            include_directories,
            include_hidden,
            max_results,
        )

    def _search_files(
        self,
        relative_path: str,
        pattern: str,
        include_directories: bool,
        include_hidden: bool,
        max_results: int,
    ) -> dict[str, Any]:
        root = self._resolve_workspace_path(relative_path)
        if not root.exists():
            raise ValueError(f"Path does not exist: {relative_path}")
        if not root.is_dir():
            raise ValueError(f"Path is not a directory: {relative_path}")

        matches: list[dict[str, str]] = []
        truncated = False
        pattern_text = pattern.replace("\\", "/")

        for target in sorted(root.rglob("*"), key=lambda item: item.as_posix().lower()):
            if not include_hidden and _path_has_hidden_part(target.relative_to(root)):
                continue
            if target.is_dir() and not include_directories:
                continue

            relative_match_path = str(target.relative_to(root)).replace("\\", "/")
            if not _match_path_pattern(relative_match_path, pattern_text):
                continue

            matches.append(
                {
                    "path": self._to_relative_path(target),
                    "type": "directory" if target.is_dir() else "file",
                }
            )
            if len(matches) >= max_results:
                truncated = True
                break

        return {
            "base_path": self._to_relative_path(root),
            "pattern": pattern,
            "matches": matches,
            "truncated": truncated,
        }


class SearchTextInFilesTool(WorkspaceTool):
    name = "search_text_in_files"
    execution_mode = "parallel"
    description = "Search UTF-8 text files in the workspace for matching text."
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Text or regular expression to search for.",
            },
            "path": {
                "type": "string",
                "description": "Relative directory inside the workspace.",
                "default": ".",
            },
            "glob": {
                "type": "string",
                "description": "Only search files that match this glob pattern.",
                "default": "*",
            },
            "regex": {
                "type": "boolean",
                "description": "Treat query as a regular expression.",
                "default": False,
            },
            "case_sensitive": {
                "type": "boolean",
                "description": "Use case-sensitive matching.",
                "default": False,
            },
            "include_hidden": {
                "type": "boolean",
                "description": "Include hidden files and directories.",
                "default": False,
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of matching lines to return.",
                "default": 50,
            },
            "max_files": {
                "type": "integer",
                "description": "Maximum number of files to inspect.",
                "default": 200,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    async def run(self, arguments: dict[str, Any]) -> ToolOutput:
        query = str(arguments.get("query", "")).strip()
        if not query:
            raise ValueError("query is required")

        relative_path = str(arguments.get("path", ".")).strip() or "."
        glob_pattern = str(arguments.get("glob", "*")).strip() or "*"
        regex = bool(arguments.get("regex", False))
        case_sensitive = bool(arguments.get("case_sensitive", False))
        include_hidden = bool(arguments.get("include_hidden", False))
        max_results = _read_positive_int(
            arguments.get("max_results", 50),
            name="max_results",
        )
        max_files = _read_positive_int(arguments.get("max_files", 200), name="max_files")

        return await asyncio.to_thread(
            self._search_text,
            query,
            relative_path,
            glob_pattern,
            regex,
            case_sensitive,
            include_hidden,
            max_results,
            max_files,
        )

    def _search_text(
        self,
        query: str,
        relative_path: str,
        glob_pattern: str,
        regex: bool,
        case_sensitive: bool,
        include_hidden: bool,
        max_results: int,
        max_files: int,
    ) -> dict[str, Any]:
        root = self._resolve_workspace_path(relative_path)
        if not root.exists():
            raise ValueError(f"Path does not exist: {relative_path}")
        if not root.is_dir():
            raise ValueError(f"Path is not a directory: {relative_path}")

        matcher = _build_text_matcher(
            query,
            regex=regex,
            case_sensitive=case_sensitive,
        )
        matches: list[dict[str, Any]] = []
        scanned_files = 0
        skipped_files = 0
        truncated = False

        for target in sorted(root.rglob("*"), key=lambda item: item.as_posix().lower()):
            if not target.is_file():
                continue
            relative_file_path = target.relative_to(root)
            if not include_hidden and _path_has_hidden_part(relative_file_path):
                continue
            if not _match_path_pattern(str(relative_file_path).replace("\\", "/"), glob_pattern):
                continue
            if scanned_files >= max_files:
                truncated = True
                break

            scanned_files += 1
            try:
                content = target.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                skipped_files += 1
                continue

            for line_number, line in enumerate(content.splitlines(), start=1):
                matched_text = matcher(line)
                if matched_text is None:
                    continue
                matches.append(
                    {
                        "path": self._to_relative_path(target),
                        "line_number": line_number,
                        "line": line,
                        "match": matched_text,
                    }
                )
                if len(matches) >= max_results:
                    truncated = True
                    break
            if truncated:
                break

        return {
            "base_path": self._to_relative_path(root),
            "query": query,
            "glob": glob_pattern,
            "regex": regex,
            "case_sensitive": case_sensitive,
            "matches": matches,
            "scanned_files": scanned_files,
            "skipped_files": skipped_files,
            "truncated": truncated,
        }


class EditTextFileTool(WritableWorkspaceTool):
    name = "edit_text_file"
    description = "Apply a small structured edit to a UTF-8 text file."
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative file path inside the workspace.",
            },
            "operation": {
                "type": "string",
                "description": "One of: replace, append, prepend.",
                "enum": ["replace", "append", "prepend"],
                "default": "replace",
            },
            "old_text": {
                "type": "string",
                "description": "Exact text to replace when operation is replace.",
                "default": "",
            },
            "new_text": {
                "type": "string",
                "description": "New text to write.",
                "default": "",
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace every matching occurrence when operation is replace.",
                "default": False,
            },
            "create_if_missing": {
                "type": "boolean",
                "description": "Create the file when it does not exist.",
                "default": False,
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        workspace: str | Path = ".",
        *,
        writes_enabled: bool = True,
    ) -> None:
        super().__init__(workspace, writes_enabled=writes_enabled)

    async def run(self, arguments: dict[str, Any]) -> ToolOutput:
        self._require_writes_enabled()
        relative_path = str(arguments.get("path", "")).strip()
        if not relative_path:
            raise ValueError("path is required")

        operation = str(arguments.get("operation", "replace")).strip().lower() or "replace"
        old_text = str(arguments.get("old_text", ""))
        new_text = str(arguments.get("new_text", ""))
        replace_all = bool(arguments.get("replace_all", False))
        create_if_missing = bool(arguments.get("create_if_missing", False))

        return await asyncio.to_thread(
            self._edit_text_file,
            relative_path,
            operation,
            old_text,
            new_text,
            replace_all,
            create_if_missing,
        )

    def _edit_text_file(
        self,
        relative_path: str,
        operation: str,
        old_text: str,
        new_text: str,
        replace_all: bool,
        create_if_missing: bool,
    ) -> dict[str, Any]:
        target = self._resolve_workspace_path(relative_path)
        file_existed = target.exists()
        if file_existed and not target.is_file():
            raise ValueError(f"Path is not a file: {relative_path}")
        if not file_existed and not create_if_missing:
            raise ValueError(f"File does not exist: {relative_path}")

        original_content = ""
        if file_existed:
            try:
                original_content = target.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("Only UTF-8 text files are supported") from exc

        if operation == "append":
            updated_content = original_content + new_text
            replacements = 0
        elif operation == "prepend":
            updated_content = new_text + original_content
            replacements = 0
        elif operation == "replace":
            if not old_text:
                raise ValueError("old_text is required when operation is replace")
            occurrences = original_content.count(old_text)
            if occurrences == 0:
                raise ValueError("old_text was not found in the file")
            if not replace_all and occurrences != 1:
                raise ValueError(
                    "old_text matched multiple times; set replace_all=true to replace them all"
                )
            replacements = occurrences if replace_all else 1
            updated_content = original_content.replace(
                old_text,
                new_text,
                -1 if replace_all else 1,
            )
        else:
            raise ValueError(f"Unsupported operation: {operation}")

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(updated_content, encoding="utf-8")
        return {
            "path": self._to_relative_path(target),
            "operation": operation,
            "created": not file_existed,
            "previous_chars": len(original_content),
            "written_chars": len(updated_content),
            "replacements": replacements,
        }

def _read_positive_int(value: Any, *, name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc

    if number <= 0:
        raise ValueError(f"{name} must be greater than 0")

    return number


def _path_has_hidden_part(relative_path: Path) -> bool:
    return any(part.startswith(".") for part in relative_path.parts if part not in {".", ".."})


def _match_path_pattern(relative_path: str, pattern: str) -> bool:
    normalized_path = relative_path.replace("\\", "/")
    normalized_pattern = str(pattern or "*").replace("\\", "/")
    return fnmatch.fnmatch(normalized_path, normalized_pattern)


def _build_text_matcher(
    query: str,
    *,
    regex: bool,
    case_sensitive: bool,
):
    if regex:
        flags = 0 if case_sensitive else re.IGNORECASE
        pattern = re.compile(query, flags)

        def match_regex(line: str) -> str | None:
            found = pattern.search(line)
            if found is None:
                return None
            return found.group(0)

        return match_regex

    normalized_query = query if case_sensitive else query.lower()

    def match_text(line: str) -> str | None:
        haystack = line if case_sensitive else line.lower()
        if normalized_query not in haystack:
            return None
        return query

    return match_text
