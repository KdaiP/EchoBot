from __future__ import annotations

import json
from typing import Any

from ..models import (
    FILE_ATTACHMENT_CONTENT_BLOCK_TYPE,
    LLMMessage,
    ToolCall,
    file_attachment_summary,
    message_content_to_text,
    normalize_message_content,
)
from .imports import Msg


def _llm_messages_to_agentscope(messages: list[LLMMessage]) -> list[Msg]:
    tool_name_by_call_id: dict[str, str] = {}
    converted: list[Msg] = []

    for message in messages:
        if message.role == "assistant" and message.tool_calls:
            blocks: list[dict[str, Any]] = []
            if message.reasoning_content:
                blocks.append(
                    {
                        "type": "think",
                        "think": message.reasoning_content,
                        "field": message.reasoning_field,
                    }
                )
            text_content = message_content_to_text(message.content).strip()
            if text_content:
                blocks.append(
                    {
                        "type": "text",
                        "text": text_content,
                    }
                )
            for tool_call in message.tool_calls:
                tool_name_by_call_id[tool_call.id] = tool_call.name
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tool_call.id,
                        "name": tool_call.name,
                        "input": _parse_json_object(tool_call.arguments),
                    }
                )
            converted.append(
                Msg(
                    name="assistant",
                    role="assistant",
                    content=blocks,
                )
            )
            continue

        if message.role == "tool":
            call_id = message.tool_call_id or "tool-result"
            tool_name = tool_name_by_call_id.get(call_id, "tool")
            converted.append(
                Msg(
                    name="tool",
                    role="user",
                    content=[
                        {
                            "type": "tool_result",
                            "id": call_id,
                            "name": tool_name,
                            "output": message.content,
                        }
                    ],
                )
            )
            continue

        content = normalize_message_content(message.content)
        if message.role == "assistant" and message.reasoning_content:
            blocks: list[dict[str, Any]] = [
                {
                    "type": "think",
                    "think": message.reasoning_content,
                    "field": message.reasoning_field,
                }
            ]
            if isinstance(content, list):
                blocks.extend(content)
            elif content.strip():
                blocks.append(
                    {
                        "type": "text",
                        "text": content,
                    }
                )
            content = blocks

        converted.append(
            Msg(
                name=message.role,
                role=message.role,
                content=content,
            )
        )

    return converted


def _agentscope_messages_to_llm(messages: list[Msg]) -> list[LLMMessage]:
    converted: list[LLMMessage] = []

    for message in messages:
        blocks = message.get_content_blocks()
        tool_use_blocks = [
            block
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "tool_use"
        ]
        tool_result_blocks = [
            block
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "tool_result"
        ]

        if tool_result_blocks:
            for block in tool_result_blocks:
                converted.append(
                    LLMMessage(
                        role="tool",
                        content=_serialize_tool_result_output(block.get("output")),
                        tool_call_id=str(block.get("id", "")),
                    )
                )
            continue

        text_content = _collect_text_content(blocks)
        reasoning_content = _collect_reasoning_content(blocks)
        reasoning_field = _collect_reasoning_field(blocks)
        if tool_use_blocks:
            converted.append(
                LLMMessage(
                    role="assistant",
                    content=text_content,
                    tool_calls=[
                        ToolCall(
                            id=str(block.get("id", "")),
                            name=str(block.get("name", "")),
                            arguments=json.dumps(
                                block.get("input", {}),
                                ensure_ascii=False,
                            ),
                        )
                        for block in tool_use_blocks
                    ],
                    reasoning_content=reasoning_content,
                    reasoning_field=reasoning_field,
                )
            )
            continue

        converted.append(
            LLMMessage(
                role=message.role,
                content=_collect_message_content(blocks),
                reasoning_content=(
                    reasoning_content if message.role == "assistant" else ""
                ),
                reasoning_field=reasoning_field,
            )
        )

    return converted


def _collect_text_content(blocks: list[dict[str, Any]]) -> str:
    texts = [
        str(block.get("text", "")).strip()
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "\n\n".join(text for text in texts if text)


def _collect_reasoning_content(blocks: list[dict[str, Any]]) -> str:
    texts = [
        str(block.get("think", "")).strip()
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "think"
    ]
    return "\n".join(text for text in texts if text)


def _collect_reasoning_field(blocks: list[dict[str, Any]]) -> str:
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "think":
            continue
        if block.get("field") == "reasoning":
            return "reasoning"
    return "reasoning_content"


def _collect_message_content(
    blocks: list[dict[str, Any]],
) -> str | list[dict[str, Any]]:
    filtered_blocks: list[dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type", "")).strip()
        if block_type in {"text", "image_url"}:
            filtered_blocks.append(dict(block))
            continue
        if block_type == FILE_ATTACHMENT_CONTENT_BLOCK_TYPE:
            summary = file_attachment_summary(block.get("file_attachment"))
            if summary:
                filtered_blocks.append(
                    {
                        "type": "text",
                        "text": summary,
                    }
                )

    if filtered_blocks:
        return filtered_blocks
    return _collect_text_content(blocks)


def _parse_json_object(raw_text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        return {"raw": raw_text}

    if isinstance(parsed, dict):
        return parsed

    return {"value": parsed}


def _serialize_tool_result_output(output: Any) -> str:
    if isinstance(output, str):
        return output

    return json.dumps(output, ensure_ascii=False)


def _tool_response_to_text(response: Any) -> str:
    content_blocks = getattr(response, "content", [])
    if not isinstance(content_blocks, list):
        return str(response)

    texts: list[str] = []
    for block in content_blocks:
        if isinstance(block, dict):
            text = block.get("text")
        else:
            text = getattr(block, "text", None)
        if isinstance(text, str) and text.strip():
            texts.append(text)

    return "\n\n".join(texts)


def _try_parse_json(text: str) -> dict[str, Any] | list[Any] | None:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None

    if isinstance(parsed, (dict, list)):
        return parsed

    return None
