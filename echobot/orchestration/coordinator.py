from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, ContextManager

from ..models import (
    FileInput,
    ImageInput,
    LLMMessage,
    MessageContent,
    TEXT_CONTENT_BLOCK_TYPE,
    build_user_message_content,
    is_message_content_empty,
    message_content_blocks,
    message_content_to_text,
)
from ..runtime.session_runner import SessionAgentRunner
from ..runtime.sessions import Session, SessionStore
from .decision import DecisionEngine
from .runs import (
    RUN_CANCELLED_TEXT,
    AgentRun,
    CompletionCallback,
    OrchestratedTurnResult,
    RunStore,
    run_can_retry,
)
from .roleplay import RoleplayEngine, ScheduledCronJobInfo, StreamCallback
from .route_modes import (
    RouteMode,
    route_mode_from_metadata,
    set_route_mode,
)
from .roles import RoleCard, RoleCardRegistry, role_name_from_metadata, set_role_name


BackgroundTaskFactory = Callable[[], Awaitable[None]]
ProviderScopeFactory = Callable[[], ContextManager[None]]
AGENT_HANDOFF_MAX_MESSAGES = 6
AGENT_HANDOFF_MAX_TOTAL_CHARS = 6000
AGENT_HANDOFF_MAX_MESSAGE_CHARS = 1800
PENDING_USER_INPUT_METADATA_KEY = "pending_user_input"


