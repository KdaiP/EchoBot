from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ..channels.types import ChannelAddress, DeliveryTarget
from ..runtime.session_service import SessionLifecycleService
from ..runtime.sessions import Session
from .delivery import DeliveryStore
from .route_bindings import RouteBindingStore


@dataclass(slots=True, frozen=True)
class RoutedSession:
    session_id: str
    title: str
    created_at: str
    updated_at: str

    @property
    def short_id(self) -> str:
        return self.session_id[:8]


@dataclass(slots=True, frozen=True)
class DeleteRoutedSessionResult:
    deleted: RoutedSession
    current: RoutedSession
    created_replacement: bool = False


class GatewaySessionService:
    """Compose core session lifecycle with channel route bindings."""

    def __init__(
        self,
        session_service: SessionLifecycleService,
        *,
        route_binding_store: RouteBindingStore,
        delivery_store: DeliveryStore | None = None,
    ) -> None:
        self._session_service = session_service
        self._route_bindings = route_binding_store
        self._delivery_store = delivery_store

    async def list_sessions(self):
        return await self._session_service.list_sessions()

    async def load_session(self, session_id: str):
        return await self._session_service.load_session(session_id)

    async def load_current_session(self):
        return await self._session_service.load_current_session()

    async def create_session(self, title: str | None = None):
        return await self._session_service.create_session(title)

    async def set_current_session(self, session_id: str) -> None:
        await self._session_service.set_current_session(session_id)

    async def switch_session(self, session_id: str):
        return await self._session_service.switch_session(session_id)

    async def rename_session(self, session_id: str, title: str):
        return await self._session_service.rename_session(session_id, title)

    async def delete_session(self, session_id: str) -> bool:
        deleted = await self._session_service.delete_session(session_id)
        if not deleted:
            return False
        await asyncio.to_thread(self._route_bindings.remove_session, session_id)
        if self._delivery_store is not None:
            await asyncio.to_thread(self._delivery_store.forget, session_id)
        return True

    async def current_routed_session(self, route_key: str) -> RoutedSession:
        session_id = await asyncio.to_thread(
            self._route_bindings.current_session_id,
            route_key,
        )
        if session_id is None:
            return await self.create_routed_session(route_key)
        try:
            return _routed_session(await self._session_service.load_session(session_id))
        except ValueError:
            await asyncio.to_thread(self._route_bindings.remove_session, session_id)
            return await self.create_routed_session(route_key)

    async def list_routed_sessions(self, route_key: str) -> list[RoutedSession]:
        session_ids = await asyncio.to_thread(
            self._route_bindings.list_session_ids,
            route_key,
        )
        if not session_ids:
            return [await self.create_routed_session(route_key)]
        sessions: list[RoutedSession] = []
        for session_id in session_ids:
            try:
                session = await self._session_service.load_session(session_id)
            except ValueError:
                await asyncio.to_thread(self._route_bindings.remove_session, session_id)
                continue
            sessions.append(_routed_session(session))
        if sessions:
            return sessions
        return [await self.create_routed_session(route_key)]

    async def create_routed_session(
        self,
        route_key: str,
        *,
        title: str | None = None,
    ) -> RoutedSession:
        session = await self._session_service.create_session(title)
        await asyncio.to_thread(
            self._route_bindings.bind_session,
            route_key,
            session.id,
        )
        return _routed_session(session)

    async def switch_routed_session(
        self,
        route_key: str,
        index: int,
    ) -> RoutedSession:
        session_id = await asyncio.to_thread(
            self._route_bindings.select_session,
            route_key,
            index,
        )
        return _routed_session(await self._session_service.load_session(session_id))

    async def rename_current_routed_session(
        self,
        route_key: str,
        title: str,
    ) -> RoutedSession:
        current = await self.current_routed_session(route_key)
        session = await self._session_service.rename_session(current.session_id, title)
        return _routed_session(session)

    async def touch_routed_session(
        self,
        route_key: str,
        session_id: str,
        *,
        updated_at: str | None = None,
    ) -> None:
        await asyncio.to_thread(
            self._route_bindings.touch_session,
            route_key,
            session_id,
            updated_at=updated_at,
        )

    async def delete_current_routed_session(
        self,
        route_key: str,
    ) -> DeleteRoutedSessionResult:
        current = await self.current_routed_session(route_key)
        removed = await asyncio.to_thread(self._route_bindings.remove_current, route_key)
        if removed is None:
            raise RuntimeError("Current route has no session binding")
        await self._session_service.purge_session(removed.session_id)
        if self._delivery_store is not None:
            await asyncio.to_thread(self._delivery_store.forget, removed.session_id)

        if removed.replacement_session_id is None:
            replacement = await self.create_routed_session(route_key)
            created_replacement = True
        else:
            replacement = _routed_session(
                await self._session_service.load_session(
                    removed.replacement_session_id
                )
            )
            created_replacement = False
        return DeleteRoutedSessionResult(
            deleted=current,
            current=replacement,
            created_replacement=created_replacement,
        )

    async def remember_delivery_target(
        self,
        session_id: str,
        address: ChannelAddress,
        metadata: dict[str, object] | None = None,
    ) -> None:
        if self._delivery_store is None:
            raise RuntimeError("Delivery store is not configured")
        await asyncio.to_thread(
            self._delivery_store.remember,
            session_id,
            address,
            metadata,
        )

    async def forget_delivery_target(self, session_id: str) -> None:
        if self._delivery_store is None:
            raise RuntimeError("Delivery store is not configured")
        await asyncio.to_thread(self._delivery_store.forget, session_id)

    async def get_session_target(self, session_id: str) -> DeliveryTarget | None:
        if self._delivery_store is None:
            return None
        return await asyncio.to_thread(
            self._delivery_store.get_session_target,
            session_id,
        )

    async def get_latest_target(self) -> DeliveryTarget | None:
        if self._delivery_store is None:
            return None
        return await asyncio.to_thread(self._delivery_store.get_latest_target)


def _routed_session(session: Session) -> RoutedSession:
    return RoutedSession(
        session_id=session.id,
        title=session.title,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )
