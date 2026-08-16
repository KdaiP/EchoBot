from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..attachments import AttachmentStore, DEFAULT_FILE_BUDGET, FileBudget
from ..agent import AgentCore
from ..config import configure_runtime_logging, load_env_file
from ..images import DEFAULT_IMAGE_BUDGET, ImageBudget
from ..memory import ReMeLightSettings, ReMeLightSupport
from ..orchestration import (
    ConversationCoordinator,
    RunStore,
    DecisionEngine,
    RoleCardRegistry,
    RoleplayEngine,
)
from ..providers.openai_compatible import (
    OpenAICompatibleProvider,
    OpenAICompatibleSettings,
)
from ..providers import (
    LLMProvider,
    LLMProviderConfigurationService,
    LLMProviderManager,
    load_optional_llm_profiles,
)
from ..runtime.legacy_session_migration import migrate_legacy_session_data
from ..runtime.session_runner import SessionAgentRunner
from ..runtime.settings import (
    AppSettings,
    DEFAULT_SHELL_SAFETY_MODE,
    LLMSelection,
    RuntimeConfigSnapshot,
    RuntimeControls,
    SettingsService,
    SpeechSettings,
)
from ..runtime.sessions import Session, SessionStore
from ..runtime.system_prompt import build_default_system_prompt
from ..scheduling.cron import CronService
from ..scheduling.heartbeat import HeartbeatService
from ..skill_support import SkillRegistry
from ..tools import ToolRegistry, create_basic_tool_registry


ToolRegistryFactory = Callable[[str, bool], ToolRegistry | None]


@dataclass(slots=True)
class RuntimeOptions:
    env_file: str = ".env"
    workspace: Path | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    delegated_ack_enabled: bool | None = None
    no_tools: bool = False
    no_skills: bool = False
    no_memory: bool = False
    no_heartbeat: bool = False
    heartbeat_interval: int | None = None
    session: str | None = None
    new_session: str | None = None


@dataclass(slots=True)
class RuntimeContext:
    workspace: Path
    attachment_store: AttachmentStore
    supports_image_input: bool
    agent: AgentCore
    session_store: SessionStore
    session: Session | None
    tool_registry: ToolRegistry | None
    skill_registry: SkillRegistry | None
    cron_service: CronService
    heartbeat_service: HeartbeatService | None
    session_runner: SessionAgentRunner
    coordinator: ConversationCoordinator
    role_registry: RoleCardRegistry
    memory_support: ReMeLightSupport | None
    heartbeat_file_path: Path
    heartbeat_interval_seconds: int
    tool_registry_factory: ToolRegistryFactory
    runtime_controls: RuntimeControls
    settings_service: SettingsService
    provider_manager: LLMProviderManager
    llm_configuration: LLMProviderConfigurationService | None = None


