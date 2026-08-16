from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from echobot import LLMMessage, SessionStore, ToolCall
from echobot.runtime.sessions import normalize_session_id


class SessionStoreTests(unittest.TestCase):
    def test_current_session_is_created_with_stable_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionStore(Path(temp_dir) / "sessions")

            session = store.load_current_session()

            self.assertEqual(session.id, store.get_current_session_id())
            self.assertTrue((store.base_dir / f"{session.id}.jsonl").exists())
            self.assertTrue(session.title)

    def test_visible_and_agent_context_share_one_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionStore(Path(temp_dir) / "sessions")
            session = store.create_session("项目讨论")
            session.history.append(LLMMessage(role="user", content="你好"))
            store.save_session(session)
            session.agent_history.extend(
                [
                    LLMMessage(
                        role="assistant",
                        content="",
                        reasoning_content="读取文件",
                        tool_calls=[
                            ToolCall(
                                id="call_1",
                                name="read_text_file",
                                arguments='{"path":"README.md"}',
                            )
                        ],
                    ),
                    LLMMessage(role="tool", content="完成", tool_call_id="call_1"),
                ]
            )
            session.agent_summary = "内部摘要"
            store.save_agent_context(session)

            loaded = store.load_session(session.id)

            self.assertEqual("项目讨论", loaded.title)
            self.assertEqual("你好", loaded.history[0].content)
            self.assertEqual("内部摘要", loaded.agent_summary)
            self.assertEqual("读取文件", loaded.agent_history[0].reasoning_content)
            self.assertEqual("call_1", loaded.agent_history[1].tool_call_id)
            self.assertEqual([f"{session.id}.jsonl", "state.jsonl"], sorted(
                path.name for path in store.base_dir.glob("*.jsonl")
            ))

    def test_rename_changes_title_without_changing_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionStore(Path(temp_dir) / "sessions")
            session = store.create_session("原始标题")
            path = store.base_dir / f"{session.id}.jsonl"

            renamed = store.rename_session(session.id, "新的标题")

            self.assertEqual(session.id, renamed.id)
            self.assertEqual("新的标题", store.load_session(session.id).title)
            self.assertTrue(path.exists())

    def test_save_appends_events_and_preserves_structured_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionStore(Path(temp_dir) / "sessions")
            session = store.create_session("图片")
            path = store.base_dir / f"{session.id}.jsonl"
            session.history.append(
                LLMMessage(
                    role="user",
                    content=[
                        {"type": "text", "text": "看图"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                    ],
                )
            )
            store.save_session(session)
            session.history.append(LLMMessage(role="assistant", content="收到"))
            store.save_session(session)

            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            loaded = store.load_session(session.id)

            self.assertEqual(
                ["session.created", "visible.message", "visible.message"],
                [record["type"] for record in records],
            )
            self.assertEqual("data:image/png;base64,AAAA", loaded.history[0].content[1]["image_url"]["url"])

    def test_system_session_is_not_listed_or_selectable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionStore(Path(temp_dir) / "sessions")
            system_session = store.ensure_system_session("heartbeat", "Heartbeat")

            self.assertEqual([], store.list_sessions())
            with self.assertRaisesRegex(ValueError, "System sessions"):
                store.set_current_session(system_session.id)

    def test_list_sessions_ignores_legacy_and_unrelated_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionStore(Path(temp_dir) / "sessions")
            session = store.create_session("新版会话")
            (store.base_dir / "default.jsonl").write_text(
                '{"type":"session","name":"default"}\n',
                encoding="utf-8",
            )
            (store.base_dir / "index.jsonl").write_text(
                '{"type":"index","current":"default"}\n',
                encoding="utf-8",
            )

            sessions = store.list_sessions()

            self.assertEqual([session.id], [item.id for item in sessions])

    def test_list_sessions_rejects_corrupt_current_session_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionStore(Path(temp_dir) / "sessions")
            store.base_dir.mkdir(parents=True)
            (store.base_dir / "broken.jsonl").write_text(
                "\n".join(
                    [
                        '{"type":"session.created","schema_version":1,'
                        '"session_id":"broken","title":"损坏会话",'
                        '"kind":"user","created_at":"2026-08-15T00:00:00+08:00"}',
                        '{"type":"unknown.event"}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Unsupported session event"):
                store.list_sessions()


class SessionIdTests(unittest.TestCase):
    def test_normalize_session_id_keeps_valid_id(self) -> None:
        self.assertEqual("demo-session_1", normalize_session_id(" demo-session_1 "))

    def test_normalize_session_id_rejects_titles_and_empty_values(self) -> None:
        for value in ["", "项目讨论", "has spaces"]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_session_id(value)
