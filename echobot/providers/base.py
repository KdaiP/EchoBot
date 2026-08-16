from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from typing import Any

from ..models import LLMMessage, LLMResponse, LLMTool, message_content_to_text


class LLMProvider(ABC):
    @abstractmethod
    async def generate(
        self,
        messages: Sequence[LLMMessage],
        *,
        tools: Sequence[LLMTool] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        raise NotImplementedError

    async def stream_generate(
        self,
        messages: Sequence[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        response = await self.generate(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        content = message_content_to_text(response.message.content)
        if content:
            yield content
