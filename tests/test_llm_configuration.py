from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from echobot.providers import (
    LLMConfigurationConflictError,
    LLMProviderConfigurationService,
    LLMProviderManager,
    load_optional_llm_profiles,
)


def profile_data(name: str = "deepseek") -> dict[str, object]:
    return {
        "name": name,
        "label": "DeepSeek",
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
        "timeout": 60,
        "max_retries": 2,
        "extra_headers": {},
        "extra_body": {},
        "supports_image_input": False,
    }


class LLMProviderConfigurationServiceTests(unittest.TestCase):
    def test_profile_and_credential_are_stored_separately(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            service = LLMProviderConfigurationService(workspace)

            runtime_profile, revision = service.create(
                profile_data(),
                api_key="secret-key",
                expected_revision=0,
            )

            self.assertEqual(1, revision)
            self.assertEqual("secret-key", runtime_profile.settings.api_key)
            public_profile = runtime_profile.public_dict(selected=True)
            self.assertTrue(public_profile["api_key_configured"])
            self.assertNotIn(
                "secret-key",
                json.dumps(public_profile, ensure_ascii=False),
            )

            providers_text = (
                workspace / ".echobot" / "llm_providers.json"
            ).read_text(encoding="utf-8")
            credentials_text = (
                workspace / ".echobot" / "secrets" / "llm_credentials.json"
            ).read_text(encoding="utf-8")
            self.assertNotIn("secret-key", providers_text)
            self.assertIn("secret-key", credentials_text)

            reloaded = LLMProviderConfigurationService(workspace)
            self.assertEqual(
                "secret-key",
                reloaded.runtime_profiles()["deepseek"].settings.api_key,
            )

    def test_update_keeps_key_when_omitted_and_can_clear_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = LLMProviderConfigurationService(temp_dir)
            service.create(profile_data(), api_key="secret-key")

            updated, revision = service.update(
                "deepseek",
                {"label": "Updated"},
                api_key=None,
                clear_api_key=False,
                expected_revision=1,
            )
            self.assertEqual("secret-key", updated.settings.api_key)
            self.assertEqual(2, revision)

            cleared, revision = service.update(
                "deepseek",
                {},
                api_key=None,
                clear_api_key=True,
                expected_revision=2,
            )
            self.assertEqual("EMPTY", cleared.settings.api_key)
            self.assertFalse(cleared.api_key_configured)
            self.assertEqual(3, revision)

    def test_empty_display_name_falls_back_to_model_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = LLMProviderConfigurationService(temp_dir)
            data = profile_data()
            data["label"] = ""

            profile, _revision = service.create(data, api_key=None)

            self.assertEqual("deepseek-chat", profile.label)
            self.assertEqual(
                "deepseek-chat",
                profile.public_dict(selected=False)["label"],
            )

    def test_stale_revision_and_sensitive_headers_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = LLMProviderConfigurationService(temp_dir)
            service.create(profile_data(), api_key=None)

            with self.assertRaises(LLMConfigurationConflictError):
                service.update(
                    "deepseek",
                    {"label": "Stale"},
                    api_key=None,
                    clear_api_key=False,
                    expected_revision=0,
                )

            invalid = profile_data("unsafe")
            invalid["extra_headers"] = {"Authorization": "Bearer secret"}
            with self.assertRaisesRegex(ValueError, "Sensitive headers"):
                service.create(invalid, api_key=None, expected_revision=1)


class OptionalLLMProfilesTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_environment_allows_an_unconfigured_manager(self) -> None:
        profiles = load_optional_llm_profiles({})
        manager = LLMProviderManager(profiles)

        self.assertEqual("", manager.active_provider_name)
        self.assertEqual([], manager.public_snapshot(revision=0)["providers"])
        with self.assertRaisesRegex(RuntimeError, "No LLM provider"):
            await manager.generate([])

        await manager.close()
