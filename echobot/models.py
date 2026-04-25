from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal


MessageRole = Literal["system", "user", "assistant", "tool"]
ReasoningField = Literal["reasoning_content", "reasoning"]
MessageContentBlock = dict[str, Any]
MessageContent = str | list[MessageContentBlock]
ImageInput = str | Mapping[str, Any]
FileInput = str | Mapping[str, Any]

TEXT_CONTENT_BLOCK_TYPE = "text"
IMAGE_URL_CONTENT_BLOCK_TYPE = "image_url"
FILE_ATTACHMENT_CONTENT_BLOCK_TYPE = "file_attachment"


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": self.arguments,
            },
        }


@dataclass(slots=True)
class LLMMessage:
    role: MessageRole
    content: MessageContent = ""
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning_content: str = ""
    reasoning_field: ReasoningField = "reasoning_content"

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "role": self.role,
            "content": normalize_message_content(self.content),
        }

        if self.name:
            data["name"] = self.name
        if self.tool_call_id:
            data["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            data["tool_calls"] = [tool_call.to_dict() for tool_call in self.tool_calls]
        if self.role == "assistant" and self.reasoning_content:
            data[self.reasoning_field] = self.reasoning_content

        return data

    @property
    def content_text(self) -> str:
        return message_content_to_text(self.content)


@dataclass(slots=True)
class LLMTool:
    name: str
    description: str
    parameters: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(slots=True)
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    prompt_cache_hit_tokens: int = 0
    prompt_cache_miss_tokens: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "LLMUsage":
        if not data:
            return cls()

        prompt_tokens = _first_usage_int(data, "prompt_tokens", "input_tokens") or 0
        completion_tokens = (
            _first_usage_int(data, "completion_tokens", "output_tokens") or 0
        )
        total_tokens = _usage_int(data, "total_tokens")
        if total_tokens is None:
            total_tokens = prompt_tokens + completion_tokens

        prompt_cache_hit_tokens = _usage_int(data, "prompt_cache_hit_tokens")
        if prompt_cache_hit_tokens is None:
            prompt_cache_hit_tokens = (
                _nested_usage_int(data, "prompt_tokens_details", "cached_tokens")
                or _nested_usage_int(data, "input_tokens_details", "cached_tokens")
                or 0
            )

        prompt_cache_miss_tokens = _usage_int(data, "prompt_cache_miss_tokens")
        if prompt_cache_miss_tokens is None:
            prompt_cache_miss_tokens = max(
                prompt_tokens - prompt_cache_hit_tokens,
                0,
            )

        return cls(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            prompt_cache_hit_tokens=prompt_cache_hit_tokens,
            prompt_cache_miss_tokens=prompt_cache_miss_tokens,
        )

    def to_dict(self) -> dict[str, int | float | None]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "prompt_cache_hit_tokens": self.prompt_cache_hit_tokens,
            "prompt_cache_miss_tokens": self.prompt_cache_miss_tokens,
            "prompt_cache_hit_rate_percent": self.prompt_cache_hit_rate_percent(),
        }

    def prompt_cache_hit_rate_percent(self) -> float | None:
        if self.prompt_tokens <= 0:
            return None

        rate = (self.prompt_cache_hit_tokens / self.prompt_tokens) * 100
        return round(rate, 2)


@dataclass(slots=True)
class LLMResponse:
    message: LLMMessage
    model: str
    finish_reason: str | None = None
    usage: LLMUsage = field(default_factory=LLMUsage)
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_response: dict[str, Any] = field(default_factory=dict)

    @property
    def reasoning_content(self) -> str:
        return self.message.reasoning_content


