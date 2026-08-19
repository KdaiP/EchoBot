from __future__ import annotations

import asyncio
from dataclasses import asdict

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response

from ..schemas import (
    ASRTranscriptionResponse,
    CreateWebLLMProviderRequest,
    EditWebLLMProviderRequest,
    TTSRequest,
    TTSVoiceModel,
    TTSVoicesResponse,
    TestWebLLMProviderRequest,
    UpdateWebASRProviderRequest,
    UpdateWebLive2DAnnotationRequest,
    UpdateWebLive2DHotkeyRequest,
    UpdateWebLLMProviderRequest,
    UpdateWebRuntimeConfigRequest,
    WebASRConfigModel,
    WebLive2DAnnotationResponse,
    WebConfigResponse,
    WebLive2DConfigModel,
    WebLive2DHotkeyResponse,
    WebLLMConfigModel,
    WebLLMModelsResponse,
    WebLLMProviderTestResponse,
    WebRuntimeConfigModel,
    WebStageConfigModel,
)
from ..services.web_console import Live2DUploadFile
from ..state import get_app_runtime
from ...runtime.settings import SettingsConflictError
from ...providers import LLMConfigurationConflictError


router = APIRouter(tags=["web"])


@router.get("/web/config", response_model=WebConfigResponse)
async def get_web_config(
    runtime=Depends(get_app_runtime),
) -> WebConfigResponse:
    if runtime.session_service is None or runtime.context is None:
        raise HTTPException(status_code=503, detail="EchoBot runtime is not ready")

    current_session = await runtime.session_service.load_current_session()
    role_name = await runtime.context.coordinator.current_role_name(
        current_session.id,
    )
    route_mode = await runtime.context.coordinator.current_route_mode(
        current_session.id,
    )
    runtime_snapshot = runtime.context.settings_service.runtime_snapshot()
    settings_revision = runtime.context.settings_service.settings.revision
    llm_snapshot = runtime.context.provider_manager.public_snapshot(
        revision=settings_revision,
        config_revision=(
            runtime.context.llm_configuration.revision
            if runtime.context.llm_configuration is not None
            else 0
        ),
    )
    payload = await runtime.web_console_service.build_frontend_config(
        session_id=current_session.id,
        session_title=current_session.title,
        role_name=role_name,
        route_mode=route_mode,
        runtime_config=runtime_snapshot,
        llm_config=llm_snapshot,
        settings_revision=settings_revision,
    )
    return WebConfigResponse(**payload)


@router.patch("/web/runtime", response_model=WebRuntimeConfigModel)
async def update_web_runtime_config(
    request: UpdateWebRuntimeConfigRequest,
    runtime=Depends(get_app_runtime),
) -> WebRuntimeConfigModel:
    if runtime.context is None:
        raise HTTPException(status_code=503, detail="EchoBot runtime is not ready")

    updates = {
        "delegated_ack_enabled": request.delegated_ack_enabled,
        "shell_safety_mode": request.shell_safety_mode,
        "file_write_enabled": request.file_write_enabled,
        "cron_mutation_enabled": request.cron_mutation_enabled,
        "web_private_network_enabled": request.web_private_network_enabled,
    }

    try:
        snapshot = await asyncio.to_thread(
            runtime.context.settings_service.apply_runtime_updates,
            updates,
            expected_revision=request.expected_revision,
        )
    except SettingsConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return WebRuntimeConfigModel(**snapshot)


@router.post("/web/runtime/reset", response_model=WebRuntimeConfigModel)
async def reset_web_runtime_config(
    expected_revision: int | None = Query(default=None),
    runtime=Depends(get_app_runtime),
) -> WebRuntimeConfigModel:
    if runtime.context is None:
        raise HTTPException(status_code=503, detail="EchoBot runtime is not ready")

    try:
        snapshot = await asyncio.to_thread(
            runtime.context.settings_service.reset_runtime,
            expected_revision=expected_revision,
        )
    except SettingsConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return WebRuntimeConfigModel(**snapshot)


