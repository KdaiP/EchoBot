from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ..models import LLMMessage, is_message_content_empty
from .console import _configure_reme_internal_console_output
from .conversion import (
    _agentscope_messages_to_llm,
    _llm_messages_to_agentscope,
    _tool_response_to_text,
    _try_parse_json,
)
from .imports import ReMeLight
from .settings import MemoryPreparationResult, ReMeLightSettings


class ReMeLightSupport:
    def __init__(self, settings: ReMeLightSettings) -> None:
        if ReMeLight is None:
            raise RuntimeError("ReMeLight is unavailable in the current environment")

        self.settings = settings
        self._reme: ReMeLight | None = None
        self._start_lock = asyncio.Lock()
        _configure_reme_internal_console_output(self.settings.console_output_enabled)

    @property
    def working_dir(self) -> Path:
        return self.settings.working_dir

    @classmethod
    def is_available(cls) -> bool:
        return ReMeLight is not None

    async def ensure_started(self) -> None:
        if self._reme is not None:
            return

        async with self._start_lock:
            if self._reme is not None:
                return

            try:
                reme = ReMeLight(
                    working_dir=str(self.settings.working_dir),
                    llm_api_key=self.settings.llm_api_key,
                    llm_base_url=self.settings.llm_base_url,
                    default_as_llm_config={
                        "backend": "openai",
                        "model_name": self.settings.llm_model,
                    },
                    default_file_store_config={
                        "fts_enabled": self.settings.fts_enabled,
                        "vector_enabled": self.settings.vector_enabled,
                    },
                    vector_weight=self.settings.vector_weight,
                    candidate_multiplier=self.settings.candidate_multiplier,
                    tool_result_threshold=self.settings.tool_result_threshold,
                    retention_days=self.settings.retention_days,
                )
            except TypeError:
                # 兼容旧版本 ReMeLight（不支持 tool_result_threshold / retention_days）
                reme = ReMeLight(
                    working_dir=str(self.settings.working_dir),
                    llm_api_key=self.settings.llm_api_key,
                    llm_base_url=self.settings.llm_base_url,
                    default_as_llm_config={
                        "backend": "openai",
                        "model_name": self.settings.llm_model,
                    },
                    default_file_store_config={
                        "fts_enabled": self.settings.fts_enabled,
                        "vector_enabled": self.settings.vector_enabled,
                    },
                    vector_weight=self.settings.vector_weight,
                    candidate_multiplier=self.settings.candidate_multiplier,
                )
            await reme.start()
            await asyncio.to_thread(self._ensure_memory_files)
            self._reme = reme

    async def close(self) -> str:
        if self._reme is None:
            return ""

        summary_task_status = ""
        try:
            summary_task_status = await self._reme.await_summary_tasks()
        finally:
            await self._reme.close()
            self._reme = None

        return summary_task_status

    async def compact_history(
        self,
        messages: list[LLMMessage],
        *,
        system_prompt: str,
        compressed_summary: str,
    ) -> MemoryPreparationResult:
        if not messages:
            return MemoryPreparationResult(
                messages=[],
                compressed_summary=compressed_summary,
            )

        await self.ensure_started()
        reme = self._require_reme()
        agent_messages = _llm_messages_to_agentscope(messages)
        processed_messages, next_summary = await reme.pre_reasoning_hook(
            messages=agent_messages,
            system_prompt=system_prompt,
            compressed_summary=compressed_summary,
            language=self.settings.language,
            max_input_length=self.settings.max_input_length,
            compact_ratio=self.settings.compact_ratio,
            memory_compact_reserve=self.settings.memory_compact_reserve,
            enable_tool_result_compact=True,
            tool_result_compact_keep_n=self.settings.tool_result_compact_keep_n,
        )
        return MemoryPreparationResult(
            messages=_agentscope_messages_to_llm(processed_messages),
            compressed_summary=next_summary,
        )

    async def remember_turn(self, messages: list[LLMMessage]) -> None:
        turn_messages = [
            message
            for message in messages
            if not (message.role == "system" and is_message_content_empty(message.content))
        ]
        if not turn_messages:
            return

        await self.ensure_started()
        reme = self._require_reme()
        agent_messages = _llm_messages_to_agentscope(turn_messages)
        compacted_messages = await reme.compact_tool_result(agent_messages)
        reme.add_async_summary_task(
            messages=compacted_messages,
            language=self.settings.language,
            max_input_length=self.settings.max_input_length,
            compact_ratio=self.settings.compact_ratio,
        )

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        min_score: float = 0.1,
    ) -> dict[str, Any]:
        await self.ensure_started()
        reme = self._require_reme()
        response = await reme.memory_search(
            query=query,
            max_results=max_results,
            min_score=min_score,
        )
        text = _tool_response_to_text(response)
        parsed = _try_parse_json(text)
        if isinstance(parsed, list):
            return {
                "query": query,
                "results": parsed,
            }

        return {
            "query": query,
            "content": text,
        }

    @staticmethod
    def build_summary_message(compressed_summary: str) -> str:
        summary = compressed_summary.strip()
        if not summary:
            return ""

        return (
            "Compressed conversation summary from earlier turns. "
            "Use it as background context. Newer messages override it when they conflict.\n\n"
            f"{summary}"
        )

    def _ensure_memory_files(self) -> None:
        self.settings.working_dir.mkdir(parents=True, exist_ok=True)
        memory_file = self.settings.working_dir / "MEMORY.md"
        if not memory_file.exists():
            memory_file.write_text(
                "# MEMORY.md\n\n"
                "Use this file for durable user preferences, important decisions, and recurring setup notes.\n",
                encoding="utf-8",
            )

    def _require_reme(self) -> ReMeLight:
        if self._reme is None:
            raise RuntimeError("ReMeLight is not started")

        return self._reme
