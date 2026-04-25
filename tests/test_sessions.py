from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from echobot import ChatSession, LLMMessage, SessionStore, ToolCall
from echobot.runtime.sessions import normalize_session_name


class SessionStoreTests(unittest.TestCase):
    def test_load_current_session_creates_default_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_store = SessionStore(Path(temp_dir) / "sessions")

            session = session_store.load_current_session()

            self.assertEqual("default", session.name)
            self.assertEqual("default", session_store.get_current_session_name())
            self.assertTrue((Path(temp_dir) / "sessions" / "default.jsonl").exists())

    def test_save_and_load_session_preserves_history_and_tool_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_store = SessionStore(Path(temp_dir) / "sessions")
            session = ChatSession(
                name="demo",
                history=[
                    LLMMessage(role="user", content="hello"),
                    LLMMessage(
                        role="assistant",
                        content="",
                        reasoning_content="I should call the file reader.",
                        tool_calls=[
                            ToolCall(
                                id="call_1",
                                name="read_text_file",
                                arguments='{"path":"README.md"}',
                            )
                        ],
                    ),
                    LLMMessage(
                        role="tool",
                        content='{"ok":true}',
                        tool_call_id="call_1",
                    ),
                ],
                updated_at="",
                compressed_summary="previous summary",
            )

            session_store.save_session(session)
            loaded_session = session_store.load_session("demo")

            self.assertEqual("demo", loaded_session.name)
            self.assertEqual("previous summary", loaded_session.compressed_summary)
            self.assertEqual(3, len(loaded_session.history))
            self.assertEqual("user", loaded_session.history[0].role)
            self.assertEqual("read_text_file", loaded_session.history[1].tool_calls[0].name)
            self.assertEqual(
                "I should call the file reader.",
                loaded_session.history[1].reasoning_content,
            )
            self.assertEqual("call_1", loaded_session.history[2].tool_call_id)

            lines = (Path(temp_dir) / "sessions" / "demo.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            self.assertEqual(4, len(lines))
            self.assertIn('"type": "session"', lines[0])
            self.assertIn('"type": "message"', lines[1])

    def test_save_and_load_session_preserves_structured_message_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_store = SessionStore(Path(temp_dir) / "sessions")
            session = ChatSession(
                name="vision",
                history=[
                    LLMMessage(
                        role="user",
                        content=[
                            {"type": "text", "text": "look at this"},
                            {
                                "type": "image_url",
                                "image_url": {"url": "data:image/png;base64,AAAA"},
                            },
                        ],
                    ),
                ],
                updated_at="",
            )

            session_store.save_session(session)
            loaded_session = session_store.load_session("vision")

            self.assertEqual(1, len(loaded_session.history))
            self.assertIsInstance(loaded_session.history[0].content, list)
            self.assertEqual("text", loaded_session.history[0].content[0]["type"])
            self.assertEqual(
                "data:image/png;base64,AAAA",
                loaded_session.history[0].content[1]["image_url"]["url"],
            )

    def test_list_sessions_returns_saved_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_store = SessionStore(Path(temp_dir) / "sessions")
            first_session = ChatSession(
                name="first",
                history=[LLMMessage(role="user", content="one")],
                updated_at="",
            )
            second_session = ChatSession(
                name="second",
                history=[
                    LLMMessage(role="user", content="one"),
                    LLMMessage(role="assistant", content="two"),
                ],
                updated_at="",
                compressed_summary="summary",
            )

            session_store.save_session(first_session)
            session_store.save_session(second_session)
            session_store.set_current_session(second_session.name)
            sessions = session_store.list_sessions()

            self.assertEqual({"first", "second"}, {item.name for item in sessions})
            counts = {item.name: item.message_count for item in sessions}
            self.assertEqual(1, counts["first"])
            self.assertEqual(2, counts["second"])

    def test_create_session_rejects_duplicate_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_store = SessionStore(Path(temp_dir) / "sessions")
            session_store.create_session("demo")

            with self.assertRaisesRegex(ValueError, "Session already exists"):
                session_store.create_session("demo")

    def test_create_session_supports_chinese_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_store = SessionStore(Path(temp_dir) / "sessions")

            created = session_store.create_session("项目讨论")
            loaded = session_store.load_session("项目讨论")

            self.assertEqual("项目讨论", created.name)
            self.assertEqual("项目讨论", loaded.name)
            self.assertTrue((Path(temp_dir) / "sessions" / "项目讨论.jsonl").exists())


class SessionNameTests(unittest.TestCase):
    def test_normalize_session_name_keeps_simple_ascii_name(self) -> None:
        self.assertEqual("demo-session_1", normalize_session_name(" Demo Session_1 "))

    def test_normalize_session_name_keeps_chinese_name(self) -> None:
        self.assertEqual("项目-讨论_1", normalize_session_name(" 项目 讨论_1 "))

    def test_normalize_session_name_rejects_empty_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            normalize_session_name("   ")
