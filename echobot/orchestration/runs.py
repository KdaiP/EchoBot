from __future__ import annotations

import asyncio
import re
import threading
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..jsonl import append_jsonl, read_jsonl
from ..models import MessageContent, normalize_message_content
from ..runtime.sessions import Session


RUN_SCHEMA_VERSION = 1
RUN_CANCELLED_TEXT = "后台任务已停止。"
RUN_INTERRUPTED_TEXT = "任务因 EchoBot 重启而中断。"
RETRYABLE_RUN_STATUSES = frozenset({"failed", "cancelled"})
RUN_STATUSES = frozenset(
    {"running", "waiting_for_input", "completed", "failed", "cancelled"}
)
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


@dataclass(slots=True)
class OrchestratedTurnResult:
    session: Session
    response_text: str
    delegated: bool
    completed: bool
    response_content: MessageContent = ""
    run_id: str | None = None
    status: str = "completed"
    role_name: str = "default"
    steps: int = 1
    agent_summary: str = ""


@dataclass(slots=True)
class AgentRun:
    run_id: str
    session_id: str
    prompt: str
    immediate_response: str
    role_name: str
    status: str
    created_at: str
    updated_at: str
    started_at: str
    finished_at: str
    route_mode: str = ""
    attempt: int = 1
    retry_of_run_id: str | None = None
    image_urls: list[dict[str, str]] = field(default_factory=list)
    file_attachments: list[dict[str, object]] = field(default_factory=list)
    final_response: str = ""
    final_response_content: MessageContent = ""
    error: str = ""
    steps: int = 0
    pending_user_input: dict[str, object] | None = None


CompletionCallback = Callable[[AgentRun], Awaitable[None]]