class ConversationCoordinator:
    def __init__(
        self,
        *,
        session_store: SessionStore,
        agent_runner: SessionAgentRunner,
        decision_engine: DecisionEngine,
        roleplay_engine: RoleplayEngine,
        role_registry: RoleCardRegistry,
        delegated_ack_enabled: bool = True,
        run_store: RunStore | None = None,
        provider_scope: ProviderScopeFactory | None = None,
    ) -> None:
        self._session_store = session_store
        self._agent_runner = agent_runner
        self._decision_engine = decision_engine
        self._roleplay_engine = roleplay_engine
        self._role_registry = role_registry
        self._delegated_ack_enabled = delegated_ack_enabled
        self._runs = run_store or RunStore()
        self._provider_scope = provider_scope
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._session_locks_guard = asyncio.Lock()
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._run_tasks: dict[str, asyncio.Task[None]] = {}
        self._deleted_sessions: set[str] = set()
        self._deleted_sessions_guard = asyncio.Lock()

    @property
    def delegated_ack_enabled(self) -> bool:
        return self._delegated_ack_enabled

    def set_delegated_ack_enabled(self, enabled: bool) -> None:
        self._delegated_ack_enabled = bool(enabled)

    async def handle_user_turn(
        self,
        session_id: str,
        prompt: str,
        *,
        image_urls: list[ImageInput] | None = None,
        file_attachments: list[FileInput] | None = None,
        role_name: str | None = None,
        route_mode: RouteMode | None = None,
        completion_callback: CompletionCallback | None = None,
        retry_of_run_id: str | None = None,
        attempt: int = 1,
    ) -> OrchestratedTurnResult:
        return await self.handle_user_turn_stream(
            session_id,
            prompt,
            image_urls=image_urls,
            file_attachments=file_attachments,
            role_name=role_name,
            route_mode=route_mode,
            completion_callback=completion_callback,
            retry_of_run_id=retry_of_run_id,
            attempt=attempt,
        )

    async def handle_user_turn_stream(
        self,
        session_id: str,
        prompt: str,
        *,
        image_urls: list[ImageInput] | None = None,
        file_attachments: list[FileInput] | None = None,
        role_name: str | None = None,
        route_mode: RouteMode | None = None,
        completion_callback: CompletionCallback | None = None,
        on_chunk: StreamCallback | None = None,
        retry_of_run_id: str | None = None,
        attempt: int = 1,
    ) -> OrchestratedTurnResult:
        provider_scope = (
            self._provider_scope()
            if self._provider_scope is not None
            else nullcontext()
        )
        with provider_scope:
            return await self._run_user_turn_stream(
                session_id,
                prompt,
                image_urls=image_urls,
                file_attachments=file_attachments,
                role_name=role_name,
                route_mode=route_mode,
                completion_callback=completion_callback,
                on_chunk=on_chunk,
                retry_of_run_id=retry_of_run_id,
                attempt=attempt,
            )

    async def _run_user_turn_stream(
        self,
        session_id: str,
        prompt: str,
        *,
        image_urls: list[ImageInput] | None = None,
        file_attachments: list[FileInput] | None = None,
        role_name: str | None = None,
        route_mode: RouteMode | None = None,
        completion_callback: CompletionCallback | None = None,
        on_chunk: StreamCallback | None = None,
        retry_of_run_id: str | None = None,
        attempt: int = 1,
    ) -> OrchestratedTurnResult:
        await self.restore_session(session_id)
        chunk_handler = on_chunk or _discard_stream_chunk
        lock = await self._session_lock(session_id)
        async with lock:
            session = await asyncio.to_thread(
                self._session_store.load_session,
                session_id,
            )
            role_card = self._resolve_turn_role(session, role_name)
            resolved_route_mode = self._resolve_turn_route_mode(session, route_mode)
            pending_user_input = _pending_user_input_from_metadata(session.metadata)
            if pending_user_input is None:
                decision = await self._decision_engine.decide(
                    prompt,
                    history=list(session.history[-8:]),
                    route_mode=resolved_route_mode,
                )
            else:
                decision = _forced_agent_decision_for_pending_input()

            if not decision.requires_agent:
                response_text = await self._roleplay_engine.stream_chat_reply(
                    session=session,
                    user_input=prompt,
                    image_urls=image_urls,
                    file_attachments=file_attachments,
                    role_card=role_card,
                    on_chunk=chunk_handler,
                )
                session.history.extend(
                    [
                        LLMMessage(
                            role="user",
                            content=build_user_message_content(
                                prompt,
                                image_urls,
                                file_attachments,
                            ),
                        ),
                        LLMMessage(role="assistant", content=response_text),
                    ]
                )
                await asyncio.to_thread(self._session_store.save_session, session)
                return OrchestratedTurnResult(
                    session=session,
                    response_text=response_text,
                    delegated=False,
                    completed=True,
                    response_content=response_text,
                    role_name=role_card.name,
                    steps=1,
                    agent_summary=session.agent_summary,
                )

            immediate_response = ""
            if self._delegated_ack_enabled and pending_user_input is None:
                immediate_response = await self._roleplay_engine.delegated_ack(
                    session=session,
                    user_input=prompt,
                    image_urls=image_urls,
                    file_attachments=file_attachments,
                    role_card=role_card,
                )
            handoff_text = _build_agent_handoff_text(
                session=session,
            )
            continuation_text = _build_pending_user_input_continuation_text(
                pending_user_input,
            )
            session.history.append(
                LLMMessage(
                    role="user",
                    content=build_user_message_content(
                        prompt,
                        image_urls,
                        file_attachments,
                    ),
                )
            )
            if immediate_response.strip():
                session.history.append(
                    LLMMessage(role="assistant", content=immediate_response)
                )
            if pending_user_input is not None:
                session.metadata = _clear_pending_user_input(session.metadata)
            await asyncio.to_thread(self._session_store.save_session, session)
            run_id = self._runs.create_run_id()
            run = await self._runs.create(
                run_id=run_id,
                session_id=session.id,
                prompt=prompt,
                immediate_response=immediate_response,
                role_name=role_card.name,
                route_mode=resolved_route_mode,
                image_urls=image_urls or [],
                file_attachments=file_attachments or [],
                attempt=attempt,
                retry_of_run_id=retry_of_run_id,
            )
            self._start_background_run(
                run.run_id,
                lambda: self._run_agent_run(
                    run.run_id,
                    session_id=session.id,
                    prompt=prompt,
                    image_urls=image_urls,
                    file_attachments=file_attachments,
                    handoff_text=handoff_text,
                    continuation_text=continuation_text,
                    completion_callback=completion_callback,
                ),
            )
            if immediate_response.strip():
                await chunk_handler(immediate_response)
            return OrchestratedTurnResult(
                session=session,
                response_text=immediate_response,
                delegated=True,
                completed=False,
                response_content=immediate_response,
                run_id=run.run_id,
                status=run.status,
                role_name=role_card.name,
                steps=0,
                agent_summary=session.agent_summary,
            )

    async def load_session(self, session_id: str) -> Session:
        lock = await self._session_lock(session_id)
        async with lock:
            return await asyncio.to_thread(
                self._session_store.load_session,
                session_id,
            )

    async def set_session_role(self, session_id: str, role_name: str) -> Session:
        role_card = self._role_registry.require(role_name)
        lock = await self._session_lock(session_id)
        async with lock:
            session = await asyncio.to_thread(
                self._session_store.load_session,
                session_id,
            )
            session.metadata = set_role_name(session.metadata, role_card.name)
            await asyncio.to_thread(self._session_store.save_session, session)
            return session

    async def current_role_name(self, session_id: str) -> str:
        lock = await self._session_lock(session_id)
        async with lock:
            session = await asyncio.to_thread(
                self._session_store.load_session,
                session_id,
            )
            role_name = role_name_from_metadata(session.metadata)
            try:
                role_card = self._role_registry.require(role_name)
            except ValueError:
                role_card = self._role_registry.require(None)
                session.metadata = set_role_name(session.metadata, role_card.name)
                await asyncio.to_thread(self._session_store.save_session, session)
            return role_card.name

    async def set_session_route_mode(
        self,
        session_id: str,
        route_mode: RouteMode,
    ) -> Session:
        lock = await self._session_lock(session_id)
        async with lock:
            session = await asyncio.to_thread(
                self._session_store.load_session,
                session_id,
            )
            session.metadata = set_route_mode(session.metadata, route_mode)
            await asyncio.to_thread(self._session_store.save_session, session)
            return session

    async def current_route_mode(self, session_id: str) -> RouteMode:
        lock = await self._session_lock(session_id)
        async with lock:
            session = await asyncio.to_thread(
                self._session_store.load_session,
                session_id,
            )
            route_mode = route_mode_from_metadata(session.metadata)
            session.metadata = set_route_mode(session.metadata, route_mode)
            await asyncio.to_thread(self._session_store.save_session, session)
            return route_mode

    def available_roles(self) -> list[str]:
        return self._role_registry.names()

    async def get_run(self, run_id: str) -> AgentRun | None:
        return await self._runs.get(run_id)

    async def list_runs(
        self,
        *,
        session_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[AgentRun]:
        return await self._runs.list_runs(
            session_id=session_id,
            status=status,
            limit=limit,
        )

    async def retry_run(
        self,
        run_id: str,
        *,
        completion_callback: CompletionCallback | None = None,
    ) -> OrchestratedTurnResult:
        run = await self._runs.get(run_id)
        if run is None:
            raise ValueError(f"任务不存在：{run_id}")
        if not run_can_retry(run):
            raise ValueError("只有失败或已取消的任务可以重试")

        route_mode = (
            run.route_mode
            if run.route_mode in {"auto", "chat_only", "force_agent"}
            else None
        )
        return await self.handle_user_turn(
            run.session_id,
            run.prompt,
            image_urls=run.image_urls,
            file_attachments=run.file_attachments,
            role_name=run.role_name,
            route_mode=route_mode,
            completion_callback=completion_callback,
            retry_of_run_id=run.run_id,
            attempt=run.attempt + 1,
        )

    async def get_run_events(
        self,
        run_id: str,
    ) -> tuple[AgentRun | None, list[dict[str, Any]]]:
        run = await self._runs.get(run_id)
        if run is None:
            return None, []
        return run, await self._runs.read_events(run_id)

    async def cancel_run(self, run_id: str) -> AgentRun | None:
        run = await self._runs.get(run_id)
        if run is None:
            return None
        if run.status != "running":
            return run

        task = self._run_tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        run = await self._runs.get(run_id)
        if run is None:
            return None
        if run.status != "running":
            return run

        final_text = await self._append_cancelled_message(run.session_id)
        return await self._runs.set_cancelled(
            run_id,
            final_response=final_text,
            final_response_content=final_text,
        )

    async def cancel_runs_for_session(self, session_id: str) -> list[AgentRun]:
        running_runs = await self._runs.list_for_session(
            session_id,
            status="running",
        )
        if not running_runs:
            return []

        tasks: list[asyncio.Task[None]] = []
        for run in running_runs:
            task = self._run_tasks.get(run.run_id)
            if task is None or task.done():
                continue
            task.cancel()
            tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        cancelled_runs: list[AgentRun] = []
        for run in running_runs:
            current_run = await self._runs.get(run.run_id)
            if current_run is None or current_run.status != "running":
                if current_run is not None:
                    cancelled_runs.append(current_run)
                continue
            updated_run = await self._runs.set_cancelled(
                run.run_id,
                final_response="",
                final_response_content="",
            )
            if updated_run is not None:
                cancelled_runs.append(updated_run)
        return cancelled_runs

    async def delete_runs_for_session(self, session_id: str) -> None:
        await self._runs.delete_for_session(session_id)

    async def mark_session_deleted(self, session_id: str) -> None:
        async with self._deleted_sessions_guard:
            self._deleted_sessions.add(session_id)
        mark_deleted = getattr(self._agent_runner, "mark_session_deleted", None)
        if mark_deleted is not None:
            await mark_deleted(session_id)

    async def restore_session(self, session_id: str) -> None:
        async with self._deleted_sessions_guard:
            self._deleted_sessions.discard(session_id)
        restore = getattr(self._agent_runner, "restore_session", None)
        if restore is not None:
            await restore(session_id)

    async def run_counts(self) -> dict[str, int]:
        return await self._runs.counts()

    async def close(self) -> None:
        run_ids = list(self._run_tasks)
        tasks = list(self._background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for run_id in run_ids:
            await self._mark_run_cancelled(run_id)
        self._background_tasks.clear()
        self._run_tasks.clear()

    async def _run_agent_run(
        self,
        run_id: str,
        *,
        session_id: str,
        prompt: str,
        image_urls: list[ImageInput] | None,
        file_attachments: list[FileInput] | None,
        handoff_text: str | None,
        continuation_text: str | None,
        completion_callback: CompletionCallback | None,
    ) -> None:
        visible_role_name = ""
        try:
            run_prompt_kwargs: dict[str, Any] = {
                "scheduled_context": False,
                "transient_system_messages": (
                    [
                        message
                        for message in [continuation_text, handoff_text]
                        if message and message.strip()
                    ]
                    or None
                ),
                "image_urls": image_urls,
                "file_attachments": file_attachments,
                "run_id": run_id,
            }

            execution = await self._agent_runner.run_prompt(
                session_id,
                prompt,
                **run_prompt_kwargs,
            )
            raw_content = message_content_to_text(
                execution.agent_result.response.message.content
            ).strip()
            outbound_content_blocks = list(
                execution.agent_result.outbound_content_blocks or []
            )
            awaiting_user_input = execution.agent_result.status == "waiting_for_input"
            scheduled_job = _extract_scheduled_cron_job(
                execution.agent_result.new_messages,
            )
            final_text, final_content, visible_role_name = await self._finalize_visible_result(
                session_id,
                prompt=prompt,
                image_urls=image_urls,
                file_attachments=file_attachments,
                raw_content=raw_content,
                is_error=False,
                scheduled_job=scheduled_job,
                outbound_content_blocks=outbound_content_blocks,
                bypass_roleplay=awaiting_user_input,
                direct_response_content=(
                    execution.agent_result.response.message.content
                    if awaiting_user_input
                    else ""
                ),
                pending_user_input=execution.agent_result.pending_user_input,
            )
            if awaiting_user_input:
                run = await self._runs.set_waiting_for_input(
                    run_id,
                    final_response=final_text,
                    final_response_content=final_content,
                    steps=execution.agent_result.steps,
                    pending_user_input=execution.agent_result.pending_user_input,
                )
            else:
                run = await self._runs.set_completed(
                    run_id,
                    final_response=final_text,
                    final_response_content=final_content,
                    steps=execution.agent_result.steps,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error_text = str(exc)
            final_text, final_content, visible_role_name = await self._finalize_visible_result(
                session_id,
                prompt=prompt,
                image_urls=image_urls,
                file_attachments=file_attachments,
                raw_content=error_text,
                is_error=True,
            )
            run = await self._runs.set_failed(
                run_id,
                final_response=final_text,
                final_response_content=final_content,
                error=error_text,
            )

        if run is None:
            return
        if not run.final_response.strip():
            return
        if completion_callback is None:
            return

        await completion_callback(
            AgentRun(
                run_id=run.run_id,
                session_id=run.session_id,
                prompt=run.prompt,
                immediate_response=run.immediate_response,
                role_name=visible_role_name or run.role_name,
                status=run.status,
                created_at=run.created_at,
                updated_at=run.updated_at,
                started_at=run.started_at,
                finished_at=run.finished_at,
                route_mode=run.route_mode,
                attempt=run.attempt,
                retry_of_run_id=run.retry_of_run_id,
                image_urls=run.image_urls,
                file_attachments=run.file_attachments,
                final_response=run.final_response,
                final_response_content=run.final_response_content,
                error=run.error,
                steps=run.steps,
                pending_user_input=run.pending_user_input,
            )
        )

    async def _finalize_visible_result(
        self,
        session_id: str,
        *,
        prompt: str,
        image_urls: list[ImageInput] | None = None,
        file_attachments: list[FileInput] | None = None,
        raw_content: str,
        is_error: bool,
        scheduled_job: ScheduledCronJobInfo | None = None,
        outbound_content_blocks: list[dict[str, Any]] | None = None,
        bypass_roleplay: bool = False,
        direct_response_content: MessageContent = "",
        pending_user_input: dict[str, Any] | None = None,
    ) -> tuple[str, MessageContent, str]:
        lock = await self._session_lock(session_id)
        async with lock:
            deleted = await self._is_session_deleted(session_id)
            if deleted:
                return "", "", ""
            session = await asyncio.to_thread(
                self._session_store.load_session,
                session_id,
            )
            role_card = self._resolve_session_role(session)
            metadata_changed = False
            normalized_pending_user_input = _normalize_pending_user_input(
                pending_user_input
            )
            if normalized_pending_user_input is not None:
                session.metadata = _set_pending_user_input(
                    session.metadata,
                    normalized_pending_user_input,
                )
                metadata_changed = True
            if bypass_roleplay:
                lead_in_text = ""
                if normalized_pending_user_input is not None:
                    lead_in_text = await self._roleplay_engine.present_user_input_request(
                        session=session,
                        follow_up_prompt=normalized_pending_user_input["prompt"],
                        choices=list(normalized_pending_user_input.get("choices") or []),
                        why_needed=str(
                            normalized_pending_user_input.get("why_needed", "")
                        ),
                        role_card=role_card,
                    )
                final_content = _build_visible_response_content(
                    text=lead_in_text,
                    base_content=direct_response_content,
                    outbound_content_blocks=outbound_content_blocks,
                )
            elif raw_content.strip():
                if is_error:
                    final_text = await self._roleplay_engine.present_agent_failure(
                        session=session,
                        user_input=prompt,
                        error_text=raw_content,
                        image_urls=image_urls,
                        file_attachments=file_attachments,
                        role_card=role_card,
                    )
                elif scheduled_job is not None:
                    final_text = (
                        await self._roleplay_engine.present_scheduled_setup_result(
                            session=session,
                            user_input=prompt,
                            agent_output=raw_content,
                            image_urls=image_urls,
                            file_attachments=file_attachments,
                            scheduled_job=scheduled_job,
                            role_card=role_card,
                        )
                    )
                else:
                    final_text = await self._roleplay_engine.present_agent_result(
                        session=session,
                        user_input=prompt,
                        agent_output=raw_content,
                        image_urls=image_urls,
                        file_attachments=file_attachments,
                        role_card=role_card,
                    )
            else:
                final_text = ""

            if not bypass_roleplay:
                final_content = _build_visible_response_content(
                    final_text,
                    outbound_content_blocks=outbound_content_blocks,
                )
            appended_message = False
            if not is_message_content_empty(final_content):
                session.history.append(
                    LLMMessage(role="assistant", content=final_content)
                )
                appended_message = True
            if appended_message or metadata_changed:
                await asyncio.to_thread(self._session_store.save_session, session)
            return (
                message_content_to_text(final_content).strip(),
                final_content,
                role_card.name,
            )

    async def present_scheduled_notification(
        self,
        session_id: str,
        raw_content: str,
    ) -> str:
        content = raw_content.strip()
        if not content:
            return ""

        lock = await self._session_lock(session_id)
        async with lock:
            deleted = await self._is_session_deleted(session_id)
            if deleted:
                return ""
            session = await asyncio.to_thread(
                self._session_store.load_session,
                session_id,
            )
            role_card = self._resolve_session_role(session)
            final_text = await self._roleplay_engine.present_scheduled_notification(
                session=session,
                reminder_text=content,
                role_card=role_card,
            )
            if final_text.strip():
                session.history.append(LLMMessage(role="assistant", content=final_text))
                await asyncio.to_thread(self._session_store.save_session, session)
            return final_text

    async def _append_cancelled_message(self, session_id: str) -> str:
        final_text = RUN_CANCELLED_TEXT
        lock = await self._session_lock(session_id)
        async with lock:
            deleted = await self._is_session_deleted(session_id)
            if deleted:
                return ""
            session = await asyncio.to_thread(
                self._session_store.load_session,
                session_id,
            )
            session.history.append(LLMMessage(role="assistant", content=final_text))
            await asyncio.to_thread(self._session_store.save_session, session)
        return final_text

    def _resolve_turn_role(
        self,
        session: Session,
        role_name: str | None,
    ) -> RoleCard:
        if role_name is not None:
            role_card = self._role_registry.require(role_name)
            session.metadata = set_role_name(session.metadata, role_card.name)
            return role_card
        return self._resolve_session_role(session)

    def _resolve_session_role(self, session: Session) -> RoleCard:
        role_name = role_name_from_metadata(session.metadata)
        try:
            role_card = self._role_registry.require(role_name)
        except ValueError:
            role_card = self._role_registry.require(None)
        session.metadata = set_role_name(session.metadata, role_card.name)
        return role_card

    def _resolve_turn_route_mode(
        self,
        session: Session,
        route_mode: RouteMode | None,
    ) -> RouteMode:
        if route_mode is not None:
            return route_mode
        return route_mode_from_metadata(session.metadata)

    async def _mark_run_cancelled(self, run_id: str) -> None:
        run = await self._runs.get(run_id)
        if run is None or run.status != "running":
            return
        await self._runs.set_cancelled(
            run_id,
            final_response=RUN_CANCELLED_TEXT,
            final_response_content=RUN_CANCELLED_TEXT,
        )

    async def _session_lock(self, session_id: str) -> asyncio.Lock:
        async with self._session_locks_guard:
            lock = self._session_locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._session_locks[session_id] = lock
            return lock

    async def _is_session_deleted(self, session_id: str) -> bool:
        async with self._deleted_sessions_guard:
            return session_id in self._deleted_sessions

    def _start_background_run(
        self,
        run_id: str,
        coroutine_factory: BackgroundTaskFactory,
    ) -> None:
        task = asyncio.create_task(
            self._run_after_yield(coroutine_factory),
            name=f"echobot_agent_run_{run_id}",
        )
        self._background_tasks.add(task)
        self._run_tasks[run_id] = task

        def cleanup(done_task: asyncio.Task[None]) -> None:
            self._background_tasks.discard(done_task)
            if self._run_tasks.get(run_id) is done_task:
                self._run_tasks.pop(run_id, None)

        task.add_done_callback(cleanup)

    async def _run_after_yield(
        self,
        coroutine_factory: BackgroundTaskFactory,
    ) -> None:
        await asyncio.sleep(0)
        await coroutine_factory()


async def _discard_stream_chunk(_chunk: str) -> None:
    return None


def _extract_scheduled_cron_job(
    messages: list[LLMMessage],
) -> ScheduledCronJobInfo | None:
    cron_add_calls: dict[str, str] = {}
    for message in messages:
        if message.role != "assistant":
            continue
        for tool_call in message.tool_calls:
            if tool_call.name != "cron":
                continue
            arguments = _try_parse_json_object(tool_call.arguments)
            if not isinstance(arguments, dict):
                continue
            action = str(arguments.get("action", "")).strip().lower()
            if action != "add":
                continue
            cron_add_calls[tool_call.id] = str(arguments.get("content", "")).strip()

    if not cron_add_calls:
        return None

    for message in messages:
        if message.role != "tool":
            continue
        if message.tool_call_id not in cron_add_calls:
            continue
        payload = _try_parse_json_object(message.content)
        if not isinstance(payload, dict) or not payload.get("ok"):
            continue
        result = payload.get("result")
        if not isinstance(result, dict) or not result.get("created"):
            continue
        job = result.get("job")
        if not isinstance(job, dict):
            continue
        return ScheduledCronJobInfo(
            name=str(job.get("name", "")).strip(),
            schedule=str(job.get("schedule", "")).strip(),
            next_run_at=_optional_text(job.get("next_run_at")),
            payload_kind=str(job.get("payload_kind", "")).strip(),
            payload_content=cron_add_calls.get(message.tool_call_id, ""),
        )

    return None


def _try_parse_json_object(text: str) -> dict[str, object] | None:
    cleaned = text.strip()
    if not cleaned:
        return None
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _build_visible_response_content(
    text: str = "",
    *,
    base_content: MessageContent = "",
    outbound_content_blocks: list[dict[str, Any]] | None = None,
) -> MessageContent:
    blocks: list[dict[str, Any]] = []
    cleaned_text = str(text or "").strip()
    if cleaned_text:
        blocks.append(
            {
                "type": TEXT_CONTENT_BLOCK_TYPE,
                "text": cleaned_text,
            }
        )
    blocks.extend(message_content_blocks(base_content))
    blocks.extend(message_content_blocks(outbound_content_blocks or []))
    if not blocks:
        return ""
    if len(blocks) == 1 and blocks[0].get("type") == TEXT_CONTENT_BLOCK_TYPE:
        return str(blocks[0].get("text", ""))
    return blocks


def _forced_agent_decision_for_pending_input():
    return SimpleNamespace(
        requires_agent=True,
        route="agent",
        reason="Continue paused task after request_user_input",
    )


def _build_agent_handoff_text(
    *,
    session: Session,
) -> str | None:
    entries = _collect_handoff_entries(session.history)
    if not entries:
        return None

    lines = [
        "Visible conversation handoff from the lightweight roleplay layer.",
        "",
        "The current user request follows immediately after this handoff.",
        "Use the visible context below to resolve references such as 'that script', 'the previous result', or 'the list above'.",
        "Treat these messages as user-visible conversation context. If they mention files, memory, schedules, or tool results, verify them with tools before relying on them.",
        f"Session ID: {session.id}",
        "",
        "Recent visible messages:",
    ]
    for index, entry in enumerate(entries, start=1):
        lines.append(
            f'<visible_message index="{index}" role="{entry.role}">'
        )
        lines.append(entry.content)
        lines.append("</visible_message>")
        lines.append("")
    return "\n".join(lines).strip()


def _normalize_pending_user_input(
    value: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    prompt = str(value.get("prompt", "")).strip()
    if not prompt:
        return None

    raw_choices = value.get("choices", [])
    if isinstance(raw_choices, list):
        choices = [str(choice).strip() for choice in raw_choices if str(choice).strip()]
    else:
        choices = []

    why_needed = str(value.get("why_needed", "")).strip()
    normalized = {
        "prompt": prompt,
        "choices": choices,
        "why_needed": why_needed,
    }
    return normalized


def _pending_user_input_from_metadata(
    metadata: dict[str, object] | None,
) -> dict[str, Any] | None:
    if not isinstance(metadata, dict):
        return None
    value = metadata.get(PENDING_USER_INPUT_METADATA_KEY)
    if not isinstance(value, dict):
        return None
    return _normalize_pending_user_input(dict(value))


def _set_pending_user_input(
    metadata: dict[str, object],
    pending_user_input: dict[str, Any],
) -> dict[str, object]:
    next_metadata = dict(metadata or {})
    next_metadata[PENDING_USER_INPUT_METADATA_KEY] = dict(pending_user_input)
    return next_metadata


def _clear_pending_user_input(
    metadata: dict[str, object],
) -> dict[str, object]:
    next_metadata = dict(metadata or {})
    next_metadata.pop(PENDING_USER_INPUT_METADATA_KEY, None)
    return next_metadata


def _build_pending_user_input_continuation_text(
    pending_user_input: dict[str, Any] | None,
) -> str | None:
    normalized = _normalize_pending_user_input(pending_user_input)
    if normalized is None:
        return None

    lines = [
        "The previous agent run paused to request missing information.",
        "Treat the current user message as the latest reply to that request or as a new instruction that supersedes it.",
        "Continue the task with the hidden agent history from the previous run.",
        "",
        "Pending user input request:",
        normalized["prompt"],
    ]
    choices = normalized.get("choices") or []
    if choices:
        lines.append("")
        lines.append("Suggested choices:")
        lines.extend(f"- {choice}" for choice in choices)
    why_needed = str(normalized.get("why_needed", "")).strip()
    if why_needed:
        lines.append("")
        lines.append("Why this was needed:")
        lines.append(why_needed)
    return "\n".join(lines).strip()


@dataclass(slots=True)
class _HandoffEntry:
    role: str
    content: str


def _collect_handoff_entries(history: list[LLMMessage]) -> list[_HandoffEntry]:
    selected: list[_HandoffEntry] = []
    remaining_chars = AGENT_HANDOFF_MAX_TOTAL_CHARS
    for message in reversed(history):
        if message.role not in {"user", "assistant"}:
            continue
        content = message.content_text.strip()
        if not content:
            continue
        if remaining_chars <= 0:
            break
        trimmed = _trim_handoff_content(
            content,
            max_chars=min(AGENT_HANDOFF_MAX_MESSAGE_CHARS, remaining_chars),
        )
        if not trimmed:
            continue
        selected.append(_HandoffEntry(role=message.role, content=trimmed))
        remaining_chars -= len(trimmed)
        if len(selected) >= AGENT_HANDOFF_MAX_MESSAGES:
            break
    selected.reverse()
    return selected


def _trim_handoff_content(content: str, *, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    stripped = content.strip()
    if len(stripped) <= max_chars:
        return stripped
    if max_chars <= 16:
        return stripped[:max_chars]
    return stripped[: max_chars - 16].rstrip() + "\n...[truncated]"
