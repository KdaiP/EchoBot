from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .manager import LLMProfile
from .openai_compatible import OpenAICompatibleSettings


PROVIDERS_FILE_NAME = "llm_providers.json"
CREDENTIALS_FILE_NAME = "llm_credentials.json"
_PROFILE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SENSITIVE_HEADER_NAMES = {
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "api-key",
}


class LLMConfigurationConflictError(RuntimeError):
    """Raised when a stale browser tries to overwrite provider settings."""


@dataclass(frozen=True, slots=True)
class StoredLLMProfile:
    name: str
    label: str
    model: str
    base_url: str
    timeout: float = 60.0
    max_retries: int = 2
    extra_headers: dict[str, str] = field(default_factory=dict)
    extra_body: dict[str, Any] = field(default_factory=dict)
    supports_image_input: bool = True

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        *,
        expected_name: str | None = None,
    ) -> StoredLLMProfile:
        name = _profile_name(data.get("name"))
        if expected_name is not None and name != expected_name:
            raise ValueError("Provider ID cannot be changed after creation")

        model = _required_text(data.get("model"), name="model")
        label = _optional_text(data.get("label")) or model
        base_url = _required_text(data.get("base_url"), name="base_url")
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")

        timeout = _positive_number(data.get("timeout", 60.0), name="timeout")
        max_retries = _non_negative_integer(
            data.get("max_retries", 2),
            name="max_retries",
        )
        extra_headers = _string_mapping(
            data.get("extra_headers", {}),
            name="extra_headers",
        )
        sensitive_headers = sorted(
            name for name in extra_headers if name.lower() in _SENSITIVE_HEADER_NAMES
        )
        if sensitive_headers:
            names = ", ".join(sensitive_headers)
            raise ValueError(
                f"Sensitive headers must not be stored in extra_headers: {names}"
            )

        extra_body = data.get("extra_body", {})
        if not isinstance(extra_body, dict):
            raise ValueError("extra_body must be an object")
        supports_image_input = data.get("supports_image_input", True)
        if not isinstance(supports_image_input, bool):
            raise ValueError("supports_image_input must be a boolean")

        return cls(
            name=name,
            label=label,
            model=model,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            extra_headers=extra_headers,
            extra_body=dict(extra_body),
            supports_image_input=supports_image_input,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "label": self.label,
            "model": self.model,
            "base_url": self.base_url,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
            "extra_headers": dict(self.extra_headers),
            "extra_body": dict(self.extra_body),
            "supports_image_input": self.supports_image_input,
        }

    def to_runtime_profile(self, api_key: str) -> LLMProfile:
        return LLMProfile(
            name=self.name,
            label=self.label,
            settings=OpenAICompatibleSettings(
                api_key=api_key or "EMPTY",
                model=self.model,
                base_url=self.base_url,
                timeout=self.timeout,
                max_retries=self.max_retries,
                extra_headers=dict(self.extra_headers),
                extra_body=dict(self.extra_body),
            ),
            supports_image_input=self.supports_image_input,
            source="user",
            editable=True,
            api_key_configured=bool(api_key),
        )


