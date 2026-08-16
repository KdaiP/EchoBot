from __future__ import annotations

from collections.abc import Awaitable, Callable

from ..models import message_content_to_text
from ..orchestration import ConversationCoordinator
from ..scheduling.cron import CronJob
from .session_runner import SessionAgentRunner


ScheduleNotifier = Callable[[str, str, str, str], Awaitable[None]]


def build_cron_job_executor(
    session_runner: SessionAgentRunner,
    coordinator: ConversationCoordinator,
    notify: ScheduleNotifier,
):
    async def execute(job: CronJob) -> str | None:
        if job.payload.kind == "text":
            await session_runner.append_assistant_message(
                job.payload.session_id,
                job.payload.content,
            )
            visible_content = await coordinator.present_scheduled_notification(
                job.payload.session_id,
                job.payload.content,
            )
            await notify(
                job.payload.session_id,
                "cron",
                job.name,
                visible_content,
            )
            return visible_content

        execution = await session_runner.run_prompt(
            job.payload.session_id,
            job.payload.content,
            scheduled_context=True,
        )
        raw_content = message_content_to_text(
            execution.agent_result.response.message.content
        ).strip()
        if raw_content:
            visible_content = await coordinator.present_scheduled_notification(
                job.payload.session_id,
                raw_content,
            )
            await notify(
                job.payload.session_id,
                "cron",
                job.name,
                visible_content,
            )
            return visible_content or None
        return None

    return execute


def build_heartbeat_executor(session_runner: SessionAgentRunner):
    async def execute(tasks: str) -> str | None:
        execution = await session_runner.run_prompt(
            "heartbeat",
            tasks,
            scheduled_context=True,
        )
        content = message_content_to_text(
            execution.agent_result.response.message.content
        ).strip()
        return content or None

    return execute
