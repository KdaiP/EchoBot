from __future__ import annotations

import os
from fastapi import APIRouter, Depends, HTTPException

from ...scheduling.heartbeat import (
    has_meaningful_heartbeat_content,
    read_or_create_heartbeat_file,
    write_heartbeat_file,
)
from ..schemas import HeartbeatConfigResponse, UpdateHeartbeatRequest
from ..state import get_app_runtime


router = APIRouter(tags=["heartbeat"])


@router.get("/heartbeat", response_model=HeartbeatConfigResponse)
async def get_heartbeat_config(
    runtime=Depends(get_app_runtime),
) -> HeartbeatConfigResponse:
    context = runtime.context
    if context is None:
        raise HTTPException(status_code=503, detail="EchoBot runtime is not ready")

    content = await read_or_create_heartbeat_file(context.heartbeat_file_path)
    return _build_heartbeat_response(
        content=content,
        file_path=context.heartbeat_file_path,
        enabled=bool(context.heartbeat_service and context.heartbeat_service.enabled),
        interval_seconds=_heartbeat_interval_seconds(runtime),
    )


@router.put("/heartbeat", response_model=HeartbeatConfigResponse)
async def update_heartbeat_config(
    request: UpdateHeartbeatRequest,
    runtime=Depends(get_app_runtime),
) -> HeartbeatConfigResponse:
    context = runtime.context
    if context is None:
        raise HTTPException(status_code=503, detail="EchoBot runtime is not ready")

    await write_heartbeat_file(context.heartbeat_file_path, request.content)
    return _build_heartbeat_response(
        content=request.content,
        file_path=context.heartbeat_file_path,
        enabled=bool(context.heartbeat_service and context.heartbeat_service.enabled),
        interval_seconds=_heartbeat_interval_seconds(runtime),
    )


def _build_heartbeat_response(
    *,
    content: str,
    file_path,
    enabled: bool,
    interval_seconds: int,
) -> HeartbeatConfigResponse:
    return HeartbeatConfigResponse(
        enabled=enabled,
        interval_seconds=max(int(interval_seconds), 1),
        file_path=_display_heartbeat_path(file_path),
        content=content,
        has_meaningful_content=has_meaningful_heartbeat_content(content),
    )


def _heartbeat_interval_seconds(runtime) -> int:
    context = runtime.context
    if context is None:
        return 0
    if context.heartbeat_service is not None:
        return context.heartbeat_service.interval_seconds
    return context.heartbeat_interval_seconds


def _display_heartbeat_path(file_path) -> str:
    path_text = str(file_path)
    if os.name != "posix":
        return path_text
    if not path_text.startswith("/private/var/"):
        return path_text
    return path_text.removeprefix("/private")
