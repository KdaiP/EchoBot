from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    OpenAIError,
)

from ..attachments import ATTACHMENT_URL_PREFIX, AttachmentStore
from ..models import (
    FILE_ATTACHMENT_CONTENT_BLOCK_TYPE,
    LLMMessage,
    LLMResponse,
    LLMTool,
    LLMUsage,
    ToolCall,
    file_attachment_summary,
    message_content_to_text,
    normalize_message_content,
)
from .base import LLMProvider

logger = logging.getLogger(__name__)
_REASONING_RESPONSE_FIELDS = ("reasoning_content", "reasoning")
_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_TIMEOUT = 60.0
_DEFAULT_MAX_RETRIES = 2
_MAX_ERROR_DETAIL_CHARS = 4000


@dataclass(slots=True)
class OpenAICompatibleSettings:
    api_key: str
    model: str
    base_url: str = _DEFAULT_BASE_URL
    timeout: float = _DEFAULT_TIMEOUT
    max_retries: int = _DEFAULT_MAX_RETRIES
    extra_headers: dict[str, str] = field(default_factory=dict)
    extra_body: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.base_url = _normalize_base_url(self.base_url)

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        prefix: str = "LLM_",
    ) -> "OpenAICompatibleSettings":
        source = os.environ if env is None else env
        api_key_name = f"{prefix}API_KEY"
        model_name = f"{prefix}MODEL"
        base_url_name = f"{prefix}BASE_URL"
        timeout_name = f"{prefix}TIMEOUT"
        max_retries_name = f"{prefix}MAX_RETRIES"
        extra_headers_name = f"{prefix}EXTRA_HEADERS"
        extra_body_name = f"{prefix}EXTRA_BODY"

        api_key = _get_required_env(source, api_key_name)
        model = _get_required_env(source, model_name)
        base_url = _get_optional_env(
            source,
            base_url_name,
            default=_DEFAULT_BASE_URL,
        )
        timeout_text = _get_optional_env(
            source,
            timeout_name,
            default=str(_DEFAULT_TIMEOUT),
        )
        max_retries_text = _get_optional_env(
            source,
            max_retries_name,
            default=str(_DEFAULT_MAX_RETRIES),
        )
        extra_headers_text = _get_optional_env(source, extra_headers_name)
        extra_body_text = _get_optional_env(source, extra_body_name)

        try:
            timeout = float(timeout_text)
        except ValueError as exc:
            raise ValueError(f"{timeout_name} must be a number") from exc
        if timeout <= 0:
            raise ValueError(f"{timeout_name} must be greater than zero")

        try:
            max_retries = int(max_retries_text)
        except ValueError as exc:
            raise ValueError(f"{max_retries_name} must be an integer") from exc
        if max_retries < 0:
            raise ValueError(f"{max_retries_name} must be zero or greater")

        extra_headers: dict[str, str] = {}
        if extra_headers_text is not None:
            try:
                parsed_headers = json.loads(extra_headers_text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{extra_headers_name} must be valid JSON") from exc
            if not isinstance(parsed_headers, dict):
                raise ValueError(f"{extra_headers_name} must be a JSON object")
            if not all(
                isinstance(name, str) and isinstance(value, str)
                for name, value in parsed_headers.items()
            ):
                raise ValueError(
                    f"{extra_headers_name} keys and values must be strings"
                )
            extra_headers = parsed_headers

        extra_body: dict[str, Any] = {}
        if extra_body_text is not None:
            try:
                parsed = json.loads(extra_body_text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{extra_body_name} must be valid JSON") from exc
            if not isinstance(parsed, dict):
                raise ValueError(f"{extra_body_name} must be a JSON object")
            extra_body = parsed

        return cls(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            extra_headers=extra_headers,
            extra_body=extra_body,
        )


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        settings: OpenAICompatibleSettings,
        *,
        attachment_store: AttachmentStore | None = None,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.settings = settings
        self._attachment_store = attachment_store
        self._client = client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def list_models(self) -> list[str]:
        try:
            result = await self._get_client().models.list()
        except APIStatusError as exc:
            raise _status_error(exc) from exc
        except APITimeoutError as exc:
            raise RuntimeError(f"LLM provider request timed out: {exc}") from exc
        except APIConnectionError as exc:
            raise RuntimeError(f"LLM provider network error: {exc}") from exc
        except OpenAIError as exc:
            raise RuntimeError(f"LLM provider request failed: {exc}") from exc

        model_ids = {
            str(getattr(model, "id", "") or "").strip()
            for model in result.data
        }
        return sorted(model_id for model_id in model_ids if model_id)

    async def generate(
        self,
        messages: Sequence[LLMMessage],
        *,
        tools: Sequence[LLMTool] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        payload = await asyncio.to_thread(
            self._build_payload_sync,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        try:
            completion = await self._get_client().chat.completions.create(
                **payload,
                extra_body=self.settings.extra_body or None,
            )
        except APIStatusError as exc:
            raise _status_error(exc) from exc
        except APITimeoutError as exc:
            raise RuntimeError(f"LLM provider request timed out: {exc}") from exc
        except APIConnectionError as exc:
            raise RuntimeError(f"LLM provider network error: {exc}") from exc
        except OpenAIError as exc:
            raise RuntimeError(f"LLM provider request failed: {exc}") from exc

        response_data = _model_to_dict(completion)
        return self._parse_response(response_data)

    async def stream_generate(
        self,
        messages: Sequence[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        payload = await asyncio.to_thread(
            self._build_payload_sync,
            messages=messages,
            tools=None,
            tool_choice=None,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        payload["stream"] = True

        try:
            stream = await self._get_client().chat.completions.create(
                **payload,
                extra_body=self.settings.extra_body or None,
            )
            async with stream:
                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    if choice.finish_reason == "length":
                        logger.warning(
                            "LLM stream hit max_tokens limit for model '%s'",
                            self.settings.model,
                        )
                    if choice.delta.content:
                        yield choice.delta.content
        except APIStatusError as exc:
            raise _status_error(exc) from exc
        except APITimeoutError as exc:
            raise RuntimeError(f"LLM provider request timed out: {exc}") from exc
        except APIConnectionError as exc:
            raise RuntimeError(f"LLM provider network error: {exc}") from exc
        except OpenAIError as exc:
            raise RuntimeError(f"LLM provider request failed: {exc}") from exc

    def _build_payload_sync(
        self,
        *,
        messages: Sequence[LLMMessage],
        tools: Sequence[LLMTool] | None,
        tool_choice: str | dict[str, Any] | None,
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.settings.model,
            "messages": [
                self._message_payload(message)
                for message in _merge_system_messages(messages)
            ],
        }

        if tools:
            payload["tools"] = [tool.to_dict() for tool in tools]
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        return payload

    def _message_payload(self, message: LLMMessage) -> dict[str, Any]:
        payload = message.to_dict()
        content = payload.get("content")
        if not isinstance(content, list):
            return payload

        resolved_content: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue

            block_type = str(block.get("type", "")).strip()
            if block_type == FILE_ATTACHMENT_CONTENT_BLOCK_TYPE:
                file_attachment = block.get("file_attachment")
                if not isinstance(file_attachment, dict):
                    continue
                attachment_text = self._file_attachment_text(file_attachment)
                if attachment_text:
                    resolved_content.append(
                        {
                            "type": "text",
                            "text": attachment_text,
                        }
                    )
                continue

            if block_type != "image_url":
                resolved_content.append(dict(block))
                continue

            image_url = block.get("image_url")
            if not isinstance(image_url, dict):
                resolved_content.append(dict(block))
                continue

            resolved_content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": self._resolve_image_url(image_url),
                    },
                }
            )

        payload["content"] = resolved_content
        return payload

    def _resolve_image_url(self, image_url: dict[str, Any]) -> str:
        attachment_id = str(image_url.get("attachment_id", "")).strip()
        raw_url = str(image_url.get("url", "")).strip()

        if not attachment_id and raw_url.startswith(ATTACHMENT_URL_PREFIX):
            attachment_id = raw_url.removeprefix(ATTACHMENT_URL_PREFIX)

        if attachment_id:
            if self._attachment_store is None:
                raise RuntimeError("Image attachments require an attachment store")
            return self._attachment_store.image_attachment_data_url(attachment_id)

        return raw_url

    def _file_attachment_text(self, file_attachment: dict[str, Any]) -> str:
        summary = file_attachment_summary(file_attachment)
        if not summary:
            return ""
        return (
            "The user attached a local file for this request.\n"
            f"{summary}\n"
            "Use the available file or workspace tools if you need to inspect it."
        )

    def _parse_response(self, data: dict[str, Any]) -> LLMResponse:
        choices = data.get("choices")
        if not choices:
            raise RuntimeError("LLM provider response is missing choices")

        choice = choices[0]
        message_data = choice.get("message", {})
        tool_calls: list[ToolCall] = []
        for item in message_data.get("tool_calls") or []:
            if not isinstance(item, dict):
                continue
            function_data = item.get("function", {})
            if not isinstance(function_data, dict):
                function_data = {}
            tool_calls.append(
                ToolCall(
                    id=item.get("id", ""),
                    name=function_data.get("name", ""),
                    arguments=function_data.get("arguments", ""),
                )
            )

        content = message_data.get("content") or ""
        reasoning_content, reasoning_field = _extract_reasoning_content(message_data)

        assistant_message = LLMMessage(
            role=message_data.get("role", "assistant"),
            content=normalize_message_content(content),
            tool_calls=tool_calls,
            reasoning_content=reasoning_content,
            reasoning_field=reasoning_field,
        )

        return LLMResponse(
            message=assistant_message,
            model=data.get("model", self.settings.model),
            finish_reason=choice.get("finish_reason"),
            usage=LLMUsage.from_dict(data.get("usage")),
            tool_calls=tool_calls,
            raw_response=data,
        )

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self.settings.api_key,
                base_url=self.settings.base_url,
                timeout=self.settings.timeout,
                max_retries=self.settings.max_retries,
                default_headers=self.settings.extra_headers or None,
            )
        return self._client

def _model_to_dict(model: Any) -> dict[str, Any]:
    model_dump = getattr(model, "model_dump", None)
    if not callable(model_dump):
        raise RuntimeError(
            f"LLM provider returned unsupported response type: {type(model).__name__}"
        )

    data = model_dump(mode="json")
    if not isinstance(data, dict):
        raise RuntimeError("LLM provider response must be a JSON object")
    return data


def _status_error(exc: APIStatusError) -> RuntimeError:
    detail = exc.body
    if isinstance(detail, (dict, list)):
        detail_text = json.dumps(detail, ensure_ascii=False)
    elif detail is None:
        detail_text = str(exc)
    else:
        detail_text = str(detail)

    detail_text = detail_text.strip()
    if len(detail_text) > _MAX_ERROR_DETAIL_CHARS:
        omitted_chars = len(detail_text) - _MAX_ERROR_DETAIL_CHARS
        detail_text = (
            f"{detail_text[:_MAX_ERROR_DETAIL_CHARS]}"
            f"... [truncated {omitted_chars} chars]"
        )
    return RuntimeError(
        f"LLM provider request failed: status={exc.status_code}, detail={detail_text}"
    )


def _merge_system_messages(messages: Sequence[LLMMessage]) -> list[LLMMessage]:
    """Merge consecutive leading system messages into one.

    Some backends (e.g. vLLM) reject requests that contain more than one
    system message or a system message that is not at position 0.
    """
    if not messages:
        return []

    system_parts: list[str] = []
    rest_start = 0
    for i, msg in enumerate(messages):
        if msg.role == "system":
            system_parts.append(message_content_to_text(msg.content))
            rest_start = i + 1
        else:
            break

    if len(system_parts) <= 1:
        return list(messages)

    merged = LLMMessage(role="system", content="\n\n".join(system_parts))
    return [merged, *messages[rest_start:]]


def _extract_reasoning_content(data: dict[str, Any]) -> tuple[str, str]:
    for field_name in _REASONING_RESPONSE_FIELDS:
        value = data.get(field_name)
        if value:
            return str(value), field_name
    return "", "reasoning_content"


def _normalize_base_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    for suffix in ("/chat/completions", "/chat"):
        if normalized.lower().endswith(suffix):
            return normalized[: -len(suffix)].rstrip("/")
    return normalized


def _get_required_env(source: Mapping[str, str], name: str) -> str:
    value = _get_optional_env(source, name)
    if value is None:
        raise ValueError(f"Missing required environment variable: {name}")

    return value


def _get_optional_env(
    source: Mapping[str, str],
    name: str,
    default: str | None = None,
) -> str | None:
    value = source.get(name)
    if value is None:
        return default

    cleaned = value.strip()
    if not cleaned:
        return default

    return cleaned
