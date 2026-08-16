from __future__ import annotations

import asyncio
import copy
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path

from .parser import compute_next_run, describe_schedule, normalize_schedule
from .types import CronJob, CronJobState, CronPayload, CronSchedule, CronStore


JobExecutor = Callable[[CronJob], Awaitable[str | None]]


class CronService:
    def __init__(
        self,
        store_path: str | Path,
        *,
        on_job: JobExecutor | None = None,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        self.store_path = Path(store_path)
        self.on_job = on_job
        self.poll_interval_seconds = poll_interval_seconds
        self._store = CronStore()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        async with self._lock:
            if self._running:
                return
            self._store = await asyncio.to_thread(self._load_store_sync)
            self._recompute_next_runs()
            await asyncio.to_thread(self._save_store_sync)
            self._running = True
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        async with self._lock:
            self._running = False
            task = self._task
            self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def list_jobs(self, *, include_disabled: bool = False) -> list[CronJob]:
        async with self._lock:
            jobs = [
                copy.deepcopy(job)
                for job in self._store.jobs
                if include_disabled or job.enabled
            ]
        jobs.sort(key=lambda item: item.state.next_run_at or "")
        return jobs

    async def get_job(self, job_id: str) -> CronJob | None:
        async with self._lock:
            job = self._find_job(job_id)
            return copy.deepcopy(job) if job is not None else None

    async def add_job(
        self,
        *,
        name: str,
        schedule: CronSchedule,
        payload: CronPayload,
        delete_after_run: bool = False,
    ) -> CronJob:
        normalize_schedule(schedule)
        now = _now_text()
        next_run = compute_next_run(schedule)
        job = CronJob(
            id=uuid.uuid4().hex[:8],
            name=name,
            enabled=True,
            schedule=schedule,
            payload=payload,
            state=CronJobState(next_run_at=_format_datetime(next_run)),
            created_at=now,
            updated_at=now,
            delete_after_run=delete_after_run,
        )
        async with self._lock:
            self._store.jobs.append(job)
            await asyncio.to_thread(self._save_store_sync)
            return copy.deepcopy(job)

    async def remove_job(self, job_id: str) -> bool:
        async with self._lock:
            before = len(self._store.jobs)
            self._store.jobs = [job for job in self._store.jobs if job.id != job_id]
            removed = len(self._store.jobs) != before
            if removed:
                await asyncio.to_thread(self._save_store_sync)
            return removed

    async def set_enabled(self, job_id: str, enabled: bool) -> CronJob | None:
        async with self._lock:
            job = self._find_job(job_id)
            if job is None:
                return None
            job.enabled = enabled
            job.updated_at = _now_text()
            if enabled:
                job.state.next_run_at = _format_datetime(
                    compute_next_run(job.schedule),
                )
            else:
                job.state.next_run_at = None
            await asyncio.to_thread(self._save_store_sync)
            return copy.deepcopy(job)

    async def run_job(self, job_id: str, *, force: bool = False) -> bool:
        async with self._lock:
            job = self._find_job(job_id)
            if job is None:
                return False
            if not force and not job.enabled:
                return False
        await self._execute_job(job_id, force=force)
        return True

    async def status(self) -> dict[str, object]:
        async with self._lock:
            return {
                "enabled": self._running,
                "jobs": len(self._store.jobs),
                "next_run_at": self._next_run_at(),
            }

    async def _run_loop(self) -> None:
        while self._running:
            await self._run_due_jobs()
            await asyncio.sleep(self._sleep_seconds())

    async def _run_due_jobs(self) -> None:
        due_job_ids: list[str] = []
        now = datetime.now().astimezone()
        async with self._lock:
            for job in self._store.jobs:
                if not job.enabled or not job.state.next_run_at:
                    continue
                next_run = _parse_datetime(job.state.next_run_at)
                if next_run is not None and next_run <= now:
                    due_job_ids.append(job.id)
        for job_id in due_job_ids:
            try:
                await self._execute_job(job_id)
            except RuntimeError:
                continue

    async def _execute_job(self, job_id: str, *, force: bool = False) -> None:
        async with self._lock:
            job = self._find_job(job_id)
            if job is None:
                return
            if not force and not job.enabled:
                return
            job.state.last_status = "running"
            job.state.last_error = None
            job.updated_at = _now_text()
            await asyncio.to_thread(self._save_store_sync)
            job_copy = copy.deepcopy(job)

        error_text: str | None = None
        try:
            if self.on_job is not None:
                await self.on_job(job_copy)
        except Exception as exc:
            error_text = str(exc)

        async with self._lock:
            job = self._find_job(job_id)
            if job is None:
                return

            now = datetime.now().astimezone()
            job.state.last_run_at = _format_datetime(now)
            job.updated_at = _now_text()
            if error_text is None:
                job.state.last_status = "ok"
                job.state.last_error = None
            else:
                job.state.last_status = "error"
                job.state.last_error = error_text

            if job.schedule.kind == "at":
                if job.delete_after_run:
                    self._store.jobs = [
                        item for item in self._store.jobs if item.id != job.id
                    ]
                else:
                    job.enabled = False
                    job.state.next_run_at = None
            else:
                job.state.next_run_at = _format_datetime(
                    compute_next_run(job.schedule, now=now),
                )
            await asyncio.to_thread(self._save_store_sync)

        if error_text is not None:
            raise RuntimeError(error_text)

    def _recompute_next_runs(self) -> None:
        now = datetime.now().astimezone()
        retained_jobs: list[CronJob] = []
        for job in self._store.jobs:
            if not job.enabled:
                job.state.next_run_at = None
                retained_jobs.append(job)
                continue
            job.schedule = normalize_schedule(job.schedule)
            next_run = compute_next_run(job.schedule, now=now)
            if self._finalize_expired_one_shot_job(job, next_run):
                continue
            job.state.next_run_at = _format_datetime(next_run)
            retained_jobs.append(job)
        self._store.jobs = retained_jobs

    def _finalize_expired_one_shot_job(
        self,
        job: CronJob,
        next_run: datetime | None,
    ) -> bool:
        if job.schedule.kind != "at" or next_run is not None:
            return False

        job.state.next_run_at = None
        if job.delete_after_run:
            return True

        # One-shot jobs that already missed their run should not remain enabled.
        job.enabled = False
        job.updated_at = _now_text()
        return False

    def _next_run_at(self) -> str | None:
        values = [
            job.state.next_run_at
            for job in self._store.jobs
            if job.enabled and job.state.next_run_at
        ]
        return min(values) if values else None

    def _sleep_seconds(self) -> float:
        next_run_at = self._next_run_at()
        if not next_run_at:
            return max(self.poll_interval_seconds, 1.0)
        next_run = _parse_datetime(next_run_at)
        if next_run is None:
            return max(self.poll_interval_seconds, 1.0)
        remaining = (next_run - datetime.now().astimezone()).total_seconds()
        if remaining <= 0:
            return 0.1
        return min(max(remaining, 0.1), max(self.poll_interval_seconds, 1.0))

    def _find_job(self, job_id: str) -> CronJob | None:
        for job in self._store.jobs:
            if job.id == job_id:
                return job
        return None

    def _load_store_sync(self) -> CronStore:
        if not self.store_path.exists():
            return CronStore()
        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return CronStore()
        if not isinstance(data, dict):
            return CronStore()
        return CronStore.from_dict(data)

    def _save_store_sync(self) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.store_path.write_text(
            json.dumps(self._store.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def summarize_job(job: CronJob) -> dict[str, object]:
    return {
        "id": job.id,
        "name": job.name,
        "enabled": job.enabled,
        "schedule": describe_schedule(job.schedule),
        "payload_kind": job.payload.kind,
        "session_id": job.payload.session_id,
        "next_run_at": job.state.next_run_at,
        "last_run_at": job.state.last_run_at,
        "last_status": job.state.last_status,
        "last_error": job.state.last_error,
    }


def _format_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone().isoformat(timespec="seconds")


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
