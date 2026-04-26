from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Protocol

from ..tools.shell import normalize_shell_safety_mode


DEFAULT_SHELL_SAFETY_MODE = "danger-full-access"


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

    def to_dict(self) -> dict[str, object]:
        return {
            "delegated_ack_enabled": self.delegated_ack_enabled,
            "shell_safety_mode": self.shell_safety_mode,
            "file_write_enabled": self.file_write_enabled,
            "cron_mutation_enabled": self.cron_mutation_enabled,
            "web_private_network_enabled": self.web_private_network_enabled,
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

    def __post_init__(self) -> None:
        self.shell_safety_mode = normalize_shell_safety_mode(self.shell_safety_mode)

    def set_shell_safety_mode(self, value: str) -> None:
        self.shell_safety_mode = normalize_shell_safety_mode(value)

    def set_file_write_enabled(self, value: bool) -> None:
        self.file_write_enabled = bool(value)

    def set_cron_mutation_enabled(self, value: bool) -> None:
        self.cron_mutation_enabled = bool(value)

    def set_web_private_network_enabled(self, value: bool) -> None:
        self.web_private_network_enabled = bool(value)


@dataclass(slots=True)
class RuntimeSettings:
    delegated_ack_enabled: bool | None = None
    selected_asr_provider: str | None = None
    selected_live2d_model: str | None = None
    shell_safety_mode: str | None = None
    file_write_enabled: bool | None = None
    cron_mutation_enabled: bool | None = None
    web_private_network_enabled: bool | None = None
    extra_values: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuntimeSettings":
        extra_values = dict(data)
        raw_value = extra_values.pop("delegated_ack_enabled", None)
        if raw_value is None:
            delegated_ack_enabled = None
        elif isinstance(raw_value, bool):
            delegated_ack_enabled = raw_value
        else:
            raise ValueError("delegated_ack_enabled must be a boolean")

        raw_asr_provider = extra_values.pop("selected_asr_provider", None)
        if raw_asr_provider is None:
            selected_asr_provider = None
        elif isinstance(raw_asr_provider, str):
            selected_asr_provider = raw_asr_provider.strip() or None
        else:
            raise ValueError("selected_asr_provider must be a string")

        raw_live2d_model = extra_values.pop("selected_live2d_model", None)
        if raw_live2d_model is None:
            selected_live2d_model = None
        elif isinstance(raw_live2d_model, str):
            selected_live2d_model = raw_live2d_model.strip() or None
        else:
            raise ValueError("selected_live2d_model must be a string")

        raw_shell_safety_mode = extra_values.pop("shell_safety_mode", None)
        if raw_shell_safety_mode is None:
            shell_safety_mode = None
        elif isinstance(raw_shell_safety_mode, str):
            shell_safety_mode = normalize_shell_safety_mode(raw_shell_safety_mode)
        else:
            raise ValueError("shell_safety_mode must be a string")

        file_write_enabled = _optional_bool(
            extra_values.pop("file_write_enabled", None),
            name="file_write_enabled",
        )
        cron_mutation_enabled = _optional_bool(
            extra_values.pop("cron_mutation_enabled", None),
            name="cron_mutation_enabled",
        )
        web_private_network_enabled = _optional_bool(
            extra_values.pop("web_private_network_enabled", None),
            name="web_private_network_enabled",
        )

        return cls(
            delegated_ack_enabled=delegated_ack_enabled,
            selected_asr_provider=selected_asr_provider,
            selected_live2d_model=selected_live2d_model,
            shell_safety_mode=shell_safety_mode,
            file_write_enabled=file_write_enabled,
            cron_mutation_enabled=cron_mutation_enabled,
            web_private_network_enabled=web_private_network_enabled,
            extra_values=extra_values,
        )

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.extra_values)
        if self.delegated_ack_enabled is not None:
            data["delegated_ack_enabled"] = self.delegated_ack_enabled
        if self.selected_asr_provider is not None:
            data["selected_asr_provider"] = self.selected_asr_provider
        if self.selected_live2d_model is not None:
            data["selected_live2d_model"] = self.selected_live2d_model
        if self.shell_safety_mode is not None:
            data["shell_safety_mode"] = self.shell_safety_mode
        if self.file_write_enabled is not None:
            data["file_write_enabled"] = self.file_write_enabled
        if self.cron_mutation_enabled is not None:
            data["cron_mutation_enabled"] = self.cron_mutation_enabled
        if self.web_private_network_enabled is not None:
            data["web_private_network_enabled"] = self.web_private_network_enabled
        return data

    def get_named_value(self, name: str) -> Any:
        if name == "delegated_ack_enabled":
            return self.delegated_ack_enabled
        if name == "selected_asr_provider":
            return self.selected_asr_provider
        if name == "selected_live2d_model":
            return self.selected_live2d_model
        if name == "shell_safety_mode":
            return self.shell_safety_mode
        if name == "file_write_enabled":
            return self.file_write_enabled
        if name == "cron_mutation_enabled":
            return self.cron_mutation_enabled
        if name == "web_private_network_enabled":
            return self.web_private_network_enabled
        raise KeyError(name)

    def set_named_value(self, name: str, value: Any) -> None:
        if name == "delegated_ack_enabled":
            if value is not None and not isinstance(value, bool):
                raise ValueError("delegated_ack_enabled must be a boolean")
            self.delegated_ack_enabled = value
            return
        if name == "selected_asr_provider":
            if value is None:
                self.selected_asr_provider = None
                return
            if not isinstance(value, str):
                raise ValueError("selected_asr_provider must be a string")
            normalized_value = value.strip()
            self.selected_asr_provider = normalized_value or None
            return
        if name == "selected_live2d_model":
            if value is None:
                self.selected_live2d_model = None
                return
            if not isinstance(value, str):
                raise ValueError("selected_live2d_model must be a string")
            normalized_value = value.strip()
            self.selected_live2d_model = normalized_value or None
            return
        if name == "shell_safety_mode":
            if value is None:
                self.shell_safety_mode = None
                return
            if not isinstance(value, str):
                raise ValueError("shell_safety_mode must be a string")
            self.shell_safety_mode = normalize_shell_safety_mode(value)
            return
        if name == "file_write_enabled":
            self.file_write_enabled = _required_bool(value, name="file_write_enabled")
            return
        if name == "cron_mutation_enabled":
            self.cron_mutation_enabled = _required_bool(
                value,
                name="cron_mutation_enabled",
            )
            return
        if name == "web_private_network_enabled":
            self.web_private_network_enabled = _required_bool(
                value,
                name="web_private_network_enabled",
            )
            return
        raise KeyError(name)

    def clear_named_value(self, name: str) -> None:
        if name == "delegated_ack_enabled":
            self.delegated_ack_enabled = None
            return
        if name == "selected_live2d_model":
            self.selected_live2d_model = None
            return
        if name == "shell_safety_mode":
            self.shell_safety_mode = None
            return
        if name == "file_write_enabled":
            self.file_write_enabled = None
            return
        if name == "cron_mutation_enabled":
            self.cron_mutation_enabled = None
            return
        if name == "web_private_network_enabled":
            self.web_private_network_enabled = None
            return
        raise KeyError(name)


