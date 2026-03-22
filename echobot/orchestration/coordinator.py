from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ..models import LLMMessage, build_user_message_content, message_content_to_text
from ..runtime.session_runner import SessionAgentRunner
from ..runtime.sessions import ChatSession, SessionStore
from .decision import DecisionEngine
from .jobs import (
    JOB_CANCELLED_TEXT,
    CompletionCallback,
    ConversationJob,
    ConversationJobStore,
    OrchestratedTurnResult,
)
from .roleplay import RoleplayEngine, ScheduledCronJobInfo, StreamCallback
from .route_modes import (
    RouteMode,
    route_mode_from_metadata,
    set_route_mode,
)
from .roles import RoleCard, RoleCardRegistry, role_name_from_metadata, set_role_name


BackgroundJobFactory = Callable[[], Awaitable[None]]
AGENT_HANDOFF_MAX_MESSAGES = 6
AGENT_HANDOFF_MAX_TOTAL_CHARS = 6000
AGENT_HANDOFF_MAX_MESSAGE_CHARS = 1800


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
        vision_context_provider: Any | None = None,
    ) -> None:
        self._session_store = session_store
        self._agent_runner = agent_runner
        self._decision_engine = decision_engine
        self._roleplay_engine = roleplay_engine
        self._role_registry = role_registry
        self._delegated_ack_enabled = delegated_ack_enabled
        self._vision_context_provider = vision_context_provider
        self._jobs = ConversationJobStore()
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._session_locks_guard = asyncio.Lock()
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._job_tasks: dict[str, asyncio.Task[None]] = {}
        self._deleted_sessions: set[str] = set()
        self._deleted_sessions_guard = asyncio.Lock()

    @property
    def delegated_ack_enabled(self) -> bool:
        return self._delegated_ack_enabled

    def set_delegated_ack_enabled(self, enabled: bool) -> None:
        self._delegated_ack_enabled = bool(enabled)

    def set_vision_context_provider(self, provider: Any) -> None:
        """注入视觉上下文提供器（由插件在 on_startup 时调用）"""
        self._vision_context_provider = provider

    def _build_vision_message(self) -> str | None:
        """从视觉提供器构建临时系统消息"""
        if self._vision_context_provider is None:
            return None
        try:
            frames = self._vision_context_provider.get_vision_context_list()
            if not frames:
                return None
            lines = [
                "[实时视觉]",
                "以下是你通过摄像头当前看到的画面，这是你真实的视觉输入。",
                "规则：",
                "1. 用户问你'看到什么''周围什么情况'等 → 直接用下面的描述回答",
                "2. 画面中出现的人名已通过人脸识别标注，可直接使用",
                "3. 自然融入视觉信息，不要说'根据摄像头''根据视觉数据'等机械表述",
                "",
                "当前画面（最新在后）：",
            ]
            has_unknown_face = False
            for f in frames[-8:]:
                desc = f.get("image_description", "")
                faces = f.get("faces", [])
                named = [fc["person_name"] for fc in faces if fc.get("person_name")]
                unnamed_count = sum(1 for fc in faces if not fc.get("person_name"))
                if unnamed_count > 0:
                    has_unknown_face = True
                face_str = f"【{', '.join(named)}】" if named else ""
                if unnamed_count > 0:
                    face_str += f"【{unnamed_count}个未知人脸】"
                import time
                t1 = time.strftime("%H:%M:%S", time.localtime(f.get("start_time", 0)))
                t2 = time.strftime("%H:%M:%S", time.localtime(f.get("end_time", 0)))
                time_str = t1 if t1 == t2 else f"{t1}~{t2}"
                lines.append(f"  [{time_str}] {desc}{face_str}")
            if has_unknown_face:
                lines.append("")
                lines.append("[主动行为指令]",)
                lines.append("画面中有你不认识的人。在回复用户时，自然地询问对方的名字，例如：'对了，我好像还不认识你，能告诉我你叫什么吗？'")
                lines.append("当用户告知名字后，立即调用 bind_face_to_name 工具将其绑定，不需要用户再次要求。")
            lines.append("[视觉结束]")
            return "\n".join(lines)
        except Exception:
            return None

    async def handle_user_turn(
        self,
        session_name: str,
        prompt: str,
        *,
        image_urls: list[str] | None = None,
        role_name: str | None = None,
        route_mode: RouteMode | None = None,
        completion_callback: CompletionCallback | None = None,
        transient_system_messages: list[str] | None = None,
    ) -> OrchestratedTurnResult:
        return await self.handle_user_turn_stream(
            session_name,
            prompt,
            image_urls=image_urls,
            role_name=role_name,
            route_mode=route_mode,
            completion_callback=completion_callback,
            transient_system_messages=transient_system_messages,
        )

    async def handle_user_turn_stream(
        self,
        session_name: str,
        prompt: str,
        *,
        image_urls: list[str] | None = None,
        role_name: str | None = None,
        route_mode: RouteMode | None = None,
        completion_callback: CompletionCallback | None = None,
        on_chunk: StreamCallback | None = None,
        transient_system_messages: list[str] | None = None,
    ) -> OrchestratedTurnResult:
        # 自动注入视觉上下文
        vision_msg = self._build_vision_message()
        if vision_msg:
            transient_system_messages = [vision_msg, *(transient_system_messages or [])]
        await self.restore_session(session_name)
        chunk_handler = on_chunk or _discard_stream_chunk
        lock = await self._session_lock(session_name)
        async with lock:
            session = await asyncio.to_thread(
                self._session_store.load_or_create_session,
                session_name,
            )
            role_card = self._resolve_turn_role(session, role_name)
            resolved_route_mode = self._resolve_turn_route_mode(session, route_mode)

            decision = await self._decision_engine.decide(
                prompt,
                history=list(session.history[-8:]),
                route_mode=resolved_route_mode,
            )

            if not decision.requires_agent:
                response_text = await self._roleplay_engine.stream_chat_reply(
                    session=session,
                    user_input=prompt,
                    image_urls=image_urls,
                    role_card=role_card,
                    on_chunk=chunk_handler,
                    transient_system_messages=transient_system_messages,
                )
                session.history.extend(
                    [
                        LLMMessage(
                            role="user",
                            content=build_user_message_content(prompt, image_urls),
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
                    role_name=role_card.name,
                    steps=1,
                    compressed_summary=session.compressed_summary,
                )

            immediate_response = ""
            if self._delegated_ack_enabled:
                immediate_response = await self._roleplay_engine.delegated_ack(
                    session=session,
                    user_input=prompt,
                    image_urls=image_urls,
                    role_card=role_card,
                )
            handoff_text = _build_agent_handoff_text(
                session=session,
            )
            session.history.append(
                LLMMessage(
                    role="user",
                    content=build_user_message_content(prompt, image_urls),
                )
            )
            if immediate_response.strip():
                session.history.append(
                    LLMMessage(role="assistant", content=immediate_response)
                )
            await asyncio.to_thread(self._session_store.save_session, session)
            create_trace_run_id = getattr(self._agent_runner, "create_trace_run_id", None)
            trace_run_id = (
                create_trace_run_id()
                if callable(create_trace_run_id)
                else None
            )
            job = await self._jobs.create(
                session_name=session.name,
                prompt=prompt,
                immediate_response=immediate_response,
                role_name=role_card.name,
                trace_run_id=trace_run_id,
            )
            self._start_background_job(
                job.job_id,
                lambda: self._run_agent_job(
                    job.job_id,
                    session_name=session.name,
                    prompt=prompt,
                    image_urls=image_urls,
                    handoff_text=handoff_text,
                    trace_run_id=trace_run_id,
                    completion_callback=completion_callback,
                    extra_transient_messages=transient_system_messages,
                ),
            )
            if immediate_response.strip():
                await chunk_handler(immediate_response)
            return OrchestratedTurnResult(
                session=session,
                response_text=immediate_response,
                delegated=True,
                completed=False,
                job_id=job.job_id,
                status=job.status,
                role_name=role_card.name,
                steps=0,
                compressed_summary=session.compressed_summary,
            )

    async def load_session(self, session_name: str) -> ChatSession:
        lock = await self._session_lock(session_name)
        async with lock:
            return await asyncio.to_thread(
                self._session_store.load_or_create_session,
                session_name,
            )

    async def set_session_role(self, session_name: str, role_name: str) -> ChatSession:
        role_card = self._role_registry.require(role_name)
        lock = await self._session_lock(session_name)
        async with lock:
            session = await asyncio.to_thread(
                self._session_store.load_or_create_session,
                session_name,
            )
            session.metadata = set_role_name(session.metadata, role_card.name)
            await asyncio.to_thread(self._session_store.save_session, session)
            return session

    async def current_role_name(self, session_name: str) -> str:
        lock = await self._session_lock(session_name)
        async with lock:
            session = await asyncio.to_thread(
                self._session_store.load_or_create_session,
                session_name,
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
        session_name: str,
        route_mode: RouteMode,
    ) -> ChatSession:
        lock = await self._session_lock(session_name)
        async with lock:
            session = await asyncio.to_thread(
                self._session_store.load_or_create_session,
                session_name,
            )
            session.metadata = set_route_mode(session.metadata, route_mode)
            await asyncio.to_thread(self._session_store.save_session, session)
            return session

    async def current_route_mode(self, session_name: str) -> RouteMode:
        lock = await self._session_lock(session_name)
        async with lock:
            session = await asyncio.to_thread(
                self._session_store.load_or_create_session,
                session_name,
            )
            route_mode = route_mode_from_metadata(session.metadata)
            session.metadata = set_route_mode(session.metadata, route_mode)
            await asyncio.to_thread(self._session_store.save_session, session)
            return route_mode

    def available_roles(self) -> list[str]:
        return self._role_registry.names()

    async def get_job(self, job_id: str) -> ConversationJob | None:
        return await self._jobs.get(job_id)

    async def get_job_trace(
        self,
        job_id: str,
    ) -> tuple[ConversationJob | None, list[dict[str, Any]]]:
        job = await self._jobs.get(job_id)
        if job is None:
            return None, []
        if not job.trace_run_id:
            return job, []
        events = await self._agent_runner.load_trace_events(
            job.session_name,
            job.trace_run_id,
        )
        return job, events

    async def cancel_job(self, job_id: str) -> ConversationJob | None:
        job = await self._jobs.get(job_id)
        if job is None:
            return None
        if job.status != "running":
            return job

        task = self._job_tasks.get(job_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        job = await self._jobs.get(job_id)
        if job is None:
            return None
        if job.status != "running":
            return job

        final_text = await self._append_cancelled_message(job.session_name)
        return await self._jobs.set_cancelled(
            job_id,
            final_response=final_text,
        )

    async def cancel_jobs_for_session(self, session_name: str) -> list[ConversationJob]:
        running_jobs = await self._jobs.list_for_session(
            session_name,
            status="running",
        )
        if not running_jobs:
            return []

        tasks: list[asyncio.Task[None]] = []
        for job in running_jobs:
            task = self._job_tasks.get(job.job_id)
            if task is None or task.done():
                continue
            task.cancel()
            tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        cancelled_jobs: list[ConversationJob] = []
        for job in running_jobs:
            current_job = await self._jobs.get(job.job_id)
            if current_job is None or current_job.status != "running":
                if current_job is not None:
                    cancelled_jobs.append(current_job)
                continue
            updated_job = await self._jobs.set_cancelled(
                job.job_id,
                final_response="",
            )
            if updated_job is not None:
                cancelled_jobs.append(updated_job)
        return cancelled_jobs

    async def mark_session_deleted(self, session_name: str) -> None:
        async with self._deleted_sessions_guard:
            self._deleted_sessions.add(session_name)
        mark_deleted = getattr(self._agent_runner, "mark_session_deleted", None)
        if mark_deleted is not None:
            await mark_deleted(session_name)

    async def restore_session(self, session_name: str) -> None:
        async with self._deleted_sessions_guard:
            self._deleted_sessions.discard(session_name)
        restore = getattr(self._agent_runner, "restore_session", None)
        if restore is not None:
            await restore(session_name)

    async def job_counts(self) -> dict[str, int]:
        return await self._jobs.counts()

    async def close(self) -> None:
        job_ids = list(self._job_tasks)
        tasks = list(self._background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for job_id in job_ids:
            await self._mark_job_cancelled(job_id)
        self._background_tasks.clear()
        self._job_tasks.clear()

    async def _run_agent_job(
        self,
        job_id: str,
        *,
        session_name: str,
        prompt: str,
        image_urls: list[str] | None,
        handoff_text: str | None,
        trace_run_id: str | None,
        completion_callback: CompletionCallback | None,
        extra_transient_messages: list[str] | None = None,
    ) -> None:
        visible_role_name = ""
        try:
            transient: list[str] = []
            if extra_transient_messages:
                transient.extend(extra_transient_messages)
            if handoff_text and handoff_text.strip():
                transient.append(handoff_text)
            run_prompt_kwargs: dict[str, Any] = {
                "scheduled_context": False,
                "transient_system_messages": transient if transient else None,
            }
            if image_urls and _supports_keyword_argument(
                self._agent_runner.run_prompt,
                "image_urls",
            ):
                run_prompt_kwargs["image_urls"] = image_urls
            if trace_run_id and _supports_keyword_argument(
                self._agent_runner.run_prompt,
                "trace_run_id",
            ):
                run_prompt_kwargs["trace_run_id"] = trace_run_id

            execution = await self._agent_runner.run_prompt(
                session_name,
                prompt,
                **run_prompt_kwargs,
            )
            raw_content = message_content_to_text(
                execution.agent_result.response.message.content
            ).strip()
            scheduled_job = _extract_scheduled_cron_job(
                execution.agent_result.new_messages,
            )
            final_text, visible_role_name = await self._finalize_visible_result(
                session_name,
                prompt=prompt,
                image_urls=image_urls,
                raw_content=raw_content,
                is_error=False,
                scheduled_job=scheduled_job,
            )
            job = await self._jobs.set_completed(
                job_id,
                final_response=final_text,
                steps=execution.agent_result.steps,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error_text = str(exc)
            final_text, visible_role_name = await self._finalize_visible_result(
                session_name,
                prompt=prompt,
                image_urls=image_urls,
                raw_content=error_text,
                is_error=True,
            )
            job = await self._jobs.set_failed(
                job_id,
                final_response=final_text,
                error=error_text,
            )

        if job is None:
            return
        if not job.final_response.strip():
            return
        if completion_callback is None:
            return

        await completion_callback(
            ConversationJob(
                job_id=job.job_id,
                session_name=job.session_name,
                prompt=job.prompt,
                immediate_response=job.immediate_response,
                role_name=visible_role_name or job.role_name,
                status=job.status,
                created_at=job.created_at,
                updated_at=job.updated_at,
                final_response=job.final_response,
                error=job.error,
                steps=job.steps,
            )
        )

    async def _finalize_visible_result(
        self,
        session_name: str,
        *,
        prompt: str,
        image_urls: list[str] | None = None,
        raw_content: str,
        is_error: bool,
        scheduled_job: ScheduledCronJobInfo | None = None,
    ) -> tuple[str, str]:
        lock = await self._session_lock(session_name)
        async with lock:
            deleted = await self._is_session_deleted(session_name)
            if deleted:
                return "", ""
            session = await asyncio.to_thread(
                self._session_store.load_or_create_session,
                session_name,
            )
            role_card = self._resolve_session_role(session)
            if raw_content.strip():
                if is_error:
                    final_text = await self._roleplay_engine.present_agent_failure(
                        session=session,
                        user_input=prompt,
                        error_text=raw_content,
                        image_urls=image_urls,
                        role_card=role_card,
                    )
                elif scheduled_job is not None:
                    final_text = (
                        await self._roleplay_engine.present_scheduled_setup_result(
                            session=session,
                            user_input=prompt,
                            agent_output=raw_content,
                            image_urls=image_urls,
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
                        role_card=role_card,
                    )
            else:
                final_text = ""

            if final_text.strip():
                session.history.append(LLMMessage(role="assistant", content=final_text))
                await asyncio.to_thread(self._session_store.save_session, session)
            return final_text, role_card.name

    async def present_scheduled_notification(
        self,
        session_name: str,
        raw_content: str,
    ) -> str:
        content = raw_content.strip()
        if not content:
            return ""

        lock = await self._session_lock(session_name)
        async with lock:
            deleted = await self._is_session_deleted(session_name)
            if deleted:
                return ""
            session = await asyncio.to_thread(
                self._session_store.load_or_create_session,
                session_name,
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

    async def _append_cancelled_message(self, session_name: str) -> str:
        final_text = JOB_CANCELLED_TEXT
        lock = await self._session_lock(session_name)
        async with lock:
            deleted = await self._is_session_deleted(session_name)
            if deleted:
                return ""
            session = await asyncio.to_thread(
                self._session_store.load_or_create_session,
                session_name,
            )
            session.history.append(LLMMessage(role="assistant", content=final_text))
            await asyncio.to_thread(self._session_store.save_session, session)
        return final_text

    def _resolve_turn_role(
        self,
        session: ChatSession,
        role_name: str | None,
    ) -> RoleCard:
        if role_name is not None:
            role_card = self._role_registry.require(role_name)
            session.metadata = set_role_name(session.metadata, role_card.name)
            return role_card
        return self._resolve_session_role(session)

    def _resolve_session_role(self, session: ChatSession) -> RoleCard:
        role_name = role_name_from_metadata(session.metadata)
        try:
            role_card = self._role_registry.require(role_name)
        except ValueError:
            role_card = self._role_registry.require(None)
        session.metadata = set_role_name(session.metadata, role_card.name)
        return role_card

    def _resolve_turn_route_mode(
        self,
        session: ChatSession,
        route_mode: RouteMode | None,
    ) -> RouteMode:
        if route_mode is not None:
            return route_mode
        return route_mode_from_metadata(session.metadata)

    async def _mark_job_cancelled(self, job_id: str) -> None:
        job = await self._jobs.get(job_id)
        if job is None or job.status != "running":
            return
        await self._jobs.set_cancelled(
            job_id,
            final_response=JOB_CANCELLED_TEXT,
        )

    async def _session_lock(self, session_name: str) -> asyncio.Lock:
        async with self._session_locks_guard:
            lock = self._session_locks.get(session_name)
            if lock is None:
                lock = asyncio.Lock()
                self._session_locks[session_name] = lock
            return lock

    async def _is_session_deleted(self, session_name: str) -> bool:
        async with self._deleted_sessions_guard:
            return session_name in self._deleted_sessions

    def _start_background_job(
        self,
        job_id: str,
        coroutine_factory: BackgroundJobFactory,
    ) -> None:
        task = asyncio.create_task(
            self._run_after_yield(coroutine_factory),
            name=f"echobot_conversation_job_{job_id}",
        )
        self._background_tasks.add(task)
        self._job_tasks[job_id] = task

        def cleanup(done_task: asyncio.Task[None]) -> None:
            self._background_tasks.discard(done_task)
            if self._job_tasks.get(job_id) is done_task:
                self._job_tasks.pop(job_id, None)

        task.add_done_callback(cleanup)

    async def _run_after_yield(
        self,
        coroutine_factory: BackgroundJobFactory,
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


def _supports_keyword_argument(callable_obj: object, argument_name: str) -> bool:
    try:
        signature = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        return False

    parameter = signature.parameters.get(argument_name)
    if parameter is None:
        return False

    return parameter.kind in {
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
    }


def _build_agent_handoff_text(
    *,
    session: ChatSession,
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
        f"Session name: {session.name}",
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
