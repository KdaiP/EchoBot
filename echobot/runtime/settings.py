from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from ..tools.shell import normalize_shell_safety_mode


DEFAULT_SHELL_SAFETY_MODE = "danger-full-access"
SETTINGS_FILE_NAME = "settings.json"


class SettingsConflictError(RuntimeError):
    """Raised when a client tries to update an outdated settings snapshot."""


@dataclass(frozen=True, slots=True)
class RuntimeSettingDefinition:
    name: str
    value_hint: str
    description: str


@dataclass(frozen=True, slots=True)
class RuntimeConfigSnapshot:
    delegated_ack_enabled: bool = True
    shell_safety_mode: str = DEFAULT_SHELL_SAFETY_MODE
    file_write_enabled: bool = True
    cron_mutation_enabled: bool = True
    web_private_network_enabled: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "shell_safety_mode",
            normalize_shell_safety_mode(self.shell_safety_mode),
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RuntimeConfigSnapshot:
        return cls(
            delegated_ack_enabled=_required_bool(
                data.get("delegated_ack_enabled"),
                name="delegated_ack_enabled",
            ),
            shell_safety_mode=normalize_shell_safety_mode(
                _required_text(data.get("shell_safety_mode"), name="shell_safety_mode")
            ),
            file_write_enabled=_required_bool(
                data.get("file_write_enabled"),
                name="file_write_enabled",
            ),
            cron_mutation_enabled=_required_bool(
                data.get("cron_mutation_enabled"),
                name="cron_mutation_enabled",
            ),
            web_private_network_enabled=_required_bool(
                data.get("web_private_network_enabled"),
                name="web_private_network_enabled",
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "delegated_ack_enabled": self.delegated_ack_enabled,
            "shell_safety_mode": self.shell_safety_mode,
            "file_write_enabled": self.file_write_enabled,
            "cron_mutation_enabled": self.cron_mutation_enabled,
            "web_private_network_enabled": self.web_private_network_enabled,
        }


@dataclass(frozen=True, slots=True)
class LLMSelection:
    active_provider: str = "default"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LLMSelection:
        active_provider = data.get("active_provider", "")
        if not isinstance(active_provider, str):
            raise ValueError("llm.active_provider must be a string")
        return cls(
            active_provider=active_provider.strip()
        )

    def to_dict(self) -> dict[str, str]:
        return {"active_provider": self.active_provider}


@dataclass(frozen=True, slots=True)
class SpeechSettings:
    asr_provider: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SpeechSettings:
        return cls(
            asr_provider=_required_text(
                data.get("asr_provider"),
                name="speech.asr_provider",
            )
        )

    def to_dict(self) -> dict[str, str]:
        return {"asr_provider": self.asr_provider}


@dataclass(frozen=True, slots=True)
class AppSettings:
    revision: int
    runtime: RuntimeConfigSnapshot
    llm: LLMSelection
    speech: SpeechSettings

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AppSettings:
        revision = data.get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise ValueError("revision must be a non-negative integer")

        runtime = _required_mapping(data.get("runtime"), name="runtime")
        llm = _required_mapping(data.get("llm"), name="llm")
        speech = _required_mapping(data.get("speech"), name="speech")
        return cls(
            revision=revision,
            runtime=RuntimeConfigSnapshot.from_dict(runtime),
            llm=LLMSelection.from_dict(llm),
            speech=SpeechSettings.from_dict(speech),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "revision": self.revision,
            "runtime": self.runtime.to_dict(),
            "llm": self.llm.to_dict(),
            "speech": self.speech.to_dict(),
        }


RUNTIME_SETTING_DEFINITIONS: dict[str, RuntimeSettingDefinition] = {
    "delegated_ack_enabled": RuntimeSettingDefinition(
        name="delegated_ack_enabled",
        value_hint="on|off",
        description="Show the task-start tip before background work",
    ),
    "shell_safety_mode": RuntimeSettingDefinition(
        name="shell_safety_mode",
        value_hint="read-only|workspace-write|danger-full-access",
        description="Control which shell commands the agent may run",
    ),
    "file_write_enabled": RuntimeSettingDefinition(
        name="file_write_enabled",
        value_hint="on|off",
        description="Allow write_text_file and edit_text_file",
    ),
    "cron_mutation_enabled": RuntimeSettingDefinition(
        name="cron_mutation_enabled",
        value_hint="on|off",
        description="Allow the agent to add, remove, run, enable, or disable cron jobs",
    ),
    "web_private_network_enabled": RuntimeSettingDefinition(
        name="web_private_network_enabled",
        value_hint="on|off",
        description="Allow fetch_web_page to access localhost and private network hosts",
    ),
}


class RuntimeSettingsCoordinator(Protocol):
    @property
    def delegated_ack_enabled(self) -> bool:
        raise NotImplementedError

    def set_delegated_ack_enabled(self, enabled: bool) -> None:
        raise NotImplementedError


@dataclass(slots=True)
class RuntimeControls:
    shell_safety_mode: str = DEFAULT_SHELL_SAFETY_MODE
    file_write_enabled: bool = True
    cron_mutation_enabled: bool = True
    web_private_network_enabled: bool = False
    supports_image_input: bool = True

    def __post_init__(self) -> None:
        self.shell_safety_mode = normalize_shell_safety_mode(self.shell_safety_mode)

    def apply(self, settings: RuntimeConfigSnapshot) -> None:
        self.shell_safety_mode = settings.shell_safety_mode
        self.file_write_enabled = settings.file_write_enabled
        self.cron_mutation_enabled = settings.cron_mutation_enabled
        self.web_private_network_enabled = settings.web_private_network_enabled


class SettingsStore:
    """Store one validated settings document using an atomic file replace."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self, defaults: AppSettings) -> AppSettings:
        if not self.path.exists():
            return defaults
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Settings file must contain a JSON object")
        return AppSettings.from_dict(payload)

    def save(self, settings: AppSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(settings.to_dict(), ensure_ascii=False, indent=2) + "\n"
        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            text=True,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            temporary_path.replace(self.path)
        finally:
            temporary_path.unlink(missing_ok=True)


class SettingsService:
    """Resolve, persist, and apply EchoBot's user-changeable settings."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        defaults: AppSettings,
        coordinator: RuntimeSettingsCoordinator | None = None,
        runtime_controls: RuntimeControls | None = None,
    ) -> None:
        self._store = SettingsStore(
            Path(workspace) / ".echobot" / SETTINGS_FILE_NAME
        )
        self._defaults = defaults
        self._coordinator = coordinator
        self._runtime_controls = runtime_controls
        self._lock = threading.Lock()
        self._settings = self._store.load(defaults)

    @property
    def settings(self) -> AppSettings:
        with self._lock:
            return self._settings

    def bind_runtime(
        self,
        coordinator: RuntimeSettingsCoordinator,
        runtime_controls: RuntimeControls,
    ) -> None:
        with self._lock:
            self._coordinator = coordinator
            self._runtime_controls = runtime_controls
            self._apply_runtime(self._settings.runtime)

    def runtime_snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "revision": self._settings.revision,
                **self._settings.runtime.to_dict(),
            }

    def apply_runtime_updates(
        self,
        updates: Mapping[str, Any],
        *,
        expected_revision: int | None = None,
    ) -> dict[str, object]:
        normalized_updates = _normalize_runtime_updates(updates)
        if not normalized_updates:
            raise ValueError("At least one runtime setting must be provided")

        with self._lock:
            self._check_revision(expected_revision)
            values = self._settings.runtime.to_dict()
            values.update(normalized_updates)
            runtime = RuntimeConfigSnapshot.from_dict(values)
            self._commit(replace(self._settings, runtime=runtime))
            self._apply_runtime(runtime)
            return {"revision": self._settings.revision, **runtime.to_dict()}

    def reset_runtime(
        self,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, object]:
        with self._lock:
            self._check_revision(expected_revision)
            runtime = self._defaults.runtime
            self._commit(replace(self._settings, runtime=runtime))
            self._apply_runtime(runtime)
            return {"revision": self._settings.revision, **runtime.to_dict()}

    def select_llm_provider(
        self,
        provider_name: str,
        *,
        expected_revision: int | None = None,
    ) -> AppSettings:
        normalized_name = _required_text(provider_name, name="provider")
        with self._lock:
            self._check_revision(expected_revision)
            candidate = replace(
                self._settings,
                llm=LLMSelection(active_provider=normalized_name),
            )
            self._commit(candidate)
            return self._settings

    def repair_llm_provider(self, provider_name: str) -> AppSettings:
        """Repair a stale selection after providers changed outside settings."""
        normalized_name = str(provider_name or "").strip()
        with self._lock:
            if self._settings.llm.active_provider == normalized_name:
                return self._settings
            candidate = replace(
                self._settings,
                llm=LLMSelection(active_provider=normalized_name),
            )
            self._commit(candidate)
            return self._settings

    def select_asr_provider(
        self,
        provider_name: str,
        *,
        expected_revision: int | None = None,
    ) -> AppSettings:
        normalized_name = _required_text(provider_name, name="provider")
        with self._lock:
            self._check_revision(expected_revision)
            candidate = replace(
                self._settings,
                speech=SpeechSettings(asr_provider=normalized_name),
            )
            self._commit(candidate)
            return self._settings

    def get_runtime_value(self, name: str) -> object:
        normalized_name = _normalize_runtime_setting_name(name)
        return self.runtime_snapshot()[normalized_name]

    def _check_revision(self, expected_revision: int | None) -> None:
        if expected_revision is None:
            return
        if expected_revision != self._settings.revision:
            raise SettingsConflictError(
                f"Settings changed: expected revision {expected_revision}, "
                f"current revision is {self._settings.revision}"
            )

    def _commit(self, candidate: AppSettings) -> None:
        next_settings = replace(candidate, revision=self._settings.revision + 1)
        self._store.save(next_settings)
        self._settings = next_settings

    def _apply_runtime(self, runtime: RuntimeConfigSnapshot) -> None:
        if self._coordinator is None or self._runtime_controls is None:
            return
        self._coordinator.set_delegated_ack_enabled(runtime.delegated_ack_enabled)
        self._runtime_controls.apply(runtime)