class RunStore:
    """One append-only JSONL log per agent run.

    Lifecycle state and diagnostic events share the same immutable ``run_id``.
    This replaces the former jobs index plus separate trace directory.
    """

    def __init__(self, base_dir: str | Path = ".echobot/runs") -> None:
        self.base_dir = Path(base_dir)
        self._lock = asyncio.Lock()
        self._sync_lock = threading.RLock()
        self._recover_interrupted_runs()

    def create_run_id(self) -> str:
        return uuid.uuid4().hex

    async def create(
        self,
        *,
        session_id: str,
        prompt: str,
        immediate_response: str,
        role_name: str,
        route_mode: str = "",
        image_urls: list[dict[str, str]] | None = None,
        file_attachments: list[dict[str, object]] | None = None,
        run_id: str | None = None,
        attempt: int = 1,
        retry_of_run_id: str | None = None,
    ) -> AgentRun:
        async with self._lock:
            resolved_run_id = run_id or self.create_run_id()
            if self._run_path(resolved_run_id).exists():
                raise ValueError(f"Run already exists: {resolved_run_id}")
            now = _now_text()
            run = AgentRun(
                run_id=resolved_run_id,
                session_id=session_id,
                prompt=prompt,
                immediate_response=immediate_response,
                role_name=role_name,
                status="running",
                created_at=now,
                updated_at=now,
                started_at=now,
                finished_at="",
                route_mode=route_mode,
                attempt=max(int(attempt), 1),
                retry_of_run_id=retry_of_run_id,
                image_urls=_copy_string_mapping_list(image_urls or []),
                file_attachments=_copy_object_mapping_list(file_attachments or []),
            )
            await asyncio.to_thread(
                self._append_records,
                run.run_id,
                [
                    {
                        "type": "run.created",
                        "schema_version": RUN_SCHEMA_VERSION,
                        **_run_to_dict(run),
                    }
                ],
            )
            return run

    async def get(self, run_id: str) -> AgentRun | None:
        async with self._lock:
            return await asyncio.to_thread(self._load_optional_run, run_id)

    async def append_event(
        self,
        run_id: str,
        event: str,
        data: dict[str, Any] | None = None,
        *,
        step: int = 0,
    ) -> None:
        record: dict[str, Any] = {
            "type": "run.event",
            "event": event,
            "created_at": _now_text(),
        }
        if step > 0:
            record["step"] = step
        if data:
            record["data"] = dict(data)
        async with self._lock:
            if not self._run_path(run_id).exists():
                raise ValueError(f"Run not found: {run_id}")
            await asyncio.to_thread(self._append_records, run_id, [record])

    async def read_events(self, run_id: str) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(self._read_events, run_id)

    async def set_completed(
        self,
        run_id: str,
        *,
        final_response: str,
        final_response_content: MessageContent = "",
        steps: int,
    ) -> AgentRun | None:
        return await self._set_status(
            run_id,
            "completed",
            final_response=final_response,
            final_response_content=final_response_content,
            steps=steps,
        )

    async def set_failed(
        self,
        run_id: str,
        *,
        final_response: str,
        final_response_content: MessageContent = "",
        error: str,
        steps: int = 0,
    ) -> AgentRun | None:
        return await self._set_status(
            run_id,
            "failed",
            final_response=final_response,
            final_response_content=final_response_content,
            error=error,
            steps=steps,
        )

    async def set_cancelled(
        self,
        run_id: str,
        *,
        final_response: str,
        final_response_content: MessageContent = "",
        steps: int = 0,
    ) -> AgentRun | None:
        return await self._set_status(
            run_id,
            "cancelled",
            final_response=final_response,
            final_response_content=final_response_content,
            steps=steps,
        )

    async def set_waiting_for_input(
        self,
        run_id: str,
        *,
        final_response: str,
        final_response_content: MessageContent = "",
        steps: int = 0,
        pending_user_input: dict[str, object] | None = None,
    ) -> AgentRun | None:
        return await self._set_status(
            run_id,
            "waiting_for_input",
            final_response=final_response,
            final_response_content=final_response_content,
            steps=steps,
            pending_user_input=dict(pending_user_input or {}),
        )

    async def counts(self) -> dict[str, int]:
        runs = await self.list_runs(limit=1_000_000)
        counts = {
            "running": 0,
            "waiting_for_input": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
        }
        for run in runs:
            counts[run.status] = counts.get(run.status, 0) + 1
        return counts

    async def list_for_session(
        self,
        session_id: str,
        *,
        status: str | None = None,
    ) -> list[AgentRun]:
        return await self.list_runs(
            session_id=session_id,
            status=status,
            limit=1_000_000,
            oldest_first=True,
        )

    async def list_runs(
        self,
        *,
        session_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        oldest_first: bool = False,
    ) -> list[AgentRun]:
        normalized_limit = max(int(limit), 1)
        async with self._lock:
            runs = await asyncio.to_thread(self._load_all_runs)
        filtered = [
            run
            for run in runs
            if (session_id is None or run.session_id == session_id)
            and (status is None or run.status == status)
        ]
        filtered.sort(
            key=lambda item: (item.updated_at, item.created_at, item.run_id),
            reverse=not oldest_first,
        )
        return filtered[:normalized_limit]

    async def delete_for_session(self, session_id: str) -> None:
        async with self._lock:
            runs = await asyncio.to_thread(self._load_all_runs)
            paths = [self._run_path(run.run_id) for run in runs if run.session_id == session_id]
            await asyncio.to_thread(_unlink_paths, paths)

    async def _set_status(
        self,
        run_id: str,
        status: str,
        *,
        final_response: str,
        final_response_content: MessageContent,
        steps: int,
        error: str = "",
        pending_user_input: dict[str, object] | None = None,
    ) -> AgentRun | None:
        if status not in RUN_STATUSES - {"running"}:
            raise ValueError(f"Unsupported run status: {status}")
        async with self._lock:
            run = await asyncio.to_thread(self._load_optional_run, run_id)
            if run is None:
                return None
            if run.status != "running":
                return run
            now = _now_text()
            record = {
                "type": "run.status_changed",
                "status": status,
                "updated_at": now,
                "finished_at": now,
                "final_response": final_response,
                "final_response_content": normalize_message_content(
                    final_response_content
                ),
                "error": error,
                "steps": max(int(steps), 0),
                "pending_user_input": pending_user_input,
            }
            await asyncio.to_thread(self._append_records, run_id, [record])
            return await asyncio.to_thread(self._load_run, run_id)

    def _recover_interrupted_runs(self) -> None:
        for run in self._load_all_runs():
            if run.status != "running":
                continue
            now = _now_text()
            self._append_records(
                run.run_id,
                [
                    {
                        "type": "run.status_changed",
                        "status": "failed",
                        "updated_at": now,
                        "finished_at": now,
                        "final_response": run.final_response or RUN_INTERRUPTED_TEXT,
                        "final_response_content": (
                            run.final_response_content or RUN_INTERRUPTED_TEXT
                        ),
                        "error": run.error or RUN_INTERRUPTED_TEXT,
                        "steps": run.steps,
                        "pending_user_input": None,
                    }
                ],
            )

    def _load_optional_run(self, run_id: str) -> AgentRun | None:
        if not self._run_path(run_id).exists():
            return None
        return self._load_run(run_id)

    def _load_all_runs(self) -> list[AgentRun]:
        if not self.base_dir.exists():
            return []
        return [self._load_run(path.stem) for path in self.base_dir.glob("*.jsonl")]

    def _load_run(self, run_id: str) -> AgentRun:
        records = _read_records(self._run_path(run_id))
        if not records or records[0].get("type") != "run.created":
            raise ValueError(f"Invalid run log: {run_id}")
        created = records[0]
        if created.get("schema_version") != RUN_SCHEMA_VERSION:
            raise ValueError(f"Unsupported run schema: {run_id}")
        run = _run_from_dict(created)
        for record in records[1:]:
            if record.get("type") != "run.status_changed":
                continue
            run.status = str(record.get("status", run.status))
            run.updated_at = str(record.get("updated_at", run.updated_at))
            run.finished_at = str(record.get("finished_at", run.finished_at))
            run.final_response = str(record.get("final_response", ""))
            run.final_response_content = normalize_message_content(
                record.get("final_response_content", "")
            )
            run.error = str(record.get("error", ""))
            run.steps = _optional_int(record.get("steps")) or 0
            pending = record.get("pending_user_input")
            run.pending_user_input = dict(pending) if isinstance(pending, dict) else None
        return run

    def _read_events(self, run_id: str) -> list[dict[str, Any]]:
        if not self._run_path(run_id).exists():
            return []
        events: list[dict[str, Any]] = []
        for record in _read_records(self._run_path(run_id)):
            if record.get("type") != "run.event":
                continue
            event: dict[str, Any] = {
                "event": record.get("event"),
                "run_id": run_id,
                "created_at": record.get("created_at"),
            }
            if "step" in record:
                event["step"] = record["step"]
            data = record.get("data")
            if isinstance(data, dict):
                event.update(data)
            events.append(event)
        return events

    def _run_path(self, run_id: str) -> Path:
        normalized = str(run_id or "").strip()
        if not _RUN_ID_PATTERN.fullmatch(normalized):
            raise ValueError("Run ID must be 1-128 letters, digits, hyphens, or underscores")
        return self.base_dir / f"{normalized}.jsonl"

    def _append_records(self, run_id: str, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        with self._sync_lock:
            path = self._run_path(run_id)
            append_jsonl(path, records)


def run_can_retry(run: AgentRun) -> bool:
    return run.status in RETRYABLE_RUN_STATUSES


def _run_to_dict(run: AgentRun) -> dict[str, object]:
    return {
        "run_id": run.run_id,
        "session_id": run.session_id,
        "prompt": run.prompt,
        "immediate_response": run.immediate_response,
        "role_name": run.role_name,
        "status": run.status,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "route_mode": run.route_mode,
        "attempt": run.attempt,
        "retry_of_run_id": run.retry_of_run_id,
        "image_urls": _copy_string_mapping_list(run.image_urls),
        "file_attachments": _copy_object_mapping_list(run.file_attachments),
        "final_response": run.final_response,
        "final_response_content": normalize_message_content(run.final_response_content),
        "error": run.error,
        "steps": run.steps,
        "pending_user_input": run.pending_user_input,
    }


def _run_from_dict(data: dict[str, Any]) -> AgentRun:
    created_at = _optional_text(data.get("created_at")) or _now_text()
    return AgentRun(
        run_id=str(data.get("run_id", "")).strip(),
        session_id=str(data.get("session_id", "")).strip(),
        prompt=str(data.get("prompt", "")),
        immediate_response=str(data.get("immediate_response", "")),
        role_name=str(data.get("role_name", "")).strip() or "default",
        status=str(data.get("status", "")).strip() or "failed",
        created_at=created_at,
        updated_at=_optional_text(data.get("updated_at")) or created_at,
        started_at=_optional_text(data.get("started_at")) or created_at,
        finished_at=_optional_text(data.get("finished_at")) or "",
        route_mode=str(data.get("route_mode", "")).strip(),
        attempt=max(_optional_int(data.get("attempt")) or 1, 1),
        retry_of_run_id=_optional_text(data.get("retry_of_run_id")),
        image_urls=_copy_string_mapping_list(data.get("image_urls") or []),
        file_attachments=_copy_object_mapping_list(data.get("file_attachments") or []),
        final_response=str(data.get("final_response", "")),
        final_response_content=normalize_message_content(
            data.get("final_response_content", "")
        ),
        error=str(data.get("error", "")),
        steps=_optional_int(data.get("steps")) or 0,
        pending_user_input=(
            dict(data["pending_user_input"])
            if isinstance(data.get("pending_user_input"), dict)
            else None
        ),
    )


def _read_records(path: Path) -> list[dict[str, Any]]:
    return read_jsonl(path, source=path.name)


def _unlink_paths(paths: list[Path]) -> None:
    for path in paths:
        if path.exists():
            path.unlink()


def _copy_string_mapping_list(values: object) -> list[dict[str, str]]:
    if not isinstance(values, list):
        return []
    return [
        {str(key): str(value) for key, value in item.items()}
        for item in values
        if isinstance(item, dict)
    ]


def _copy_object_mapping_list(values: object) -> list[dict[str, object]]:
    if not isinstance(values, list):
        return []
    return [dict(item) for item in values if isinstance(item, dict)]


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="microseconds")
