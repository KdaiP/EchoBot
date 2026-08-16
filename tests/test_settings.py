from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from echobot.models import LLMMessage, LLMResponse
from echobot.providers import LLMProviderManager, load_llm_profiles
from echobot.providers.base import LLMProvider
from echobot.runtime.settings import (
    AppSettings,
    LLMSelection,
    RuntimeConfigSnapshot,
    RuntimeControls,
    SettingsConflictError,
    SettingsService,
    SpeechSettings,
)


class CoordinatorStub:
    def __init__(self) -> None:
        self.delegated_ack_enabled = True

    def set_delegated_ack_enabled(self, enabled: bool) -> None:
        self.delegated_ack_enabled = enabled


def build_defaults() -> AppSettings:
    return AppSettings(
        revision=0,
        runtime=RuntimeConfigSnapshot(),
        llm=LLMSelection(active_provider="default"),
        speech=SpeechSettings(asr_provider="fake-asr"),
    )


class SettingsServiceTests(unittest.TestCase):
    def test_update_is_atomic_versioned_and_applied(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            coordinator = CoordinatorStub()
            controls = RuntimeControls()
            service = SettingsService(workspace, defaults=build_defaults())
            service.bind_runtime(coordinator, controls)

            snapshot = service.apply_runtime_updates(
                {
                    "delegated_ack_enabled": False,
                    "shell_safety_mode": "read-only",
                },
                expected_revision=0,
            )

            self.assertEqual(1, snapshot["revision"])
            self.assertFalse(coordinator.delegated_ack_enabled)
            self.assertEqual("read-only", controls.shell_safety_mode)
            settings_path = workspace / ".echobot" / "settings.json"
            payload = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(1, payload["revision"])
            self.assertEqual("read-only", payload["runtime"]["shell_safety_mode"])
            self.assertEqual([], list(settings_path.parent.glob("*.tmp")))

    def test_stale_revision_does_not_overwrite_newer_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = SettingsService(temp_dir, defaults=build_defaults())
            service.apply_runtime_updates(
                {"file_write_enabled": False},
                expected_revision=0,
            )

            with self.assertRaises(SettingsConflictError):
                service.select_asr_provider(
                    "backup-asr",
                    expected_revision=0,
                )

            self.assertEqual("fake-asr", service.settings.speech.asr_provider)

    def test_llm_and_speech_choices_share_one_document(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = SettingsService(temp_dir, defaults=build_defaults())
            service.select_llm_provider("backup")
            service.select_asr_provider("backup-asr")

            reloaded = SettingsService(temp_dir, defaults=build_defaults())
            self.assertEqual("backup", reloaded.settings.llm.active_provider)
            self.assertEqual("backup-asr", reloaded.settings.speech.asr_provider)
            self.assertEqual(2, reloaded.settings.revision)


class NamedProvider(LLMProvider):
    def __init__(self, name: str) -> None:
        self.name = name

    async def generate(self, messages, **_kwargs) -> LLMResponse:
        del messages
        return LLMResponse(
            message=LLMMessage(role="assistant", content=self.name),
            model=self.name,
        )


class LLMProviderManagerTests(unittest.IsolatedAsyncioTestCase):
    def test_profiles_are_loaded_without_exposing_api_keys(self) -> None:
        profiles = load_llm_profiles(
            {
                "LLM_API_KEY": "default-secret",
                "LLM_MODEL": "default-model",
                "ECHOBOT_LLM_PROFILES": json.dumps(
                    {
                        "backup": {
                            "label": "Backup",
                            "api_key": "backup-secret",
                            "model": "backup-model",
                            "base_url": "https://backup.example/v1",
                            "supports_image_input": False,
                        }
                    },
                    ensure_ascii=False,
                ),
            }
        )
        manager = LLMProviderManager(profiles, active_provider="default")

        profile = manager.select("backup")
        snapshot = manager.public_snapshot(revision=3)

        self.assertFalse(profile.supports_image_input)
        self.assertEqual("backup", snapshot["active_provider"])
        self.assertNotIn("backup-secret", json.dumps(snapshot, ensure_ascii=False))

    async def test_active_provider_is_pinned_for_one_agent_turn(self) -> None:
        profiles = load_llm_profiles(
            {
                "LLM_API_KEY": "default-secret",
                "LLM_MODEL": "default-model",
                "ECHOBOT_LLM_PROFILES": json.dumps(
                    {
                        "backup": {
                            "api_key": "backup-secret",
                            "model": "backup-model",
                        }
                    },
                    ensure_ascii=False,
                ),
            }
        )
        manager = LLMProviderManager(profiles, active_provider="default")
        manager._providers = {
            "default": NamedProvider("default"),
            "backup": NamedProvider("backup"),
        }

        with manager.pin_active_provider():
            manager.select("backup")
            with manager.pin_active_provider():
                pinned = await manager.generate([])
        switched = await manager.generate([])

        self.assertEqual("default", pinned.model)
        self.assertEqual("backup", switched.model)
