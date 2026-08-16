from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from ..asr import ASRService, build_default_asr_service
from ..channels import ChannelManager, ChannelsConfig, MessageBus, load_channels_config
from ..gateway import (
    DeliveryStore,
    GatewayRuntime,
    GatewaySessionService,
    RouteBindingStore,
)
from ..models import LLMMessage
from ..providers import OpenAICompatibleProvider
from ..runtime.bootstrap import RuntimeContext, RuntimeOptions, build_runtime_context
from ..runtime.session_service import SessionLifecycleService
from ..tts import TTSService, build_default_tts_service
from .services.chat import ChatService
from .services.channels import ChannelService
from .services.roles import RoleService
from .services.web_console import WebConsoleService


RuntimeContextBuilder = Callable[[RuntimeOptions], RuntimeContext]
TTSServiceBuilder = Callable[[Path], TTSService]
ASRServiceBuilder = Callable[[Path], ASRService]
logger = logging.getLogger(__name__)


class AppRuntime:
    def __init__(
        self,
        *,
        runtime_options: RuntimeOptions,
        channel_config_path: str | Path,
        context_builder: RuntimeContextBuilder | None = None,
        tts_service_builder: TTSServiceBuilder | None = None,
        asr_service_builder: ASRServiceBuilder | None = None,
    ) -> None:
        self.runtime_options = runtime_options
        self.channel_config_path = _resolve_runtime_path(
            runtime_options.workspace,
            channel_config_path,
        )
        self._context_builder = context_builder or _default_context_builder
        self._tts_service_builder = tts_service_builder or _default_tts_service_builder
        self._asr_service_builder = asr_service_builder or _default_asr_service_builder

        self.context: RuntimeContext | None = None
        self.bus: MessageBus | None = None
        self.channels_config: ChannelsConfig | None = None
        self.channel_manager: ChannelManager | None = None
        self.delivery_store: DeliveryStore | None = None
        self.route_binding_store: RouteBindingStore | None = None
        self.gateway: GatewayRuntime | None = None
        self.gateway_task: asyncio.Task[None] | None = None
        self.session_service: GatewaySessionService | None = None
        self.chat_service: ChatService | None = None
        self.role_service: RoleService | None = None
        self.channel_service: ChannelService | None = None
        self.web_console_service: WebConsoleService | None = None
        self.tts_service: TTSService | None = None
        self.asr_service: ASRService | None = None
        self._settings_change_lock = asyncio.Lock()
        self._started = False

    @property
    def workspace(self) -> Path:
        if self.context is None:
            raise RuntimeError("App runtime has not been started")
        return self.context.workspace

    async def start(self) -> None:
        if self._started:
            return

        self.context = self._context_builder(self.runtime_options)
        self.bus = MessageBus()
        self.channels_config = load_channels_config(self.channel_config_path)
        self.channel_manager = ChannelManager(
            self.channels_config,
            self.bus,
            attachment_store=self.context.attachment_store,
        )
        self.delivery_store = DeliveryStore(
            self.context.workspace / ".echobot" / "delivery.json",
        )
        self.route_binding_store = RouteBindingStore(
            self.context.workspace / ".echobot" / "route_bindings.jsonl",
        )
        core_session_service = SessionLifecycleService(
            self.context.session_store,
            coordinator=self.context.coordinator,
        )
        self.session_service = GatewaySessionService(
            core_session_service,
            route_binding_store=self.route_binding_store,
            delivery_store=self.delivery_store,
        )
        self.gateway = GatewayRuntime(
            self.context,
            self.bus,
            session_service=self.session_service,
        )
        self.chat_service = ChatService(
            self.context.coordinator,
            self.session_service,
        )
        self.role_service = RoleService(
            self.context.role_registry,
            self.context.session_store,
        )
        self.channel_service = ChannelService(
            config_path=self.channel_config_path,
            get_status=self.channel_status,
            reload_channels=self.reload_channels,
        )
        self.asr_service = self._asr_service_builder(self.context.workspace)
        self.tts_service = self._tts_service_builder(self.context.workspace)
        self.web_console_service = WebConsoleService(
            self.context.workspace,
            self.tts_service,
            self.asr_service,
        )
        selected_asr_provider = (
            self.context.settings_service.settings.speech.asr_provider
        )
        asr_snapshot = await self.asr_service.status_snapshot()
        available_asr_providers = {
            provider.name for provider in asr_snapshot.asr_providers
        }
        if selected_asr_provider in available_asr_providers:
            await self.asr_service.set_selected_asr_provider(selected_asr_provider)
        else:
            logger.warning(
                "Configured ASR provider %s is unavailable; keeping %s",
                selected_asr_provider,
                asr_snapshot.selected_asr_provider,
            )
            await self.asr_service.on_startup()

        await self.channel_manager.start_all()
        self.gateway_task = asyncio.create_task(
            self.gateway.run(),
            name="echobot_gateway_runtime",
        )
        self._started = True

    async def select_llm_provider(
        self,
        provider_name: str,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, object]:
        if self.context is None:
            raise RuntimeError("App runtime has not been started")

        async with self._settings_change_lock:
            if not self.context.provider_manager.has_profile(provider_name):
                raise ValueError(f"Unknown LLM provider profile: {provider_name}")
            settings = await asyncio.to_thread(
                self.context.settings_service.select_llm_provider,
                provider_name,
                expected_revision=expected_revision,
            )
            profile = self.context.provider_manager.select(provider_name)
            self.context.supports_image_input = profile.supports_image_input
            self.context.runtime_controls.supports_image_input = (
                profile.supports_image_input
            )
            return self._llm_snapshot(settings_revision=settings.revision)

    async def create_llm_provider(
        self,
        data: dict[str, object],
        *,
        api_key: str | None,
        expected_config_revision: int | None = None,
    ) -> dict[str, object]:
        context = self._require_llm_configuration()
        provider_name = str(data.get("name", "") or "").strip().lower()

        async with self._settings_change_lock:
            if context.provider_manager.has_profile(provider_name):
                raise ValueError(f"LLM provider already exists: {provider_name}")
            profile, _revision = await asyncio.to_thread(
                context.llm_configuration.create,
                data,
                api_key=api_key,
                expected_revision=expected_config_revision,
            )
            await context.provider_manager.upsert_profile(profile)

            settings = context.settings_service.settings
            if not context.provider_manager.active_provider_name:
                settings = await asyncio.to_thread(
                    context.settings_service.repair_llm_provider,
                    profile.name,
                )
                context.provider_manager.select(profile.name)
                self._apply_active_llm_profile(profile)
            return self._llm_snapshot(settings_revision=settings.revision)

    async def update_llm_provider(
        self,
        provider_name: str,
        updates: dict[str, object],
        *,
        api_key: str | None,
        clear_api_key: bool,
        expected_config_revision: int | None = None,
    ) -> dict[str, object]:
        context = self._require_llm_configuration()

        async with self._settings_change_lock:
            current = context.provider_manager.get_profile(provider_name)
            if current is None:
                raise ValueError(f"Unknown LLM provider profile: {provider_name}")
            if not current.editable:
                raise ValueError("Environment providers are read-only")
            profile, _revision = await asyncio.to_thread(
                context.llm_configuration.update,
                provider_name,
                updates,
                api_key=api_key,
                clear_api_key=clear_api_key,
                expected_revision=expected_config_revision,
            )
            await context.provider_manager.upsert_profile(profile)
            if context.provider_manager.active_provider_name == profile.name:
                self._apply_active_llm_profile(profile)
            return self._llm_snapshot()

    async def delete_llm_provider(
        self,
        provider_name: str,
        *,
        expected_config_revision: int | None = None,
    ) -> dict[str, object]:
        context = self._require_llm_configuration()

        async with self._settings_change_lock:
            current = context.provider_manager.get_profile(provider_name)
            if current is None:
                raise ValueError(f"Unknown LLM provider profile: {provider_name}")
            if not current.editable:
                raise ValueError("Environment providers are read-only")
            if context.provider_manager.active_provider_name == provider_name:
                raise ValueError(
                    "Select another LLM provider before deleting this one"
                )
            await asyncio.to_thread(
                context.llm_configuration.delete,
                provider_name,
                expected_revision=expected_config_revision,
            )
            await context.provider_manager.delete_profile(provider_name)
            return self._llm_snapshot()

    async def test_llm_provider(
        self,
        data: dict[str, object],
        *,
        api_key: str | None,
        existing_name: str | None = None,
    ) -> dict[str, object]:
        context = self._require_llm_configuration()
        profile = await asyncio.to_thread(
            context.llm_configuration.build_draft_profile,
            data,
            api_key=api_key,
            existing_name=existing_name,
        )
        provider = OpenAICompatibleProvider(profile.settings)
        try:
            response = await provider.generate(
                [LLMMessage(role="user", content="Reply with OK.")],
                max_tokens=1,
                temperature=0,
            )
            return {
                "success": True,
                "message": "Connection successful",
                "model": response.model or profile.settings.model,
            }
        finally:
            await provider.close()

    async def discover_llm_models(
        self,
        data: dict[str, object],
        *,
        api_key: str | None,
        existing_name: str | None = None,
    ) -> dict[str, object]:
        context = self._require_llm_configuration()
        profile = await asyncio.to_thread(
            context.llm_configuration.build_draft_profile,
            data,
            api_key=api_key,
            existing_name=existing_name,
        )
        provider = OpenAICompatibleProvider(profile.settings)
        try:
            return {"models": await provider.list_models()}
        finally:
            await provider.close()

    def _require_llm_configuration(self) -> RuntimeContext:
        if self.context is None or self.context.llm_configuration is None:
            raise RuntimeError("LLM provider configuration is unavailable")
        return self.context

    def _apply_active_llm_profile(self, profile) -> None:
        if self.context is None:
            return
        self.context.supports_image_input = profile.supports_image_input
        self.context.runtime_controls.supports_image_input = (
            profile.supports_image_input
        )

    def _llm_snapshot(
        self,
        *,
        settings_revision: int | None = None,
    ) -> dict[str, object]:
        if self.context is None:
            raise RuntimeError("App runtime has not been started")
        if settings_revision is None:
            settings_revision = self.context.settings_service.settings.revision
        config_revision = (
            self.context.llm_configuration.revision
            if self.context.llm_configuration is not None
            else 0
        )
        return self.context.provider_manager.public_snapshot(
            revision=settings_revision,
            config_revision=config_revision,
        )

    async def select_asr_provider(
        self,
        provider_name: str,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, object]:
        if self.context is None or self.asr_service is None:
            raise RuntimeError("App runtime has not been started")

        async with self._settings_change_lock:
            previous_provider = self.asr_service.selected_asr_provider
            try:
                await self.asr_service.set_selected_asr_provider(provider_name)
                settings = await asyncio.to_thread(
                    self.context.settings_service.select_asr_provider,
                    provider_name,
                    expected_revision=expected_revision,
                )
            except Exception:
                if self.asr_service.selected_asr_provider != previous_provider:
                    try:
                        await self.asr_service.set_selected_asr_provider(
                            previous_provider
                        )
                    except Exception:
                        logger.exception(
                            "Failed to restore ASR provider %s",
                            previous_provider,
                        )
                raise

            snapshot = await self.asr_service.status_snapshot()
            return {
                **asdict(snapshot),
                "revision": settings.revision,
            }

    async def stop(self) -> None:
        if not self._started:
            return

        if self.gateway_task is not None:
            self.gateway_task.cancel()
            await asyncio.gather(self.gateway_task, return_exceptions=True)
            self.gateway_task = None

        if self.channel_manager is not None:
            await self.channel_manager.stop_all()

        if self.context is not None:
            await self.context.coordinator.close()
            await self.context.provider_manager.close()
        if self.tts_service is not None:
            await self.tts_service.close()
        if self.asr_service is not None:
            await self.asr_service.close()

        self._started = False

    async def reload_channels(
        self,
        config: ChannelsConfig | None = None,
    ) -> None:
        if self.bus is None:
            raise RuntimeError("App runtime has not been started")

        next_config = config or load_channels_config(self.channel_config_path)
        next_manager = ChannelManager(
            next_config,
            self.bus,
            attachment_store=self.context.attachment_store,
        )
        await next_manager.start_all()

        previous_manager = self.channel_manager
        self.channel_manager = next_manager
        self.channels_config = next_config

        if previous_manager is not None:
            await previous_manager.stop_all()

    def channel_status(self) -> dict[str, dict[str, bool]]:
        if self.channel_manager is None:
            return {}
        return self.channel_manager.get_status()

    async def health_snapshot(self) -> dict[str, object]:
        if self.context is None or self.bus is None or self.session_service is None:
            raise RuntimeError("App runtime has not been started")

        current_session = await self.session_service.load_current_session()
        current_role = await self.context.coordinator.current_role_name(
            current_session.id,
        )
        run_counts = await self.context.coordinator.run_counts()
        return {
            "status": "ok",
            "workspace": str(self.context.workspace),
            "current_session_id": current_session.id,
            "current_session_title": current_session.title,
            "current_role": current_role,
            "channels": self.channel_status(),
            "bus": {
                "inbound_size": self.bus.inbound_size,
                "outbound_size": self.bus.outbound_size,
            },
            "runs": run_counts,
        }


def _default_context_builder(options: RuntimeOptions) -> RuntimeContext:
    return build_runtime_context(options, load_session_state=False)


def _default_tts_service_builder(workspace: Path) -> TTSService:
    return build_default_tts_service(workspace)


def _default_asr_service_builder(workspace: Path) -> ASRService:
    return build_default_asr_service(workspace)


def _resolve_runtime_path(
    workspace: Path | None,
    path: str | Path,
) -> Path:
    resolved_path = Path(path).expanduser()
    if resolved_path.is_absolute() or workspace is None:
        return resolved_path
    return workspace / resolved_path