def parse_text_runtime_setting_value(name: str, raw_value: str) -> object:
    normalized_name = _normalize_runtime_setting_name(name)
    cleaned = str(raw_value or "").strip().lower()
    if normalized_name == "shell_safety_mode":
        return normalize_shell_safety_mode(cleaned)
    return _parse_on_off_value(cleaned, name=normalized_name)


def format_runtime_setting_value(name: str, value: object) -> str:
    normalized_name = _normalize_runtime_setting_name(name)
    if normalized_name == "shell_safety_mode":
        return str(value or "")
    return "on" if bool(value) else "off"


def _normalize_runtime_updates(updates: Mapping[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for raw_name, value in updates.items():
        if value is None or raw_name == "expected_revision":
            continue
        name = _normalize_runtime_setting_name(raw_name)
        if name == "shell_safety_mode":
            if not isinstance(value, str):
                raise ValueError("shell_safety_mode must be a string")
            normalized[name] = normalize_shell_safety_mode(value)
        else:
            normalized[name] = _required_bool(value, name=name)
    return normalized


def _normalize_runtime_setting_name(name: str) -> str:
    normalized_name = str(name or "").strip().lower()
    if normalized_name not in RUNTIME_SETTING_DEFINITIONS:
        raise KeyError(normalized_name)
    return normalized_name


def _parse_on_off_value(cleaned: str, *, name: str) -> bool:
    if cleaned in {"on", "true", "enable", "enabled"}:
        return True
    if cleaned in {"off", "false", "disable", "disabled"}:
        return False
    raise ValueError(f"Invalid value for {name}. Use on or off.")


def _required_bool(value: Any, *, name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{name} must be a boolean")


def _required_text(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _required_mapping(value: Any, *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value
