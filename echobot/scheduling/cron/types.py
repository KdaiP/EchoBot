from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


@dataclass(slots=True)
class CronSchedule:
    kind: Literal["at", "every", "cron"]
    at: str | None = None
    every_seconds: int | None = None
    expr: str | None = None
    timezone: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CronSchedule":
        return cls(
            kind=str(data.get("kind", "every")),  # type: ignore[arg-type]
            at=_optional_text(data.get("at")),
            every_seconds=_optional_int(data.get("every_seconds")),
            expr=_optional_text(data.get("expr")),
            timezone=_optional_text(data.get("timezone")),
        )


@dataclass(slots=True)
class CronPayload:
    kind: Literal["agent", "text"] = "agent"
    content: str = ""
    session_id: str = "heartbeat"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CronPayload":
        return cls(
            kind=str(data.get("kind", "agent")),  # type: ignore[arg-type]
            content=str(data.get("content", "")),
            session_id=str(data.get("session_id", "heartbeat")),
        )


@dataclass(slots=True)
class CronJobState:
    next_run_at: str | None = None
    last_run_at: str | None = None
    last_status: Literal["ok", "error", "running", "skipped"] | None = None
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CronJobState":
        return cls(
            next_run_at=_optional_text(data.get("next_run_at")),
            last_run_at=_optional_text(data.get("last_run_at")),
            last_status=_optional_text(data.get("last_status")),  # type: ignore[arg-type]
            last_error=_optional_text(data.get("last_error")),
        )


@dataclass(slots=True)
class CronJob:
    id: str
    name: str
    enabled: bool = True
    schedule: CronSchedule = field(
        default_factory=lambda: CronSchedule(kind="every"),
    )
    payload: CronPayload = field(default_factory=CronPayload)
    state: CronJobState = field(default_factory=CronJobState)
    created_at: str = ""
    updated_at: str = ""
    delete_after_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "schedule": self.schedule.to_dict(),
            "payload": self.payload.to_dict(),
            "state": self.state.to_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "delete_after_run": self.delete_after_run,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CronJob":
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            enabled=bool(data.get("enabled", True)),
            schedule=CronSchedule.from_dict(dict(data.get("schedule", {}))),
            payload=CronPayload.from_dict(dict(data.get("payload", {}))),
            state=CronJobState.from_dict(dict(data.get("state", {}))),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            delete_after_run=bool(data.get("delete_after_run", False)),
        )


@dataclass(slots=True)
class CronStore:
    version: int = 1
    jobs: list[CronJob] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "jobs": [job.to_dict() for job in self.jobs],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CronStore":
        raw_jobs = data.get("jobs", [])
        jobs = [
            CronJob.from_dict(dict(item))
            for item in raw_jobs
            if isinstance(item, dict)
        ]
        return cls(
            version=int(data.get("version", 1)),
            jobs=jobs,
        )


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)