class LLMProviderConfigurationService:
    """Persist user-created LLM profiles without exposing their credentials."""

    def __init__(self, workspace: str | Path) -> None:
        root = Path(workspace) / ".echobot"
        self._providers_path = root / PROVIDERS_FILE_NAME
        self._credentials_path = root / "secrets" / CREDENTIALS_FILE_NAME
        self._lock = threading.Lock()
        self._revision, self._profiles = self._load_profiles()
        self._credentials = self._load_credentials()

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    def runtime_profiles(self) -> dict[str, LLMProfile]:
        with self._lock:
            return {
                name: profile.to_runtime_profile(self._credentials.get(name, ""))
                for name, profile in self._profiles.items()
            }

    def create(
        self,
        data: Mapping[str, Any],
        *,
        api_key: str | None,
        expected_revision: int | None = None,
    ) -> tuple[LLMProfile, int]:
        profile = StoredLLMProfile.from_dict(data)
        with self._lock:
            self._check_revision(expected_revision)
            if profile.name in self._profiles:
                raise ValueError(f"LLM provider already exists: {profile.name}")

            next_profiles = dict(self._profiles)
            next_profiles[profile.name] = profile
            next_credentials = dict(self._credentials)
            cleaned_api_key = str(api_key or "").strip()
            if cleaned_api_key:
                next_credentials[profile.name] = cleaned_api_key

            self._save_credentials(next_credentials)
            try:
                next_revision = self._save_profiles(next_profiles)
            except Exception:
                self._save_credentials(self._credentials)
                raise

            self._profiles = next_profiles
            self._credentials = next_credentials
            self._revision = next_revision
            return (
                profile.to_runtime_profile(next_credentials.get(profile.name, "")),
                next_revision,
            )

    def update(
        self,
        name: str,
        updates: Mapping[str, Any],
        *,
        api_key: str | None,
        clear_api_key: bool,
        expected_revision: int | None = None,
    ) -> tuple[LLMProfile, int]:
        normalized_name = _profile_name(name)
        with self._lock:
            self._check_revision(expected_revision)
            current = self._profiles.get(normalized_name)
            if current is None:
                raise ValueError(f"Unknown user LLM provider: {normalized_name}")

            values = current.to_dict()
            values.update({key: value for key, value in updates.items() if value is not None})
            values["name"] = normalized_name
            profile = StoredLLMProfile.from_dict(
                values,
                expected_name=normalized_name,
            )
            next_profiles = dict(self._profiles)
            next_profiles[normalized_name] = profile
            next_credentials = dict(self._credentials)
            if clear_api_key:
                next_credentials.pop(normalized_name, None)
            elif api_key is not None and api_key.strip():
                next_credentials[normalized_name] = api_key.strip()

            self._save_credentials(next_credentials)
            try:
                next_revision = self._save_profiles(next_profiles)
            except Exception:
                self._save_credentials(self._credentials)
                raise

            self._profiles = next_profiles
            self._credentials = next_credentials
            self._revision = next_revision
            return (
                profile.to_runtime_profile(next_credentials.get(normalized_name, "")),
                next_revision,
            )

    def delete(
        self,
        name: str,
        *,
        expected_revision: int | None = None,
    ) -> int:
        normalized_name = _profile_name(name)
        with self._lock:
            self._check_revision(expected_revision)
            if normalized_name not in self._profiles:
                raise ValueError(f"Unknown user LLM provider: {normalized_name}")

            next_profiles = dict(self._profiles)
            del next_profiles[normalized_name]
            next_revision = self._save_profiles(next_profiles)

            next_credentials = dict(self._credentials)
            next_credentials.pop(normalized_name, None)
            try:
                self._save_credentials(next_credentials)
            except OSError:
                # The removed credential is now orphaned, but the deleted profile
                # cannot use it. Keep the successful configuration deletion.
                pass

            self._profiles = next_profiles
            self._credentials = next_credentials
            self._revision = next_revision
            return next_revision

    def build_draft_profile(
        self,
        data: Mapping[str, Any],
        *,
        api_key: str | None,
        existing_name: str | None = None,
    ) -> LLMProfile:
        with self._lock:
            base: dict[str, Any] = {}
            stored_key = ""
            if existing_name:
                normalized_name = _profile_name(existing_name)
                current = self._profiles.get(normalized_name)
                if current is not None:
                    base = current.to_dict()
                    stored_key = self._credentials.get(normalized_name, "")
            base.update({key: value for key, value in data.items() if value is not None})
            if not base.get("name"):
                base["name"] = existing_name or "draft"
            profile = StoredLLMProfile.from_dict(base)
            effective_key = api_key.strip() if api_key and api_key.strip() else stored_key
            return profile.to_runtime_profile(effective_key)

    def _check_revision(self, expected_revision: int | None) -> None:
        if expected_revision is None:
            return
        if expected_revision != self._revision:
            raise LLMConfigurationConflictError(
                "LLM provider configuration changed: "
                f"expected revision {expected_revision}, current revision is {self._revision}"
            )

    def _load_profiles(self) -> tuple[int, dict[str, StoredLLMProfile]]:
        if not self._providers_path.exists():
            return 0, {}
        payload = json.loads(self._providers_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("LLM providers file must contain an object")
        revision = payload.get("revision", 0)
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise ValueError("LLM providers revision must be a non-negative integer")
        raw_profiles = payload.get("providers", [])
        if not isinstance(raw_profiles, list):
            raise ValueError("LLM providers must be a list")
        profiles: dict[str, StoredLLMProfile] = {}
        for raw_profile in raw_profiles:
            if not isinstance(raw_profile, dict):
                raise ValueError("Each LLM provider must be an object")
            profile = StoredLLMProfile.from_dict(raw_profile)
            if profile.name in profiles:
                raise ValueError(f"Duplicate LLM provider: {profile.name}")
            profiles[profile.name] = profile
        return revision, profiles

    def _load_credentials(self) -> dict[str, str]:
        if not self._credentials_path.exists():
            return {}
        payload = json.loads(self._credentials_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not all(
            isinstance(name, str) and isinstance(value, str)
            for name, value in payload.items()
        ):
            raise ValueError("LLM credentials file must contain string values")
        return dict(payload)

    def _save_profiles(self, profiles: Mapping[str, StoredLLMProfile]) -> int:
        next_revision = self._revision + 1
        payload = {
            "revision": next_revision,
            "providers": [profile.to_dict() for profile in profiles.values()],
        }
        _atomic_json_write(self._providers_path, payload)
        return next_revision

    def _save_credentials(self, credentials: Mapping[str, str]) -> None:
        _atomic_json_write(self._credentials_path, dict(credentials), private=True)


def _atomic_json_write(path: Path, payload: object, *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        if private:
            try:
                os.chmod(temporary_path, 0o600)
            except OSError:
                pass
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        temporary_path.replace(path)
        if private:
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
    finally:
        temporary_path.unlink(missing_ok=True)


def _profile_name(value: Any) -> str:
    name = _required_text(value, name="name").lower()
    if not _PROFILE_NAME_PATTERN.fullmatch(name):
        raise ValueError(
            "Provider ID must start with a lowercase letter and contain only "
            "lowercase letters, numbers, underscores, or hyphens (max 64 characters)"
        )
    return name


def _required_text(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("label must be a string")
    return value.strip()


def _positive_number(value: Any, *, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if number <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return number


def _non_negative_integer(value: Any, *, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if number < 0:
        raise ValueError(f"{name} must be zero or greater")
    return number


def _string_mapping(value: Any, *, name: str) -> dict[str, str]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str)
        for key, item in value.items()
    ):
        raise ValueError(f"{name} must be an object with string values")
    return dict(value)
