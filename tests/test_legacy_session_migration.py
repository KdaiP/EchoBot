from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from echobot.gateway.route_bindings import RouteBindingStore
from echobot.runtime.legacy_session_migration import migrate_legacy_session_data
from echobot.runtime.sessions import SessionStore


class LegacySessionMigrationTests(unittest.TestCase):
    def test_migrates_session_context_current_and_route_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            data_dir = workspace / ".echobot"
            sessions_dir = data_dir / "sessions"
            agent_sessions_dir = data_dir / "agent_sessions"
            sessions_dir.mkdir(parents=True)
            agent_sessions_dir.mkdir()

            self._write_legacy_session(
                sessions_dir / "default.jsonl",
                "default",
                messages=[{"role": "user", "content": "你好"}],
                summary="可见摘要",
                metadata={"role_name": "default"},
            )
            routed_id = "qq_user__session__abc123"
            self._write_legacy_session(
                sessions_dir / f"{routed_id}.jsonl",
                routed_id,
                messages=[{"role": "assistant", "content": "旧回复"}],
            )
            self._write_legacy_session(
                agent_sessions_dir / "default.jsonl",
                "default",
                messages=[
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "name": "demo_tool",
                                "arguments": "{}",
                            }
                        ],
                    }
                ],
                summary="工具摘要",
            )
            self._write_json(
                sessions_dir / "index.jsonl",
                {"current_session": "default"},
                jsonl=True,
            )
            self._write_json(
                data_dir / "route_sessions.json",
                {
                    "routes": {
                        "qq_user": {
                            "current_session_name": routed_id,
                            "sessions": [
                                {
                                    "session_name": routed_id,
                                    "title": "QQ 旧会话",
                                    "created_at": "2026-04-01T09:00:00+08:00",
                                    "updated_at": "2026-04-01T10:00:00+08:00",
                                }
                            ],
                        }
                    }
                },
            )

            report = migrate_legacy_session_data(workspace)

            store = SessionStore(sessions_dir)
            default = store.load_session("default")
            routed = store.load_session(routed_id)
            bindings = RouteBindingStore(data_dir / "route_bindings.jsonl")
            self.assertEqual(2, report.sessions_migrated)
            self.assertEqual(1, report.agent_contexts_migrated)
            self.assertTrue(report.current_session_migrated)
            self.assertEqual(1, report.route_bindings_migrated)
            self.assertEqual("你好", default.history[0].content)
            self.assertEqual("call_1", default.agent_history[0].tool_calls[0].id)
            self.assertEqual("工具摘要", default.agent_summary)
            self.assertEqual({"role_name": "default"}, default.metadata)
            self.assertEqual("QQ 旧会话", routed.title)
            self.assertEqual("default", store.get_current_session_id())
            self.assertEqual(routed_id, bindings.current_session_id("qq_user"))
            self.assertFalse((sessions_dir / "index.jsonl").exists())
            self.assertFalse(agent_sessions_dir.exists())
            self.assertFalse((data_dir / "route_sessions.json").exists())

            second_report = migrate_legacy_session_data(workspace)

            self.assertFalse(second_report.changed)

    def test_attaches_legacy_agent_context_to_an_existing_current_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            sessions_dir = workspace / ".echobot" / "sessions"
            agent_sessions_dir = workspace / ".echobot" / "agent_sessions"
            store = SessionStore(sessions_dir)
            store.create_session("当前会话", session_id="shared")
            agent_sessions_dir.mkdir()
            self._write_legacy_session(
                agent_sessions_dir / "shared.jsonl",
                "shared",
                messages=[{"role": "user", "content": "内部问题"}],
                summary="旧摘要",
            )

            report = migrate_legacy_session_data(workspace)

            migrated = store.load_session("shared")
            self.assertEqual(0, report.sessions_migrated)
            self.assertEqual(1, report.agent_contexts_migrated)
            self.assertEqual("内部问题", migrated.agent_history[0].content)
            self.assertEqual("旧摘要", migrated.agent_summary)
            self.assertFalse(agent_sessions_dir.exists())

    def test_preserves_newer_current_session_and_route_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            data_dir = workspace / ".echobot"
            sessions_dir = data_dir / "sessions"
            store = SessionStore(sessions_dir)
            modern = store.create_session("现代会话", session_id="modern")
            old_route_id = "route__session__old"
            self._write_legacy_session(
                sessions_dir / f"{old_route_id}.jsonl",
                old_route_id,
                messages=[],
            )
            self._write_json(
                sessions_dir / "index.jsonl",
                {"current_session": old_route_id},
                jsonl=True,
            )
            bindings = RouteBindingStore(data_dir / "route_bindings.jsonl")
            bindings.bind_session("route", modern.id)
            self._write_json(
                data_dir / "route_sessions.json",
                {
                    "routes": {
                        "route": {
                            "current_session_name": old_route_id,
                            "sessions": [
                                {
                                    "session_name": old_route_id,
                                    "title": "旧路由会话",
                                }
                            ],
                        }
                    }
                },
            )

            migrate_legacy_session_data(workspace)

            self.assertEqual(modern.id, store.get_current_session_id())
            self.assertEqual(modern.id, bindings.current_session_id("route"))
            self.assertIn(old_route_id, bindings.list_session_ids("route"))

    def test_does_not_consume_a_malformed_legacy_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            path = workspace / ".echobot" / "sessions" / "broken.jsonl"
            path.parent.mkdir(parents=True)
            original = (
                '{"type":"session","name":"broken"}\n'
                "not-json\n"
                '{"type":"message","role":"user","content":"保留"}\n'
            )
            path.write_text(original, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Invalid JSONL record"):
                migrate_legacy_session_data(workspace)

            self.assertEqual(original, path.read_text(encoding="utf-8"))

    def test_does_not_overwrite_an_unrecognized_visible_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            sessions_dir = workspace / ".echobot" / "sessions"
            agent_sessions_dir = workspace / ".echobot" / "agent_sessions"
            sessions_dir.mkdir(parents=True)
            original = '{"type":"foreign","value":"保留"}\n'
            visible_path = sessions_dir / "shared.jsonl"
            visible_path.write_text(original, encoding="utf-8")
            self._write_legacy_session(
                agent_sessions_dir / "shared.jsonl",
                "shared",
                messages=[{"role": "user", "content": "内部问题"}],
            )

            with self.assertRaisesRegex(ValueError, "is not a current session log"):
                migrate_legacy_session_data(workspace)

            self.assertEqual(original, visible_path.read_text(encoding="utf-8"))
            self.assertTrue((agent_sessions_dir / "shared.jsonl").exists())

    @staticmethod
    def _write_legacy_session(
        path: Path,
        session_id: str,
        *,
        messages: list[dict[str, object]],
        summary: str = "",
        metadata: dict[str, object] | None = None,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        records = [
            {
                "type": "session",
                "name": session_id,
                "updated_at": "2026-04-01T10:00:00+08:00",
                "compressed_summary": summary,
                "metadata": metadata or {},
            },
            *[{"type": "message", **message} for message in messages],
        ]
        path.write_text(
            "".join(
                json.dumps(record, ensure_ascii=False) + "\n" for record in records
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _write_json(path: Path, data: object, *, jsonl: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        suffix = "\n" if jsonl else ""
        path.write_text(
            json.dumps(data, ensure_ascii=False) + suffix,
            encoding="utf-8",
        )