def build_runtime_context(
    options: RuntimeOptions,
    *,
    load_session_state: bool,
) -> RuntimeContext:
    workspace = (options.workspace or Path(".")).resolve()
    # Temporary one-shot upgrade hook. The current stores do not read old schemas.
    migrate_legacy_session_data(workspace)
    env_file_path = _resolve_runtime_path(workspace, options.env_file)
    load_env_file(str(env_file_path))
    default_runtime_config = _default_runtime_config(options)
    llm_configuration = LLMProviderConfigurationService(workspace)
    user_profiles = llm_configuration.runtime_profiles()
    environment_profiles = load_optional_llm_profiles()
    llm_profiles = {**user_profiles, **environment_profiles}
    requested_default_provider = os.environ.get(
        "ECHOBOT_LLM_PROVIDER",
        "",
    ).strip()
    default_provider_name = (
        requested_default_provider
        if requested_default_provider in llm_profiles
        else next(iter(llm_profiles), "")
    )
    default_settings = AppSettings(
        revision=0,
        runtime=default_runtime_config,
        llm=LLMSelection(active_provider=default_provider_name),
        speech=SpeechSettings(
            asr_provider=os.environ.get(
                "ECHOBOT_ASR_PROVIDER",
                "sherpa-sense-voice",
            ).strip()
            or "sherpa-sense-voice",
        ),
    )
    settings_service = SettingsService(workspace, defaults=default_settings)
    app_settings = settings_service.settings
    if app_settings.llm.active_provider not in llm_profiles:
        app_settings = settings_service.repair_llm_provider(
            default_provider_name
        )
    active_profile = llm_profiles.get(app_settings.llm.active_provider)
    runtime_controls = RuntimeControls(
        shell_safety_mode=app_settings.runtime.shell_safety_mode,
        file_write_enabled=app_settings.runtime.file_write_enabled,
        cron_mutation_enabled=app_settings.runtime.cron_mutation_enabled,
        web_private_network_enabled=app_settings.runtime.web_private_network_enabled,
        supports_image_input=(
            active_profile.supports_image_input
            if active_profile is not None
            else False
        ),
    )
    configure_runtime_logging()
    lightweight_max_tokens = _env_int("ECHOBOT_LIGHTWEIGHT_MAX_TOKENS", 4096)
    agent_max_steps = _env_int("ECHOBOT_AGENT_MAX_STEPS", 50)
    supports_image_input = (
        active_profile.supports_image_input
        if active_profile is not None
        else False
    )
    attachment_store = AttachmentStore(
        workspace / ".echobot" / "attachments",
        image_budget=_image_budget_from_env(),
        file_budget=_file_budget_from_env(),
    )
    provider_manager = LLMProviderManager(
        llm_profiles,
        active_provider=app_settings.llm.active_provider,
        attachment_store=attachment_store,
    )
    decider_provider = _build_provider_from_env(
        prefix="DECIDER_LLM_",
        fallback_provider=provider_manager,
        attachment_store=attachment_store,
    )
    role_provider = _build_provider_from_env(
        prefix="ROLE_LLM_",
        fallback_provider=provider_manager,
        attachment_store=attachment_store,
    )

    memory_support = None
    if (
        active_profile is not None
        and not options.no_memory
        and ReMeLightSupport.is_available()
    ):
        memory_settings = ReMeLightSettings.from_provider_settings(
            workspace,
            active_profile.settings,
        )
        memory_support = ReMeLightSupport(memory_settings)

    cron_store_path = workspace / ".echobot" / "cron" / "jobs.json"
    heartbeat_file_path = _heartbeat_file_path(workspace)
    heartbeat_interval_seconds = _heartbeat_interval_seconds(options)
    agent = AgentCore(
        provider_manager,
        system_prompt=_build_system_prompt_provider(
            workspace=workspace,
            memory_support=memory_support,
            cron_store_path=cron_store_path,
            heartbeat_file_path=heartbeat_file_path,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            runtime_controls=runtime_controls,
        ),
        memory_support=memory_support,
    )
    session_store = SessionStore(workspace / ".echobot" / "sessions")
    session = _load_session(session_store, options) if load_session_state else None
    cron_service = CronService(cron_store_path)
    tool_registry_factory = _build_tool_registry_factory(
        options,
        workspace=workspace,
        attachment_store=attachment_store,
        memory_support=memory_support,
        cron_service=cron_service,
        runtime_controls=runtime_controls,
    )
    tool_registry = None
    if session is not None:
        tool_registry = tool_registry_factory(session.id, False)
    skill_registry = None if options.no_skills else SkillRegistry.discover()
    run_store = RunStore(workspace / ".echobot" / "runs")
    session_runner = SessionAgentRunner(
        agent,
        session_store,
        skill_registry=skill_registry,
        tool_registry_factory=tool_registry_factory,
        default_temperature=options.temperature,
        default_max_tokens=options.max_tokens,
        default_max_steps=agent_max_steps,
        run_store=run_store,
        provider_scope=provider_manager.pin_active_provider,
    )
    role_registry = RoleCardRegistry.discover(project_root=workspace)
    decision_engine = DecisionEngine(
        AgentCore(decider_provider),
        max_tokens=lightweight_max_tokens,
    )
    roleplay_engine = RoleplayEngine(
        AgentCore(role_provider),
        role_registry,
        default_temperature=options.temperature,
        default_max_tokens=options.max_tokens,
        lightweight_max_tokens=lightweight_max_tokens,
    )
    coordinator = ConversationCoordinator(
        session_store=session_store,
        agent_runner=session_runner,
        decision_engine=decision_engine,
        roleplay_engine=roleplay_engine,
        role_registry=role_registry,
        delegated_ack_enabled=(
            app_settings.runtime.delegated_ack_enabled
        ),
        run_store=run_store,
        provider_scope=provider_manager.pin_active_provider,
    )
    heartbeat_service = None
    if not options.no_heartbeat and _heartbeat_enabled():
        heartbeat_service = HeartbeatService(
            heartbeat_file=heartbeat_file_path,
            provider=provider_manager,
            interval_seconds=heartbeat_interval_seconds,
            enabled=True,
        )

    settings_service.bind_runtime(coordinator, runtime_controls)

    return RuntimeContext(
        workspace=workspace,
        attachment_store=attachment_store,
        supports_image_input=supports_image_input,
        agent=agent,
        session_store=session_store,
        session=session,
        tool_registry=tool_registry,
        skill_registry=skill_registry,
        cron_service=cron_service,
        heartbeat_service=heartbeat_service,
        session_runner=session_runner,
        coordinator=coordinator,
        role_registry=role_registry,
        memory_support=memory_support,
        heartbeat_file_path=heartbeat_file_path,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
        tool_registry_factory=tool_registry_factory,
        runtime_controls=runtime_controls,
        settings_service=settings_service,
        provider_manager=provider_manager,
        llm_configuration=llm_configuration,
    )