class RuntimeSettingsStore:
    _locks_guard: ClassVar[threading.Lock] = threading.Lock()
    _locks_by_path: ClassVar[dict[Path, threading.Lock]] = {}

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = self._lock_for_path(self.path.resolve())

    def load(self) -> RuntimeSettings:
        with self._lock:
            return self._load_unlocked()

    def save(self, settings: RuntimeSettings) -> RuntimeSettings:
        with self._lock:
            return self._save_unlocked(settings)

    def update(
        self,
        updater: Callable[[RuntimeSettings], None],
    ) -> RuntimeSettings:
        with self._lock:
            settings = self._load_unlocked()
            updater(settings)
            return self._save_unlocked(settings)

    def update_named_value(self, name: str, value: Any) -> RuntimeSettings:
        return self.update(lambda settings: settings.set_named_value(name, value))

    @classmethod
    def _lock_for_path(cls, path: Path) -> threading.Lock:
        with cls._locks_guard:
            lock = cls._locks_by_path.get(path)
            if lock is None:
                lock = threading.Lock()
                cls._locks_by_path[path] = lock
            return lock

    def _load_unlocked(self) -> RuntimeSettings:
        if not self.path.exists():
            return RuntimeSettings()

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Runtime settings file must contain a JSON object")
        return RuntimeSettings.from_dict(payload)

    def _save_unlocked(self, settings: RuntimeSettings) -> RuntimeSettings:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(settings.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return settings


class RuntimeSettingsManager:
    def __init__(
        self,
        workspace: str | Path,
        *,
        coordinator: RuntimeSettingsCoordinator,
        runtime_controls: RuntimeControls,
    ) -> None:
        self._coordinator = coordinator
        self._runtime_controls = runtime_controls
        self._store = RuntimeSettingsStore(
            Path(workspace) / ".echobot" / "runtime_settings.json",
        )

    @property
    def definitions(self) -> dict[str, RuntimeSettingDefinition]:
        return RUNTIME_SETTING_DEFINITIONS

    def snapshot(self) -> dict[str, object]:
        return runtime_settings_snapshot(
            self._coordinator,
            self._runtime_controls,
        )

    def get(self, name: str) -> object:
        normalized_name = _normalize_runtime_setting_name(name)
        return self.snapshot()[normalized_name]

    def apply_named_value(self, name: str, value: Any) -> dict[str, object]:
        normalized_name = _normalize_runtime_setting_name(name)
        return self.apply_updates({normalized_name: value})

    def apply_updates(self, updates: Mapping[str, Any]) -> dict[str, object]:
        normalized_updates = _normalize_runtime_updates(updates)
        if not normalized_updates:
            raise ValueError("At least one runtime setting must be provided")

        self._store.update(
            lambda settings: _apply_runtime_updates_to_settings(
                settings,
                normalized_updates,
            )
        )

        for name, value in normalized_updates.items():
            _apply_runtime_setting(
                self._coordinator,
                self._runtime_controls,
                name,
                value,
            )

        return self.snapshot()

    def reset_overrides(
        self,
        defaults: Mapping[str, Any],
    ) -> dict[str, object]:
        normalized_defaults = _normalize_runtime_defaults(defaults)
        self._store.update(_clear_runtime_override_values)

        for name, value in normalized_defaults.items():
            _apply_runtime_setting(
                self._coordinator,
                self._runtime_controls,
                name,
                value,
            )

        return self.snapshot()


def runtime_settings_snapshot(
    coordinator: RuntimeSettingsCoordinator,
    runtime_controls: RuntimeControls,
) -> dict[str, object]:
    return {
        "delegated_ack_enabled": coordinator.delegated_ack_enabled,
        "shell_safety_mode": runtime_controls.shell_safety_mode,
        "file_write_enabled": runtime_controls.file_write_enabled,
        "cron_mutation_enabled": runtime_controls.cron_mutation_enabled,
        "web_private_network_enabled": runtime_controls.web_private_network_enabled,
    }


def parse_text_runtime_setting_value(name: str, raw_value: str) -> object:
    normalized_name = _normalize_runtime_setting_name(name)
    cleaned = str(raw_value or "").strip().lower()
    if normalized_name == "delegated_ack_enabled":
        return _parse_on_off_value(
            cleaned,
            name="delegated_ack_enabled",
        )
    if normalized_name == "shell_safety_mode":
        return normalize_shell_safety_mode(cleaned)
    if normalized_name in {
        "file_write_enabled",
        "cron_mutation_enabled",
        "web_private_network_enabled",
    }:
        return _parse_on_off_value(cleaned, name=normalized_name)
    raise KeyError(normalized_name)


def format_runtime_setting_value(name: str, value: object) -> str:
    normalized_name = _normalize_runtime_setting_name(name)
    if normalized_name == "shell_safety_mode":
        return str(value or "")
    if normalized_name in RUNTIME_SETTING_DEFINITIONS:
        return "on" if bool(value) else "off"
    raise KeyError(normalized_name)


def _normalize_runtime_updates(
    updates: Mapping[str, Any],
) -> dict[str, Any]:
    normalized_updates: dict[str, Any] = {}
    for raw_name, value in updates.items():
        if value is None:
            continue
        name = _normalize_runtime_setting_name(raw_name)
        normalized_updates[name] = value
    return normalized_updates


def _normalize_runtime_defaults(
    defaults: Mapping[str, Any],
) -> dict[str, Any]:
    normalized_defaults: dict[str, Any] = {}
    for name in RUNTIME_SETTING_DEFINITIONS:
        if name not in defaults:
            raise KeyError(name)
        value = defaults[name]
        if name == "shell_safety_mode":
            normalized_defaults[name] = normalize_shell_safety_mode(str(value))
            continue
        if not isinstance(value, bool):
            raise ValueError(f"{name} must be a boolean")
        normalized_defaults[name] = value
    return normalized_defaults


def _normalize_runtime_setting_name(name: str) -> str:
    normalized_name = str(name or "").strip().lower()
    if normalized_name not in RUNTIME_SETTING_DEFINITIONS:
        raise KeyError(normalized_name)
    return normalized_name


def _apply_runtime_setting(
    coordinator: RuntimeSettingsCoordinator,
    runtime_controls: RuntimeControls,
    name: str,
    value: Any,
) -> None:
    if name == "delegated_ack_enabled":
        coordinator.set_delegated_ack_enabled(bool(value))
        return
    if name == "shell_safety_mode":
        runtime_controls.set_shell_safety_mode(str(value))
        return
    if name == "file_write_enabled":
        runtime_controls.set_file_write_enabled(bool(value))
        return
    if name == "cron_mutation_enabled":
        runtime_controls.set_cron_mutation_enabled(bool(value))
        return
    if name == "web_private_network_enabled":
        runtime_controls.set_web_private_network_enabled(bool(value))
        return
    raise KeyError(name)


def _apply_runtime_updates_to_settings(
    settings: RuntimeSettings,
    updates: Mapping[str, Any],
) -> None:
    for name, value in updates.items():
        settings.set_named_value(name, value)


def _clear_runtime_override_values(settings: RuntimeSettings) -> None:
    for name in RUNTIME_SETTING_DEFINITIONS:
        settings.clear_named_value(name)


def _parse_on_off_value(cleaned: str, *, name: str) -> bool:
    if cleaned in {"on", "true", "enable", "enabled"}:
        return True
    if cleaned in {"off", "false", "disable", "disabled"}:
        return False
    raise ValueError(f"Invalid value for {name}. Use on or off.")


def _optional_bool(value: Any, *, name: str) -> bool | None:
    if value is None:
        return None
    return _required_bool(value, name=name)


def _required_bool(value: Any, *, name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{name} must be a boolean")
