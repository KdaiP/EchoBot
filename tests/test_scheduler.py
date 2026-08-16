from __future__ import annotations

import asyncio
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path

from echobot import LLMMessage, LLMResponse, ToolCall, build_default_system_prompt
from echobot.scheduling.cron import (
    CronJob,
    CronJobState,
    CronPayload,
    CronSchedule,
    CronService,
    CronStore,
    compute_next_run,
)
from echobot.scheduling.heartbeat import HeartbeatService
from echobot.cli.chat import _build_schedule_notifier
from echobot.providers.base import LLMProvider
from echobot.tools.cron import CronTool


class CronParserTests(unittest.TestCase):
    def test_compute_next_run_for_every_schedule(self) -> None:
        now = datetime.now().astimezone()
        next_run = compute_next_run(
            CronSchedule(kind="every", every_seconds=30),
            now=now,
        )

        self.assertIsNotNone(next_run)
        assert next_run is not None
        self.assertGreaterEqual((next_run - now).total_seconds(), 30)

    def test_compute_next_run_for_five_field_cron(self) -> None:
        now = datetime(2026, 3, 8, 8, 15).astimezone()
        next_run = compute_next_run(
            CronSchedule(kind="cron", expr="0 9 * * *"),
            now=now,
        )

        self.assertEqual(9, next_run.hour)
        self.assertEqual(0, next_run.minute)


class CronServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_add_job_persists_store(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "cron" / "jobs.json"
            service = CronService(store_path)

            job = await service.add_job(
                name="demo",
                schedule=CronSchedule(kind="every", every_seconds=60),
                payload=CronPayload(content="hello", session_id="demo"),
            )

            self.assertTrue(store_path.exists())
            saved = json.loads(store_path.read_text(encoding="utf-8"))
            self.assertEqual(job.id, saved["jobs"][0]["id"])
            self.assertEqual("demo", saved["jobs"][0]["name"])

    async def test_running_service_executes_due_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "cron" / "jobs.json"
            called: list[str] = []

            async def on_job(job) -> str:
                called.append(job.id)
                return "done"

            service = CronService(
                store_path,
                on_job=on_job,
                poll_interval_seconds=0.05,
            )
            await service.add_job(
                name="due-now",
                schedule=CronSchedule(
                    kind="at",
                    at=(
                        datetime.now().astimezone() + timedelta(seconds=1)
                    ).isoformat(timespec="seconds"),
                ),
                payload=CronPayload(content="hello", session_id="demo"),
                delete_after_run=True,
            )
            await service.start()
            try:
                await asyncio.sleep(1.5)
            finally:
                await service.stop()

            self.assertEqual(1, len(called))

    async def test_start_prunes_expired_delete_after_run_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "cron" / "jobs.json"
            past_time = (
                datetime.now().astimezone() - timedelta(minutes=5)
            ).isoformat(timespec="seconds")
            store = CronStore(
                jobs=[
                    CronJob(
                        id="expired-delete",
                        name="expired reminder",
                        enabled=True,
                        schedule=CronSchedule(kind="at", at=past_time),
                        payload=CronPayload(content="hello", session_id="demo"),
                        state=CronJobState(next_run_at=None),
                        created_at=past_time,
                        updated_at=past_time,
                        delete_after_run=True,
                    )
                ]
            )
            store_path.parent.mkdir(parents=True, exist_ok=True)
            store_path.write_text(
                json.dumps(store.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            service = CronService(store_path)
            await service.start()
            try:
                jobs = await service.list_jobs(include_disabled=True)
                status = await service.status()
            finally:
                await service.stop()

            saved = json.loads(store_path.read_text(encoding="utf-8"))
            self.assertEqual([], jobs)
            self.assertEqual([], saved["jobs"])
            self.assertEqual(0, status["jobs"])

    async def test_start_disables_expired_one_time_jobs_without_delete_after_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "cron" / "jobs.json"
            past_time = (
                datetime.now().astimezone() - timedelta(minutes=5)
            ).isoformat(timespec="seconds")
            store = CronStore(
                jobs=[
                    CronJob(
                        id="expired-keep",
                        name="missed reminder",
                        enabled=True,
                        schedule=CronSchedule(kind="at", at=past_time),
                        payload=CronPayload(content="hello", session_id="demo"),
                        state=CronJobState(next_run_at=None),
                        created_at=past_time,
                        updated_at=past_time,
                        delete_after_run=False,
                    )
                ]
            )
            store_path.parent.mkdir(parents=True, exist_ok=True)
            store_path.write_text(
                json.dumps(store.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            service = CronService(store_path)
            await service.start()
            try:
                enabled_jobs = await service.list_jobs()
                all_jobs = await service.list_jobs(include_disabled=True)
            finally:
                await service.stop()

            saved = json.loads(store_path.read_text(encoding="utf-8"))
            self.assertEqual([], enabled_jobs)
            self.assertEqual(1, len(all_jobs))
            self.assertFalse(all_jobs[0].enabled)
            self.assertIsNone(all_jobs[0].state.next_run_at)
            self.assertFalse(saved["jobs"][0]["enabled"])
            self.assertIsNone(saved["jobs"][0]["state"]["next_run_at"])


class CronToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_cron_tool_adds_and_lists_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = CronService(Path(temp_dir) / "cron" / "jobs.json")
            tool = CronTool(service, session_id="demo")

            created = await tool.run(
                {
                    "action": "add",
                    "content": "Check todos",
                    "every_seconds": 60,
                }
            )
            listed = await tool.run({"action": "list"})

            self.assertTrue(created["created"])
            self.assertEqual("demo", created["job"]["session_id"])
            self.assertEqual(1, len(listed["jobs"]))
            self.assertEqual("agent", listed["jobs"][0]["payload_kind"])

    async def test_cron_tool_uses_delay_seconds_for_one_time_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = CronService(Path(temp_dir) / "cron" / "jobs.json")
            tool = CronTool(service, session_id="demo")

            created = await tool.run(
                {
                    "action": "add",
                    "content": "20 秒后提醒我喝水",
                    "delay_seconds": 20,
                    "task_type": "text",
                }
            )

            job = created["job"]
            saved = json.loads(service.store_path.read_text(encoding="utf-8"))
            self.assertTrue(created["created"])
            self.assertEqual("text", job["payload_kind"])
            self.assertIsNotNone(job["next_run_at"])
            self.assertEqual("at", saved["jobs"][0]["schedule"]["kind"])
            delay = int(
                (
                    datetime.fromisoformat(saved["jobs"][0]["schedule"]["at"])
                    - datetime.fromisoformat(saved["jobs"][0]["created_at"])
                ).total_seconds()
            )
            self.assertGreaterEqual(delay, 19)
            self.assertLessEqual(delay, 20)
            self.assertTrue(saved["jobs"][0]["delete_after_run"])

    async def test_cron_tool_blocks_mutation_in_scheduled_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = CronService(Path(temp_dir) / "cron" / "jobs.json")
            tool = CronTool(
                service,
                session_id="demo",
                allow_mutations=False,
            )

            with self.assertRaisesRegex(ValueError, "disabled while a scheduled task"):
                await tool.run(
                    {
                        "action": "add",
                        "content": "Check todos",
                        "every_seconds": 60,
                    }
                )


class FakeHeartbeatProvider(LLMProvider):
    def __init__(self, response: LLMResponse) -> None:
        self.response = response
        self.calls = 0

    async def generate(
        self,
        messages,
        *,
        tools=None,
        tool_choice=None,
        temperature=None,
        max_tokens=None,
    ) -> LLMResponse:
        del messages, tools, tool_choice, temperature, max_tokens
        self.calls += 1
        return self.response


class HeartbeatServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_trigger_now_skips_template_only_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            heartbeat_file = Path(temp_dir) / "HEARTBEAT.md"
            heartbeat_file.write_text(
                "# HEARTBEAT.md\n\n<!-- no tasks -->\n",
                encoding="utf-8",
            )
            provider = FakeHeartbeatProvider(
                LLMResponse(
                    message=LLMMessage(role="assistant", content=""),
                    model="fake",
                )
            )
            service = HeartbeatService(
                heartbeat_file=heartbeat_file,
                provider=provider,
            )

            result = await service.trigger_now()

            self.assertIsNone(result)
            self.assertEqual(0, provider.calls)

    async def test_trigger_now_runs_tasks_when_provider_requests_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            heartbeat_file = Path(temp_dir) / "HEARTBEAT.md"
            heartbeat_file.write_text("- [ ] Check todos\n", encoding="utf-8")
            provider = FakeHeartbeatProvider(
                LLMResponse(
                    message=LLMMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_1",
                                name="heartbeat_decision",
                                arguments='{"action":"run","tasks":"review todos"}',
                            )
                        ],
                    ),
                    model="fake",
                    tool_calls=[
                        ToolCall(
                            id="call_1",
                            name="heartbeat_decision",
                            arguments='{"action":"run","tasks":"review todos"}',
                        )
                    ],
                )
            )
            called_with: list[str] = []
            notifications: list[str] = []

            async def on_execute(tasks: str) -> str:
                called_with.append(tasks)
                return "done"

            async def on_notify(message: str) -> None:
                notifications.append(message)

            service = HeartbeatService(
                heartbeat_file=heartbeat_file,
                provider=provider,
                on_execute=on_execute,
                on_notify=on_notify,
            )

            result = await service.trigger_now()

            self.assertEqual("done", result)
            self.assertEqual(["review todos"], called_with)
            self.assertEqual(["done"], notifications)


class SystemPromptSchedulingTests(unittest.TestCase):
    def test_default_system_prompt_can_include_scheduling_details(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)

            prompt = build_default_system_prompt(
                workspace,
                enable_scheduling=True,
                cron_store_path=workspace / ".echobot" / "cron" / "jobs.json",
                heartbeat_file_path=workspace / "HEARTBEAT.md",
                heartbeat_interval_seconds=900,
            )

            self.assertIn("## Scheduling", prompt)
            self.assertIn("HEARTBEAT.md", prompt)
            self.assertIn("cron", prompt)

    def test_default_system_prompt_uses_hidden_heartbeat_file_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)

            prompt = build_default_system_prompt(
                workspace,
                enable_scheduling=True,
            )

            self.assertIn(
                str((workspace / ".echobot" / "HEARTBEAT.md").resolve()),
                prompt,
            )


class ScheduleNotifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_notifier_prints_single_line_when_title_matches_content(self) -> None:
        notifier = _build_schedule_notifier("cron", "喝水提醒：请记得喝水！")
        stream = io.StringIO()

        with redirect_stdout(stream):
            await notifier("喝水提醒：请记得喝水！")

        lines = [line for line in stream.getvalue().splitlines() if line.strip()]
        self.assertEqual(["[cron] 喝水提醒：请记得喝水！"], lines)