def _build_tool_registry_factory(
    options: RuntimeOptions,
    *,
    workspace: Path,
    attachment_store: AttachmentStore,
    memory_support: ReMeLightSupport | None,
    cron_service: CronService,
    runtime_controls: RuntimeControls,
) -> ToolRegistryFactory:
    def factory(session_id: str, scheduled_context: bool) -> ToolRegistry | None:
        if options.no_tools:
            return None
        return create_basic_tool_registry(
            workspace,
            attachment_store=attachment_store,
            supports_image_input=runtime_controls.supports_image_input,
            memory_support=memory_support,
            cron_service=cron_service,
            session_id=session_id,
            allow_file_writes=runtime_controls.file_write_enabled,
            allow_cron_mutations=(
                runtime_controls.cron_mutation_enabled and not scheduled_context
            ),
            allow_private_network=runtime_controls.web_private_network_enabled,
            shell_safety_mode=runtime_controls.shell_safety_mode,
        )

    return factory


def _build_system_prompt_provider(
    *,
    workspace: Path,
    memory_support: ReMeLightSupport | None,
    cron_store_path: Path,
    heartbeat_file_path: Path,
    heartbeat_interval_seconds: int,
    runtime_controls: RuntimeControls,
):
    def provider() -> str:
        return build_default_system_prompt(
            workspace,
            supports_image_input=runtime_controls.supports_image_input,
            enable_project_memory=memory_support is not None,
            memory_workspace=(
                memory_support.working_dir
                if memory_support is not None
                else None
            ),
            enable_scheduling=True,
            cron_store_path=cron_store_path,
            heartbeat_file_path=heartbeat_file_path,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            shell_safety_mode=runtime_controls.shell_safety_mode,
            file_write_enabled=runtime_controls.file_write_enabled,
            cron_mutation_enabled=runtime_controls.cron_mutation_enabled,
            web_private_network_enabled=runtime_controls.web_private_network_enabled,
        )

    return provider


def _load_session(
    session_store: SessionStore,
    options: RuntimeOptions,
) -> Session:
    if options.new_session:
        return session_store.create_session(options.new_session)

    if options.session:
        session = session_store.load_session(options.session)
        session_store.set_current_session(session.id)
        return session

    return session_store.load_current_session()


def _heartbeat_file_path(workspace: Path) -> Path:
    file_name = os.environ.get(
        "ECHOBOT_HEARTBEAT_FILE",
        ".echobot/HEARTBEAT.md",
    )
    return workspace / file_name


def _heartbeat_interval_seconds(options: RuntimeOptions) -> int:
    if options.heartbeat_interval is not None:
        return max(int(options.heartbeat_interval), 1)
    raw_value = os.environ.get("ECHOBOT_HEARTBEAT_INTERVAL_SECONDS", "1800")
    try:
        value = int(raw_value)
    except ValueError:
        value = 1800
    return max(value, 1)


