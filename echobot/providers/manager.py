from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from ..attachments import AttachmentStore
from ..models import LLMMessage, LLMResponse, LLMTool
from .base import LLMProvider
from .openai_compatible import OpenAICompatibleProvider, OpenAICompatibleSettings


@dataclass(frozen=True, slots=True)
class LLMProfile:
    name: str
    label: str
    settings: OpenAICompatibleSettings
    supports_image_input: bool = True
    source: str = "environment"
    editable: bool = False
    api_key_configured: bool | None = None

    def public_dict(self, *, selected: bool) -> dict[str, object]:
        return {
            "name": self.name,
            "label": self.label,
            "model": self.settings.model,
            "base_url": self.settings.base_url,
            "timeout": self.settings.timeout,
            "max_retries": self.settings.max_retries,
            "extra_headers": {
                name: (
                    "***"
                    if name.lower()
                    in {
                        "authorization",
                        "proxy-authorization",
                        "x-api-key",
                        "api-key",
                    }
                    else value
                )
                for name, value in self.settings.extra_headers.items()
            },
            "extra_body": dict(self.settings.extra_body),
            "supports_image_input": self.supports_image_input,
            "source": self.source,
            "editable": self.editable,
            "api_key_configured": (
                self.api_key_configured
                if self.api_key_configured is not None
                else bool(self.settings.api_key.strip())
                and self.settings.api_key.strip().upper() != "EMPTY"
            ),
            "selected": selected,
        }


