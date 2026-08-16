from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from ....asr import ASRService
from ....tts import TTSService
from .live2d import Live2DService, Live2DUploadFile
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

    async def build_frontend_config(
        self,
        *,
        session_id: str,
        session_title: str,
        role_name: str,
        route_mode: str,
        runtime_config: dict[str, Any],
        llm_config: dict[str, Any],
        settings_revision: int,
    ) -> dict[str, Any]:
        live2d = await self._live2d_service.build_config()
        stage = await self._stage_background_service.build_config()
        return {
            "session_id": session_id,
            "session_title": session_title,
            "role_name": role_name,
            "route_mode": route_mode,
            "runtime": dict(runtime_config),
            "llm": dict(llm_config),
            "live2d": live2d or self._live2d_service.empty_config(),
            "stage": stage,
            "asr": {
                **asdict(await self._asr_service.status_snapshot()),
                "revision": settings_revision,
            },
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
