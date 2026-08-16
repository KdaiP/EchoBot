from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from echobot.orchestration import RunStore


class RunStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifecycle_and_events_share_one_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = RunStore(Path(temp_dir) / "runs")
            run = await store.create(
                session_id="session1",
                prompt="检查项目",
                immediate_response="处理中",
                role_name="default",
            )
            await store.append_event(
                run.run_id,
                "tool_result",
                {"tool_name": "read_text_file", "is_error": False},
                step=1,
            )
            completed = await store.set_completed(
                run.run_id,
                final_response="完成",
                final_response_content="完成",
                steps=2,
            )

            self.assertIsNotNone(completed)
            assert completed is not None
            self.assertEqual("completed", completed.status)
            self.assertEqual("完成", completed.final_response)
            self.assertEqual(2, completed.steps)
            self.assertEqual(
                [
                    {
                        "event": "tool_result",
                        "run_id": run.run_id,
                        "created_at": (await store.read_events(run.run_id))[0]["created_at"],
                        "step": 1,
                        "tool_name": "read_text_file",
                        "is_error": False,
                    }
                ],
                await store.read_events(run.run_id),
            )
            paths = list((Path(temp_dir) / "runs").glob("*.jsonl"))
            self.assertEqual([f"{run.run_id}.jsonl"], [path.name for path in paths])
            records = [json.loads(line) for line in paths[0].read_text(encoding="utf-8").splitlines()]
            self.assertEqual(
                ["run.created", "run.event", "run.status_changed"],
                [record["type"] for record in records],
            )

    async def test_restart_marks_running_run_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "runs"
            first_store = RunStore(run_dir)
            run = await first_store.create(
                session_id="session1",
                prompt="长任务",
                immediate_response="",
                role_name="default",
            )

            reloaded_store = await asyncio.to_thread(RunStore, run_dir)
            recovered = await reloaded_store.get(run.run_id)

            self.assertIsNotNone(recovered)
            assert recovered is not None
            self.assertEqual("failed", recovered.status)
            self.assertIn("重启", recovered.error)

    async def test_delete_for_session_only_deletes_matching_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = RunStore(Path(temp_dir) / "runs")
            first = await store.create(
                session_id="first",
                prompt="one",
                immediate_response="",
                role_name="default",
            )
            second = await store.create(
                session_id="second",
                prompt="two",
                immediate_response="",
                role_name="default",
            )

            await store.delete_for_session("first")

            self.assertIsNone(await store.get(first.run_id))
            self.assertIsNotNone(await store.get(second.run_id))
