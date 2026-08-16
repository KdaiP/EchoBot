from __future__ import annotations

import asyncio
import logging

from ..channels import InboundMessage, MessageBus, OutboundMessage
from ..channels.types import DeliveryTarget
from ..commands.bindings import GatewayCommandContext, dispatch_gateway_command
from ..models import MessageContent, is_message_content_empty, message_content_to_text
from ..runtime.scheduled_tasks import (
    build_cron_job_executor as build_shared_cron_job_executor,
    build_heartbeat_executor as build_shared_heartbeat_executor,
)
from ..runtime.bootstrap import RuntimeContext
from ..runtime.session_service import SessionLifecycleService
from ..turn_inputs import (
    has_file_processing_capability,
    resolve_attachment_files,
    resolve_file_attachment_route_mode,
)
from .delivery import DeliveryStore
from .route_bindings import RouteBindingStore
from .session_service import GatewaySessionService


logger = logging.getLogger(__name__)


class GatewayRuntime:
    def __init__(
        self,
        context: RuntimeContext,
        bus: MessageBus,
        session_service: GatewaySessionService | None = None,
        delivery_store: DeliveryStore | None = None,
        route_binding_store: RouteBindingStore | None = None,
        *,
        max_inflight_messages: int = 32,
    ) -> None:
        self._context = context
        self._bus = bus
        if session_service is None:
            delivery_store = delivery_store or DeliveryStore(
                context.workspace / ".echobot" / "delivery.json",
            )
            route_binding_store = route_binding_store or RouteBindingStore(
                context.workspace / ".echobot" / "route_bindings.jsonl",
            )
            core_session_service = SessionLifecycleService(
                context.session_store,
                coordinator=context.coordinator,
            )
            session_service = GatewaySessionService(
                core_session_service,
                route_binding_store=route_binding_store,
                delivery_store=delivery_store,
            )
        self._session_service = session_service
        self._inflight_tasks: set[asyncio.Task[None]] = set()
        self._inflight_semaphore = asyncio.Semaphore(max(max_inflight_messages, 1))
        self._route_locks: dict[str, asyncio.Lock] = {}
        self._route_locks_guard = asyncio.Lock()

    async def run(self) -> None:
        self._context.cron_service.on_job = self._build_cron_job_executor()
        if self._context.heartbeat_service is not None:
            self._context.heartbeat_service.on_execute = (
                self._build_heartbeat_executor()
            )
            self._context.heartbeat_service.on_notify = self._notify_latest

        await self._context.cron_service.start()
        if self._context.heartbeat_service is not None:
            await self._context.heartbeat_service.start()

        logger.info("Gateway runtime started")
        try:
            while True:
                await self._inflight_semaphore.acquire()
                message = await self._bus.consume_inbound()
                task = asyncio.create_task(self._handle_inbound_message_task(message))
                self._inflight_tasks.add(task)
                task.add_done_callback(self._inflight_tasks.discard)
        finally:
            await self._shutdown()

    async def handle_inbound_message(self, message: InboundMessage) -> None:
        route_lock = await self._route_lock(message.route_key)
        async with route_lock:
            await self._handle_inbound_message(message)

    async def _handle_inbound_message_task(self, message: InboundMessage) -> None:
        try:
            await self.handle_inbound_message(message)
        finally:
            self._inflight_semaphore.release()

    async def _handle_inbound_message(self, message: InboundMessage) -> None:
        route_key = message.route_key
        command_result = await dispatch_gateway_command(
            GatewayCommandContext(
                coordinator=self._context.coordinator,
                settings_service=self._context.settings_service,
                session_service=self._session_service,
                route_key=route_key,
                address=message.address,
                metadata=message.metadata,
            ),
            message.text,
        )
        if command_result is not None:
            await self._bus.publish_outbound(
                OutboundMessage(
                    address=message.address,
                    text=command_result.text,
                    metadata=dict(message.metadata),
                )
            )
            return

        route_session = await self._session_service.current_routed_session(
            route_key,
        )
        await self._session_service.remember_delivery_target(
            route_session.session_id,
            message.address,
            message.metadata,
        )
        try:
            image_urls = (
                list(message.image_urls)
                if self._context.supports_image_input
                else []
            )
            file_attachments = await _resolve_gateway_files(
                message,
                self._context.attachment_store,
                self._context.workspace,
            )
            execution = await self._context.coordinator.handle_user_turn(
                route_session.session_id,
                message.text,
                image_urls=image_urls,
                file_attachments=file_attachments,
                route_mode=await self._resolve_effective_route_mode(
                    route_session.session_id,
                    has_file_attachments=bool(file_attachments),
                ),
                completion_callback=self._completion_callback_for_session(
                    route_session.session_id,
                ),
            )
            content: MessageContent = execution.response_content
            await self._session_service.touch_routed_session(
                route_key,
                route_session.session_id,
                updated_at=execution.session.updated_at,
            )
            if is_message_content_empty(content) and execution.delegated and not execution.completed:
                return
            if is_message_content_empty(content):
                content = "Model returned no text content."
        except ValueError as exc:
            content = str(exc)
        except RuntimeError as exc:
            content = f"Request failed: {exc}"
        await self._bus.publish_outbound(
            OutboundMessage(
                address=message.address,
                content=content,
                metadata=dict(message.metadata),
            )
        )

    def _completion_callback_for_session(
        self,
        session_id: str,
    ):
        async def notify(run) -> None:
            await self._publish_session_response(
                session_id,
                run.final_response_content,
                metadata={
                    "async_result": True,
                    "run_id": run.run_id,
                    "run_status": run.status,
                },
            )

        return notify

    def _build_cron_job_executor(self):
        return build_shared_cron_job_executor(
            self._context.session_runner,
            self._context.coordinator,
            self._notify_schedule,
        )

    def _build_heartbeat_executor(self):
        return build_shared_heartbeat_executor(self._context.session_runner)

    async def _notify_session(
        self,
        session_id: str,
        content: MessageContent,
        *,
        kind: str,
        title: str,
    ) -> None:
        target = await self._session_service.get_session_target(session_id)
        await self._publish_notification(
            target,
            content,
            kind=kind,
            title=title,
        )

    async def _publish_session_response(
        self,
        session_id: str,
        content: MessageContent,
        *,
        metadata: dict[str, object] | None = None,
    ) -> None:
        target = await self._session_service.get_session_target(session_id)
        if target is None:
            logger.info("[reply] %s", message_content_to_text(content))
            return
        next_metadata = dict(target.metadata)
        if metadata is not None:
            next_metadata.update(metadata)
        await self._bus.publish_outbound(
            OutboundMessage(
                address=target.address,
                content=content,
                metadata=next_metadata,
            )
        )

    async def _notify_latest(self, content: MessageContent) -> None:
        target = await self._session_service.get_latest_target()
        await self._publish_notification(
            target,
            content,
            kind="heartbeat",
            title="Periodic check-in",
        )

    async def _publish_notification(
        self,
        target: DeliveryTarget | None,
        content: MessageContent,
        *,
        kind: str,
        title: str,
    ) -> None:
        if target is None:
            logger.info("[%s] %s", kind, title)
            text_content = message_content_to_text(content)
            for line in text_content.splitlines() or [text_content]:
                logger.info("[%s] %s", kind, line)
            return
        metadata = dict(target.metadata)
        metadata["scheduled"] = True
        metadata["schedule_kind"] = kind
        metadata["schedule_title"] = title
        await self._bus.publish_outbound(
            OutboundMessage(
                address=target.address,
                content=content,
                metadata=metadata,
            )
        )

    async def _notify_schedule(
        self,
        session_id: str,
        kind: str,
        title: str,
        content: MessageContent,
    ) -> None:
        await self._notify_session(
            session_id,
            content,
            kind=kind,
            title=title,
        )

    async def _resolve_effective_route_mode(
        self,
        session_id: str,
        *,
        has_file_attachments: bool,
    ):
        can_process_files = False
        current_route_mode = None
        if has_file_attachments:
            can_process_files = has_file_processing_capability(
                self._context.skill_registry,
                getattr(self._context, "tool_registry_factory", None),
                session_id,
            )
        if can_process_files:
            current_route_mode = await self._context.coordinator.current_route_mode(
                session_id,
            )

        return resolve_file_attachment_route_mode(
            requested_route_mode=None,
            current_route_mode=current_route_mode,
            has_file_attachments=has_file_attachments,
            can_process_files=can_process_files,
        )

    async def _shutdown(self) -> None:
        tasks = list(self._inflight_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._context.cron_service.stop()
        if self._context.heartbeat_service is not None:
            await self._context.heartbeat_service.stop()
        await self._context.coordinator.close()
        if self._context.memory_support is not None:
            await self._context.memory_support.close()

    async def _route_lock(self, route_key: str) -> asyncio.Lock:
        async with self._route_locks_guard:
            lock = self._route_locks.get(route_key)
            if lock is None:
                lock = asyncio.Lock()
                self._route_locks[route_key] = lock
            return lock


async def _resolve_gateway_files(
    message: InboundMessage,
    attachment_store,
    workspace,
) -> list[dict[str, object]]:
    if not message.files:
        return []

    return await asyncio.to_thread(
        resolve_attachment_files,
        attachment_store,
        workspace,
        message.files,
    )
