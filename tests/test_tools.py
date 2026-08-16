from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import threading
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from echobot import (
    AgentCore,
    AgentRequest,
    AttachmentStore,
    LLMMessage,
    WebRequestTool,
    create_basic_tool_registry,
)
from echobot.models import LLMResponse, ToolCall
from echobot.providers.base import LLMProvider
from echobot.tools import BaseTool, ToolRegistry
from echobot.tools.builtin import _decode_command_output


class EchoTool(BaseTool):
    name = "echo_tool"
    description = "Return the same text."
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
        },
        "required": ["text"],
        "additionalProperties": False,
    }

    async def run(self, arguments: dict[str, str]) -> dict[str, str]:
        return {"echo": arguments["text"]}


class _StaticResponseHandler(BaseHTTPRequestHandler):
    response_body = b"hello"
    content_type = "text/plain; charset=utf-8"
    status = 200

    def do_GET(self) -> None:  # noqa: N802
        body = self.response_body
        self.send_response(self.status)
        self.send_header("Content-Type", self.content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


class LocalHttpServer:
    def __init__(
        self,
        body: str | bytes,
        *,
        content_type: str = "text/plain; charset=utf-8",
        status: int = 200,
    ) -> None:
        self.body = body
        self.content_type = content_type
        self.status = status
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "LocalHttpServer":
        if isinstance(self.body, bytes):
            response_body = self.body
        else:
            response_body = self.body.encode("utf-8")

        handler_class = type(
            "TestHandler",
            (_StaticResponseHandler,),
            {
                "response_body": response_body,
                "content_type": self.content_type,
                "status": self.status,
            },
        )
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("Server is not running")

        host, port = self._server.server_address
        return f"http://{host}:{port}/"


class FakeToolProvider(LLMProvider):
    def __init__(self) -> None:
        self.calls = 0
        self.seen_messages: list[list[LLMMessage]] = []

    async def generate(
        self,
        messages,
        *,
        tools=None,
        tool_choice=None,
        temperature=None,
        max_tokens=None,
    ) -> LLMResponse:
        del tools, tool_choice, temperature, max_tokens
        self.calls += 1
        self.seen_messages.append(list(messages))

        if self.calls == 1:
            tool_calls = [
                ToolCall(
                    id="call_1",
                    name="echo_tool",
                    arguments='{"text": "hello"}',
                )
            ]
            return LLMResponse(
                message=LLMMessage(
                    role="assistant",
                    content="",
                    tool_calls=tool_calls,
                ),
                model="fake-model",
                finish_reason="tool_calls",
                tool_calls=tool_calls,
            )

        return LLMResponse(
            message=LLMMessage(role="assistant", content="done"),
            model="fake-model",
            finish_reason="stop",
        )


class FakeMemorySearchSupport:
    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        min_score: float = 0.1,
    ) -> dict[str, object]:
        return {
            "query": query,
            "max_results": max_results,
            "min_score": min_score,
            "results": [{"path": "MEMORY.md", "content": "saved note"}],
        }


class AgentToolLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_core_runs_tool_loop(self) -> None:
        provider = FakeToolProvider()
        agent = AgentCore(provider)
        registry = ToolRegistry([EchoTool()])

        result = await agent.run(
            AgentRequest(prompt="test", tool_registry=registry)
        )

        self.assertEqual("done", result.response.message.content)
        self.assertEqual(2, provider.calls)
        self.assertEqual("tool", provider.seen_messages[1][-1].role)
        tool_payload = json.loads(provider.seen_messages[1][-1].content)
        self.assertTrue(tool_payload["ok"])
        self.assertEqual("hello", tool_payload["result"]["echo"])


class BasicToolRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_view_image_tool_promotes_image_into_model_context(self) -> None:
        tiny_png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8u8AAAAASUVORK5CYII="
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            image_path = workspace / "pixel.png"
            image_path.write_bytes(tiny_png)
            attachment_store = AttachmentStore(workspace / ".echobot" / "attachments")
            registry = create_basic_tool_registry(
                workspace,
                attachment_store=attachment_store,
            )

            result = await registry.execute(
                ToolCall(
                    id="call_view_image",
                    name="view_image",
                    arguments='{"path": "pixel.png"}',
                )
            )

            payload = json.loads(result.content)
            self.assertTrue(payload["ok"])
            self.assertEqual("pixel.png", payload["result"]["path"])
            self.assertEqual(1, len(result.promoted_image_urls))
            self.assertEqual(
                "attachment://" + payload["result"]["attachment_id"],
                result.promoted_image_urls[0]["url"],
            )

    def test_registry_omits_view_image_when_vision_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            attachment_store = AttachmentStore(workspace / ".echobot" / "attachments")
            registry = create_basic_tool_registry(
                workspace,
                attachment_store=attachment_store,
                supports_image_input=False,
            )

            self.assertNotIn("view_image", registry.names())
            self.assertIn("send_image_to_user", registry.names())
            self.assertIn("send_file_to_user", registry.names())

    async def test_send_image_tool_returns_outbound_image_block(self) -> None:
        tiny_png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8u8AAAAASUVORK5CYII="
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            image_path = workspace / "preview.png"
            image_path.write_bytes(tiny_png)
            attachment_store = AttachmentStore(workspace / ".echobot" / "attachments")
            registry = create_basic_tool_registry(
                workspace,
                attachment_store=attachment_store,
            )

            result = await registry.execute(
                ToolCall(
                    id="call_send_image",
                    name="send_image_to_user",
                    arguments='{"path": "preview.png"}',
                )
            )

            payload = json.loads(result.content)
            self.assertTrue(payload["ok"])
            self.assertEqual("preview.png", payload["result"]["path"])
            self.assertEqual(1, len(result.outbound_content_blocks))
            self.assertEqual("image_url", result.outbound_content_blocks[0]["type"])
            self.assertEqual(
                "attachment://" + payload["result"]["attachment_id"],
                result.outbound_content_blocks[0]["image_url"]["url"],
            )

    async def test_send_file_tool_returns_outbound_file_block(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            file_path = workspace / "report.txt"
            file_path.write_text("hello", encoding="utf-8")
            attachment_store = AttachmentStore(workspace / ".echobot" / "attachments")
            registry = create_basic_tool_registry(
                workspace,
                attachment_store=attachment_store,
            )

            result = await registry.execute(
                ToolCall(
                    id="call_send_file",
                    name="send_file_to_user",
                    arguments='{"path": "report.txt"}',
                )
            )

            payload = json.loads(result.content)
            self.assertTrue(payload["ok"])
            self.assertEqual("report.txt", payload["result"]["path"])
            self.assertEqual(1, len(result.outbound_content_blocks))
            self.assertEqual("file_attachment", result.outbound_content_blocks[0]["type"])
            self.assertEqual(
                "report.txt",
                result.outbound_content_blocks[0]["file_attachment"]["name"],
            )
            self.assertEqual(
                "report.txt",
                result.outbound_content_blocks[0]["file_attachment"]["workspace_path"],
            )

    def test_decode_command_output_prefers_utf8_when_locale_is_not_utf8(self) -> None:
        raw_bytes = "Beijing: 🌫  +34°F\n".encode("utf-8")

        with patch(
            "echobot.tools.builtin.locale.getpreferredencoding",
            return_value="cp936",
        ):
            decoded = _decode_command_output(raw_bytes)

        self.assertEqual("Beijing: 🌫  +34°F\n", decoded)

    def test_decode_command_output_falls_back_to_locale_encoding(self) -> None:
        raw_bytes = "天气晴".encode("gbk")

        with patch(
            "echobot.tools.builtin.locale.getpreferredencoding",
            return_value="cp936",
        ):
            decoded = _decode_command_output(raw_bytes)

        self.assertEqual("天气晴", decoded)

    async def test_file_tools_stay_inside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            registry = create_basic_tool_registry(workspace)

            write_result = await registry.execute(
                ToolCall(
                    id="call_write",
                    name="write_text_file",
                    arguments='{"path": "notes/test.txt", "content": "hello"}',
                )
            )
            read_result = await registry.execute(
                ToolCall(
                    id="call_read",
                    name="read_text_file",
                    arguments='{"path": "notes/test.txt"}',
                )
            )
            bad_result = await registry.execute(
                ToolCall(
                    id="call_bad",
                    name="read_text_file",
                    arguments='{"path": "../secret.txt"}',
                )
            )

            write_payload = json.loads(write_result.content)
            read_payload = json.loads(read_result.content)
            bad_payload = json.loads(bad_result.content)

            self.assertTrue(write_payload["ok"])
            self.assertEqual("hello", read_payload["result"]["content"])
            self.assertFalse(bad_payload["ok"])

    async def test_web_request_tool_reads_local_page_when_private_access_is_enabled(self) -> None:
        registry = ToolRegistry([WebRequestTool(allow_private_network=True)])
        with LocalHttpServer("hello web tool") as server:
            result = await registry.execute(
                ToolCall(
                    id="call_web",
                    name="fetch_web_page",
                    arguments=json.dumps({"url": server.url}, ensure_ascii=False),
                )
            )

        payload = json.loads(result.content)
        self.assertTrue(payload["ok"])
        self.assertEqual(200, payload["result"]["status"])
        self.assertIn("hello web tool", payload["result"]["content"])
        self.assertEqual("text", payload["result"]["content_kind"])

    async def test_web_request_tool_blocks_private_network_by_default(self) -> None:
        registry = create_basic_tool_registry()
        result = await registry.execute(
            ToolCall(
                id="call_web_private",
                name="fetch_web_page",
                arguments=json.dumps({"url": "http://127.0.0.1/"}, ensure_ascii=False),
            )
        )

        payload = json.loads(result.content)
        self.assertFalse(payload["ok"])
        self.assertIn("Private network addresses are not allowed", payload["error"])

    async def test_web_request_tool_extracts_html_text(self) -> None:
        registry = ToolRegistry([WebRequestTool(allow_private_network=True)])
        html_page = (
            "<html><head><title>Example</title><style>.hidden{display:none;}</style></head>"
            "<body><h1>Hello</h1><p>World</p><script>ignore_me()</script></body></html>"
        )

        with LocalHttpServer(html_page, content_type="text/html; charset=utf-8") as server:
            result = await registry.execute(
                ToolCall(
                    id="call_web_html",
                    name="fetch_web_page",
                    arguments=json.dumps({"url": server.url}, ensure_ascii=False),
                )
            )

        payload = json.loads(result.content)
        self.assertTrue(payload["ok"])
        self.assertEqual("html", payload["result"]["content_kind"])
        self.assertIn("Hello", payload["result"]["content"])
        self.assertIn("World", payload["result"]["content"])
        self.assertNotIn("ignore_me()", payload["result"]["content"])

    async def test_web_request_tool_supports_non_ascii_url_query(self) -> None:
        registry = ToolRegistry([WebRequestTool(allow_private_network=True)])
        seen_path: dict[str, str] = {}

        class RecordingHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                seen_path["value"] = self.path
                body = "ok".encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                del format, args

        server = ThreadingHTTPServer(("127.0.0.1", 0), RecordingHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            url = f"http://127.0.0.1:{server.server_port}/s?wd=新闻&tn=news"
            result = await registry.execute(
                ToolCall(
                    id="call_web_non_ascii_url",
                    name="fetch_web_page",
                    arguments=json.dumps({"url": url}, ensure_ascii=False),
                )
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        payload = json.loads(result.content)
        self.assertTrue(payload["ok"])
        self.assertEqual("/s?wd=%E6%96%B0%E9%97%BB&tn=news", seen_path["value"])
        self.assertEqual(url, payload["result"]["requested_url"])
        self.assertIn("%E6%96%B0%E9%97%BB", payload["result"]["url"])

    async def test_web_request_tool_rejects_binary_content(self) -> None:
        registry = ToolRegistry([WebRequestTool(allow_private_network=True)])
        png_header = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"

        with LocalHttpServer(png_header, content_type="image/png") as server:
            result = await registry.execute(
                ToolCall(
                    id="call_web_binary",
                    name="fetch_web_page",
                    arguments=json.dumps({"url": server.url}, ensure_ascii=False),
                )
            )

        payload = json.loads(result.content)
        self.assertFalse(payload["ok"])
        self.assertIn("Only text responses are supported", payload["error"])

    async def test_command_execution_tool_runs_in_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "subdir").mkdir()
            registry = create_basic_tool_registry(workspace)

            result = await registry.execute(
                ToolCall(
                    id="call_shell",
                    name="run_shell_command",
                    arguments=json.dumps(
                        {
                            "command": 'python -c "from pathlib import Path; print(Path.cwd().name)"',
                            "workdir": "subdir",
                        },
                        ensure_ascii=False,
                    ),
                )
            )

            payload = json.loads(result.content)
            self.assertTrue(payload["ok"])
            self.assertEqual(0, payload["result"]["return_code"])
            self.assertIn("subdir", payload["result"]["stdout"])

    async def test_command_execution_tool_blocks_write_command_in_read_only_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            registry = create_basic_tool_registry(
                workspace,
                shell_safety_mode="read-only",
            )

            result = await registry.execute(
                ToolCall(
                    id="call_shell_blocked",
                    name="run_shell_command",
                    arguments=json.dumps(
                        {
                            "command": 'Set-Content note.txt "hello"',
                        },
                        ensure_ascii=False,
                    ),
                )
            )

            payload = json.loads(result.content)
            self.assertFalse(payload["ok"])
            self.assertIn("shell safety mode", payload["error"])

    async def test_command_execution_tool_blocks_interpreter_bypass_in_read_only_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            registry = create_basic_tool_registry(
                workspace,
                shell_safety_mode="read-only",
            )

            result = await registry.execute(
                ToolCall(
                    id="call_shell_python_bypass",
                    name="run_shell_command",
                    arguments=json.dumps(
                        {
                            "command": (
                                'python -c "from pathlib import Path; '
                                "Path('blocked.txt').write_text('x', encoding='utf-8')\""
                            ),
                        },
                        ensure_ascii=False,
                    ),
                )
            )

            payload = json.loads(result.content)
            self.assertFalse(payload["ok"])
            self.assertIn("danger-full-access", payload["error"])
            self.assertFalse((workspace / "blocked.txt").exists())

    async def test_command_execution_tool_allows_simple_workspace_write_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            registry = create_basic_tool_registry(
                workspace,
                shell_safety_mode="workspace-write",
            )
            command = 'Set-Content note.txt "hello"' if os.name == "nt" else "touch note.txt"

            result = await registry.execute(
                ToolCall(
                    id="call_shell_workspace_write",
                    name="run_shell_command",
                    arguments=json.dumps(
                        {
                            "command": command,
                        },
                        ensure_ascii=False,
                    ),
                )
            )

            payload = json.loads(result.content)
            self.assertTrue(payload["ok"])
            self.assertTrue((workspace / "note.txt").exists())

    async def test_command_execution_tool_respects_disabled_workspace_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            registry = create_basic_tool_registry(
                workspace,
                allow_file_writes=False,
                shell_safety_mode="workspace-write",
            )
            command = 'Set-Content note.txt "hello"' if os.name == "nt" else "touch note.txt"

            result = await registry.execute(
                ToolCall(
                    id="call_shell_workspace_write_blocked",
                    name="run_shell_command",
                    arguments=json.dumps(
                        {
                            "command": command,
                        },
                        ensure_ascii=False,
                    ),
                )
            )

            payload = json.loads(result.content)
            self.assertFalse(payload["ok"])
            self.assertIn("workspace file writes are disabled", payload["error"])
            self.assertFalse((workspace / "note.txt").exists())

    async def test_file_write_tools_can_be_disabled_by_runtime_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            source_file = workspace / "demo.txt"
            source_file.write_text("hello", encoding="utf-8")
            registry = create_basic_tool_registry(
                workspace,
                allow_file_writes=False,
            )

            write_result = await registry.execute(
                ToolCall(
                    id="call_write_blocked",
                    name="write_text_file",
                    arguments=json.dumps(
                        {
                            "path": "blocked.txt",
                            "content": "data",
                        },
                        ensure_ascii=False,
                    ),
                )
            )
            edit_result = await registry.execute(
                ToolCall(
                    id="call_edit_blocked",
                    name="edit_text_file",
                    arguments=json.dumps(
                        {
                            "path": "demo.txt",
                            "old_text": "hello",
                            "new_text": "world",
                        },
                        ensure_ascii=False,
                    ),
                )
            )

            write_payload = json.loads(write_result.content)
            edit_payload = json.loads(edit_result.content)

            self.assertFalse(write_payload["ok"])
            self.assertIn("已禁用文件写入工具", write_payload["error"])
            self.assertFalse(edit_payload["ok"])
            self.assertIn("已禁用文件写入工具", edit_payload["error"])

    async def test_command_execution_tool_blocks_parent_directory_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            registry = create_basic_tool_registry(
                workspace,
                shell_safety_mode="workspace-write",
            )

            result = await registry.execute(
                ToolCall(
                    id="call_shell_parent_path",
                    name="run_shell_command",
                    arguments=json.dumps(
                        {
                            "command": 'Get-Content ..\\secret.txt',
                        },
                        ensure_ascii=False,
                    ),
                )
            )

            payload = json.loads(result.content)
            self.assertFalse(payload["ok"])
            self.assertIn("outside the workspace", payload["error"])

    async def test_search_and_edit_tools_cover_common_code_workflows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            source_file = workspace / "src" / "demo.py"
            source_file.parent.mkdir(parents=True, exist_ok=True)
            source_file.write_text(
                "def greet():\n    return 'hello'\n",
                encoding="utf-8",
            )
            registry = create_basic_tool_registry(workspace)

            search_files_result = await registry.execute(
                ToolCall(
                    id="call_search_files",
                    name="search_files",
                    arguments=json.dumps(
                        {"pattern": "*.py"},
                        ensure_ascii=False,
                    ),
                )
            )
            search_text_result = await registry.execute(
                ToolCall(
                    id="call_search_text",
                    name="search_text_in_files",
                    arguments=json.dumps(
                        {"query": "return", "glob": "*.py"},
                        ensure_ascii=False,
                    ),
                )
            )
            edit_result = await registry.execute(
                ToolCall(
                    id="call_edit",
                    name="edit_text_file",
                    arguments=json.dumps(
                        {
                            "path": "src/demo.py",
                            "old_text": "hello",
                            "new_text": "hi",
                        },
                        ensure_ascii=False,
                    ),
                )
            )

            search_files_payload = json.loads(search_files_result.content)
            search_text_payload = json.loads(search_text_result.content)
            edit_payload = json.loads(edit_result.content)

            self.assertTrue(search_files_payload["ok"])
            self.assertEqual("src/demo.py", search_files_payload["result"]["matches"][0]["path"])
            self.assertTrue(search_text_payload["ok"])
            self.assertEqual("src/demo.py", search_text_payload["result"]["matches"][0]["path"])
            self.assertTrue(edit_payload["ok"])
            self.assertEqual("hi", source_file.read_text(encoding="utf-8").split("'")[1])

    @unittest.skipUnless(shutil.which("git"), "git is required for git tool tests")
    async def test_git_tools_report_status_and_diff(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            subprocess.run(
                ["git", "init"],
                cwd=workspace,
                check=True,
                capture_output=True,
                text=True,
            )
            tracked_file = workspace / "demo.txt"
            tracked_file.write_text("before\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "demo.txt"],
                cwd=workspace,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=workspace,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "EchoBot Test"],
                cwd=workspace,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "init"],
                cwd=workspace,
                check=True,
                capture_output=True,
                text=True,
            )
            tracked_file.write_text("after\n", encoding="utf-8")

            registry = create_basic_tool_registry(workspace)
            status_result = await registry.execute(
                ToolCall(
                    id="call_git_status",
                    name="git_status",
                    arguments="{}",
                )
            )
            diff_result = await registry.execute(
                ToolCall(
                    id="call_git_diff",
                    name="git_diff",
                    arguments=json.dumps(
                        {"path": "demo.txt"},
                        ensure_ascii=False,
                    ),
                )
            )

            status_payload = json.loads(status_result.content)
            diff_payload = json.loads(diff_result.content)

            self.assertTrue(status_payload["ok"])
            self.assertIn("demo.txt", status_payload["result"]["text"])
            self.assertTrue(diff_payload["ok"])
            self.assertIn("-before", diff_payload["result"]["diff"])
            self.assertIn("+after", diff_payload["result"]["diff"])

    async def test_basic_tool_registry_can_register_memory_search(self) -> None:
        registry = create_basic_tool_registry(
            memory_support=FakeMemorySearchSupport(),
        )

        result = await registry.execute(
            ToolCall(
                id="call_memory",
                name="memory_search",
                arguments=json.dumps(
                    {
                        "query": "user preference",
                        "max_results": 2,
                        "min_score": 0.2,
                    },
                    ensure_ascii=False,
                ),
            )
        )

        payload = json.loads(result.content)
        self.assertTrue(payload["ok"])
        self.assertEqual("user preference", payload["result"]["query"])
        self.assertEqual(2, payload["result"]["max_results"])
        self.assertEqual(0.2, payload["result"]["min_score"])
        self.assertEqual("MEMORY.md", payload["result"]["results"][0]["path"])