class LLMProviderManager(LLMProvider):
    """Route LLM calls to one of the deployment-defined provider profiles."""

    def __init__(
        self,
        profiles: Mapping[str, LLMProfile],
        *,
        active_provider: str = "",
        attachment_store: AttachmentStore | None = None,
    ) -> None:
        self._profiles = dict(profiles)
        if active_provider and active_provider not in self._profiles:
            raise ValueError(f"Unknown LLM provider profile: {active_provider}")
        if self._profiles and not active_provider:
            active_provider = next(iter(self._profiles))

        self._attachment_store = attachment_store
        self._providers: dict[str, LLMProvider] = {}
        self._active_provider = active_provider
        self._pinned_provider: ContextVar[LLMProvider | None] = ContextVar(
            "echobot_pinned_llm_provider",
            default=None,
        )
        self._pin_counts: dict[int, int] = {}
        self._retired_providers: dict[int, LLMProvider] = {}
        self._close_tasks: set[asyncio.Task[None]] = set()

    @property
    def active_provider_name(self) -> str:
        return self._active_provider

    @property
    def active_profile(self) -> LLMProfile | None:
        return self._profiles.get(self._active_provider)

    def has_profile(self, name: str) -> bool:
        return name in self._profiles

    def get_profile(self, name: str) -> LLMProfile | None:
        return self._profiles.get(str(name or "").strip())

    def select(self, name: str) -> LLMProfile:
        normalized_name = str(name or "").strip()
        if normalized_name not in self._profiles:
            raise ValueError(f"Unknown LLM provider profile: {normalized_name}")
        self._provider_for(normalized_name)
        self._active_provider = normalized_name
        profile = self.active_profile
        assert profile is not None
        return profile

    def public_snapshot(
        self,
        *,
        revision: int,
        config_revision: int = 0,
    ) -> dict[str, object]:
        return {
            "revision": revision,
            "config_revision": config_revision,
            "active_provider": self._active_provider,
            "providers": [
                profile.public_dict(selected=name == self._active_provider)
                for name, profile in self._profiles.items()
            ],
        }

    async def upsert_profile(self, profile: LLMProfile) -> None:
        previous_provider = self._providers.pop(profile.name, None)
        self._profiles[profile.name] = profile
        if previous_provider is not None:
            await self._retire_provider(previous_provider)

    async def delete_profile(self, name: str) -> None:
        if name == self._active_provider:
            raise ValueError("Select another LLM provider before deleting this one")
        if name not in self._profiles:
            raise ValueError(f"Unknown LLM provider profile: {name}")
        del self._profiles[name]
        previous_provider = self._providers.pop(name, None)
        if previous_provider is not None:
            await self._retire_provider(previous_provider)

    async def close(self) -> None:
        providers = list(self._providers.values()) + list(
            self._retired_providers.values()
        )
        self._providers.clear()
        self._retired_providers.clear()
        await asyncio.gather(
            *(_close_provider(provider) for provider in providers),
            *self._close_tasks,
            return_exceptions=True,
        )
        self._close_tasks.clear()

    @contextmanager
    def pin_active_provider(self) -> Iterator[None]:
        provider = self._pinned_provider.get()
        if provider is None:
            provider = self._provider_for(self._active_provider)
        provider_id = id(provider)
        self._pin_counts[provider_id] = self._pin_counts.get(provider_id, 0) + 1
        token = self._pinned_provider.set(provider)
        try:
            yield
        finally:
            self._pinned_provider.reset(token)
            remaining = self._pin_counts.get(provider_id, 1) - 1
            if remaining > 0:
                self._pin_counts[provider_id] = remaining
            else:
                self._pin_counts.pop(provider_id, None)
                retired = self._retired_providers.pop(provider_id, None)
                if retired is not None:
                    self._schedule_close(retired)

    async def generate(
        self,
        messages: Sequence[LLMMessage],
        *,
        tools: Sequence[LLMTool] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        return await self._current_provider().generate(
            messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def stream_generate(
        self,
        messages: Sequence[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        async for chunk in self._current_provider().stream_generate(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            yield chunk

    def _current_provider(self) -> LLMProvider:
        return self._pinned_provider.get() or self._provider_for(self._active_provider)

    def _provider_for(self, name: str) -> LLMProvider:
        if not name or name not in self._profiles:
            raise RuntimeError(
                "No LLM provider is configured. Open the web control panel and "
                "add an LLM provider first."
            )
        provider = self._providers.get(name)
        if provider is None:
            provider = OpenAICompatibleProvider(
                self._profiles[name].settings,
                attachment_store=self._attachment_store,
            )
            self._providers[name] = provider
        return provider

    async def _retire_provider(self, provider: LLMProvider) -> None:
        provider_id = id(provider)
        if self._pin_counts.get(provider_id, 0) > 0:
            self._retired_providers[provider_id] = provider
            return
        await _close_provider(provider)

    def _schedule_close(self, provider: LLMProvider) -> None:
        task = asyncio.create_task(_close_provider(provider))
        self._close_tasks.add(task)
        task.add_done_callback(self._close_tasks.discard)


def load_llm_profiles(
    env: Mapping[str, str] | None = None,
) -> dict[str, LLMProfile]:
    source = os.environ if env is None else env
    default_settings = OpenAICompatibleSettings.from_env(source)
    profiles = {
        "default": LLMProfile(
            name="default",
            label=source.get("ECHOBOT_LLM_DEFAULT_LABEL", "Default").strip()
            or "Default",
            settings=default_settings,
            supports_image_input=_env_bool(
                source,
                "ECHOBOT_LLM_SUPPORTS_IMAGE_INPUT",
                True,
            ),
        )
    }

    raw_profiles = source.get("ECHOBOT_LLM_PROFILES", "").strip()
    if not raw_profiles:
        return profiles

    try:
        payload = json.loads(raw_profiles)
    except json.JSONDecodeError as exc:
        raise ValueError("ECHOBOT_LLM_PROFILES must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("ECHOBOT_LLM_PROFILES must be a JSON object")

    for raw_name, raw_config in payload.items():
        name = str(raw_name or "").strip()
        if not name or name == "default":
            raise ValueError("LLM profile names must be non-empty and cannot be 'default'")
        if not isinstance(raw_config, dict):
            raise ValueError(f"LLM profile {name!r} must be a JSON object")
        profiles[name] = _profile_from_dict(name, raw_config)
    return profiles


def load_optional_llm_profiles(
    env: Mapping[str, str] | None = None,
) -> dict[str, LLMProfile]:
    source = os.environ if env is None else env
    has_api_key = bool(source.get("LLM_API_KEY", "").strip())
    has_model = bool(source.get("LLM_MODEL", "").strip())
    has_extra_profiles = bool(source.get("ECHOBOT_LLM_PROFILES", "").strip())
    if not has_api_key and not has_model and not has_extra_profiles:
        return {}
    return load_llm_profiles(source)


async def _close_provider(provider: LLMProvider) -> None:
    close = getattr(provider, "close", None)
    if callable(close):
        result = close()
        if result is not None:
            await result


def _profile_from_dict(name: str, data: Mapping[str, Any]) -> LLMProfile:
    api_key = _required_text(data.get("api_key"), name=f"{name}.api_key")
    model = _required_text(data.get("model"), name=f"{name}.model")
    label_value = data.get("label", name)
    label = _required_text(label_value, name=f"{name}.label")
    base_url_value = data.get("base_url", "https://api.openai.com/v1")
    base_url = _required_text(base_url_value, name=f"{name}.base_url")
    timeout = _number(data.get("timeout", 60.0), name=f"{name}.timeout", minimum=0)
    max_retries = _integer(
        data.get("max_retries", 2),
        name=f"{name}.max_retries",
        minimum=0,
    )
    extra_headers = _string_mapping(
        data.get("extra_headers", {}),
        name=f"{name}.extra_headers",
    )
    extra_body = data.get("extra_body", {})
    if not isinstance(extra_body, dict):
        raise ValueError(f"{name}.extra_body must be an object")
    supports_image_input = data.get("supports_image_input", True)
    if not isinstance(supports_image_input, bool):
        raise ValueError(f"{name}.supports_image_input must be a boolean")

    return LLMProfile(
        name=name,
        label=label,
        settings=OpenAICompatibleSettings(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            extra_headers=extra_headers,
            extra_body=dict(extra_body),
        ),
        supports_image_input=supports_image_input,
    )


def _env_bool(
    env: Mapping[str, str],
    name: str,
    default: bool,
) -> bool:
    raw_value = env.get(name)
    if raw_value is None or not raw_value.strip():
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _required_text(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _number(value: Any, *, name: str, minimum: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if number <= minimum:
        raise ValueError(f"{name} must be greater than {minimum}")
    return number


def _integer(value: Any, *, name: str, minimum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if number < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return number


def _string_mapping(value: Any, *, name: str) -> dict[str, str]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str)
        for key, item in value.items()
    ):
        raise ValueError(f"{name} must be an object with string values")
    return dict(value)
