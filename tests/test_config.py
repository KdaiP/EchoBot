from __future__ import annotations

import logging
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from echobot.config import (
    _configure_loguru_reme_logging,
    configure_runtime_logging,
)
from echobot.images import DEFAULT_IMAGE_BUDGET
from echobot.runtime.bootstrap import RuntimeOptions, build_runtime_context
from echobot.runtime.settings import DEFAULT_SHELL_SAFETY_MODE


class RuntimeLoggingConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reme_logger = logging.getLogger("reme")
        self.as_logger = logging.getLogger("as")
        self.original_reme_level = self.reme_logger.level
        self.original_as_level = self.as_logger.level
        self.original_reme_handler_levels = [handler.level for handler in self.reme_logger.handlers]
        self.original_as_handler_levels = [handler.level for handler in self.as_logger.handlers]

    def tearDown(self) -> None:
        self.reme_logger.setLevel(self.original_reme_level)
        for handler, level in zip(self.reme_logger.handlers, self.original_reme_handler_levels):
            handler.setLevel(level)

        self.as_logger.setLevel(self.original_as_level)
        for handler, level in zip(self.as_logger.handlers, self.original_as_handler_levels):
            handler.setLevel(level)

    def test_configure_runtime_logging_updates_reme_logger_level(self) -> None:
        configure_runtime_logging({"REME_LOG_LEVEL": "WARNING"})

        self.assertEqual(logging.WARNING, self.reme_logger.level)
        for handler in self.reme_logger.handlers:
            self.assertEqual(logging.WARNING, handler.level)

    def test_configure_runtime_logging_updates_agentscope_logger_level(self) -> None:
        configure_runtime_logging({"AGENTSCOPE_LOG_LEVEL": "ERROR"})

        self.assertEqual(logging.ERROR, self.as_logger.level)
        for handler in self.as_logger.handlers:
            self.assertEqual(logging.ERROR, handler.level)

    def test_configure_runtime_logging_rejects_invalid_log_level(self) -> None:
        with self.assertRaisesRegex(ValueError, "REME_LOG_LEVEL must be one of"):
            configure_runtime_logging({"REME_LOG_LEVEL": "QUIET"})

    def test_configure_loguru_reme_logging_suppresses_reme_info(self) -> None:
        try:
            from loguru import logger
        except ImportError:
            self.skipTest("loguru is not installed")

        stream = io.StringIO()
        _configure_loguru_reme_logging("WARNING", sink=stream)

        reme_logger = logger.patch(
            lambda record: record.update(name="reme.core.utils.pydantic_config_parser")
        )
        other_logger = logger.patch(lambda record: record.update(name="demo.module"))

        reme_logger.info("hidden info")
        reme_logger.warning("visible warning")
        other_logger.info("other info")

        output = stream.getvalue()
        self.assertNotIn("hidden info", output)
        self.assertIn("visible warning", output)
        self.assertIn("other info", output)


