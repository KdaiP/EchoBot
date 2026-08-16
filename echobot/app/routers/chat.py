from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from ...orchestration import AgentRun, OrchestratedTurnResult
from ...turn_inputs import (
    has_file_processing_capability,
    resolve_attachment_files,
    resolve_attachment_images,
    resolve_file_attachment_route_mode,
)
from ..schemas import (
    AgentRunEventsResponse,
    AgentRunResponse,
    AgentRunsResponse,
    AgentRunSummaryModel,
    ChatRequest,
    ChatResponse,
)
from ..state import get_app_runtime


router = APIRouter(tags=["chat"])
logger = logging.getLogger(__name__)


@router.post("/chat", response_model=ChatResponse)
async def run_chat(
    request: ChatRequest,
    runtime=Depends(get_app_runtime),
) -> ChatResponse:
    try:
        session_id = await _resolve_session_id(request, runtime)
        image_urls = await _resolve_chat_images(
            request,
            runtime.context.attachment_store,
            supports_image_input=runtime.context.supports_image_input,
        )
        file_attachments = await _resolve_chat_files(
            request,
            runtime.context.attachment_store,
            runtime.context.workspace,
        )
        result = await runtime.chat_service.run_prompt(
            session_id,
            request.prompt,
            image_urls=image_urls,
            file_attachments=file_attachments,
            role_name=request.role_name,
            route_mode=await _resolve_effective_route_mode(
                request,
                runtime,
                session_id=session_id,
                has_file_attachments=bool(file_attachments),
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=_error_message(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=_error_message(exc)) from exc
    except Exception as exc:
        logger.exception("Chat request failed")
        raise HTTPException(status_code=500, detail=_error_message(exc)) from exc

    return _build_chat_response(result)


@router.post("/chat/stream")
async def run_chat_stream(
    request: ChatRequest,
    runtime=Depends(get_app_runtime),
) -> StreamingResponse:
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def on_chunk(chunk: str) -> None:
        await queue.put(
            _stream_payload_bytes(
                {
                    "type": "chunk",
                    "delta": chunk,
                }
            )
        )

    async def produce() -> None:
        try:
            session_id = await _resolve_session_id(request, runtime)
            image_urls = await _resolve_chat_images(
                request,
                runtime.context.attachment_store,
                supports_image_input=runtime.context.supports_image_input,
            )
            file_attachments = await _resolve_chat_files(
                request,
                runtime.context.attachment_store,
                runtime.context.workspace,
            )
            result = await runtime.chat_service.run_prompt_stream(
                session_id,
                request.prompt,
                image_urls=image_urls,
                file_attachments=file_attachments,
                role_name=request.role_name,
                route_mode=await _resolve_effective_route_mode(
                    request,
                    runtime,
                    session_id=session_id,
                    has_file_attachments=bool(file_attachments),
                ),
                on_chunk=on_chunk,
            )
        except (ValueError, RuntimeError) as exc:
            await queue.put(
                _stream_payload_bytes(
                    {
                        "type": "error",
                        "message": _error_message(exc),
                    }
                )
            )
        except Exception as exc:
            logger.exception("Chat stream failed")
            await queue.put(
                _stream_payload_bytes(
                    {
                        "type": "error",
                        "message": _error_message(exc),
                    }
                )
            )
        else:
            await queue.put(
                _stream_payload_bytes(
                    {
                        "type": "done",
                        "session_id": result.session.id,
                        "session_title": result.session.title,
                        "response": result.response_text,
                        "response_content": result.response_content,
                        "updated_at": result.session.updated_at,
                        "steps": result.steps,
                        "agent_summary": result.agent_summary,
                        "delegated": result.delegated,
                        "completed": result.completed,
                        "run_id": result.run_id,
                        "status": result.status,
                        "role_name": result.role_name,
                    }
                )
            )
        finally:
            await queue.put(None)

    producer_task = asyncio.create_task(produce())

    async def body():
        try:
            while True:
                payload = await queue.get()
                if payload is None:
                    break
                yield payload
        finally:
            if not producer_task.done():
                producer_task.cancel()
            with suppress(asyncio.CancelledError):
                await producer_task

    return StreamingResponse(
        body(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/chat/runs", response_model=AgentRunsResponse)
async def list_agent_runs(
    session_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    runtime=Depends(get_app_runtime),
) -> AgentRunsResponse:
    runs = await runtime.chat_service.list_runs(
        session_id=session_id,
        status=status,
        limit=limit,
    )
    return AgentRunsResponse(
        runs=[_build_agent_run_summary(run) for run in runs],
    )


@router.get("/chat/runs/{run_id}", response_model=AgentRunResponse)
async def get_agent_run(
    run_id: str,
    runtime=Depends(get_app_runtime),
) -> AgentRunResponse:
    run = await runtime.chat_service.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

    return _build_agent_run_response(run)


@router.get("/chat/runs/{run_id}/events", response_model=AgentRunEventsResponse)
async def get_agent_run_events(
    run_id: str,
    runtime=Depends(get_app_runtime),
) -> AgentRunEventsResponse:
    run, events = await runtime.chat_service.get_run_events(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

    return AgentRunEventsResponse(
        run_id=run.run_id,
        session_id=run.session_id,
        status=run.status,
        updated_at=run.updated_at,
        events=events,
    )


@router.post("/chat/runs/{run_id}/cancel", response_model=AgentRunResponse)
async def cancel_agent_run(
    run_id: str,
    runtime=Depends(get_app_runtime),
) -> AgentRunResponse:
    run = await runtime.chat_service.cancel_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

    return _build_agent_run_response(run)


@router.post("/chat/runs/{run_id}/retry", response_model=ChatResponse)
async def retry_agent_run(
    run_id: str,
    runtime=Depends(get_app_runtime),
) -> ChatResponse:
    try:
        result = await runtime.chat_service.retry_run(run_id)
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "不存在" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc

    return _build_chat_response(result)


def _stream_payload_bytes(payload: dict[str, object]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def _error_message(exc: Exception) -> str:
    return str(exc).strip() or type(exc).__name__


def _build_chat_response(result: OrchestratedTurnResult) -> ChatResponse:
    return ChatResponse(
        session_id=result.session.id,
        session_title=result.session.title,
        response=result.response_text,
        response_content=result.response_content,
        updated_at=result.session.updated_at,
        steps=result.steps,
        agent_summary=result.agent_summary,
        delegated=result.delegated,
        completed=result.completed,
        run_id=result.run_id,
        status=result.status,
        role_name=result.role_name,
    )


def _build_agent_run_response(run: AgentRun) -> AgentRunResponse:
    response_text = run.final_response or run.immediate_response
    return AgentRunResponse(
        run_id=run.run_id,
        session_id=run.session_id,
        prompt=run.prompt,
        role_name=run.role_name,
        status=run.status,
        attempt=run.attempt,
        retry_of_run_id=run.retry_of_run_id,
        can_retry=run.status in {"failed", "cancelled"},
        response=response_text,
        response_content=run.final_response_content or response_text,
        error=run.error,
        steps=run.steps,
        pending_user_input=run.pending_user_input,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        updated_at=run.updated_at,
    )


def _build_agent_run_summary(run: AgentRun) -> AgentRunSummaryModel:
    return AgentRunSummaryModel(
        run_id=run.run_id,
        session_id=run.session_id,
        prompt=run.prompt,
        role_name=run.role_name,
        status=run.status,
        attempt=run.attempt,
        retry_of_run_id=run.retry_of_run_id,
        can_retry=run.status in {"failed", "cancelled"},
        error=run.error,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        updated_at=run.updated_at,
    )


async def _resolve_chat_images(
    request: ChatRequest,
    attachment_store,
    *,
    supports_image_input: bool,
) -> list[dict[str, str]]:
    if not supports_image_input or not request.images:
        return []
    return await asyncio.to_thread(
        resolve_attachment_images,
        attachment_store,
        request.images,
    )


async def _resolve_chat_files(
    request: ChatRequest,
    attachment_store,
    workspace,
) -> list[dict[str, object]]:
    if not request.files:
        return []
    return await asyncio.to_thread(
        resolve_attachment_files,
        attachment_store,
        workspace,
        request.files,
    )


async def _resolve_effective_route_mode(
    request: ChatRequest,
    runtime,
    *,
    session_id: str,
    has_file_attachments: bool,
):
    can_process_files = False
    current_route_mode = None
    if has_file_attachments:
        can_process_files = has_file_processing_capability(
            runtime.context.skill_registry,
            getattr(runtime.context, "tool_registry_factory", None),
            session_id,
        )
    if request.route_mode is None and can_process_files:
        current_route_mode = await runtime.chat_service.current_route_mode(
            session_id,
        )

    return resolve_file_attachment_route_mode(
        requested_route_mode=request.route_mode,
        current_route_mode=current_route_mode,
        has_file_attachments=has_file_attachments,
        can_process_files=can_process_files,
    )


async def _resolve_session_id(request: ChatRequest, runtime) -> str:
    if request.session_id:
        return request.session_id
    session = await runtime.session_service.load_current_session()
    return session.id
