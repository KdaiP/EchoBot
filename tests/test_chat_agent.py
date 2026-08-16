from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

from echobot import LLMMessage, ToolCall
from echobot.cli.chat import _build_streamed_assistant_writer
from echobot.cli.trace import (
    build_tool_call_trace_title,
    build_tool_result_trace_title,
    format_json_text,
    print_tool_trace,
)


class ChatAgentTraceTests(unittest.TestCase):
    def test_format_json_text_pretty_prints_json(self) -> None:
        formatted = format_json_text('{"ok":true,"result":{"name":"demo"}}')
        self.assertIn('"ok": true', formatted)
        self.assertIn('"name": "demo"', formatted)

    def test_print_tool_trace_outputs_skill_specific_labels(self) -> None:
        messages = [
            LLMMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        name="activate_skill",
                        arguments='{"name":"demo-skill"}',
                    ),
                    ToolCall(
                        id="call_2",
                        name="read_text_file",
                        arguments='{"path":"README.md"}',
                    ),
                ],
            ),
            LLMMessage(
                role="tool",
                content=(
                    '{"ok":true,"result":{"kind":"skill_activation",'
                    '"name":"demo-skill","already_active":false}}'
                ),
                tool_call_id="call_1",
            ),
        ]

        stream = io.StringIO()
        with redirect_stdout(stream):
            print_tool_trace(messages)

        output = stream.getvalue()
        self.assertIn("[skill-call] activate_skill", output)
        self.assertIn("[tool-call] read_text_file", output)
        self.assertIn("[skill-activate] demo-skill", output)

    def test_build_tool_trace_titles_detect_skill_results(self) -> None:
        self.assertEqual(
            "[skill-call] activate_skill",
            build_tool_call_trace_title("activate_skill"),
        )
        self.assertEqual(
            "[skill-activate] demo-skill (already active)",
            build_tool_result_trace_title(
                "activate_skill",
                (
                    '{"ok":true,"result":{"kind":"skill_activation",'
                    '"name":"demo-skill","already_active":true}}'
                ),
            ),
        )


class ChatAgentAsyncTurnTests(unittest.IsolatedAsyncioTestCase):
    async def test_streamed_assistant_writer_prints_prefix_once(self) -> None:
        on_chunk, started = _build_streamed_assistant_writer()

        stream = io.StringIO()
        with redirect_stdout(stream):
            await on_chunk("Hel")
            await on_chunk("lo")

        self.assertTrue(started())
        self.assertEqual("Assistant> Hello", stream.getvalue())