def _first_usage_int(data: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = _usage_int(data, key)
        if value is not None:
            return value
    return None


def _usage_int(data: dict[str, Any], key: str) -> int | None:
    if key not in data:
        return None

    try:
        return int(data[key])
    except (TypeError, ValueError):
        return None


def _nested_usage_int(
    data: dict[str, Any],
    outer_key: str,
    inner_key: str,
) -> int | None:
    nested = data.get(outer_key)
    if not isinstance(nested, dict):
        return None
    return _usage_int(nested, inner_key)


def build_user_message_content(
    text: str,
    image_urls: Sequence[ImageInput] | None = None,
    file_attachments: Sequence[FileInput] | None = None,
) -> MessageContent:
    return build_message_content(
        text,
        image_urls=image_urls,
        file_attachments=file_attachments,
    )


def build_message_content(
    text: str,
    *,
    image_urls: Sequence[ImageInput] | None = None,
    file_attachments: Sequence[FileInput] | None = None,
) -> MessageContent:
    cleaned_text = str(text or "").strip()
    cleaned_images = [
        image_payload
        for image_payload in (
            normalize_image_input(image_input)
            for image_input in image_urls or []
        )
        if image_payload is not None
    ]
    cleaned_files = [
        file_payload
        for file_payload in (
            normalize_file_attachment_input(file_input)
            for file_input in file_attachments or []
        )
        if file_payload is not None
    ]
    if not cleaned_images and not cleaned_files:
        return cleaned_text

    content_blocks: list[MessageContentBlock] = []
    if cleaned_text:
        content_blocks.append(
            {
                "type": TEXT_CONTENT_BLOCK_TYPE,
                "text": cleaned_text,
            }
        )
    for file_attachment in cleaned_files:
        content_blocks.append(
            {
                "type": FILE_ATTACHMENT_CONTENT_BLOCK_TYPE,
                "file_attachment": file_attachment,
            }
        )
    for image_url in cleaned_images:
        content_blocks.append(
            {
                "type": IMAGE_URL_CONTENT_BLOCK_TYPE,
                "image_url": image_url,
            }
        )
    return content_blocks


def normalize_message_content(value: Any) -> MessageContent:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return str(value or "")

    blocks: list[MessageContentBlock] = []
    for item in value:
        normalized_block = normalize_message_content_block(item)
        if normalized_block is not None:
            blocks.append(normalized_block)
    return blocks


def normalize_message_content_block(value: Any) -> MessageContentBlock | None:
    if not isinstance(value, Mapping):
        return None

    block_type = str(value.get("type", "")).strip()
    if not block_type:
        return None

    if block_type == TEXT_CONTENT_BLOCK_TYPE:
        text = str(value.get("text", "")).strip()
        if not text:
            return None
        return {
            "type": TEXT_CONTENT_BLOCK_TYPE,
            "text": text,
        }

    if block_type == IMAGE_URL_CONTENT_BLOCK_TYPE:
        image_url = normalize_image_input(value.get("image_url"))
        if image_url is None:
            return None
        return {
            "type": IMAGE_URL_CONTENT_BLOCK_TYPE,
            "image_url": image_url,
        }

    if block_type == FILE_ATTACHMENT_CONTENT_BLOCK_TYPE:
        file_attachment = normalize_file_attachment_input(value.get("file_attachment"))
        if file_attachment is None:
            return None
        return {
            "type": FILE_ATTACHMENT_CONTENT_BLOCK_TYPE,
            "file_attachment": file_attachment,
        }

    return dict(value)


def message_content_blocks(content: MessageContent) -> list[MessageContentBlock]:
    normalized = normalize_message_content(content)
    if isinstance(normalized, str):
        cleaned_text = normalized.strip()
        if not cleaned_text:
            return []
        return [
            {
                "type": TEXT_CONTENT_BLOCK_TYPE,
                "text": cleaned_text,
            }
        ]
    return normalized


def message_content_to_text(content: MessageContent) -> str:
    if isinstance(content, str):
        return content

    text_parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue

        block_type = str(block.get("type", "")).strip()
        if block_type == TEXT_CONTENT_BLOCK_TYPE:
            text = str(block.get("text", "")).strip()
            if text:
                text_parts.append(text)
            continue

        if block_type == IMAGE_URL_CONTENT_BLOCK_TYPE:
            text_parts.append("[image]")
            continue

        if block_type == FILE_ATTACHMENT_CONTENT_BLOCK_TYPE:
            summary = file_attachment_summary(block.get("file_attachment"))
            if summary:
                text_parts.append(summary)
            continue

        if block_type:
            text_parts.append(f"[{block_type}]")

    return "\n\n".join(part for part in text_parts if part)


def message_content_image_urls(content: MessageContent) -> list[str]:
    if isinstance(content, str):
        return []

    image_urls: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if str(block.get("type", "")).strip() != IMAGE_URL_CONTENT_BLOCK_TYPE:
            continue

        image_url = block.get("image_url")
        if not isinstance(image_url, dict):
            continue

        url = str(image_url.get("url", "")).strip()
        if url:
            image_urls.append(url)

    return image_urls


def is_message_content_empty(content: MessageContent) -> bool:
    if isinstance(content, str):
        return not content.strip()

    return (
        len(message_content_image_urls(content)) == 0
        and len(message_content_file_attachments(content)) == 0
        and not message_content_to_text(content).strip()
    )


def normalize_image_input(value: ImageInput) -> dict[str, str] | None:
    if isinstance(value, Mapping):
        url = str(value.get("url", "")).strip()
        if not url:
            return None

        image_payload = {"url": url}
        preview_url = str(value.get("preview_url", "")).strip()
        if preview_url:
            image_payload["preview_url"] = preview_url
        attachment_id = str(value.get("attachment_id", "")).strip()
        if attachment_id:
            image_payload["attachment_id"] = attachment_id
        return image_payload

    url = str(value or "").strip()
    if not url:
        return None
    return {"url": url}


def normalize_file_attachment_input(value: FileInput) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        attachment_id = str(value.get("attachment_id", "")).strip()
        download_url = str(value.get("download_url", "")).strip()
        name = str(value.get("name", "")).strip()
        workspace_path = str(value.get("workspace_path", "")).strip()
        content_type = str(value.get("content_type", "")).strip()
        size_bytes = _optional_positive_int(value.get("size_bytes"))

        if not any([attachment_id, download_url, name, workspace_path]):
            return None

        normalized: dict[str, Any] = {
            "name": name or "file",
        }
        if attachment_id:
            normalized["attachment_id"] = attachment_id
        if download_url:
            normalized["download_url"] = download_url
        if workspace_path:
            normalized["workspace_path"] = workspace_path
        if content_type:
            normalized["content_type"] = content_type
        if size_bytes is not None:
            normalized["size_bytes"] = size_bytes
        return normalized

    attachment_id = str(value or "").strip()
    if not attachment_id:
        return None
    return {
        "attachment_id": attachment_id,
        "name": "file",
    }


def message_content_file_attachments(content: MessageContent) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return []

    file_attachments: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if str(block.get("type", "")).strip() != FILE_ATTACHMENT_CONTENT_BLOCK_TYPE:
            continue

        normalized = normalize_file_attachment_input(block.get("file_attachment"))
        if normalized is not None:
            file_attachments.append(normalized)
    return file_attachments


def file_attachment_summary(value: Any) -> str:
    normalized = normalize_file_attachment_input(value)
    if normalized is None:
        return ""

    name = str(normalized.get("name", "")).strip() or "file"
    details = [name]

    workspace_path = str(normalized.get("workspace_path", "")).strip()
    if workspace_path:
        details.append(f"path={workspace_path}")

    content_type = str(normalized.get("content_type", "")).strip()
    if content_type:
        details.append(f"type={content_type}")

    size_bytes = _optional_positive_int(normalized.get("size_bytes"))
    if size_bytes is not None:
        details.append(f"size={size_bytes} bytes")

    return "file: " + " | ".join(details)


def _optional_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return parsed
