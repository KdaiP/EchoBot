from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from ..models import (
    FileInput,
    ImageInput,
    MessageContent,
    message_content_to_text,
    normalize_message_content,
)


def _slug(value: str) -> str:
    cleaned = value.strip().lower()
    if not cleaned:
        return "default"
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789-_"
    normalized = "".join(character for character in cleaned if character in allowed)
    return normalized or "default"


@dataclass(slots=True)
class ChannelAddress:
    channel: str
    chat_id: str
    thread_id: str | None = None
    user_id: str | None = None

    @property
    def route_key(self) -> str:
        payload = self.to_dict()
        digest = hashlib.sha1(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:8]
        parts = [_slug(self.channel), _slug(self.chat_id)]
        if self.thread_id:
            parts.append(_slug(self.thread_id))
        parts.append(digest)
        return "__".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "chat_id": self.chat_id,
            "thread_id": self.thread_id,
            "user_id": self.user_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChannelAddress":
        return cls(
            channel=str(data.get("channel", "")).strip(),
            chat_id=str(data.get("chat_id", "")).strip(),
            thread_id=_read_optional_text(data.get("thread_id")),
            user_id=_read_optional_text(data.get("user_id")),
        )


@dataclass(slots=True)
class InboundMessage:
    address: ChannelAddress
    sender_id: str
    text: str
    image_urls: list[ImageInput] = field(default_factory=list)
    files: list[FileInput] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def route_key(self) -> str:
        return self.address.route_key


@dataclass(slots=True)
class OutboundMessage:
    address: ChannelAddress
    text: str = ""
    content: MessageContent | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.content is None:
            self.content = normalize_message_content(self.text)
        else:
            self.content = normalize_message_content(self.content)

        if not self.text:
            self.text = message_content_to_text(self.content)


@dataclass(slots=True)
class DeliveryTarget:
    address: ChannelAddress
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address.to_dict(),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DeliveryTarget":
        return cls(
            address=ChannelAddress.from_dict(
                dict(data.get("address", {})),
            ),
            metadata=dict(data.get("metadata", {})),
        )


def _read_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
