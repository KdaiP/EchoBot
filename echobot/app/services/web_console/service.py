from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from ....asr import ASRService
from ....tts import TTSService
from .live2d import Live2DService, Live2DUploadFile
from .settings import WebRuntimeSettingsService
from .stage import StageBackgroundService


class WebConsoleService:
    def __init__(
        self,
        workspace: Path,
        tts_service: TTSService,
        asr_service: ASRService,
    ) -> None:
        self._tts_service = tts_service
        self._asr_service = asr_service
        app_root = Path(__file__).resolve().parents[2]
        self._runtime_settings_service = WebRuntimeSettingsService(workspace)
        self._live2d_service = Live2DService(
            workspace / ".echobot" / "live2d",
            app_root / "builtin_live2d",
        )
        self._stage_background_service = StageBackgroundService(
            workspace / ".echobot" / "web" / "backgrounds",
            app_root / "builtin_stage_backgrounds",
        )

    @property
    def tts_service(self) -> TTSService:
        return self._tts_service

    @property
    def asr_service(self) -> ASRService:
        return self._asr_service

    async def initialize_runtime_settings(self) -> bool:
        settings = await self._runtime_settings_service.load_settings()
        selected_asr_provider = settings.selected_asr_provider
        if not selected_asr_provider:
            return False

        try:
            await self._asr_service.set_selected_asr_provider(selected_asr_provider)
        except ValueError:
            return False
        return True

    async def build_frontend_config(
        self,
        *,
        session_name: str,
        role_name: str,
        route_mode: str,
        runtime_config: dict[str, Any],
    ) -> dict[str, Any]:
        settings = await self._runtime_settings_service.load_settings()
        live2d = await self._live2d_service.build_config(
            selected_selection_key=settings.selected_live2d_model,
        )
        stage = await self._stage_background_service.build_config()
        return {
            "session_name": session_name,
            "role_name": role_name,
            "route_mode": route_mode,
            "runtime": dict(runtime_config),
            "live2d": live2d or self._live2d_service.empty_config(),
            "stage": stage,
            "asr": asdict(await self._asr_service.status_snapshot()),
            "tts": {
                "default_provider": self._tts_service.default_provider,
                "default_voice": self._tts_service.default_voice_for(),
                "default_voices": {
                    provider_name: self._tts_service.default_voice_for(provider_name)
                    for provider_name in self._tts_service.provider_names()
                },
                "providers": [
                    asdict(status)
                    for status in self._tts_service.providers_status()
                ],
            },
        }

    async def build_stage_config(self) -> dict[str, Any]:
        return await self._stage_background_service.build_config()

    async def set_selected_asr_provider(self, provider_name: str) -> dict[str, Any]:
        await self._asr_service.set_selected_asr_provider(provider_name)
        await self._runtime_settings_service.save_selected_asr_provider(provider_name)
        return asdict(await self._asr_service.status_snapshot())

    def resolve_live2d_asset(self, asset_path: str) -> Path:
        return self._live2d_service.resolve_asset(asset_path)

    async def render_live2d_model_json(self, asset_path: str) -> str:
        return await self._live2d_service.render_model_json(asset_path)

    async def save_live2d_directory(
        self,
        *,
        uploaded_files: list[Live2DUploadFile],
    ) -> dict[str, Any]:
        return await self._live2d_service.save_directory(uploaded_files)

    async def save_live2d_annotation(
        self,
        *,
        selection_key: str,
        kind: str,
        file: str,
        note: str,
    ) -> dict[str, Any]:
        return await self._live2d_service.save_annotation(
            selection_key=selection_key,
            kind=kind,
            file=file,
            note=note,
        )

    async def save_live2d_hotkey(
        self,
        *,
        selection_key: str,
        hotkey_key: str,
        shortcut_tokens: list[str],
        restore_default: bool = False,
    ) -> dict[str, Any]:
        return await self._live2d_service.save_hotkey(
            selection_key=selection_key,
            hotkey_key=hotkey_key,
            shortcut_tokens=shortcut_tokens,
            restore_default=restore_default,
        )

    async def save_selected_live2d_model(
        self,
        *,
        selection_key: str,
    ) -> dict[str, Any]:
        payload = await self._live2d_service.save_selection(selection_key)
        await self._runtime_settings_service.save_selected_live2d_model(
            payload["selection_key"],
        )
        return payload

    def resolve_stage_background_asset(self, asset_path: str) -> Path:
        return self._stage_background_service.resolve_asset(asset_path)

    async def save_stage_background(
        self,
        *,
        filename: str,
        content_type: str | None,
        file_bytes: bytes,
    ) -> dict[str, Any]:
        return await self._stage_background_service.save_background(
            filename=filename,
            content_type=content_type,
            file_bytes=file_bytes,
        )


__all__ = [
    "Live2DUploadFile",
    "WebConsoleService",
]
