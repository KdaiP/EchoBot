from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ....runtime.settings import RuntimeSettings, RuntimeSettingsStore


class WebRuntimeSettingsService:
    def __init__(self, workspace: Path) -> None:
        self._store = RuntimeSettingsStore(
            workspace / ".echobot" / "runtime_settings.json",
        )

    async def load_settings(self) -> RuntimeSettings:
        return await asyncio.to_thread(self._store.load)

    async def save_selected_asr_provider(
        self,
        provider_name: str,
    ) -> dict[str, Any]:
        settings = await asyncio.to_thread(
            self._store.update_named_value,
            "selected_asr_provider",
            provider_name,
        )
        return settings.to_dict()

    async def save_selected_live2d_model(
        self,
        selection_key: str,
    ) -> dict[str, Any]:
        settings = await asyncio.to_thread(
            self._store.update_named_value,
            "selected_live2d_model",
            selection_key,
        )
        return settings.to_dict()


__all__ = [
    "WebRuntimeSettingsService",
]
