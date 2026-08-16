from __future__ import annotations

import asyncio

from ..orchestration import ConversationCoordinator
from .sessions import Session, SessionInfo, SessionStore, normalize_session_id


class SessionLifecycleService:
    """The single lifecycle boundary for durable sessions."""

    def __init__(
        self,
        session_store: SessionStore,
        *,
        coordinator: ConversationCoordinator | None = None,
    ) -> None:
        self._session_store = session_store
        self._coordinator = coordinator

    async def list_sessions(self) -> list[SessionInfo]:
        return await asyncio.to_thread(self._session_store.list_sessions)

    async def load_session(self, session_id: str) -> Session:
        return await asyncio.to_thread(self._session_store.load_session, session_id)

    async def load_current_session(self) -> Session:
        return await asyncio.to_thread(self._session_store.load_current_session)

    async def create_session(self, title: str | None = None) -> Session:
        session = await asyncio.to_thread(self._session_store.create_session, title)
        await self._restore_session_state(session.id)
        return session

    async def set_current_session(self, session_id: str) -> None:
        await asyncio.to_thread(
            self._session_store.set_current_session,
            normalize_session_id(session_id),
        )

    async def switch_session(self, session_id: str) -> Session:
        session = await self.load_session(session_id)
        await self.set_current_session(session.id)
        await self._restore_session_state(session.id)
        return session

    async def rename_session(self, session_id: str, title: str) -> Session:
        return await asyncio.to_thread(
            self._session_store.rename_session,
            normalize_session_id(session_id),
            title,
        )

    async def delete_session(self, session_id: str) -> bool:
        normalized_id = normalize_session_id(session_id)
        try:
            session = await self.load_session(normalized_id)
        except ValueError:
            return False
        if session.kind != "user":
            return False
        await self.purge_session(normalized_id)
        return True

    async def purge_session(self, session_id: str) -> None:
        normalized_id = normalize_session_id(session_id)
        if self._coordinator is not None:
            await self._coordinator.mark_session_deleted(normalized_id)
            await self._coordinator.cancel_runs_for_session(normalized_id)

        current_id = await asyncio.to_thread(
            self._session_store.get_current_session_id
        )
        await asyncio.to_thread(self._session_store.delete_session, normalized_id)

        if self._coordinator is not None:
            await self._coordinator.delete_runs_for_session(normalized_id)

        if current_id != normalized_id:
            return
        remaining = await self.list_sessions()
        if remaining:
            await self.set_current_session(remaining[0].id)
            return
        replacement = await self.create_session("New session")
        await self.set_current_session(replacement.id)

    async def _restore_session_state(self, session_id: str) -> None:
        if self._coordinator is not None:
            await self._coordinator.restore_session(session_id)
