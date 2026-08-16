from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path
from typing import Any

from ..attachments import AttachmentStore
from ..models import (
    FILE_ATTACHMENT_CONTENT_BLOCK_TYPE,
    IMAGE_URL_CONTENT_BLOCK_TYPE,
)
from .base import ToolExecutionOutput, ToolOutput
from .filesystem import WorkspaceTool


class _WorkspaceAttachmentTool(WorkspaceTool):
    def _resolve_existing_file_path(self, file_path: str) -> Path:
        candidate = Path(file_path).expanduser()
        if candidate.is_absolute():
            target = candidate.resolve()
        else:
            target = self._resolve_workspace_path(file_path)

        if not target.exists():
            raise ValueError(f"File does not exist: {file_path}")
        if not target.is_file():
            raise ValueError(f"Path is not a file: {file_path}")
        return target

    def _display_path(self, target: Path) -> str:
        try:
            return self._to_relative_path(target)
        except ValueError:
            return str(target)


class ViewImageTool(_WorkspaceAttachmentTool):
    name = "view_image"
    execution_mode = "parallel"
    description = (
        "Load a local image file into the next model request so the model "
        "can inspect it visually."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Local image path. Relative paths are resolved from the "
                    "workspace. Absolute paths are also supported."
                ),
            }
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        workspace: str | Path = ".",
        *,
        attachment_store: AttachmentStore | None = None,
    ) -> None:
        super().__init__(workspace)
        self.attachment_store = attachment_store

    async def run(self, arguments: dict[str, Any]) -> ToolOutput:
        image_path = str(arguments.get("path", "")).strip()
        if not image_path:
            raise ValueError("path is required")
        if self.attachment_store is None:
            raise RuntimeError("view_image requires an attachment store")

        return await asyncio.to_thread(self._load_image, image_path)

    def _load_image(self, image_path: str) -> ToolExecutionOutput:
        if self.attachment_store is None:
            raise RuntimeError("view_image requires an attachment store")

        target = self._resolve_existing_file_path(image_path)
        image_bytes = target.read_bytes()
        content_type, _encoding = mimetypes.guess_type(target.name)
        attachment = self.attachment_store.create_image_attachment(
            image_bytes,
            content_type=content_type,
            filename=target.name,
        )
        display_path = self._display_path(target)
        return ToolExecutionOutput(
            data={
                "path": display_path,
                "attachment_id": attachment.attachment_id,
                "preview_url": attachment.preview_url,
                "width": attachment.width,
                "height": attachment.height,
                "message": f"Loaded image into model context: {display_path}",
            },
            promoted_image_urls=[attachment.to_message_image()],
        )


class SendImageToUserTool(_WorkspaceAttachmentTool):
    name = "send_image_to_user"
    description = "Send a local image file to the user in the current conversation."
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Local image path. Relative paths are resolved from the "
                    "workspace. Absolute paths are also supported."
                ),
            }
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        workspace: str | Path = ".",
        *,
        attachment_store: AttachmentStore | None = None,
    ) -> None:
        super().__init__(workspace)
        self.attachment_store = attachment_store

    async def run(self, arguments: dict[str, Any]) -> ToolOutput:
        image_path = str(arguments.get("path", "")).strip()
        if not image_path:
            raise ValueError("path is required")
        if self.attachment_store is None:
            raise RuntimeError("send_image_to_user requires an attachment store")

        return await asyncio.to_thread(self._send_image, image_path)

    def _send_image(self, image_path: str) -> ToolExecutionOutput:
        if self.attachment_store is None:
            raise RuntimeError("send_image_to_user requires an attachment store")

        target = self._resolve_existing_file_path(image_path)
        image_bytes = target.read_bytes()
        content_type, _encoding = mimetypes.guess_type(target.name)
        attachment = self.attachment_store.create_image_attachment(
            image_bytes,
            content_type=content_type,
            filename=target.name,
        )
        display_path = self._display_path(target)
        return ToolExecutionOutput(
            data={
                "path": display_path,
                "attachment_id": attachment.attachment_id,
                "url": attachment.attachment_url,
                "preview_url": attachment.preview_url,
                "width": attachment.width,
                "height": attachment.height,
                "message": f"Queued image for user delivery: {display_path}",
            },
            outbound_content_blocks=[
                {
                    "type": IMAGE_URL_CONTENT_BLOCK_TYPE,
                    "image_url": attachment.to_message_image(),
                }
            ],
        )


class SendFileToUserTool(_WorkspaceAttachmentTool):
    name = "send_file_to_user"
    description = "Send a local file to the user in the current conversation."
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Local file path. Relative paths are resolved from the "
                    "workspace. Absolute paths are also supported."
                ),
            }
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        workspace: str | Path = ".",
        *,
        attachment_store: AttachmentStore | None = None,
    ) -> None:
        super().__init__(workspace)
        self.attachment_store = attachment_store

    async def run(self, arguments: dict[str, Any]) -> ToolOutput:
        file_path = str(arguments.get("path", "")).strip()
        if not file_path:
            raise ValueError("path is required")
        if self.attachment_store is None:
            raise RuntimeError("send_file_to_user requires an attachment store")

        return await asyncio.to_thread(self._send_file, file_path)

    def _send_file(self, file_path: str) -> ToolExecutionOutput:
        if self.attachment_store is None:
            raise RuntimeError("send_file_to_user requires an attachment store")

        target = self._resolve_existing_file_path(file_path)
        file_bytes = target.read_bytes()
        content_type, _encoding = mimetypes.guess_type(target.name)
        attachment = self.attachment_store.create_file_attachment(
            file_bytes,
            content_type=content_type,
            filename=target.name,
        )
        display_path = self._display_path(target)
        file_attachment = {
            "attachment_id": attachment.attachment_id,
            "name": attachment.original_filename or target.name,
            "download_url": attachment.download_url,
            "workspace_path": display_path,
            "content_type": attachment.content_type,
            "size_bytes": attachment.size_bytes,
        }
        return ToolExecutionOutput(
            data={
                "path": display_path,
                "attachment_id": attachment.attachment_id,
                "download_url": attachment.download_url,
                "name": file_attachment["name"],
                "content_type": attachment.content_type,
                "size_bytes": attachment.size_bytes,
                "message": f"Queued file for user delivery: {display_path}",
            },
            outbound_content_blocks=[
                {
                    "type": FILE_ATTACHMENT_CONTENT_BLOCK_TYPE,
                    "file_attachment": file_attachment,
                }
            ],
        )
