from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any, ContextManager, Protocol

from ..agent import (
    AgentCheckpoint,
    AgentCore,
    AgentEvent,
    AgentEventHandler,
    AgentRequest,
    AgentRunResult,
)
from ..models import FileInput, ImageInput, LLMMessage, message_content_to_text
from ..skill_support import SkillRegistry
from ..tools import ToolRegistry
from .sessions import Session, SessionStore


ToolRegistryFactory = Callable[[str, bool], ToolRegistry | None]
ProviderScopeFactory = Callable[[], ContextManager[None]]
logger = logging.getLogger(__name__)


class RunEventStore(Protocol):
    async def append_event(
        self,
        run_id: str,
        event: str,
        data: dict[str, Any] | None = None,
        *,
        step: int = 0,
    ) -> None: ...

@dataclass(slots=True)
class SessionRunResult:
    session: Session
    agent_result: AgentRunResult
    run_id: str | None = None


class SessionAgentRunner:
    """Run the tool-capable agent against a session's internal surface."""

    def __init__(
        self,
        agent: AgentCore,
        session_store: SessionStore,
        *,
        skill_registry: SkillRegistry | None = None,
        tool_registry_factory: ToolRegistryFactory | None = None,
        default_temperature: float | None = None,
        default_max_tokens: int | None = None,
        default_max_steps: int = 50,
        run_store: RunEventStore | None = None,
        provider_scope: ProviderScopeFactory | None = None,
    ) -> None:
        self._agent = agent
        self._session_store = session_store
        self._skill_registry = skill_registry
        self._tool_registry_factory = tool_registry_factory
        self._default_temperature = default_temperature
        self._default_max_tokens = default_max_tokens
        self._default_max_steps = max(int(default_max_steps), 1)
        self._run_store = run_store
        self._provider_scope = provider_scope
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()
        self._deleted_sessions: set[str] = set()
        self._deleted_sessions_guard = asyncio.Lock()

    async def load_session(self, session_id: str) -> Session:
        lock = await self._session_lock(session_id)
        async with lock:
            return await asyncio.to_thread(self._session_store.load_session, session_id)

    async def mark_session_deleted(self, session_id: str) -> None:
        async with self._deleted_sessions_guard:
            self._deleted_sessions.add(session_id)

    async def restore_session(self, session_id: str) -> None:
        async with self._deleted_sessions_guard:
            self._deleted_sessions.discard(session_id)

    async def run_prompt(
        self,
        session_id: str,
        prompt: str,
        *,
        image_urls: Sequence[ImageInput] | None = None,
        file_attachments: Sequence[FileInput] | None = None,
        scheduled_context: bool = False,
        extra_system_messages: Sequence[str] | None = None,
        transient_system_messages: Sequence[str] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        run_id: str | None = None,
    ) -> SessionRunResult:
        lock = await self._session_lock(session_id)
        async with lock:
            if await self._is_session_deleted(session_id):
                raise RuntimeError(f"Session is deleted: {session_id}")
            session = await self._load_execution_session(
                session_id,
                scheduled_context=scheduled_context,
            )
            tool_registry = await self._build_tool_registry(
                session.id,
                scheduled_context,
            )
            event_handler = self._build_event_handler(run_id)
            if event_handler is not None:
                await event_handler(
                    AgentEvent(
                        type="turn_started",
                        step=0,
                        data={
                            "prompt": prompt,
                            "image_count": len(image_urls or []),
                            "file_count": len(file_attachments or []),
                            "scheduled_context": scheduled_context,
                            "history_length": len(session.agent_history),
                            "tool_names": (
                                tool_registry.names()
                                if tool_registry is not None
                                else []
                            ),
                            "extra_system_messages_count": len(
                                extra_system_messages or []
                            ),
                            "transient_system_messages_count": len(
                                transient_system_messages or []
                            ),
                        },
                    )
                )

            async def save_checkpoint(checkpoint: AgentCheckpoint) -> None:
                session.agent_history = list(checkpoint.history)
                session.agent_summary = checkpoint.compressed_summary
                if not await self._is_session_deleted(session.id):
                    await asyncio.to_thread(
                        self._session_store.save_agent_context,
                        session,
                    )

            try:
                provider_scope = (
                    self._provider_scope()
                    if self._provider_scope is not None
                    else nullcontext()
                )
                with provider_scope:
                    result = await self._agent.run(
                        AgentRequest(
                            prompt=prompt,
                            history=list(session.agent_history),
                            image_urls=image_urls or (),
                            file_attachments=file_attachments or (),
                            compressed_summary=session.agent_summary,
                            skill_registry=self._skill_registry,
                            tool_registry=tool_registry,
                            extra_system_messages=extra_system_messages or (),
                            transient_system_messages=transient_system_messages or (),
                            temperature=(
                                self._default_temperature
                                if temperature is None
                                else temperature
                            ),
                            max_tokens=(
                                self._default_max_tokens
                                if max_tokens is None
                                else max_tokens
                            ),
                            max_steps=self._default_max_steps,
                        ),
                        event_handler=event_handler,
                        checkpoint_handler=save_checkpoint,
                    )
            except Exception as exc:
                if event_handler is not None:
                    await event_handler(
                        AgentEvent(
                            type="turn_failed",
                            step=0,
                            data={
                                "error": str(exc),
                                "error_type": type(exc).__name__,
                            },
                        )
                    )
                raise

            if event_handler is not None:
                await event_handler(
                    AgentEvent(
                        type="turn_completed",
                        step=result.steps,
                        data={
                            "steps": result.steps,
                            "status": result.status,
                            "history_length": len(session.agent_history),
                            "final_message": _message_to_event_dict(
                                result.response.message
                            ),
                            "usage": result.response.usage.to_dict(),
                            "compressed_summary": session.agent_summary,
                            "pending_user_input": result.pending_user_input,
                        },
                    )
                )
            return SessionRunResult(
                session=session,
                agent_result=result,
                run_id=run_id,
            )

    async def append_assistant_message(
        self,
        session_id: str,
        content: str,
    ) -> Session:
        lock = await self._session_lock(session_id)
        async with lock:
            if await self._is_session_deleted(session_id):
                raise RuntimeError(f"Session is deleted: {session_id}")
            session = await asyncio.to_thread(self._session_store.load_session, session_id)
            session.agent_history.append(LLMMessage(role="assistant", content=content))
            await asyncio.to_thread(self._session_store.save_agent_context, session)
            return session

    async def _load_execution_session(
        self,
        session_id: str,
        *,
        scheduled_context: bool,
    ) -> Session:
        if scheduled_context and session_id == "heartbeat":
            return await asyncio.to_thread(
                self._session_store.ensure_system_session,
                "heartbeat",
                "Heartbeat",
            )
        return await asyncio.to_thread(self._session_store.load_session, session_id)

    async def _build_tool_registry(
        self,
        session_id: str,
        scheduled_context: bool,
    ) -> ToolRegistry | None:
        if self._tool_registry_factory is None:
            return None
        return await asyncio.to_thread(
            self._tool_registry_factory,
            session_id,
            scheduled_context,
        )

    async def _session_lock(self, session_id: str) -> asyncio.Lock:
        async with self._locks_guard:
            lock = self._locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[session_id] = lock
            return lock

    async def _is_session_deleted(self, session_id: str) -> bool:
        async with self._deleted_sessions_guard:
            return session_id in self._deleted_sessions

    def _build_event_handler(self, run_id: str | None) -> AgentEventHandler | None:
        if self._run_store is None or run_id is None:
            return None

        async def handle_event(event: AgentEvent) -> None:
            try:
                await self._run_store.append_event(
                    run_id,
                    event.type,
                    dict(event.data),
                    step=event.step,
                )
            except Exception:
                logger.exception("Failed to persist run event '%s'", event.type)

        return handle_event


def _message_to_event_dict(message: LLMMessage) -> dict[str, Any]:
    return {
        "role": message.role,
        "content": message.content,
        "content_text": message_content_to_text(message.content),
        "name": message.name,
        "tool_call_id": message.tool_call_id,
        "tool_calls": [
            {
                "id": tool_call.id,
                "name": tool_call.name,
                "arguments": tool_call.arguments,
            }
            for tool_call in message.tool_calls
        ],
    }
