from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from ..models import LLMMessage


SystemPromptFactory = Callable[[], str | Awaitable[str]]
SystemPromptValue = str | SystemPromptFactory


class MemorySupport(Protocol):
    async def compact_history(
        self,
        messages: list[LLMMessage],
        *,
        system_prompt: str,
        compressed_summary: str,
    ) -> MemoryPreparation: ...

    async def remember_turn(self, messages: list[LLMMessage]) -> None: ...

    def build_summary_message(self, compressed_summary: str) -> str: ...


class MemoryPreparation(Protocol):
    messages: list[LLMMessage]
    compressed_summary: str


@dataclass(slots=True)
class PreparedContext:
    request_messages: list[LLMMessage]
    persistent_messages: list[LLMMessage]
    compressed_summary: str


class ContextPreparer:
    """Build provider input and apply memory compaction at one boundary."""

    def __init__(
        self,
        system_prompt: SystemPromptValue | None,
        memory_support: MemorySupport | None,
    ) -> None:
        self._system_prompt = system_prompt
        self._memory_support = memory_support

    async def prepare(
        self,
        persistent_messages: list[LLMMessage],
        *,
        compressed_summary: str,
        extra_system_messages: Sequence[str],
        transient_system_messages: Sequence[str],
    ) -> PreparedContext:
        system_messages = await self._system_messages(extra_system_messages)
        system_prompt_text = "\n\n".join(system_messages)
        next_persistent_messages = list(persistent_messages)
        next_summary = compressed_summary

        if self._memory_support is not None:
            prepared = await self._memory_support.compact_history(
                next_persistent_messages,
                system_prompt=system_prompt_text,
                compressed_summary=next_summary,
            )
            next_persistent_messages = list(prepared.messages)
            next_summary = prepared.compressed_summary

        request_messages = [
            LLMMessage(role="system", content=content)
            for content in system_messages
        ]
        if self._memory_support is not None:
            summary_message = self._memory_support.build_summary_message(next_summary)
            if summary_message:
                request_messages.append(
                    LLMMessage(role="system", content=summary_message)
                )
        request_messages.extend(
            LLMMessage(role="system", content=content)
            for content in transient_system_messages
            if content.strip()
        )
        request_messages.extend(next_persistent_messages)
        return PreparedContext(
            request_messages=request_messages,
            persistent_messages=next_persistent_messages,
            compressed_summary=next_summary,
        )

    async def remember(self, messages: list[LLMMessage]) -> None:
        if self._memory_support is not None:
            await self._memory_support.remember_turn(messages)

    async def _system_messages(
        self,
        extra_system_messages: Sequence[str],
    ) -> list[str]:
        messages: list[str] = []
        prompt = await self._resolve_system_prompt()
        if prompt:
            messages.append(prompt)
        messages.extend(content for content in extra_system_messages if content.strip())
        return messages

    async def _resolve_system_prompt(self) -> str:
        if self._system_prompt is None:
            return ""
        if not callable(self._system_prompt):
            return self._system_prompt.strip()

        if inspect.iscoroutinefunction(self._system_prompt):
            value = await self._system_prompt()
        else:
            value = await asyncio.to_thread(self._system_prompt)
        if inspect.isawaitable(value):
            value = await value
        return str(value).strip()