@router.get("/web/live2d/{asset_path:path}")
async def get_live2d_asset(
    asset_path: str,
    runtime=Depends(get_app_runtime),
) -> Response:
    try:
        asset_file = runtime.web_console_service.resolve_live2d_asset(asset_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Live2D asset not found: {asset_path}") from exc

    if asset_file.name.endswith(".model3.json"):
        model_json = await runtime.web_console_service.render_live2d_model_json(asset_path)
        return Response(content=model_json, media_type="application/json")

    return FileResponse(asset_file)


@router.get("/web/stage/backgrounds/{asset_path:path}")
async def get_stage_background_asset(
    asset_path: str,
    runtime=Depends(get_app_runtime),
) -> FileResponse:
    try:
        asset_file = runtime.web_console_service.resolve_stage_background_asset(asset_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Stage background not found: {asset_path}") from exc
    return FileResponse(asset_file)


@router.post("/web/stage/backgrounds", response_model=WebStageConfigModel)
async def upload_stage_background(
    image: UploadFile = File(...),
    runtime=Depends(get_app_runtime),
) -> WebStageConfigModel:
    try:
        file_bytes = await image.read()
        payload = await runtime.web_console_service.save_stage_background(
            filename=image.filename or "",
            content_type=image.content_type,
            file_bytes=file_bytes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return WebStageConfigModel(**payload)


@router.post("/web/live2d", response_model=WebLive2DConfigModel)
async def upload_live2d_directory(
    files: list[UploadFile] = File(...),
    relative_paths: list[str] = Form(...),
    runtime=Depends(get_app_runtime),
) -> WebLive2DConfigModel:
    try:
        if len(files) != len(relative_paths):
            raise ValueError("Uploaded Live2D files and paths do not match")

        uploaded_files: list[Live2DUploadFile] = []
        for upload, relative_path in zip(files, relative_paths, strict=True):
            uploaded_files.append(
                Live2DUploadFile(
                    relative_path=relative_path,
                    file_bytes=await upload.read(),
                )
            )

        payload = await runtime.web_console_service.save_live2d_directory(
            uploaded_files=uploaded_files,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return WebLive2DConfigModel(**payload)


@router.patch("/web/live2d/annotations", response_model=WebLive2DAnnotationResponse)
async def update_live2d_annotation(
    request: UpdateWebLive2DAnnotationRequest,
    runtime=Depends(get_app_runtime),
) -> WebLive2DAnnotationResponse:
    try:
        payload = await runtime.web_console_service.save_live2d_annotation(
            selection_key=request.selection_key,
            kind=request.kind,
            file=request.file,
            note=request.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return WebLive2DAnnotationResponse(**payload)


@router.patch("/web/live2d/hotkeys", response_model=WebLive2DHotkeyResponse)
async def update_live2d_hotkey(
    request: UpdateWebLive2DHotkeyRequest,
    runtime=Depends(get_app_runtime),
) -> WebLive2DHotkeyResponse:
    try:
        payload = await runtime.web_console_service.save_live2d_hotkey(
            selection_key=request.selection_key,
            hotkey_key=request.hotkey_key,
            shortcut_tokens=request.shortcut_tokens,
            restore_default=request.restore_default,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return WebLive2DHotkeyResponse(**payload)


@router.get("/web/tts/voices", response_model=TTSVoicesResponse)
async def get_tts_voices(
    provider: str | None = Query(default=None),
    runtime=Depends(get_app_runtime),
) -> TTSVoicesResponse:
    try:
        voices = await runtime.web_console_service.tts_service.list_voices(provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    provider_name = provider or runtime.web_console_service.tts_service.default_provider
    return TTSVoicesResponse(
        provider=provider_name,
        voices=[
            TTSVoiceModel(
                name=voice.name,
                short_name=voice.short_name,
                locale=voice.locale,
                gender=voice.gender,
                display_name=voice.display_name,
            )
            for voice in voices
        ],
    )


@router.post("/web/tts")
async def synthesize_tts(
    request: TTSRequest,
    runtime=Depends(get_app_runtime),
) -> Response:
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="TTS text must not be empty")

    try:
        speech = await runtime.web_console_service.tts_service.synthesize(
            text=text,
            provider_name=request.provider,
            voice=request.voice,
            rate=request.rate,
            volume=request.volume,
            pitch=request.pitch,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    def _safe_header(value: str) -> str:
        try:
            value.encode("latin-1")
            return value
        except UnicodeEncodeError:
            return value.encode("utf-8").decode("latin-1")

    return Response(
        content=speech.audio_bytes,
        media_type=speech.content_type,
        headers={
            "X-TTS-Provider": _safe_header(str(speech.provider)),
            "X-TTS-Voice": _safe_header(str(speech.voice)),
        },
    )


@router.get("/web/asr/status", response_model=WebASRConfigModel)
async def get_asr_status(runtime=Depends(get_app_runtime)) -> WebASRConfigModel:
    snapshot = await runtime.web_console_service.asr_service.status_snapshot()
    revision = runtime.context.settings_service.settings.revision
    return WebASRConfigModel(**asdict(snapshot), revision=revision)


@router.patch("/web/asr/provider", response_model=WebASRConfigModel)
async def update_asr_provider(
    request: UpdateWebASRProviderRequest,
    runtime=Depends(get_app_runtime),
) -> WebASRConfigModel:
    try:
        payload = await runtime.select_asr_provider(
            request.provider,
            expected_revision=request.expected_revision,
        )
    except SettingsConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return WebASRConfigModel(**payload)


@router.get("/web/llm", response_model=WebLLMConfigModel)
async def get_llm_config(runtime=Depends(get_app_runtime)) -> WebLLMConfigModel:
    if runtime.context is None:
        raise HTTPException(status_code=503, detail="EchoBot runtime is not ready")
    revision = runtime.context.settings_service.settings.revision
    return WebLLMConfigModel(
        **runtime.context.provider_manager.public_snapshot(
            revision=revision,
            config_revision=(
                runtime.context.llm_configuration.revision
                if runtime.context.llm_configuration is not None
                else 0
            ),
        )
    )


@router.patch("/web/llm/provider", response_model=WebLLMConfigModel)
async def update_llm_provider(
    request: UpdateWebLLMProviderRequest,
    runtime=Depends(get_app_runtime),
) -> WebLLMConfigModel:
    try:
        payload = await runtime.select_llm_provider(
            request.provider,
            expected_revision=request.expected_revision,
        )
    except SettingsConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return WebLLMConfigModel(**payload)


@router.post("/web/llm/providers/test", response_model=WebLLMProviderTestResponse)
async def test_web_llm_provider(
    request: TestWebLLMProviderRequest,
    runtime=Depends(get_app_runtime),
) -> WebLLMProviderTestResponse:
    try:
        payload = await runtime.test_llm_provider(
            request.profile_dict(),
            api_key=request.api_key,
            existing_name=request.existing_name,
        )
    except (ValueError, RuntimeError) as exc:
        return WebLLMProviderTestResponse(
            success=False,
            message=str(exc),
        )
    return WebLLMProviderTestResponse(**payload)


@router.post(
    "/web/llm/providers/discover-models",
    response_model=WebLLMModelsResponse,
)
async def discover_web_llm_models(
    request: TestWebLLMProviderRequest,
    runtime=Depends(get_app_runtime),
) -> WebLLMModelsResponse:
    try:
        payload = await runtime.discover_llm_models(
            request.profile_dict(),
            api_key=request.api_key,
            existing_name=request.existing_name,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WebLLMModelsResponse(**payload)


@router.post("/web/llm/providers", response_model=WebLLMConfigModel)
async def create_web_llm_provider(
    request: CreateWebLLMProviderRequest,
    runtime=Depends(get_app_runtime),
) -> WebLLMConfigModel:
    try:
        payload = await runtime.create_llm_provider(
            request.profile_dict(),
            api_key=request.api_key,
            expected_config_revision=request.expected_config_revision,
        )
    except LLMConfigurationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return WebLLMConfigModel(**payload)


@router.patch(
    "/web/llm/providers/{provider_name}",
    response_model=WebLLMConfigModel,
)
async def edit_web_llm_provider(
    provider_name: str,
    request: EditWebLLMProviderRequest,
    runtime=Depends(get_app_runtime),
) -> WebLLMConfigModel:
    try:
        payload = await runtime.update_llm_provider(
            provider_name,
            request.updates_dict(),
            api_key=request.api_key,
            clear_api_key=request.clear_api_key,
            expected_config_revision=request.expected_config_revision,
        )
    except LLMConfigurationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return WebLLMConfigModel(**payload)


@router.delete(
    "/web/llm/providers/{provider_name}",
    response_model=WebLLMConfigModel,
)
async def delete_web_llm_provider(
    provider_name: str,
    expected_config_revision: int | None = Query(default=None),
    runtime=Depends(get_app_runtime),
) -> WebLLMConfigModel:
    try:
        payload = await runtime.delete_llm_provider(
            provider_name,
            expected_config_revision=expected_config_revision,
        )
    except LLMConfigurationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return WebLLMConfigModel(**payload)


@router.post("/web/asr", response_model=ASRTranscriptionResponse)
async def transcribe_audio(
    request: Request,
    runtime=Depends(get_app_runtime),
) -> ASRTranscriptionResponse:
    audio_bytes = await request.body()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="ASR audio body must not be empty")

    try:
        result = await runtime.web_console_service.asr_service.transcribe_wav_bytes(audio_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return ASRTranscriptionResponse(text=result.text, language=result.language)


@router.websocket("/web/asr/ws")
async def asr_websocket(websocket: WebSocket) -> None:
    runtime = getattr(websocket.app.state, "runtime", None)
    if runtime is None or runtime.web_console_service is None:
        await websocket.close(code=1011, reason="EchoBot runtime is not ready")
        return

    await websocket.accept()

    try:
        session = await runtime.web_console_service.asr_service.create_realtime_session()
    except RuntimeError as exc:
        await websocket.send_json({"type": "error", "message": str(exc)})
        await websocket.close(code=1013, reason="ASR is not ready")
        return

    snapshot = await runtime.web_console_service.asr_service.status_snapshot()
    await websocket.send_json(
        {
            "type": "ready",
            "sample_rate": snapshot.sample_rate,
            "state": snapshot.state,
            "detail": snapshot.detail,
        }
    )

    try:
        while True:
            message = await websocket.receive()

            event_type = message.get("type")
            if event_type == "websocket.disconnect":
                break

            payload_bytes = message.get("bytes")
            if payload_bytes is not None:
                events = await session.accept_audio_bytes(payload_bytes)
                for event in events:
                    await websocket.send_json(event)
                continue

            payload_text = message.get("text")
            if payload_text == "flush":
                events = await session.flush()
                for event in events:
                    await websocket.send_json(event)
                await websocket.send_json({"type": "flush_complete"})
                continue
            if payload_text == "reset":
                await session.reset()
                await websocket.send_json({"type": "reset"})
                continue
    except WebSocketDisconnect:
        pass
    finally:
        try:
            events = await session.flush()
        except Exception:
            return
        for event in events:
            try:
                await websocket.send_json(event)
            except RuntimeError:
                break
