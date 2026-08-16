from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from ..scheduling.cron import CronPayload, CronSchedule, CronService, summarize_job
from .base import BaseTool, ToolOutput


class CronTool(BaseTool):
    name = "cron"
    description = (
        "Manage scheduled jobs. Use it for exact or one-time reminders. "
        "Use delay_seconds for reminders like 'in 20 seconds'. "
        "Use every_seconds only for repeating jobs. "
        "For loose periodic checklists, edit HEARTBEAT.md instead."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "list", "remove", "run", "enable", "disable"],
            },
            "name": {
                "type": "string",
                "description": "Job name. Optional for add.",
            },
            "content": {
                "type": "string",
                "description": "Task text for the job.",
            },
            "task_type": {
                "type": "string",
                "enum": ["agent", "text"],
                "description": "agent = run through the model, text = send fixed text.",
            },
            "every_seconds": {
                "type": "integer",
                "minimum": 1,
                "description": "Repeat interval in seconds for recurring jobs only.",
            },
            "delay_seconds": {
                "type": "integer",
                "minimum": 1,
                "description": "One-time delay in seconds. Prefer this for reminders like 'in 20 seconds'.",
            },
            "cron_expr": {
                "type": "string",
                "description": "Five-field cron expression like '0 9 * * 1-5'.",
            },
            "timezone": {
                "type": "string",
                "description": "IANA timezone used with cron_expr.",
            },
            "at": {
                "type": "string",
                "description": "One-time ISO datetime. If no timezone is given, local time is used.",
            },
            "job_id": {
                "type": "string",
                "description": "Job id for remove/run/enable/disable.",
            },
            "session_id": {
                "type": "string",
                "description": "Stable session ID used by the scheduled run.",
            },
            "include_disabled": {
                "type": "boolean",
                "description": "Include disabled jobs when listing.",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        cron_service: CronService,
        *,
        session_id: str,
        allow_mutations: bool = True,
    ) -> None:
        self._cron_service = cron_service
        self._session_id = session_id
        self._allow_mutations = allow_mutations

    async def run(self, arguments: dict[str, Any]) -> ToolOutput:
        action = str(arguments.get("action", "")).strip().lower()
        if action == "add":
            return await self._add_job(arguments)
        if action == "list":
            return await self._list_jobs(arguments)
        if action == "remove":
            return await self._remove_job(arguments)
        if action == "run":
            return await self._run_job(arguments)
        if action == "enable":
            return await self._set_enabled(arguments, enabled=True)
        if action == "disable":
            return await self._set_enabled(arguments, enabled=False)
        raise ValueError(f"Unsupported cron action: {action}")

    async def _add_job(self, arguments: dict[str, Any]) -> ToolOutput:
        self._require_mutation_allowed()
        content = str(arguments.get("content", "")).strip()
        if not content:
            raise ValueError("content is required for cron add")
        schedule = self._build_schedule(arguments)
        task_type = str(arguments.get("task_type", "agent")).strip().lower() or "agent"
        name = str(arguments.get("name", "")).strip() or _default_job_name(content)
        session_id = str(arguments.get("session_id", "")).strip() or self._session_id
        delete_after_run = schedule.kind == "at"
        job = await self._cron_service.add_job(
            name=name,
            schedule=schedule,
            payload=CronPayload(
                kind=task_type,  # type: ignore[arg-type]
                content=content,
                session_id=session_id,
            ),
            delete_after_run=delete_after_run,
        )
        return {
            "created": True,
            "job": summarize_job(job),
        }

    async def _list_jobs(self, arguments: dict[str, Any]) -> ToolOutput:
        include_disabled = bool(arguments.get("include_disabled", False))
        jobs = await self._cron_service.list_jobs(
            include_disabled=include_disabled,
        )
        return {
            "jobs": [summarize_job(job) for job in jobs],
        }

    async def _remove_job(self, arguments: dict[str, Any]) -> ToolOutput:
        self._require_mutation_allowed()
        job_id = str(arguments.get("job_id", "")).strip()
        if not job_id:
            raise ValueError("job_id is required for cron remove")
        return {
            "removed": await self._cron_service.remove_job(job_id),
            "job_id": job_id,
        }

    async def _run_job(self, arguments: dict[str, Any]) -> ToolOutput:
        self._require_mutation_allowed()
        job_id = str(arguments.get("job_id", "")).strip()
        if not job_id:
            raise ValueError("job_id is required for cron run")
        return {
            "started": await self._cron_service.run_job(job_id, force=True),
            "job_id": job_id,
        }

    async def _set_enabled(
        self,
        arguments: dict[str, Any],
        *,
        enabled: bool,
    ) -> ToolOutput:
        self._require_mutation_allowed()
        job_id = str(arguments.get("job_id", "")).strip()
        if not job_id:
            raise ValueError("job_id is required")
        job = await self._cron_service.set_enabled(job_id, enabled)
        return {
            "updated": job is not None,
            "job": summarize_job(job) if job is not None else None,
        }

    def _require_mutation_allowed(self) -> None:
        if self._allow_mutations:
            return
        raise ValueError(
            "cron mutations are disabled while a scheduled task is running",
        )

    def _build_schedule(self, arguments: dict[str, Any]) -> CronSchedule:
        every_seconds = arguments.get("every_seconds")
        delay_seconds = arguments.get("delay_seconds")
        cron_expr = str(arguments.get("cron_expr", "")).strip()
        at = str(arguments.get("at", "")).strip()
        timezone = str(arguments.get("timezone", "")).strip() or None
        chosen = sum(bool(value) for value in (delay_seconds, every_seconds, cron_expr, at))
        if chosen != 1:
            raise ValueError(
                "Exactly one of delay_seconds, every_seconds, cron_expr, or at is required",
            )
        if delay_seconds:
            return CronSchedule(
                kind="at",
                at=_delay_to_iso_datetime(int(delay_seconds)),
            )
        if every_seconds:
            return CronSchedule(
                kind="every",
                every_seconds=int(every_seconds),
            )
        if cron_expr:
            return CronSchedule(
                kind="cron",
                expr=cron_expr,
                timezone=timezone,
            )
        assert at
        return CronSchedule(kind="at", at=_normalize_iso_datetime(at))


def _default_job_name(content: str) -> str:
    return content[:40].strip() or "scheduled-job"


def _normalize_iso_datetime(value: str) -> str:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone().isoformat(timespec="seconds")


def _delay_to_iso_datetime(delay_seconds: int) -> str:
    if delay_seconds <= 0:
        raise ValueError("delay_seconds must be greater than 0")
    target = datetime.now().astimezone() + timedelta(seconds=delay_seconds)
    return target.isoformat(timespec="seconds")