class RuntimeBootstrapConfigTests(unittest.TestCase):
    def test_build_runtime_context_reads_agent_max_steps_from_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            env_file = workspace / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "LLM_API_KEY=test-key",
                        "LLM_MODEL=test-model",
                        "LLM_BASE_URL=https://example.com/v1",
                        "LLM_TIMEOUT=60",
                        "ECHOBOT_AGENT_MAX_STEPS=77",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                context = build_runtime_context(
                    RuntimeOptions(
                        workspace=workspace,
                        no_memory=True,
                        no_tools=True,
                        no_skills=True,
                        no_heartbeat=True,
                    ),
                    load_session_state=False,
                )

            self.assertEqual(77, context.session_runner._default_max_steps)

    def test_build_runtime_context_reads_delegated_ack_toggle_from_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            env_file = workspace / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "LLM_API_KEY=test-key",
                        "LLM_MODEL=test-model",
                        "LLM_BASE_URL=https://example.com/v1",
                        "LLM_TIMEOUT=60",
                        "ECHOBOT_DELEGATED_ACK_ENABLED=false",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                context = build_runtime_context(
                    RuntimeOptions(
                        workspace=workspace,
                        no_memory=True,
                        no_tools=True,
                        no_skills=True,
                        no_heartbeat=True,
                    ),
                    load_session_state=False,
                )

            self.assertFalse(context.coordinator._delegated_ack_enabled)

    def test_build_runtime_context_reads_persisted_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            env_file = workspace / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "LLM_API_KEY=test-key",
                        "LLM_MODEL=test-model",
                        "LLM_BASE_URL=https://example.com/v1",
                        "LLM_TIMEOUT=60",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            settings_path = workspace / ".echobot" / "settings.json"
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(
                """{
  "revision": 1,
  "runtime": {
    "delegated_ack_enabled": false,
    "shell_safety_mode": "danger-full-access",
    "file_write_enabled": true,
    "cron_mutation_enabled": true,
    "web_private_network_enabled": false
  },
  "llm": {"active_provider": "default"},
  "speech": {"asr_provider": "sherpa-sense-voice"}
}
""",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                context = build_runtime_context(
                    RuntimeOptions(
                        workspace=workspace,
                        no_memory=True,
                        no_tools=True,
                        no_skills=True,
                        no_heartbeat=True,
                    ),
                    load_session_state=False,
                )

            self.assertFalse(context.coordinator._delegated_ack_enabled)

    def test_build_runtime_context_reads_shell_safety_mode_from_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            env_file = workspace / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "LLM_API_KEY=test-key",
                        "LLM_MODEL=test-model",
                        "LLM_BASE_URL=https://example.com/v1",
                        "LLM_TIMEOUT=60",
                        "ECHOBOT_SHELL_SAFETY_MODE=read-only",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                context = build_runtime_context(
                    RuntimeOptions(
                        workspace=workspace,
                        no_memory=True,
                        no_tools=True,
                        no_skills=True,
                        no_heartbeat=True,
                    ),
                    load_session_state=False,
                )

            self.assertEqual("read-only", context.runtime_controls.shell_safety_mode)

    def test_build_runtime_context_defaults_shell_safety_mode_to_full_access(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            env_file = workspace / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "LLM_API_KEY=test-key",
                        "LLM_MODEL=test-model",
                        "LLM_BASE_URL=https://example.com/v1",
                        "LLM_TIMEOUT=60",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                context = build_runtime_context(
                    RuntimeOptions(
                        workspace=workspace,
                        no_memory=True,
                        no_tools=True,
                        no_skills=True,
                        no_heartbeat=True,
                    ),
                    load_session_state=False,
                )

            self.assertEqual(
                DEFAULT_SHELL_SAFETY_MODE,
                context.runtime_controls.shell_safety_mode,
            )

    def test_build_runtime_context_reads_image_budget_from_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            env_file = workspace / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "LLM_API_KEY=test-key",
                        "LLM_MODEL=test-model",
                        "LLM_BASE_URL=https://example.com/v1",
                        "LLM_TIMEOUT=60",
                        "ECHOBOT_IMAGE_MAX_INPUT_BYTES=31457280",
                        "ECHOBOT_IMAGE_MAX_OUTPUT_BYTES=6291456",
                        "ECHOBOT_IMAGE_MAX_SIDE=4096",
                        "ECHOBOT_IMAGE_MAX_PIXELS=32000000",
                        "ECHOBOT_FILE_MAX_INPUT_BYTES=10485760",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                context = build_runtime_context(
                    RuntimeOptions(
                        workspace=workspace,
                        no_memory=True,
                        no_tools=True,
                        no_skills=True,
                        no_heartbeat=True,
                    ),
                    load_session_state=False,
                )

            budget = context.attachment_store.image_budget
            self.assertEqual(31_457_280, budget.max_input_bytes)
            self.assertEqual(6_291_456, budget.max_output_bytes)
            self.assertEqual(4096, budget.max_side)
            self.assertEqual(32_000_000, budget.max_pixels)
            self.assertEqual(
                DEFAULT_IMAGE_BUDGET.start_quality,
                budget.start_quality,
            )
            self.assertEqual(
                10_485_760,
                context.attachment_store.file_budget.max_input_bytes,
            )

    def test_build_runtime_context_reads_image_input_toggle_from_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            env_file = workspace / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "LLM_API_KEY=test-key",
                        "LLM_MODEL=test-model",
                        "LLM_BASE_URL=https://example.com/v1",
                        "LLM_TIMEOUT=60",
                        "ECHOBOT_LLM_SUPPORTS_IMAGE_INPUT=false",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                context = build_runtime_context(
                    RuntimeOptions(
                        workspace=workspace,
                        no_memory=True,
                        no_skills=True,
                        no_heartbeat=True,
                    ),
                    load_session_state=False,
                )

            self.assertFalse(context.supports_image_input)
            registry = context.tool_registry_factory("default", False)
            assert registry is not None
            self.assertNotIn("view_image", registry.names())

    def test_build_runtime_context_can_start_without_llm_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            with patch.dict(os.environ, {}, clear=True):
                context = build_runtime_context(
                    RuntimeOptions(
                        workspace=workspace,
                        env_file="missing.env",
                        no_tools=True,
                        no_memory=True,
                        no_skills=True,
                        no_heartbeat=True,
                    ),
                    load_session_state=False,
                )

            self.assertEqual("", context.provider_manager.active_provider_name)
            self.assertEqual(
                [],
                context.provider_manager.public_snapshot(revision=0)["providers"],
            )
            self.assertFalse(context.supports_image_input)