def _heartbeat_enabled() -> bool:
    raw_value = os.environ.get("ECHOBOT_HEARTBEAT_ENABLED", "true").strip().lower()
    return raw_value not in {"0", "false", "no", "off"}


def _delegated_ack_enabled(options: RuntimeOptions) -> bool:
    if options.delegated_ack_enabled is not None:
        return bool(options.delegated_ack_enabled)
    return _env_bool("ECHOBOT_DELEGATED_ACK_ENABLED", True)


def _shell_safety_mode() -> str:
    raw_value = os.environ.get("ECHOBOT_SHELL_SAFETY_MODE", "").strip().lower()
    if raw_value:
        return raw_value
    return DEFAULT_SHELL_SAFETY_MODE


def _file_write_enabled() -> bool:
    return _env_bool("ECHOBOT_FILE_WRITE_ENABLED", True)


def _cron_mutation_enabled() -> bool:
    return _env_bool("ECHOBOT_CRON_MUTATION_ENABLED", True)


def _web_private_network_enabled() -> bool:
    return _env_bool("ECHOBOT_WEB_PRIVATE_NETWORK_ENABLED", False)


def _default_runtime_config(options: RuntimeOptions) -> RuntimeConfigSnapshot:
    return RuntimeConfigSnapshot(
        delegated_ack_enabled=_delegated_ack_enabled(options),
        shell_safety_mode=_shell_safety_mode(),
        file_write_enabled=_file_write_enabled(),
        cron_mutation_enabled=_cron_mutation_enabled(),
        web_private_network_enabled=_web_private_network_enabled(),
    )


def _env_int(name: str, default: int) -> int:
    raw_value = os.environ.get(name, "").strip()
    if not raw_value:
        return default
    try:
        return max(int(raw_value), 1)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default

    cleaned = raw_value.strip().lower()
    if not cleaned:
        return default
    return cleaned not in {"0", "false", "no", "off"}


def _image_budget_from_env() -> ImageBudget:
    defaults = DEFAULT_IMAGE_BUDGET
    return ImageBudget(
        max_input_bytes=_env_int(
            "ECHOBOT_IMAGE_MAX_INPUT_BYTES",
            defaults.max_input_bytes,
        ),
        max_output_bytes=_env_int(
            "ECHOBOT_IMAGE_MAX_OUTPUT_BYTES",
            defaults.max_output_bytes,
        ),
        max_side=_env_int(
            "ECHOBOT_IMAGE_MAX_SIDE",
            defaults.max_side,
        ),
        max_pixels=_env_int(
            "ECHOBOT_IMAGE_MAX_PIXELS",
            defaults.max_pixels,
        ),
        start_quality=defaults.start_quality,
        min_quality=defaults.min_quality,
        quality_step=defaults.quality_step,
        resize_step=defaults.resize_step,
        max_resize_attempts=defaults.max_resize_attempts,
    )


def _file_budget_from_env() -> FileBudget:
    defaults = DEFAULT_FILE_BUDGET
    return FileBudget(
        max_input_bytes=_env_int(
            "ECHOBOT_FILE_MAX_INPUT_BYTES",
            defaults.max_input_bytes,
        ),
    )


def _resolve_runtime_path(workspace: Path, path: str | Path) -> Path:
    resolved_path = Path(path).expanduser()
    if resolved_path.is_absolute():
        return resolved_path
    return workspace / resolved_path


def _build_provider_from_env(
    *,
    prefix: str,
    fallback_provider: LLMProvider,
    attachment_store: AttachmentStore,
) -> LLMProvider:
    if _has_provider_env(prefix):
        return OpenAICompatibleProvider(
            OpenAICompatibleSettings.from_env(prefix=prefix),
            attachment_store=attachment_store,
        )
    return fallback_provider


def _has_provider_env(prefix: str) -> bool:
    api_key_name = f"{prefix}API_KEY"
    model_name = f"{prefix}MODEL"
    return bool(os.environ.get(api_key_name, "").strip()) and bool(
        os.environ.get(model_name, "").strip()
    )
