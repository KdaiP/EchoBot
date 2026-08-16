from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from ...models import FileInput, ImageInput
from ...orchestration import AgentRun, ConversationCoordinator, OrchestratedTurnResult, RouteMode


StreamCallback = Callable[[str], Awaitable[None]]


class CurrentSessionService(Protocol):
    async def set_current_session(self, session_id: str) -> None: ...


class ChatService:
    def __init__(
        self,
        coordinator: ConversationCoordinator,
        session_service: CurrentSessionService,
    ) -> None:
        self._coordinator = coordinator
        self._session_service = session_service

    async def run_prompt(
        self,
        session_id: str,
        prompt: str,
        *,
        image_urls: list[ImageInput] | None = None,
        file_attachments: list[FileInput] | None = None,
        role_name: str | None = None,
        route_mode: RouteMode | None = None,
    ) -> OrchestratedTurnResult:
        result = await self._coordinator.handle_user_turn(
            session_id,
            prompt,
            image_urls=image_urls,
            file_attachments=file_attachments,
            role_name=role_name,
            route_mode=route_mode,
        )
        await self._session_service.set_current_session(result.session.id)
        return result

    async def run_prompt_stream(
        self,
        session_id: str,
        prompt: str,
        *,
        image_urls: list[ImageInput] | None = None,
        file_attachments: list[FileInput] | None = None,
        role_name: str | None = None,
        route_mode: RouteMode | None = None,
        on_chunk: StreamCallback | None = None,
    ) -> OrchestratedTurnResult:
        result = await self._coordinator.handle_user_turn_stream(
            session_id,
            prompt,
            image_urls=image_urls,
            file_attachments=file_attachments,
            role_name=role_name,
            route_mode=route_mode,
            on_chunk=on_chunk,
        )
        await self._session_service.set_current_session(result.session.id)
        return result

    async def set_role(self, session_id: str, role_name: str):
        session = await self._coordinator.set_session_role(session_id, role_name)
        await self._session_service.set_current_session(session.id)
        return session

    async def current_role_name(self, session_id: str) -> str:
        return await self._coordinator.current_role_name(session_id)

    async def set_route_mode(self, session_id: str, route_mode: RouteMode):
        session = await self._coordinator.set_session_route_mode(session_id, route_mode)
        await self._session_service.set_current_session(session.id)
        return session

    async def current_route_mode(self, session_id: str) -> RouteMode:
        return await self._coordinator.current_route_mode(session_id)

    def available_roles(self) -> list[str]:
        return self._coordinator.available_roles()

    async def get_run(self, run_id: str) -> AgentRun | None:
        return await self._coordinator.get_run(run_id)

    async def list_runs(
        self,
        *,
        session_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[AgentRun]:
        return await self._coordinator.list_runs(
            session_id=session_id,
            status=status,
            limit=limit,
        )

    async def get_run_events(
        self,
        run_id: str,
    ) -> tuple[AgentRun | None, list[dict[str, Any]]]:
        return await self._coordinator.get_run_events(run_id)

    async def cancel_run(self, run_id: str) -> AgentRun | None:
        return await self._coordinator.cancel_run(run_id)

    async def retry_run(self, run_id: str) -> OrchestratedTurnResult:
        result = await self._coordinator.retry_run(run_id)
        await self._session_service.set_current_session(result.session.id)
        return result
