from __future__ import annotations

import asyncio

from ...naming import normalize_name_token
from ...orchestration import (
    DEFAULT_ROLE_NAME,
    RoleCard,
    RoleCardRegistry,
    normalize_role_name,
    role_name_from_metadata,
    set_role_name,
)
from ...runtime.sessions import SessionStore


class RoleService:
    def __init__(
        self,
        role_registry: RoleCardRegistry,
        session_store: SessionStore,
    ) -> None:
        self._role_registry = role_registry
        self._session_store = session_store
        self._lock = asyncio.Lock()

    async def list_roles(self) -> list[RoleCard]:
        return await asyncio.to_thread(self._role_registry.cards)

    async def get_role(self, role_name: str) -> RoleCard:
        normalized_name = _validate_role_name(role_name)
        return await asyncio.to_thread(self._role_registry.require, normalized_name)

    async def create_role(self, name: str, prompt: str) -> RoleCard:
        normalized_name = _validate_role_name(name)
        _ensure_custom_role_name(normalized_name)
        normalized_prompt = _validate_role_prompt(prompt)

        async with self._lock:
            existing_card = await asyncio.to_thread(
                self._role_registry.get,
                normalized_name,
            )
            if existing_card is not None:
                raise ValueError(f"Role already exists: {normalized_name}")
            return await asyncio.to_thread(
                self._write_role_card_sync,
                normalized_name,
                normalized_prompt,
            )

    async def update_role(self, role_name: str, prompt: str) -> RoleCard:
        normalized_name = _validate_role_name(role_name)
        _ensure_custom_role_name(normalized_name)
        normalized_prompt = _validate_role_prompt(prompt)

        async with self._lock:
            await asyncio.to_thread(self._role_registry.require, normalized_name)
            return await asyncio.to_thread(
                self._write_role_card_sync,
                normalized_name,
                normalized_prompt,
            )

    async def delete_role(self, role_name: str) -> str:
        normalized_name = _validate_role_name(role_name)
        _ensure_custom_role_name(normalized_name)

        async with self._lock:
            await asyncio.to_thread(self._role_registry.require, normalized_name)
            deleted = await asyncio.to_thread(
                self._delete_role_files_sync,
                normalized_name,
            )
            if not deleted:
                raise ValueError(f"Role file not found: {normalized_name}")
            await asyncio.to_thread(
                self._reset_deleted_role_sessions_sync,
                normalized_name,
            )
            return normalized_name

    def _write_role_card_sync(self, role_name: str, prompt: str) -> RoleCard:
        target_path = self._role_registry.managed_role_path(role_name)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(prompt.rstrip() + "\n", encoding="utf-8")
        self._role_registry.reload()
        return self._role_registry.require(role_name)

    def _delete_role_files_sync(self, role_name: str) -> bool:
        matched_paths = self._role_registry.role_file_paths(role_name)
        deleted = False
        for file_path in matched_paths:
            if not file_path.exists():
                continue
            file_path.unlink()
            deleted = True
        self._role_registry.reload()
        return deleted

    def _reset_deleted_role_sessions_sync(self, deleted_role_name: str) -> None:
        for session_info in self._session_store.list_sessions():
            session = self._session_store.load_session(session_info.id)
            active_role_name = role_name_from_metadata(session.metadata)
            if active_role_name != deleted_role_name:
                continue
            session.metadata = set_role_name(session.metadata, DEFAULT_ROLE_NAME)
            self._session_store.save_session(session)


def _validate_role_name(role_name: str) -> str:
    raw_name = str(role_name or "").strip()
    if not raw_name:
        raise ValueError("Role name cannot be empty")
    if not normalize_name_token(raw_name):
        raise ValueError("Role name must contain letters, digits, hyphen, or underscore")
    normalized_name = normalize_role_name(raw_name)
    return normalized_name


def _ensure_custom_role_name(role_name: str) -> None:
    if role_name == DEFAULT_ROLE_NAME:
        raise ValueError("Default role card cannot be modified from the web console")


def _validate_role_prompt(prompt: str) -> str:
    normalized_prompt = str(prompt or "").strip()
    if not normalized_prompt:
        raise ValueError("Role card content cannot be empty")
    return normalized_prompt
