from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from echobot import AgentCore, AgentTraceStore, LLMMessage, LLMResponse
from echobot.attachments import AttachmentStore
from echobot.asr import ASRStatusSnapshot, ProviderStatusSnapshot, TranscriptionResult
from echobot.app import create_app
from echobot.channels import ChannelAddress
from echobot.orchestration import (
    ConversationCoordinator,
    DecisionEngine,
    RoleCardRegistry,
    RoleplayEngine,
)
from echobot.providers.base import LLMProvider
from echobot.runtime.bootstrap import RuntimeContext, RuntimeOptions
from echobot.runtime.settings import (
    DEFAULT_SHELL_SAFETY_MODE,
    RuntimeConfigSnapshot,
    RuntimeControls,
    RuntimeSettingsStore,
)
from echobot.runtime.session_runner import SessionAgentRunner
from echobot.runtime.sessions import SessionStore
from echobot.scheduling.cron import (
    CronJob,
    CronJobState,
    CronPayload,
    CronSchedule,
    CronService,
    CronStore,
)
from echobot.scheduling.heartbeat import HeartbeatService
from echobot.tts import (
    SynthesizedSpeech,
    TTSProvider,
    TTSSynthesisOptions,
    TTSService,
    VoiceOption,
)


os.environ.setdefault("ECHOBOT_ASR_SHERPA_AUTO_DOWNLOAD", "false")
os.environ.setdefault("ECHOBOT_VAD_SILERO_AUTO_DOWNLOAD", "false")


def make_chat_png_bytes() -> bytes:
    image = Image.new("RGBA", (2, 2), (255, 0, 0, 128))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def make_chat_text_bytes() -> bytes:
    return "hello from uploaded file\n".encode("utf-8")


class FakeProvider(LLMProvider):
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
        system_text = "\n".join(
            message.content_text
            for message in messages
            if getattr(message, "role", "") == "system"
        )
        user_text = messages[-1].content_text if messages else ""
        if "The system decided this request needs the full agent" in system_text:
            content = "working"
        elif user_text.startswith("The full agent finished the task."):
            content = "done"
        elif user_text.startswith("The full agent failed while handling the task."):
            content = "failed"
        else:
            content = "pong"
        return LLMResponse(
            message=LLMMessage(role="assistant", content=content),
            model="fake-model",
        )

    async def stream_generate(
        self,
        messages,
        *,
        tools=None,
        tool_choice=None,
        temperature=None,
        max_tokens=None,
    ):
        response = await self.generate(
            messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        content = response.message.content
        if not content:
            return
        midpoint = max(len(content) // 2, 1)
        yield content[:midpoint]
        if midpoint < len(content):
            yield content[midpoint:]


class SlowAgentProvider(LLMProvider):
    async def generate(
        self,
        messages,
        *,
        tools=None,
        tool_choice=None,
        temperature=None,
        max_tokens=None,
    ) -> LLMResponse:
        del messages, tools, tool_choice, temperature, max_tokens
        await asyncio.sleep(2)
        return LLMResponse(
            message=LLMMessage(role="assistant", content="done-late"),
            model="slow-fake-model",
        )


class SlowAckProvider(FakeProvider):
    async def stream_generate(
        self,
        messages,
        *,
        tools=None,
        tool_choice=None,
        temperature=None,
        max_tokens=None,
    ):
        response = await self.generate(
            messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        content = response.message.content
        if not content:
            return
        midpoint = max(len(content) // 2, 1)
        yield content[:midpoint]
        await asyncio.sleep(0.1)
        if midpoint < len(content):
            yield content[midpoint:]


class FakeTTSProvider(TTSProvider):
    name = "edge"
    label = "Fake Edge TTS"

    @property
    def default_voice(self) -> str:
        return "zh-CN-XiaoxiaoNeural"

    async def list_voices(self) -> list[VoiceOption]:
        return [
            VoiceOption(
                name="Microsoft Xiaoxiao Online (Natural)",
                short_name="zh-CN-XiaoxiaoNeural",
                locale="zh-CN",
                gender="Female",
                display_name="Xiaoxiao",
            ),
        ]

    async def synthesize(
        self,
        *,
        text: str,
        options: TTSSynthesisOptions | None = None,
    ) -> SynthesizedSpeech:
        selected_voice = (options.voice if options else None) or self.default_voice
        payload = f"fake-audio:{selected_voice}:{text}".encode("utf-8")
        return SynthesizedSpeech(
            audio_bytes=payload,
            content_type="audio/mpeg",
            file_extension="mp3",
            provider=self.name,
            voice=selected_voice,
        )


class FakeKokoroTTSProvider(TTSProvider):
    name = "kokoro"
    label = "Fake Sherpa Kokoro"

    @property
    def default_voice(self) -> str:
        return "zf_001"

    async def list_voices(self) -> list[VoiceOption]:
        return [
            VoiceOption(
                name="zf_001 (3)",
                short_name="zf_001",
                locale="zh-CN",
                gender="Female",
                display_name="Chinese zf_001",
            ),
            VoiceOption(
                name="af_maple (0)",
                short_name="af_maple",
                locale="en-US",
                gender="Female",
                display_name="American af_maple",
            ),
        ]

    async def synthesize(
        self,
        *,
        text: str,
        options: TTSSynthesisOptions | None = None,
    ) -> SynthesizedSpeech:
        selected_voice = (options.voice if options else None) or self.default_voice
        payload = f"fake-kokoro:{selected_voice}:{text}".encode("utf-8")
        return SynthesizedSpeech(
            audio_bytes=payload,
            content_type="audio/wav",
            file_extension="wav",
            provider=self.name,
            voice=selected_voice,
        )


class FakeRealtimeASRSession:
    async def accept_audio_bytes(self, audio_bytes: bytes) -> list[dict[str, object]]:
        if not audio_bytes:
            return []
        text = audio_bytes.decode("utf-8", errors="ignore").strip() or "voice"
        return [
            {
                "type": "transcript",
                "text": text,
                "language": "zh",
                "final": True,
                "start_ms": 0,
            }
        ]

    async def flush(self) -> list[dict[str, object]]:
        return []

    async def reset(self) -> None:
        return None


class FakeASRService:
    def __init__(self) -> None:
        self.selected_asr_provider = "fake-asr"

    async def on_startup(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def status_snapshot(self) -> ASRStatusSnapshot:
        return ASRStatusSnapshot(
            available=True,
            state="ready",
            detail="ASR ready",
            sample_rate=16000,
            selected_asr_provider=self.selected_asr_provider,
            selected_vad_provider="fake-vad",
            always_listen_supported=True,
            asr_providers=[
                ProviderStatusSnapshot(
                    kind="asr",
                    name="fake-asr",
                    label="Fake ASR",
                    selected=self.selected_asr_provider == "fake-asr",
                    available=True,
                    state="ready",
                    detail="ASR ready",
                    resource_directory="D:/fake-models/asr",
                ),
                ProviderStatusSnapshot(
                    kind="asr",
                    name="backup-asr",
                    label="Backup ASR",
                    selected=self.selected_asr_provider == "backup-asr",
                    available=True,
                    state="ready",
                    detail="Backup ASR ready",
                    resource_directory="D:/fake-models/asr-backup",
                ),
            ],
            vad_providers=[
                ProviderStatusSnapshot(
                    kind="vad",
                    name="fake-vad",
                    label="Fake VAD",
                    selected=True,
                    available=True,
                    state="ready",
                    detail="VAD ready",
                    resource_directory="D:/fake-models/vad",
                )
            ],
        )

    async def transcribe_wav_bytes(self, audio_bytes: bytes) -> TranscriptionResult:
        text = f"voice-{len(audio_bytes)}" if audio_bytes else ""
        return TranscriptionResult(text=text, language="zh")

    async def create_realtime_session(self) -> FakeRealtimeASRSession:
        return FakeRealtimeASRSession()

    async def set_selected_asr_provider(self, provider_name: str) -> None:
        normalized_name = provider_name.strip()
        if normalized_name not in {"fake-asr", "backup-asr"}:
            raise ValueError(f"Unknown ASR provider: {provider_name}")
        self.selected_asr_provider = normalized_name


def build_test_context(options: RuntimeOptions) -> RuntimeContext:
    workspace = (options.workspace or Path(".")).resolve()
    agent = AgentCore(FakeProvider())
    session_store = SessionStore(workspace / ".echobot" / "sessions")
    agent_session_store = SessionStore(workspace / ".echobot" / "agent_sessions")
    trace_store = AgentTraceStore(workspace / ".echobot" / "agent_traces")
    session_runner = SessionAgentRunner(
        agent,
        agent_session_store,
        trace_store=trace_store,
    )
    role_registry = RoleCardRegistry.discover(project_root=workspace)
    coordinator = ConversationCoordinator(
        session_store=session_store,
        agent_runner=session_runner,
        decision_engine=DecisionEngine(),
        roleplay_engine=RoleplayEngine(AgentCore(FakeProvider()), role_registry),
        role_registry=role_registry,
        delegated_ack_enabled=_delegated_ack_enabled(options),
    )
    heartbeat_service = None
    if not options.no_heartbeat:
        heartbeat_service = HeartbeatService(
            heartbeat_file=workspace / ".echobot" / "HEARTBEAT.md",
            provider=FakeProvider(),
            interval_seconds=60,
        )
    return RuntimeContext(
        workspace=workspace,
        attachment_store=AttachmentStore(workspace / ".echobot" / "attachments"),
        supports_image_input=True,
        agent=agent,
        session_store=session_store,
        agent_session_store=agent_session_store,
        session=None,
        tool_registry=None,
        skill_registry=None,
        cron_service=CronService(workspace / ".echobot" / "cron" / "jobs.json"),
        heartbeat_service=heartbeat_service,
        session_runner=session_runner,
        coordinator=coordinator,
        role_registry=role_registry,
        memory_support=None,
        heartbeat_file_path=workspace / ".echobot" / "HEARTBEAT.md",
        heartbeat_interval_seconds=60,
        tool_registry_factory=lambda *_args: None,
        runtime_controls=_runtime_controls(options),
        default_runtime_config=_default_runtime_config(options),
    )


def build_slow_agent_test_context(options: RuntimeOptions) -> RuntimeContext:
    workspace = (options.workspace or Path(".")).resolve()
    agent = AgentCore(SlowAgentProvider())
    session_store = SessionStore(workspace / ".echobot" / "sessions")
    agent_session_store = SessionStore(workspace / ".echobot" / "agent_sessions")
    trace_store = AgentTraceStore(workspace / ".echobot" / "agent_traces")
    session_runner = SessionAgentRunner(
        agent,
        agent_session_store,
        trace_store=trace_store,
    )
    role_registry = RoleCardRegistry.discover(project_root=workspace)
    coordinator = ConversationCoordinator(
        session_store=session_store,
        agent_runner=session_runner,
        decision_engine=DecisionEngine(),
        roleplay_engine=RoleplayEngine(AgentCore(FakeProvider()), role_registry),
        role_registry=role_registry,
        delegated_ack_enabled=_delegated_ack_enabled(options),
    )
    heartbeat_service = None
    if not options.no_heartbeat:
        heartbeat_service = HeartbeatService(
            heartbeat_file=workspace / ".echobot" / "HEARTBEAT.md",
            provider=FakeProvider(),
            interval_seconds=60,
        )
    return RuntimeContext(
        workspace=workspace,
        attachment_store=AttachmentStore(workspace / ".echobot" / "attachments"),
        supports_image_input=True,
        agent=agent,
        session_store=session_store,
        agent_session_store=agent_session_store,
        session=None,
        tool_registry=None,
        skill_registry=None,
        cron_service=CronService(workspace / ".echobot" / "cron" / "jobs.json"),
        heartbeat_service=heartbeat_service,
        session_runner=session_runner,
        coordinator=coordinator,
        role_registry=role_registry,
        memory_support=None,
        heartbeat_file_path=workspace / ".echobot" / "HEARTBEAT.md",
        heartbeat_interval_seconds=60,
        tool_registry_factory=lambda *_args: None,
        runtime_controls=_runtime_controls(options),
        default_runtime_config=_default_runtime_config(options),
    )


def build_slow_ack_test_context(options: RuntimeOptions) -> RuntimeContext:
    workspace = (options.workspace or Path(".")).resolve()
    agent = AgentCore(FakeProvider())
    session_store = SessionStore(workspace / ".echobot" / "sessions")
    agent_session_store = SessionStore(workspace / ".echobot" / "agent_sessions")
    trace_store = AgentTraceStore(workspace / ".echobot" / "agent_traces")
    session_runner = SessionAgentRunner(
        agent,
        agent_session_store,
        trace_store=trace_store,
    )
    role_registry = RoleCardRegistry.discover(project_root=workspace)
    coordinator = ConversationCoordinator(
        session_store=session_store,
        agent_runner=session_runner,
        decision_engine=DecisionEngine(),
        roleplay_engine=RoleplayEngine(AgentCore(SlowAckProvider()), role_registry),
        role_registry=role_registry,
        delegated_ack_enabled=_delegated_ack_enabled(options),
    )
    heartbeat_service = None
    if not options.no_heartbeat:
        heartbeat_service = HeartbeatService(
            heartbeat_file=workspace / ".echobot" / "HEARTBEAT.md",
            provider=FakeProvider(),
            interval_seconds=60,
        )
    return RuntimeContext(
        workspace=workspace,
        attachment_store=AttachmentStore(workspace / ".echobot" / "attachments"),
        supports_image_input=True,
        agent=agent,
        session_store=session_store,
        agent_session_store=agent_session_store,
        session=None,
        tool_registry=None,
        skill_registry=None,
        cron_service=CronService(workspace / ".echobot" / "cron" / "jobs.json"),
        heartbeat_service=heartbeat_service,
        session_runner=session_runner,
        coordinator=coordinator,
        role_registry=role_registry,
        memory_support=None,
        heartbeat_file_path=workspace / ".echobot" / "HEARTBEAT.md",
        heartbeat_interval_seconds=60,
        tool_registry_factory=lambda *_args: None,
        runtime_controls=_runtime_controls(options),
        default_runtime_config=_default_runtime_config(options),
    )


def build_test_tts_service(_workspace: Path) -> TTSService:
    return TTSService(
        {
            "edge": FakeTTSProvider(),
            "kokoro": FakeKokoroTTSProvider(),
        },
        default_provider="edge",
    )


def build_test_asr_service(_workspace: Path) -> FakeASRService:
    return FakeASRService()


def _delegated_ack_enabled(options: RuntimeOptions) -> bool:
    if options.delegated_ack_enabled is None:
        store = RuntimeSettingsStore(
            (options.workspace or Path(".")).resolve()
            / ".echobot"
            / "runtime_settings.json",
        )
        return store.load().delegated_ack_enabled is not False
    return bool(options.delegated_ack_enabled)


def _runtime_controls(options: RuntimeOptions) -> RuntimeControls:
    store = RuntimeSettingsStore(
        (options.workspace or Path(".")).resolve()
        / ".echobot"
        / "runtime_settings.json",
    )
    settings = store.load()
    shell_safety_mode = settings.shell_safety_mode or DEFAULT_SHELL_SAFETY_MODE
    return RuntimeControls(
        shell_safety_mode=shell_safety_mode,
        file_write_enabled=(
            True if settings.file_write_enabled is None else settings.file_write_enabled
        ),
        cron_mutation_enabled=(
            True
            if settings.cron_mutation_enabled is None
            else settings.cron_mutation_enabled
        ),
        web_private_network_enabled=(
            False
            if settings.web_private_network_enabled is None
            else settings.web_private_network_enabled
        ),
    )


def _default_runtime_config(options: RuntimeOptions) -> RuntimeConfigSnapshot:
    return RuntimeConfigSnapshot(
        delegated_ack_enabled=(
            True
            if options.delegated_ack_enabled is None
            else bool(options.delegated_ack_enabled)
        ),
        shell_safety_mode=DEFAULT_SHELL_SAFETY_MODE,
        file_write_enabled=True,
        cron_mutation_enabled=True,
        web_private_network_enabled=False,
    )


def write_test_live2d_model(workspace: Path) -> None:
    model_dir = workspace / ".echobot" / "live2d" / "兔兔"
    texture_dir = model_dir / "兔兔 .4096"
    texture_dir.mkdir(parents=True, exist_ok=True)

    model_payload = {
        "Version": 3,
        "FileReferences": {
            "Moc": "兔兔 .moc3",
            "Textures": [
                "兔兔 .4096/texture_00.png",
            ],
            "DisplayInfo": "兔兔 .cdi3.json",
        },
    }
    display_info_payload = {
        "Version": 3,
        "Parameters": [
            {"Id": "ParamMouthOpenY", "Name": "嘴巴开合"},
            {"Id": "ParamMouthForm", "Name": "嘴型"},
        ],
    }

    (model_dir / "兔兔 .model3.json").write_text(
        json.dumps(model_payload, ensure_ascii=False),
        encoding="utf-8",
    )
    (model_dir / "兔兔 .cdi3.json").write_text(
        json.dumps(display_info_payload, ensure_ascii=False),
        encoding="utf-8",
    )
    (model_dir / "兔兔 .moc3").write_bytes(b"fake-moc3")
    (texture_dir / "texture_00.png").write_bytes(b"fake-png")


def write_test_hiyori_live2d_model(workspace: Path) -> None:
    model_dir = workspace / ".echobot" / "live2d" / "hiyori_pro_en" / "runtime"
    texture_dir = model_dir / "hiyori_pro_t11.2048"
    texture_dir.mkdir(parents=True, exist_ok=True)

    model_payload = {
        "Version": 3,
        "FileReferences": {
            "Moc": "hiyori_pro_t11.moc3",
            "Textures": [
                "hiyori_pro_t11.2048/texture_00.png",
            ],
            "DisplayInfo": "hiyori_pro_t11.cdi3.json",
        },
    }
    display_info_payload = {
        "Version": 3,
        "Parameters": [
            {"Id": "ParamMouthOpenY", "Name": "Mouth Open"},
            {"Id": "ParamMouthForm", "Name": "Mouth Form"},
        ],
    }

    (model_dir / "hiyori_pro_t11.model3.json").write_text(
        json.dumps(model_payload, ensure_ascii=False),
        encoding="utf-8",
    )
    (model_dir / "hiyori_pro_t11.cdi3.json").write_text(
        json.dumps(display_info_payload, ensure_ascii=False),
        encoding="utf-8",
    )
    (model_dir / "hiyori_pro_t11.moc3").write_bytes(b"fake-moc3")
    (texture_dir / "texture_00.png").write_bytes(b"fake-png")


def write_test_vtube_live2d_model(workspace: Path) -> None:
    model_dir = workspace / ".echobot" / "live2d" / "yumi"
    texture_dir = model_dir / "textures"
    texture_dir.mkdir(parents=True, exist_ok=True)

    model_payload = {
        "Version": 3,
        "FileReferences": {
            "Moc": "yumi.moc3",
            "Textures": [
                "textures/texture_00.png",
            ],
            "DisplayInfo": "yumi.cdi3.json",
        },
        "Groups": [
            {
                "Target": "Parameter",
                "Name": "LipSync",
                "Ids": ["ParamMouthOpenY"],
            },
        ],
    }
    display_info_payload = {
        "Version": 3,
        "Parameters": [
            {"Id": "ParamMouthOpenY", "Name": "Mouth Open"},
            {"Id": "ParamMouthForm", "Name": "Mouth Form"},
            {"Id": "ParamEyeSmile", "Name": "Smile"},
        ],
    }
    vtube_payload = {
        "FileReferences": {
            "Model": "yumi.model3.json",
            "IdleAnimation": "wave.motion3.json",
        },
        "Hotkeys": [
            {
                "HotkeyID": "hk_exp_smile",
                "Name": "Smile",
                "Action": "ToggleExpression",
                "File": "smile.exp3.json",
                "Triggers": {
                    "Trigger1": "Alt",
                    "Trigger2": "N1",
                    "Trigger3": "",
                    "ScreenButton": -1,
                },
            },
            {
                "HotkeyID": "hk_motion_wave",
                "Name": "Wave",
                "Action": "TriggerAnimation",
                "File": "wave.motion3.json",
                "Triggers": {
                    "Trigger1": "LeftControl",
                    "Trigger2": "F3",
                    "Trigger3": "",
                    "ScreenButton": -1,
                },
            },
            {
                "HotkeyID": "hk_clear",
                "Name": "Clear Expressions",
                "Action": "RemoveAllExpressions",
                "File": "",
                "Triggers": {
                    "Trigger1": "LeftControl",
                    "Trigger2": "Tab",
                    "Trigger3": "Space",
                    "ScreenButton": -1,
                },
            },
        ],
    }
    smile_expression_payload = {
        "Type": "Live2D Expression",
        "Parameters": [
            {"Id": "ParamEyeSmile", "Value": 1.0, "Blend": "Add"},
        ],
    }
    sad_expression_payload = {
        "Type": "Live2D Expression",
        "Parameters": [
            {"Id": "ParamMouthForm", "Value": -1.0, "Blend": "Set"},
        ],
    }
    motion_payload = {
        "Version": 3,
        "Meta": {
            "Duration": 1.0,
            "Loop": False,
            "AreBeziersRestricted": True,
            "CurveCount": 0,
            "TotalSegmentCount": 0,
            "TotalPointCount": 0,
            "UserDataCount": 0,
            "TotalUserDataSize": 0,
            "Fps": 30,
        },
        "Curves": [],
        "UserData": [],
    }
    annotations_payload = {
        "version": 1,
        "expressions": {
            "smile.exp3.json": "默认笑脸",
        },
        "motions": {},
        "hotkeys": {},
    }

    (model_dir / "yumi.model3.json").write_text(
        json.dumps(model_payload, ensure_ascii=False),
        encoding="utf-8",
    )
    (model_dir / "yumi.cdi3.json").write_text(
        json.dumps(display_info_payload, ensure_ascii=False),
        encoding="utf-8",
    )
    (model_dir / "yumi.vtube.json").write_text(
        json.dumps(vtube_payload, ensure_ascii=False),
        encoding="utf-8",
    )
    (model_dir / "smile.exp3.json").write_text(
        json.dumps(smile_expression_payload, ensure_ascii=False),
        encoding="utf-8",
    )
    (model_dir / "sad.exp3.json").write_text(
        json.dumps(sad_expression_payload, ensure_ascii=False),
        encoding="utf-8",
    )
    (model_dir / "wave.motion3.json").write_text(
        json.dumps(motion_payload, ensure_ascii=False),
        encoding="utf-8",
    )
    (model_dir / "jump.motion3.json").write_text(
        json.dumps(motion_payload, ensure_ascii=False),
        encoding="utf-8",
    )
    (model_dir / "echobot.live2d.json").write_text(
        json.dumps(annotations_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (model_dir / "yumi.moc3").write_bytes(b"fake-yumi-moc3")
    (texture_dir / "texture_00.png").write_bytes(b"fake-png")


def write_test_split_runtime_live2d_models(workspace: Path) -> None:
    base_dir = workspace / ".echobot" / "live2d" / "duo"
    runtime_specs = [
        {
            "runtime_name": "alpha",
            "model_name": "alpha",
            "parameter_id": "ParamAlphaSmile",
            "default_note": "alpha default note",
            "function_key": "F1",
        },
        {
            "runtime_name": "beta",
            "model_name": "beta",
            "parameter_id": "ParamBetaSmile",
            "default_note": "beta default note",
            "function_key": "F2",
        },
    ]

    for runtime_spec in runtime_specs:
        runtime_dir = base_dir / runtime_spec["runtime_name"]
        texture_dir = runtime_dir / "textures"
        texture_dir.mkdir(parents=True, exist_ok=True)

        model_payload = {
            "Version": 3,
            "FileReferences": {
                "Moc": f"{runtime_spec['model_name']}.moc3",
                "Textures": [
                    "textures/texture_00.png",
                ],
                "DisplayInfo": f"{runtime_spec['model_name']}.cdi3.json",
            },
        }
        display_info_payload = {
            "Version": 3,
            "Parameters": [
                {"Id": "ParamMouthOpenY", "Name": "Mouth Open"},
                {"Id": runtime_spec["parameter_id"], "Name": "Smile"},
            ],
        }
        vtube_payload = {
            "FileReferences": {
                "Model": f"{runtime_spec['model_name']}.model3.json",
            },
            "Hotkeys": [
                {
                    "HotkeyID": "hk_smile",
                    "Name": "Smile",
                    "Action": "ToggleExpression",
                    "File": "smile.exp3.json",
                    "Triggers": {
                        "Trigger1": "LeftControl",
                        "Trigger2": runtime_spec["function_key"],
                        "Trigger3": "",
                        "ScreenButton": -1,
                    },
                },
            ],
        }
        expression_payload = {
            "Type": "Live2D Expression",
            "Parameters": [
                {"Id": runtime_spec["parameter_id"], "Value": 1.0, "Blend": "Add"},
            ],
        }
        annotations_payload = {
            "version": 1,
            "expressions": {
                "smile.exp3.json": runtime_spec["default_note"],
            },
            "motions": {},
            "hotkeys": {},
        }

        (runtime_dir / f"{runtime_spec['model_name']}.model3.json").write_text(
            json.dumps(model_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        (runtime_dir / f"{runtime_spec['model_name']}.cdi3.json").write_text(
            json.dumps(display_info_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        (runtime_dir / f"{runtime_spec['model_name']}.vtube.json").write_text(
            json.dumps(vtube_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        (runtime_dir / "smile.exp3.json").write_text(
            json.dumps(expression_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        (runtime_dir / "echobot.live2d.json").write_text(
            json.dumps(annotations_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (runtime_dir / f"{runtime_spec['model_name']}.moc3").write_bytes(b"fake-moc3")
        (texture_dir / "texture_00.png").write_bytes(b"fake-png")


def write_test_cron_jobs(workspace: Path) -> None:
    cron_store_path = workspace / ".echobot" / "cron" / "jobs.json"
    cron_store_path.parent.mkdir(parents=True, exist_ok=True)
    store = CronStore(
        jobs=[
            CronJob(
                id="job_enabled",
                name="Morning summary",
                enabled=True,
                schedule=CronSchedule(kind="every", every_seconds=3600),
                payload=CronPayload(
                    kind="agent",
                    content="Summarize today's priorities",
                    session_name="default",
                ),
                state=CronJobState(
                    next_run_at="2030-01-01T09:00:00+08:00",
                    last_run_at="2030-01-01T08:00:00+08:00",
                    last_status="ok",
                ),
                created_at="2030-01-01T07:30:00+08:00",
                updated_at="2030-01-01T08:00:00+08:00",
            ),
            CronJob(
                id="job_disabled",
                name="Disabled reminder",
                enabled=False,
                schedule=CronSchedule(kind="cron", expr="0 9 * * 1-5", timezone="Asia/Shanghai"),
                payload=CronPayload(
                    kind="text",
                    content="Standup time",
                    session_name="team",
                ),
                state=CronJobState(
                    last_run_at="2030-01-01T07:55:00+08:00",
                    last_status="error",
                    last_error="network timeout",
                ),
                created_at="2030-01-01T07:00:00+08:00",
                updated_at="2030-01-01T07:55:00+08:00",
            ),
        ]
    )
    cron_store_path.write_text(
        json.dumps(store.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_test_heartbeat_file(workspace: Path, content: str) -> None:
    heartbeat_file_path = workspace / ".echobot" / "HEARTBEAT.md"
    heartbeat_file_path.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_file_path.write_text(content, encoding="utf-8")


class AppApiTests(unittest.TestCase):
    def test_health_and_channel_endpoints_work(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
            )

            with TestClient(app) as client:
                health = client.get("/api/health")
                definitions = client.get("/api/channels/definitions")
                config = client.get("/api/channels/config")
                roles = client.get("/api/roles")

            self.assertEqual(200, health.status_code)
            self.assertEqual("ok", health.json()["status"])
            self.assertEqual("default", health.json()["current_session"])
            self.assertEqual("default", health.json()["current_role"])
            self.assertEqual(200, definitions.status_code)
            self.assertEqual(["console", "telegram", "qq"], [item["name"] for item in definitions.json()])
            self.assertEqual(200, config.status_code)
            self.assertIn("telegram", config.json())
            self.assertEqual(200, roles.status_code)
            self.assertEqual(["default"], [item["name"] for item in roles.json()])

    def test_session_and_chat_endpoints_share_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
            )

            with TestClient(app) as client:
                created = client.post("/api/sessions", json={"name": "demo"})
                replied = client.post(
                    "/api/chat",
                    json={
                        "session_name": "demo",
                        "prompt": "ping",
                    },
                )
                current = client.get("/api/sessions/current")
                detail = client.get("/api/sessions/demo")

            self.assertEqual(200, created.status_code)
            self.assertEqual("demo", created.json()["name"])
            self.assertEqual(200, replied.status_code)
            self.assertEqual("demo", replied.json()["session_name"])
            self.assertEqual("pong", replied.json()["response"])
            self.assertEqual("pong", replied.json()["response_content"])
            self.assertFalse(replied.json()["delegated"])
            self.assertTrue(replied.json()["completed"])
            self.assertEqual("default", replied.json()["role_name"])
            self.assertEqual(200, current.status_code)
            self.assertEqual("demo", current.json()["name"])
            self.assertEqual(200, detail.status_code)
            self.assertEqual("default", detail.json()["role_name"])
            self.assertEqual("auto", detail.json()["route_mode"])
            self.assertEqual(2, len(detail.json()["history"]))
            self.assertEqual("user", detail.json()["history"][0]["role"])
            self.assertEqual("assistant", detail.json()["history"][1]["role"])

    def test_chat_endpoint_accepts_image_only_requests(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
            )

            with TestClient(app) as client:
                uploaded = client.post(
                    "/api/attachments/images",
                    files={
                        "file": (
                            "cat.png",
                            make_chat_png_bytes(),
                            "image/png",
                        )
                    },
                )
                replied = client.post(
                    "/api/chat",
                    json={
                        "session_name": "vision",
                        "prompt": "",
                        "images": [
                            {
                                "attachment_id": uploaded.json()["attachment_id"],
                            }
                        ],
                    },
                )
                detail = client.get("/api/sessions/vision")

            self.assertEqual(200, uploaded.status_code)
            self.assertEqual(200, replied.status_code)
            self.assertEqual("pong", replied.json()["response"])
            self.assertEqual(200, detail.status_code)
            self.assertIsInstance(detail.json()["history"][0]["content"], list)
            self.assertTrue(
                detail.json()["history"][0]["content"][0]["image_url"]["url"].startswith("attachment://")
            )
            self.assertTrue(
                detail.json()["history"][0]["content"][0]["image_url"]["preview_url"].startswith(
                    "/api/attachments/",
                )
            )

    def test_chat_endpoint_ignores_images_when_vision_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
            )

            with TestClient(app) as client:
                runtime = client.app.state.runtime
                runtime.context.supports_image_input = False
                uploaded = client.post(
                    "/api/attachments/images",
                    files={
                        "file": (
                            "cat.png",
                            make_chat_png_bytes(),
                            "image/png",
                        )
                    },
                )
                replied = client.post(
                    "/api/chat",
                    json={
                        "session_name": "vision-off",
                        "prompt": "",
                        "images": [
                            {
                                "attachment_id": uploaded.json()["attachment_id"],
                            }
                        ],
                    },
                )
                detail = client.get("/api/sessions/vision-off")

            self.assertEqual(200, uploaded.status_code)
            self.assertEqual(200, replied.status_code)
            self.assertEqual("pong", replied.json()["response"])
            self.assertEqual(200, detail.status_code)
            self.assertEqual("", detail.json()["history"][0]["content"])

    def test_chat_endpoint_returns_structured_response_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
            )

            with TestClient(app) as client:
                runtime = client.app.state.runtime
                session = runtime.context.session_store.load_or_create_session("demo")

                async def fake_run_prompt(*args, **kwargs):
                    del args, kwargs
                    return SimpleNamespace(
                        session=session,
                        response_text="Attached the report.",
                        response_content=[
                            {
                                "type": "text",
                                "text": "Attached the report.",
                            },
                            {
                                "type": "file_attachment",
                                "file_attachment": {
                                    "attachment_id": "file_demo",
                                    "name": "report.txt",
                                    "download_url": "/api/attachments/file_demo/content",
                                    "workspace_path": "report.txt",
                                    "content_type": "text/plain",
                                    "size_bytes": 5,
                                },
                            },
                        ],
                        delegated=False,
                        completed=True,
                        job_id=None,
                        status="completed",
                        role_name="default",
                        steps=1,
                        compressed_summary="",
                    )

                runtime.chat_service.run_prompt = fake_run_prompt
                replied = client.post(
                    "/api/chat",
                    json={
                        "session_name": "demo",
                        "prompt": "send the report",
                    },
                )

            self.assertEqual(200, replied.status_code)
            payload = replied.json()
            self.assertEqual("Attached the report.", payload["response"])
            self.assertIsInstance(payload["response_content"], list)
            self.assertEqual("text", payload["response_content"][0]["type"])
            self.assertEqual("file_attachment", payload["response_content"][1]["type"])

    def test_chat_endpoint_accepts_file_only_requests(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
            )

            with TestClient(app) as client:
                uploaded = client.post(
                    "/api/attachments/files",
                    files={
                        "file": (
                            "notes.txt",
                            make_chat_text_bytes(),
                            "text/plain",
                        )
                    },
                )
                upload_payload = uploaded.json()
                downloaded = client.get(upload_payload["download_url"])
                replied = client.post(
                    "/api/chat",
                    json={
                        "session_name": "files",
                        "prompt": "帮我看看这个文件是做什么的",
                        "files": [
                            {
                                "attachment_id": upload_payload["attachment_id"],
                            }
                        ],
                    },
                )
                detail = client.get("/api/sessions/files")

            self.assertEqual(200, uploaded.status_code)
            self.assertTrue(upload_payload["attachment_id"].startswith("file_"))
            self.assertEqual("text/plain", upload_payload["content_type"])
            self.assertTrue(upload_payload["download_url"].startswith("/api/attachments/"))
            self.assertTrue(upload_payload["workspace_path"].startswith(".echobot/attachments/files/file_"))
            self.assertTrue(upload_payload["workspace_path"].endswith(".txt"))

            self.assertEqual(200, downloaded.status_code)
            self.assertTrue(downloaded.headers["content-type"].startswith("text/plain"))
            self.assertEqual(make_chat_text_bytes(), downloaded.content)

            self.assertEqual(200, replied.status_code)
            self.assertEqual("pong", replied.json()["response"])
            self.assertEqual(200, detail.status_code)
            user_content = detail.json()["history"][0]["content"]
            self.assertIsInstance(user_content, list)
            self.assertEqual("text", user_content[0]["type"])
            self.assertEqual("帮我看看这个文件是做什么的", user_content[0]["text"])
            self.assertEqual("file_attachment", user_content[1]["type"])
            self.assertEqual("notes.txt", user_content[1]["file_attachment"]["name"])
            self.assertEqual(
                upload_payload["attachment_id"],
                user_content[1]["file_attachment"]["attachment_id"],
            )
            self.assertEqual(
                upload_payload["download_url"],
                user_content[1]["file_attachment"]["download_url"],
            )
            self.assertEqual(
                upload_payload["size_bytes"],
                user_content[1]["file_attachment"]["size_bytes"],
            )

    def test_chat_endpoint_rejects_wrong_attachment_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
            )

            with TestClient(app) as client:
                uploaded = client.post(
                    "/api/attachments/files",
                    files={
                        "file": (
                            "notes.txt",
                            make_chat_text_bytes(),
                            "text/plain",
                        )
                    },
                )
                replied = client.post(
                    "/api/chat",
                    json={
                        "session_name": "wrong-kind",
                        "prompt": "",
                        "images": [
                            {
                                "attachment_id": uploaded.json()["attachment_id"],
                            }
                        ],
                    },
                )

            self.assertEqual(200, uploaded.status_code)
            self.assertEqual(400, replied.status_code)
            self.assertIn("not an image", replied.json()["detail"])

    def test_chat_endpoint_keeps_chat_only_route_for_file_requests(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
            )

            with TestClient(app) as client:
                runtime = client.app.state.runtime
                runtime.context.tool_registry_factory = (
                    lambda *_args: SimpleNamespace(names=lambda: ["read_text_file"])
                )
                switched = client.put(
                    "/api/sessions/default/route-mode",
                    json={"route_mode": "chat_only"},
                )
                uploaded = client.post(
                    "/api/attachments/files",
                    files={
                        "file": (
                            "notes.txt",
                            make_chat_text_bytes(),
                            "text/plain",
                        )
                    },
                )
                replied = client.post(
                    "/api/chat",
                    json={
                        "session_name": "default",
                        "prompt": "Please set a cron reminder",
                        "files": [
                            {
                                "attachment_id": uploaded.json()["attachment_id"],
                            }
                        ],
                    },
                )
                detail = client.get("/api/sessions/default")

            self.assertEqual(200, switched.status_code)
            self.assertEqual(200, uploaded.status_code)
            self.assertEqual(200, replied.status_code)
            self.assertFalse(replied.json()["delegated"])
            self.assertEqual("pong", replied.json()["response"])
            self.assertEqual(200, detail.status_code)
            self.assertEqual("chat_only", detail.json()["route_mode"])

    def test_route_mode_endpoint_and_chat_overrides_work(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
            )

            with TestClient(app) as client:
                switched = client.put(
                    "/api/sessions/default/route-mode",
                    json={"route_mode": "chat_only"},
                )
                direct_reply = client.post(
                    "/api/chat",
                    json={
                        "session_name": "default",
                        "prompt": "Please set a cron reminder",
                    },
                )
                forced_reply = client.post(
                    "/api/chat",
                    json={
                        "session_name": "default",
                        "prompt": "ping",
                        "route_mode": "force_agent",
                    },
                )
                detail = client.get("/api/sessions/default")

            self.assertEqual(200, switched.status_code)
            self.assertEqual("chat_only", switched.json()["route_mode"])

            self.assertEqual(200, direct_reply.status_code)
            self.assertFalse(direct_reply.json()["delegated"])
            self.assertTrue(direct_reply.json()["completed"])
            self.assertEqual("pong", direct_reply.json()["response"])

            self.assertEqual(200, forced_reply.status_code)
            self.assertTrue(forced_reply.json()["delegated"])
            self.assertFalse(forced_reply.json()["completed"])
            self.assertEqual("working", forced_reply.json()["response"])
            self.assertTrue(forced_reply.json()["job_id"])

            self.assertEqual(200, detail.status_code)
            self.assertEqual("chat_only", detail.json()["route_mode"])

    def test_role_endpoints_support_crud_and_session_switch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
            )

            role_file = workspace / ".echobot" / "roles" / "helper-cat.md"

            with TestClient(app) as client:
                created = client.post(
                    "/api/roles",
                    json={
                        "name": "Helper Cat",
                        "prompt": "# Helper Cat\n\nStay concise.",
                    },
                )
                listed = client.get("/api/roles")
                detail = client.get("/api/roles/helper-cat")
                switched = client.put(
                    "/api/sessions/default/role",
                    json={"role_name": "helper-cat"},
                )
                replied = client.post(
                    "/api/chat",
                    json={
                        "session_name": "default",
                        "prompt": "ping",
                    },
                )
                deleted = client.delete("/api/roles/helper-cat")
                current = client.get("/api/sessions/current")
                listed_after_delete = client.get("/api/roles")

            self.assertEqual(200, created.status_code)
            self.assertEqual("helper-cat", created.json()["name"])
            self.assertTrue(created.json()["editable"])
            self.assertTrue(str(created.json()["source_path"]).endswith("helper-cat.md"))

            self.assertEqual(200, listed.status_code)
            self.assertEqual(["default", "helper-cat"], [item["name"] for item in listed.json()])

            self.assertEqual(200, detail.status_code)
            self.assertEqual("# Helper Cat\n\nStay concise.", detail.json()["prompt"])

            self.assertEqual(200, switched.status_code)
            self.assertEqual("helper-cat", switched.json()["role_name"])

            self.assertEqual(200, replied.status_code)
            self.assertEqual("helper-cat", replied.json()["role_name"])

            self.assertEqual(200, deleted.status_code)
            self.assertTrue(deleted.json()["deleted"])
            self.assertEqual("helper-cat", deleted.json()["name"])
            self.assertFalse(role_file.exists())

            self.assertEqual(200, current.status_code)
            self.assertEqual("default", current.json()["role_name"])

            self.assertEqual(200, listed_after_delete.status_code)
            self.assertEqual(["default"], [item["name"] for item in listed_after_delete.json()])

    def test_session_endpoints_support_chinese_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
            )

            created_name = "项目讨论"
            renamed_name = "二号会话"

            with TestClient(app) as client:
                created = client.post("/api/sessions", json={"name": created_name})
                current = client.get("/api/sessions/current")
                detail = client.get(f"/api/sessions/{quote(created_name, safe='')}")
                renamed = client.patch(
                    f"/api/sessions/{quote(created_name, safe='')}",
                    json={"name": renamed_name},
                )
                renamed_detail = client.get(f"/api/sessions/{quote(renamed_name, safe='')}")

            self.assertEqual(200, created.status_code)
            self.assertEqual(created_name, created.json()["name"])
            self.assertTrue((workspace / ".echobot" / "sessions" / f"{renamed_name}.jsonl").exists())

            self.assertEqual(200, current.status_code)
            self.assertEqual(created_name, current.json()["name"])

            self.assertEqual(200, detail.status_code)
            self.assertEqual(created_name, detail.json()["name"])

            self.assertEqual(200, renamed.status_code)
            self.assertEqual(renamed_name, renamed.json()["name"])

            self.assertEqual(200, renamed_detail.status_code)
            self.assertEqual(renamed_name, renamed_detail.json()["name"])

    def test_role_endpoints_support_chinese_role_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
            )

            role_name = "助手猫娘"
            role_prompt = "# 助手猫娘\n\n用简洁中文回答。"
            role_path = quote(role_name, safe="")
            role_file = workspace / ".echobot" / "roles" / f"{role_name}.md"

            with TestClient(app) as client:
                created = client.post(
                    "/api/roles",
                    json={
                        "name": role_name,
                        "prompt": role_prompt,
                    },
                )
                detail = client.get(f"/api/roles/{role_path}")
                switched = client.put(
                    "/api/sessions/default/role",
                    json={"role_name": role_name},
                )
                replied = client.post(
                    "/api/chat",
                    json={
                        "session_name": "default",
                        "prompt": "ping",
                    },
                )
                deleted = client.delete(f"/api/roles/{role_path}")

            self.assertEqual(200, created.status_code)
            self.assertEqual(role_name, created.json()["name"])
            self.assertTrue(str(created.json()["source_path"]).endswith(f"{role_name}.md"))

            self.assertEqual(200, detail.status_code)
            self.assertEqual(role_prompt, detail.json()["prompt"])

            self.assertEqual(200, switched.status_code)
            self.assertEqual(role_name, switched.json()["role_name"])

            self.assertEqual(200, replied.status_code)
            self.assertEqual(role_name, replied.json()["role_name"])

            self.assertEqual(200, deleted.status_code)
            self.assertTrue(deleted.json()["deleted"])
            self.assertEqual(role_name, deleted.json()["name"])
            self.assertFalse(role_file.exists())

    def test_default_role_card_is_read_only_from_web_api(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
            )

            with TestClient(app) as client:
                detail = client.get("/api/roles/default")
                updated = client.put(
                    "/api/roles/default",
                    json={"prompt": "# Default\n\nChanged."},
                )
                deleted = client.delete("/api/roles/default")

            self.assertEqual(200, detail.status_code)
            self.assertFalse(detail.json()["editable"])
            self.assertFalse(detail.json()["deletable"])
            self.assertEqual(400, updated.status_code)
            self.assertIn("Default role card cannot be modified", updated.json()["detail"])
            self.assertEqual(400, deleted.status_code)
            self.assertIn("Default role card cannot be modified", deleted.json()["detail"])

    def test_session_endpoint_can_rename_current_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
                asr_service_builder=build_test_asr_service,
            )

            with TestClient(app) as client:
                client.post("/api/sessions", json={"name": "demo"})
                client.post(
                    "/api/chat",
                    json={
                        "session_name": "demo",
                        "prompt": "ping",
                    },
                )

                renamed = client.patch("/api/sessions/demo", json={"name": "demo-renamed"})
                current = client.get("/api/sessions/current")
                renamed_detail = client.get("/api/sessions/demo-renamed")
                missing = client.get("/api/sessions/demo")

            self.assertEqual(200, renamed.status_code)
            self.assertEqual("demo-renamed", renamed.json()["name"])
            self.assertEqual(2, len(renamed.json()["history"]))
            self.assertEqual(200, current.status_code)
            self.assertEqual("demo-renamed", current.json()["name"])
            self.assertEqual(200, renamed_detail.status_code)
            self.assertEqual("demo-renamed", renamed_detail.json()["name"])
            self.assertEqual(404, missing.status_code)

    def test_delete_session_endpoint_removes_route_session_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
                asr_service_builder=build_test_asr_service,
            )

            route_key = "telegram__12345__deadbeef"

            with TestClient(app) as client:
                runtime = client.app.state.runtime
                route_session = runtime.route_session_store.create_session(
                    route_key,
                    title="Route chat",
                )
                runtime.context.session_store.load_or_create_session(
                    route_session.session_name,
                )
                runtime.context.agent_session_store.load_or_create_session(
                    route_session.session_name,
                )
                runtime.delivery_store.remember(
                    route_session.session_name,
                    ChannelAddress(channel="telegram", chat_id="12345"),
                    {"message_id": 9},
                )
                runtime.context.session_store.set_current_session(
                    route_session.session_name,
                )

                deleted = client.delete(
                    f"/api/sessions/{quote(route_session.session_name, safe='')}",
                )
                current_session = client.get("/api/sessions/current")

                replacement = runtime.route_session_store.get_current_session(route_key)

            self.assertEqual(200, deleted.status_code)
            self.assertTrue(deleted.json()["deleted"])
            self.assertNotEqual(route_session.session_name, replacement.session_name)
            self.assertFalse(
                (
                    workspace
                    / ".echobot"
                    / "sessions"
                    / f"{route_session.session_name}.jsonl"
                ).exists()
            )
            self.assertFalse(
                (
                    workspace
                    / ".echobot"
                    / "agent_sessions"
                    / f"{route_session.session_name}.jsonl"
                ).exists()
            )
            self.assertIsNone(
                runtime.delivery_store.get_session_target(route_session.session_name),
            )
            self.assertEqual(200, current_session.status_code)
            self.assertNotEqual(
                route_session.session_name,
                current_session.json()["name"],
            )

    def test_chat_endpoint_returns_job_for_agent_style_requests(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
            )

            with TestClient(app) as client:
                replied = client.post(
                    "/api/chat",
                    json={
                        "session_name": "demo",
                        "prompt": "Please set a cron reminder",
                    },
                )

                self.assertEqual(200, replied.status_code)
                payload = replied.json()
                self.assertTrue(payload["delegated"])
                self.assertFalse(payload["completed"])
                self.assertEqual("running", payload["status"])
                self.assertEqual("working", payload["response"])
                self.assertTrue(payload["job_id"])

                job_id = payload["job_id"]
                final = None
                for _ in range(20):
                    final = client.get(f"/api/chat/jobs/{job_id}")
                    if final.json()["status"] != "running":
                        break
                    time.sleep(0.01)

                assert final is not None
            self.assertEqual(200, final.status_code)
            self.assertEqual("completed", final.json()["status"])
            self.assertEqual("done", final.json()["response"])
            self.assertEqual("done", final.json()["response_content"])

    def test_chat_endpoint_can_disable_agent_ack(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    delegated_ack_enabled=False,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
            )

            with TestClient(app) as client:
                replied = client.post(
                    "/api/chat",
                    json={
                        "session_name": "demo",
                        "prompt": "Please set a cron reminder",
                    },
                )

                self.assertEqual(200, replied.status_code)
                payload = replied.json()
                self.assertTrue(payload["delegated"])
                self.assertFalse(payload["completed"])
                self.assertEqual("running", payload["status"])
                self.assertEqual("", payload["response"])
                self.assertTrue(payload["job_id"])

                job_id = payload["job_id"]
                final = None
                for _ in range(20):
                    final = client.get(f"/api/chat/jobs/{job_id}")
                    if final.json()["status"] != "running":
                        break
                    time.sleep(0.01)

                detail = client.get("/api/sessions/demo")

            assert final is not None
            self.assertEqual(200, final.status_code)
            self.assertEqual("completed", final.json()["status"])
            self.assertEqual("done", final.json()["response"])
            self.assertEqual("done", final.json()["response_content"])
            self.assertEqual(200, detail.status_code)
            history_contents = [item["content"] for item in detail.json()["history"]]
            self.assertNotIn("working", history_contents)
            self.assertIn("done", history_contents)

    def test_chat_job_cancel_endpoint_stops_running_background_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_slow_agent_test_context,
            )

            with TestClient(app) as client:
                replied = client.post(
                    "/api/chat",
                    json={
                        "session_name": "demo",
                        "prompt": "Please set a cron reminder",
                    },
                )

                self.assertEqual(200, replied.status_code)
                job_id = replied.json()["job_id"]
                self.assertTrue(job_id)

                cancelled = client.post(f"/api/chat/jobs/{job_id}/cancel")
                final = client.get(f"/api/chat/jobs/{job_id}")
                detail = client.get("/api/sessions/demo")

            self.assertEqual(200, cancelled.status_code)
            self.assertEqual("cancelled", cancelled.json()["status"])
            self.assertEqual(
                "后台任务已停止。",
                cancelled.json()["response"],
            )

            self.assertEqual(200, final.status_code)
            self.assertEqual("cancelled", final.json()["status"])
            self.assertEqual(
                "后台任务已停止。",
                final.json()["response"],
            )

            self.assertEqual(200, detail.status_code)
            history_contents = [item["content"] for item in detail.json()["history"]]
            self.assertIn("working", history_contents)
            self.assertIn("后台任务已停止。", history_contents)

    def test_chat_job_list_endpoint_returns_latest_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
            )

            with TestClient(app) as client:
                first = client.post(
                    "/api/chat",
                    json={
                        "session_name": "demo",
                        "prompt": "Please set a cron reminder",
                    },
                )
                second = client.post(
                    "/api/chat",
                    json={
                        "session_name": "demo",
                        "prompt": "Please set a cron reminder",
                    },
                )
                jobs = client.get("/api/chat/jobs?session_name=demo&limit=10")

            self.assertEqual(200, first.status_code)
            self.assertEqual(200, second.status_code)
            self.assertEqual(200, jobs.status_code)
            payload = jobs.json()
            self.assertEqual(2, len(payload["jobs"]))
            self.assertEqual("demo", payload["jobs"][0]["session_name"])
            self.assertIn("prompt", payload["jobs"][0])
            self.assertIn("attempt", payload["jobs"][0])
            self.assertIn("started_at", payload["jobs"][0])

    def test_chat_job_retry_endpoint_starts_new_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_slow_agent_test_context,
            )

            with TestClient(app) as client:
                replied = client.post(
                    "/api/chat",
                    json={
                        "session_name": "demo",
                        "prompt": "Please set a cron reminder",
                    },
                )
                original_job_id = replied.json()["job_id"]
                cancelled = client.post(f"/api/chat/jobs/{original_job_id}/cancel")
                retried = client.post(f"/api/chat/jobs/{original_job_id}/retry")

                self.assertEqual(200, cancelled.status_code)
                self.assertEqual(200, retried.status_code)
                self.assertNotEqual(original_job_id, retried.json()["job_id"])
                self.assertEqual("running", retried.json()["status"])

                retried_job_id = retried.json()["job_id"]
                final = None
                for _ in range(300):
                    final = client.get(f"/api/chat/jobs/{retried_job_id}")
                    if final.json()["status"] != "running":
                        break
                    time.sleep(0.01)

            assert final is not None
            self.assertEqual(200, final.status_code)
            self.assertEqual("completed", final.json()["status"])
            self.assertEqual(2, final.json()["attempt"])
            self.assertEqual(original_job_id, final.json()["retry_of_job_id"])

    def test_chat_job_trace_endpoint_returns_recorded_trace_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
            )

            with TestClient(app) as client:
                replied = client.post(
                    "/api/chat",
                    json={
                        "session_name": "demo",
                        "prompt": "Please set a cron reminder",
                    },
                )

                self.assertEqual(200, replied.status_code)
                job_id = replied.json()["job_id"]
                self.assertTrue(job_id)

                trace_response = None
                for _ in range(20):
                    trace_response = client.get(f"/api/chat/jobs/{job_id}/trace")
                    events = trace_response.json()["events"]
                    if events and events[-1]["event"] == "turn_completed":
                        break
                    time.sleep(0.01)

            assert trace_response is not None
            self.assertEqual(200, trace_response.status_code)
            payload = trace_response.json()
            self.assertEqual(job_id, payload["job_id"])
            self.assertEqual("completed", payload["status"])
            self.assertGreaterEqual(len(payload["events"]), 3)
            self.assertEqual("turn_started", payload["events"][0]["event"])
            self.assertEqual("assistant_message", payload["events"][1]["event"])
            self.assertEqual("turn_completed", payload["events"][-1]["event"])
            self.assertEqual(
                "pong",
                payload["events"][-1]["final_message"]["content"],
            )

    def test_chat_stream_endpoint_streams_roleplay_chunks_and_final_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
            )

            with TestClient(app) as client:
                with client.stream(
                    "POST",
                    "/api/chat/stream",
                    json={
                        "session_name": "demo",
                        "prompt": "ping",
                    },
                ) as response:
                    lines = [
                        line if isinstance(line, str) else line.decode("utf-8")
                        for line in response.iter_lines()
                        if line
                    ]

            self.assertEqual(200, response.status_code)
            events = [json.loads(line) for line in lines]
            self.assertEqual("chunk", events[0]["type"])
            self.assertEqual("chunk", events[1]["type"])
            self.assertEqual("po", events[0]["delta"])
            self.assertEqual("ng", events[1]["delta"])
            self.assertEqual("done", events[-1]["type"])
            self.assertEqual("pong", events[-1]["response"])
            self.assertEqual("pong", events[-1]["response_content"])
            self.assertFalse(events[-1]["delegated"])
            self.assertTrue(events[-1]["completed"])

    def test_chat_stream_endpoint_streams_agent_ack_before_background_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
            )

            with TestClient(app) as client:
                with client.stream(
                    "POST",
                    "/api/chat/stream",
                    json={
                        "session_name": "demo",
                        "prompt": "Please set a cron reminder",
                    },
                ) as response:
                    lines = [
                        line if isinstance(line, str) else line.decode("utf-8")
                        for line in response.iter_lines()
                        if line
                    ]

                done_event = json.loads(lines[-1])
                final_job = None
                for _ in range(20):
                    final_job = client.get(f"/api/chat/jobs/{done_event['job_id']}")
                    if final_job.json()["status"] != "running":
                        break
                    time.sleep(0.01)

            self.assertEqual(200, response.status_code)
            events = [json.loads(line) for line in lines]
            self.assertEqual("chunk", events[0]["type"])
            self.assertEqual("done", done_event["type"])
            self.assertTrue(done_event["delegated"])
            self.assertFalse(done_event["completed"])
            self.assertEqual("working", done_event["response"])
            self.assertTrue(done_event["job_id"])

            assert final_job is not None
            self.assertEqual(200, final_job.status_code)
            self.assertEqual("completed", final_job.json()["status"])
            self.assertEqual("done", final_job.json()["response"])
            self.assertEqual("done", final_job.json()["response_content"])

    def test_chat_stream_disconnect_after_ack_still_runs_background_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_slow_ack_test_context,
            )

            with TestClient(app) as client:
                with client.stream(
                    "POST",
                    "/api/chat/stream",
                    json={
                        "session_name": "demo",
                        "prompt": "Please set a cron reminder",
                    },
                ) as response:
                    first_line = None
                    for line in response.iter_lines():
                        if not line:
                            continue
                        first_line = (
                            line
                            if isinstance(line, str)
                            else line.decode("utf-8")
                        )
                        break

                self.assertEqual(200, response.status_code)
                self.assertIsNotNone(first_line)
                first_event = json.loads(first_line)
                self.assertEqual("chunk", first_event["type"])
                self.assertEqual("working", first_event["delta"])

                detail = None
                for _ in range(30):
                    detail = client.get("/api/sessions/demo")
                    contents = [
                        item["content"]
                        for item in detail.json()["history"]
                    ]
                    if "done" in contents:
                        break
                    time.sleep(0.01)

            assert detail is not None
            self.assertEqual(200, detail.status_code)
            history_contents = [item["content"] for item in detail.json()["history"]]
            self.assertIn("working", history_contents)
            self.assertIn("done", history_contents)

    def test_cron_endpoints_return_status_and_job_list(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            write_test_cron_jobs(workspace)
            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
            )

            with TestClient(app) as client:
                status = client.get("/api/cron/status")
                jobs = client.get("/api/cron/jobs?include_disabled=true")

            self.assertEqual(200, status.status_code)
            self.assertTrue(status.json()["enabled"])
            self.assertEqual(2, status.json()["jobs"])
            self.assertTrue(status.json()["next_run_at"])

            self.assertEqual(200, jobs.status_code)
            payload = jobs.json()
            self.assertEqual(2, len(payload["jobs"]))
            jobs_by_id = {
                item["id"]: item
                for item in payload["jobs"]
            }
            self.assertEqual("Morning summary", jobs_by_id["job_enabled"]["name"])
            self.assertEqual("every 3600s", jobs_by_id["job_enabled"]["schedule"])
            self.assertEqual("agent", jobs_by_id["job_enabled"]["payload_kind"])
            self.assertTrue(jobs_by_id["job_enabled"]["enabled"])
            self.assertEqual("Disabled reminder", jobs_by_id["job_disabled"]["name"])
            self.assertEqual("error", jobs_by_id["job_disabled"]["last_status"])
            self.assertEqual("network timeout", jobs_by_id["job_disabled"]["last_error"])

    def test_cron_delete_endpoint_removes_job_and_returns_not_found_afterwards(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            write_test_cron_jobs(workspace)
            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
            )

            with TestClient(app) as client:
                deleted = client.delete("/api/cron/jobs/job_enabled")
                jobs = client.get("/api/cron/jobs?include_disabled=true")
                missing = client.delete("/api/cron/jobs/job_enabled")

            self.assertEqual(200, deleted.status_code)
            self.assertEqual(
                {
                    "deleted": True,
                    "job_id": "job_enabled",
                },
                deleted.json(),
            )

            self.assertEqual(200, jobs.status_code)
            payload = jobs.json()
            self.assertEqual(1, len(payload["jobs"]))
            self.assertEqual("job_disabled", payload["jobs"][0]["id"])

            saved = json.loads(
                (workspace / ".echobot" / "cron" / "jobs.json").read_text(encoding="utf-8"),
            )
            self.assertEqual(1, len(saved["jobs"]))
            self.assertEqual("job_disabled", saved["jobs"][0]["id"])

            self.assertEqual(404, missing.status_code)
            self.assertEqual("Cron job not found: job_enabled", missing.json()["detail"])

    def test_heartbeat_endpoint_returns_content_and_allows_updates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            write_test_heartbeat_file(
                workspace,
                "# HEARTBEAT.md\n\n- [ ] Check inbox\n",
            )
            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
            )

            updated_content = "# HEARTBEAT.md\n\n- [ ] Review roadmap\n"

            with TestClient(app) as client:
                heartbeat = client.get("/api/heartbeat")
                saved = client.put(
                    "/api/heartbeat",
                    json={"content": updated_content},
                )

            self.assertEqual(200, heartbeat.status_code)
            self.assertTrue(heartbeat.json()["enabled"])
            self.assertEqual(60, heartbeat.json()["interval_seconds"])
            self.assertEqual(
                str(workspace / ".echobot" / "HEARTBEAT.md"),
                heartbeat.json()["file_path"],
            )
            self.assertEqual("# HEARTBEAT.md\n\n- [ ] Check inbox\n", heartbeat.json()["content"])
            self.assertTrue(heartbeat.json()["has_meaningful_content"])

            self.assertEqual(200, saved.status_code)
            self.assertEqual(updated_content, saved.json()["content"])
            self.assertTrue(saved.json()["has_meaningful_content"])
            self.assertEqual(
                updated_content,
                (workspace / ".echobot" / "HEARTBEAT.md").read_text(encoding="utf-8"),
            )

    def test_desktop_routes_expose_minimal_desktop_ui(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            write_test_live2d_model(workspace)

            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
                tts_service_builder=build_test_tts_service,
                asr_service_builder=build_test_asr_service,
            )

            with TestClient(app) as client:
                page = client.get("/desktop")
                config = client.get("/api/web/config")

                self.assertEqual(200, page.status_code)
                self.assertIn('id="desktop-stage"', page.text)
                self.assertIn('id="live2d-stage"', page.text)
                self.assertIn('class="live2d-stage desktop-live2d-stage"', page.text)
                self.assertIn('data-desktop-transparent-stage="true"', page.text)
                self.assertIn('id="live2d-canvas"', page.text)
                self.assertIn('id="desktop-drag-button"', page.text)
                self.assertIn('id="desktop-voice-button"', page.text)
                self.assertIn('id="desktop-web-button"', page.text)
                self.assertIn('class="desktop-tool-icon"', page.text)
                web_index = page.text.index('id="desktop-web-button"')
                voice_index = page.text.index('id="desktop-voice-button"')
                drag_index = page.text.index('id="desktop-drag-button"')
                self.assertLess(web_index, voice_index)
                self.assertLess(voice_index, drag_index)
                self.assertNotIn(">拖动块<", page.text)
                self.assertNotIn(">语音服务<", page.text)
                self.assertNotIn(">Web<", page.text)
                self.assertIn('class="desktop-toolbar"', page.text)
                self.assertIn('id="tts-provider-select"', page.text)
                self.assertIn('id="voice-select"', page.text)
                self.assertIn('id="asr-provider-select"', page.text)
                self.assertIn('id="always-listen-checkbox"', page.text)
                self.assertIn('id="prompt-input"', page.text)
                self.assertIn('id="chat-form"', page.text)
                self.assertNotIn('desktop-status-panel', page.text)
                self.assertNotIn('id="session-sidebar-toggle"', page.text)
                self.assertNotIn('id="role-sidebar-toggle"', page.text)
                self.assertNotIn('id="runtime-panel"', page.text)
                self.assertNotIn('id="heartbeat-panel"', page.text)
                self.assertNotIn('id="live2d-drawer"', page.text)
                self.assertIn('EchoBot Desktop Pet', page.text)
                self.assertEqual(200, config.status_code)
                self.assertTrue(config.json()["live2d"]["available"])

    def test_web_console_routes_expose_static_ui_and_live2d_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            write_test_live2d_model(workspace)

            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
                tts_service_builder=build_test_tts_service,
                asr_service_builder=build_test_asr_service,
            )

            with TestClient(app) as client:
                page = client.get("/web")
                config = client.get("/api/web/config")

                self.assertEqual(200, page.status_code)
                self.assertIn('id="model-select"', page.text)
                self.assertIn('id="session-sidebar-toggle"', page.text)
                self.assertIn('id="session-list"', page.text)
                self.assertIn('id="record-button"', page.text)
                self.assertIn('id="always-listen-checkbox"', page.text)
                self.assertIn('id="role-select"', page.text)
                self.assertIn('id="stop-agent-button"', page.text)
                self.assertIn('id="role-editor"', page.text)
                self.assertIn('id="tts-provider-select"', page.text)
                self.assertIn('id="asr-provider-select"', page.text)
                self.assertIn('id="route-mode-select"', page.text)
                self.assertIn('id="runtime-panel"', page.text)
                self.assertIn('id="runtime-reset-button"', page.text)
                self.assertIn('id="delegated-ack-checkbox"', page.text)
                self.assertIn('id="shell-safety-mode-select"', page.text)
                self.assertIn('id="file-write-enabled-checkbox"', page.text)
                self.assertIn('id="cron-mutation-enabled-checkbox"', page.text)
                self.assertIn('id="web-private-network-enabled-checkbox"', page.text)
                self.assertIn('id="heartbeat-panel"', page.text)
                self.assertIn('id="heartbeat-input"', page.text)
                self.assertIn('id="heartbeat-save-button"', page.text)
                self.assertIn('id="agent-trace-panel"', page.text)
                self.assertIn('id="agent-trace-events"', page.text)
                self.assertIn('id="live2d-panel"', page.text)
                self.assertIn('id="live2d-drawer"', page.text)
                self.assertIn('id="live2d-drawer-toggle"', page.text)
                self.assertIn('id="live2d-hotkeys-checkbox"', page.text)
                self.assertIn('id="live2d-upload-button"', page.text)
                self.assertIn('id="live2d-upload-input"', page.text)
                self.assertIn('id="stage-background-select"', page.text)
                self.assertIn('id="stage-background-upload-button"', page.text)
                self.assertIn('id="stage-background-position-x-input"', page.text)
                self.assertIn('id="stage-background-position-y-input"', page.text)
                self.assertIn('id="stage-background-scale-input"', page.text)
                self.assertIn('id="stage-background-transform-reset-button"', page.text)
                self.assertIn('id="stage-effects-particles-enabled-checkbox"', page.text)
                self.assertIn('id="stage-effects-particle-density-input"', page.text)
                self.assertIn('id="stage-effects-particle-opacity-input"', page.text)
                self.assertIn('id="stage-effects-particle-size-input"', page.text)
                self.assertIn('id="stage-effects-particle-speed-input"', page.text)
                self.assertIn('id="window-file-drop-overlay"', page.text)
                self.assertIn('id="message-image-dialog"', page.text)
                self.assertIn('id="message-image-dialog-image"', page.text)
                self.assertNotIn('id="message-image-dialog-link"', page.text)
                self.assertIn("EchoBot Web Console", page.text)
                self.assertIn("HEARTBEAT 周期任务", page.text)
                self.assertIn("CRON 定时任务", page.text)

                self.assertEqual(200, config.status_code)
                payload = config.json()
                self.assertEqual("default", payload["session_name"])
                self.assertEqual("auto", payload["route_mode"])
                self.assertTrue(payload["runtime"]["delegated_ack_enabled"])
                self.assertEqual(
                    "danger-full-access",
                    payload["runtime"]["shell_safety_mode"],
                )
                self.assertTrue(payload["runtime"]["file_write_enabled"])
                self.assertTrue(payload["runtime"]["cron_mutation_enabled"])
                self.assertFalse(payload["runtime"]["web_private_network_enabled"])
                self.assertEqual("default", payload["stage"]["default_background_key"])
                self.assertEqual("default", payload["stage"]["backgrounds"][0]["key"])
                self.assertEqual("不使用背景", payload["stage"]["backgrounds"][0]["label"])
                builtin_background = next(
                    (
                        item
                        for item in payload["stage"]["backgrounds"]
                        if item["kind"] == "builtin"
                    ),
                    None,
                )
                self.assertIsNotNone(builtin_background)
                self.assertTrue(payload["asr"]["available"])
                self.assertEqual("ready", payload["asr"]["state"])
                self.assertEqual(16000, payload["asr"]["sample_rate"])
                self.assertEqual("fake-asr", payload["asr"]["selected_asr_provider"])
                self.assertTrue(
                    any(item["name"] == "backup-asr" for item in payload["asr"]["asr_providers"])
                )
                self.assertEqual("edge", payload["tts"]["default_provider"])
                self.assertEqual("zh-CN-XiaoxiaoNeural", payload["tts"]["default_voices"]["edge"])
                self.assertEqual("zf_001", payload["tts"]["default_voices"]["kokoro"])
                self.assertTrue(
                    any(item["name"] == "kokoro" for item in payload["tts"]["providers"])
                )
                self.assertTrue(payload["live2d"]["available"])
                self.assertEqual("workspace", payload["live2d"]["source"])
                self.assertEqual("兔兔 ", payload["live2d"]["model_name"])
                self.assertIn("ParamMouthOpenY", payload["live2d"]["lip_sync_parameter_ids"])
                self.assertEqual("ParamMouthForm", payload["live2d"]["mouth_form_parameter_id"])
                self.assertIn("%E5%85%94%E5%85%94", payload["live2d"]["model_url"])
                self.assertTrue(payload["live2d"]["selection_key"].startswith("workspace:"))
                self.assertTrue(
                    any(
                        item["source"] == "workspace"
                        and item["directory_name"] == "兔兔"
                        for item in payload["live2d"]["models"]
                    )
                )
                self.assertTrue(
                    any(item["source"] == "builtin" for item in payload["live2d"]["models"])
                )

                model_response = client.get(payload["live2d"]["model_url"])
                builtin_background_response = client.get(builtin_background["url"])
                texture_response = client.get(
                    "/api/web/live2d/workspace/%E5%85%94%E5%85%94/%E5%85%94%E5%85%94%20.4096/texture_00.png",
                )

                self.assertEqual(200, model_response.status_code)
                self.assertEqual(200, builtin_background_response.status_code)
                self.assertGreater(len(builtin_background_response.content), 0)
                self.assertEqual(200, texture_response.status_code)
                self.assertIn("DisplayInfo", model_response.text)

    def test_web_console_runtime_toggle_updates_config_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            settings_path = workspace / ".echobot" / "runtime_settings.json"
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(
                json.dumps(
                    {
                        "delegated_ack_enabled": True,
                        "shell_safety_mode": "danger-full-access",
                        "file_write_enabled": True,
                        "cron_mutation_enabled": True,
                        "web_private_network_enabled": False,
                        "future_setting": "keep-me",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
                tts_service_builder=build_test_tts_service,
                asr_service_builder=build_test_asr_service,
            )

            with TestClient(app) as client:
                updated = client.patch(
                    "/api/web/runtime",
                    json={
                        "delegated_ack_enabled": False,
                        "shell_safety_mode": "read-only",
                        "file_write_enabled": False,
                        "cron_mutation_enabled": False,
                        "web_private_network_enabled": True,
                    },
                )
                config = client.get("/api/web/config")

            self.assertEqual(200, updated.status_code)
            self.assertFalse(updated.json()["delegated_ack_enabled"])
            self.assertEqual("read-only", updated.json()["shell_safety_mode"])
            self.assertFalse(updated.json()["file_write_enabled"])
            self.assertFalse(updated.json()["cron_mutation_enabled"])
            self.assertTrue(updated.json()["web_private_network_enabled"])
            self.assertEqual(200, config.status_code)
            self.assertFalse(config.json()["runtime"]["delegated_ack_enabled"])
            self.assertEqual("read-only", config.json()["runtime"]["shell_safety_mode"])
            self.assertFalse(config.json()["runtime"]["file_write_enabled"])
            self.assertFalse(config.json()["runtime"]["cron_mutation_enabled"])
            self.assertTrue(config.json()["runtime"]["web_private_network_enabled"])

            self.assertTrue(settings_path.exists())
            settings_payload = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(
                {
                    "delegated_ack_enabled": False,
                    "shell_safety_mode": "read-only",
                    "file_write_enabled": False,
                    "cron_mutation_enabled": False,
                    "web_private_network_enabled": True,
                    "future_setting": "keep-me",
                },
                settings_payload,
            )

            restarted_app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
                tts_service_builder=build_test_tts_service,
                asr_service_builder=build_test_asr_service,
            )

            with TestClient(restarted_app) as client:
                restarted_config = client.get("/api/web/config")

            self.assertEqual(200, restarted_config.status_code)
            self.assertFalse(restarted_config.json()["runtime"]["delegated_ack_enabled"])
            self.assertEqual(
                "read-only",
                restarted_config.json()["runtime"]["shell_safety_mode"],
            )
            self.assertFalse(restarted_config.json()["runtime"]["file_write_enabled"])
            self.assertFalse(restarted_config.json()["runtime"]["cron_mutation_enabled"])
            self.assertTrue(restarted_config.json()["runtime"]["web_private_network_enabled"])

    def test_web_console_runtime_patch_updates_only_requested_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            settings_path = workspace / ".echobot" / "runtime_settings.json"
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(
                json.dumps(
                    {
                        "delegated_ack_enabled": False,
                        "shell_safety_mode": "read-only",
                        "file_write_enabled": True,
                        "cron_mutation_enabled": False,
                        "web_private_network_enabled": True,
                        "future_setting": "keep-me",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
                tts_service_builder=build_test_tts_service,
                asr_service_builder=build_test_asr_service,
            )

            with TestClient(app) as client:
                updated = client.patch(
                    "/api/web/runtime",
                    json={"file_write_enabled": False},
                )
                config = client.get("/api/web/config")

            self.assertEqual(200, updated.status_code)
            self.assertFalse(updated.json()["delegated_ack_enabled"])
            self.assertEqual("read-only", updated.json()["shell_safety_mode"])
            self.assertFalse(updated.json()["file_write_enabled"])
            self.assertFalse(updated.json()["cron_mutation_enabled"])
            self.assertTrue(updated.json()["web_private_network_enabled"])

            self.assertEqual(200, config.status_code)
            self.assertFalse(config.json()["runtime"]["delegated_ack_enabled"])
            self.assertEqual("read-only", config.json()["runtime"]["shell_safety_mode"])
            self.assertFalse(config.json()["runtime"]["file_write_enabled"])
            self.assertFalse(config.json()["runtime"]["cron_mutation_enabled"])
            self.assertTrue(config.json()["runtime"]["web_private_network_enabled"])

            settings_payload = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(
                {
                    "delegated_ack_enabled": False,
                    "shell_safety_mode": "read-only",
                    "file_write_enabled": False,
                    "cron_mutation_enabled": False,
                    "web_private_network_enabled": True,
                    "future_setting": "keep-me",
                },
                settings_payload,
            )

    def test_web_console_runtime_reset_clears_overrides_and_restores_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            settings_path = workspace / ".echobot" / "runtime_settings.json"
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(
                json.dumps(
                    {
                        "delegated_ack_enabled": True,
                        "shell_safety_mode": "read-only",
                        "file_write_enabled": False,
                        "cron_mutation_enabled": False,
                        "web_private_network_enabled": True,
                        "future_setting": "keep-me",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            runtime_options = RuntimeOptions(
                workspace=workspace,
                delegated_ack_enabled=False,
                no_tools=True,
                no_skills=True,
                no_memory=True,
                no_heartbeat=True,
            )
            app = create_app(
                runtime_options=runtime_options,
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
                tts_service_builder=build_test_tts_service,
                asr_service_builder=build_test_asr_service,
            )

            with TestClient(app) as client:
                reset = client.post("/api/web/runtime/reset")
                config = client.get("/api/web/config")

            self.assertEqual(200, reset.status_code)
            self.assertFalse(reset.json()["delegated_ack_enabled"])
            self.assertEqual("danger-full-access", reset.json()["shell_safety_mode"])
            self.assertTrue(reset.json()["file_write_enabled"])
            self.assertTrue(reset.json()["cron_mutation_enabled"])
            self.assertFalse(reset.json()["web_private_network_enabled"])

            self.assertEqual(200, config.status_code)
            self.assertFalse(config.json()["runtime"]["delegated_ack_enabled"])
            self.assertEqual(
                "danger-full-access",
                config.json()["runtime"]["shell_safety_mode"],
            )
            self.assertTrue(config.json()["runtime"]["file_write_enabled"])
            self.assertTrue(config.json()["runtime"]["cron_mutation_enabled"])
            self.assertFalse(config.json()["runtime"]["web_private_network_enabled"])

            settings_payload = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(
                {
                    "future_setting": "keep-me",
                },
                settings_payload,
            )

            restarted_app = create_app(
                runtime_options=runtime_options,
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
                tts_service_builder=build_test_tts_service,
                asr_service_builder=build_test_asr_service,
            )

            with TestClient(restarted_app) as client:
                restarted_config = client.get("/api/web/config")

            self.assertEqual(200, restarted_config.status_code)
            self.assertFalse(restarted_config.json()["runtime"]["delegated_ack_enabled"])
            self.assertEqual(
                "danger-full-access",
                restarted_config.json()["runtime"]["shell_safety_mode"],
            )
            self.assertTrue(restarted_config.json()["runtime"]["file_write_enabled"])
            self.assertTrue(restarted_config.json()["runtime"]["cron_mutation_enabled"])
            self.assertFalse(
                restarted_config.json()["runtime"]["web_private_network_enabled"]
            )

    def test_web_console_asr_provider_switch_updates_config_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            settings_path = workspace / ".echobot" / "runtime_settings.json"
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(
                json.dumps(
                    {
                        "delegated_ack_enabled": True,
                        "future_setting": "keep-me",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
                tts_service_builder=build_test_tts_service,
                asr_service_builder=build_test_asr_service,
            )

            with TestClient(app) as client:
                updated = client.patch(
                    "/api/web/asr/provider",
                    json={
                        "provider": "backup-asr",
                    },
                )
                config = client.get("/api/web/config")

            self.assertEqual(200, updated.status_code)
            self.assertEqual("backup-asr", updated.json()["selected_asr_provider"])
            self.assertEqual(200, config.status_code)
            self.assertEqual("backup-asr", config.json()["asr"]["selected_asr_provider"])

            self.assertTrue(settings_path.exists())
            settings_payload = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(
                {
                    "delegated_ack_enabled": True,
                    "selected_asr_provider": "backup-asr",
                    "future_setting": "keep-me",
                },
                settings_payload,
            )

            restarted_app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
                tts_service_builder=build_test_tts_service,
                asr_service_builder=build_test_asr_service,
            )

            with TestClient(restarted_app) as client:
                restarted_config = client.get("/api/web/config")

            self.assertEqual(200, restarted_config.status_code)
            self.assertEqual("backup-asr", restarted_config.json()["asr"]["selected_asr_provider"])

    def test_web_console_stage_background_upload_and_asset_routes_work(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            write_test_live2d_model(workspace)

            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
                tts_service_builder=build_test_tts_service,
                asr_service_builder=build_test_asr_service,
            )

            with TestClient(app) as client:
                uploaded = client.post(
                    "/api/web/stage/backgrounds",
                    files={
                        "image": ("sunset.png", b"fake-image-bytes", "image/png"),
                    },
                )
                config = client.get("/api/web/config")

                self.assertEqual(200, uploaded.status_code)
                uploaded_payload = uploaded.json()
                self.assertTrue(
                    any(item["label"] == "sunset" for item in uploaded_payload["backgrounds"])
                )

                background_item = next(
                    item
                    for item in uploaded_payload["backgrounds"]
                    if item["label"] == "sunset"
                )
                self.assertEqual("uploaded", background_item["kind"])
                asset_response = client.get(background_item["url"])

                self.assertEqual(200, asset_response.status_code)
                self.assertEqual(b"fake-image-bytes", asset_response.content)
                self.assertEqual(200, config.status_code)
                self.assertTrue(
                    any(item["label"] == "sunset" for item in config.json()["stage"]["backgrounds"])
                )

    def test_web_console_live2d_folder_upload_and_asset_routes_work(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            write_test_live2d_model(workspace)

            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
                tts_service_builder=build_test_tts_service,
                asr_service_builder=build_test_asr_service,
            )

            model_payload = {
                "Version": 3,
                "FileReferences": {
                    "Moc": "cat.moc3",
                    "Textures": [
                        "textures/texture_00.png",
                    ],
                    "DisplayInfo": "cat.cdi3.json",
                },
            }
            display_info_payload = {
                "Version": 3,
                "Parameters": [
                    {"Id": "ParamMouthOpenY", "Name": "Mouth Open"},
                    {"Id": "ParamMouthForm", "Name": "Mouth Form"},
                ],
            }

            with TestClient(app) as client:
                uploaded = client.post(
                    "/api/web/live2d",
                    files=[
                        (
                            "files",
                            ("cat.model3.json", json.dumps(model_payload, ensure_ascii=False), "application/json"),
                        ),
                        ("relative_paths", (None, "cat_model/runtime/cat.model3.json")),
                        (
                            "files",
                            ("cat.cdi3.json", json.dumps(display_info_payload, ensure_ascii=False), "application/json"),
                        ),
                        ("relative_paths", (None, "cat_model/runtime/cat.cdi3.json")),
                        ("files", ("cat.moc3", b"fake-cat-moc3", "application/octet-stream")),
                        ("relative_paths", (None, "cat_model/runtime/cat.moc3")),
                        ("files", ("texture_00.png", b"fake-cat-png", "image/png")),
                        ("relative_paths", (None, "cat_model/runtime/textures/texture_00.png")),
                        ("files", ("README.txt", "ignore-me", "text/plain")),
                        ("relative_paths", (None, "cat_model/README.txt")),
                    ],
                )
                config = client.get("/api/web/config")

                self.assertEqual(200, uploaded.status_code)
                uploaded_payload = uploaded.json()
                uploaded_model = next(
                    item
                    for item in uploaded_payload["models"]
                    if item["selection_key"] == "workspace:cat_model/runtime/cat.model3.json"
                )

                self.assertEqual("workspace", uploaded_model["source"])
                self.assertEqual("cat_model", uploaded_model["directory_name"])
                self.assertEqual("cat", uploaded_model["model_name"])
                self.assertEqual(["ParamMouthOpenY"], uploaded_model["lip_sync_parameter_ids"])

                model_response = client.get(uploaded_model["model_url"])
                texture_response = client.get(
                    f"/api/web/live2d/workspace/{quote('cat_model/runtime/textures/texture_00.png')}",
                )

                self.assertEqual(200, model_response.status_code)
                self.assertIn("DisplayInfo", model_response.text)
                self.assertEqual(200, texture_response.status_code)
                self.assertEqual(b"fake-cat-png", texture_response.content)
                self.assertEqual(200, config.status_code)
                self.assertTrue(
                    any(
                        item["selection_key"] == "workspace:cat_model/runtime/cat.model3.json"
                        for item in config.json()["live2d"]["models"]
                    )
                )

    def test_web_console_discovers_live2d_controls_from_vtube_json_and_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            write_test_vtube_live2d_model(workspace)

            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
                tts_service_builder=build_test_tts_service,
                asr_service_builder=build_test_asr_service,
            )

            with TestClient(app) as client:
                config = client.get("/api/web/config")

            self.assertEqual(200, config.status_code)
            payload = config.json()["live2d"]
            self.assertEqual("workspace", payload["source"])
            self.assertEqual("yumi", payload["directory_name"])
            self.assertTrue(payload["annotations_writable"])
            self.assertEqual(
                ["smile.exp3.json", "sad.exp3.json"],
                [item["file"] for item in payload["expressions"]],
            )
            self.assertEqual(
                "默认笑脸",
                next(item["note"] for item in payload["expressions"] if item["file"] == "smile.exp3.json"),
            )
            self.assertEqual(
                ["wave.motion3.json", "jump.motion3.json"],
                [item["file"] for item in payload["motions"]],
            )
            self.assertEqual(
                ["hk_exp_smile", "hk_motion_wave", "hk_clear"],
                [item["hotkey_id"] for item in payload["hotkeys"]],
            )
            self.assertEqual(
                ["alt", "digit1"],
                payload["hotkeys"][0]["shortcut_tokens"],
            )
            self.assertEqual("Ctrl + F3", payload["hotkeys"][1]["shortcut_label"])
            self.assertTrue(payload["hotkeys"][2]["supported"])

    def test_web_console_patches_model3_json_with_discovered_live2d_controls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            write_test_vtube_live2d_model(workspace)

            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
                tts_service_builder=build_test_tts_service,
                asr_service_builder=build_test_asr_service,
            )

            with TestClient(app) as client:
                config = client.get("/api/web/config")
                model_response = client.get(config.json()["live2d"]["model_url"])

            self.assertEqual(200, config.status_code)
            self.assertEqual(200, model_response.status_code)
            patched_model = model_response.json()
            self.assertEqual(
                ["smile.exp3.json", "sad.exp3.json"],
                [item["File"] for item in patched_model["FileReferences"]["Expressions"]],
            )
            self.assertIn("EchoBotIdle", patched_model["FileReferences"]["Motions"])
            self.assertIn("EchoBotAuto", patched_model["FileReferences"]["Motions"])
            self.assertEqual(
                "wave.motion3.json",
                patched_model["FileReferences"]["Motions"]["EchoBotIdle"][0]["File"],
            )
            self.assertEqual(
                "jump.motion3.json",
                patched_model["FileReferences"]["Motions"]["EchoBotAuto"][0]["File"],
            )

    def test_web_console_can_save_live2d_annotations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            write_test_vtube_live2d_model(workspace)

            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
                tts_service_builder=build_test_tts_service,
                asr_service_builder=build_test_asr_service,
            )

            with TestClient(app) as client:
                config = client.get("/api/web/config")
                selection_key = config.json()["live2d"]["selection_key"]
                expression_saved = client.patch(
                    "/api/web/live2d/annotations",
                    json={
                        "selection_key": selection_key,
                        "kind": "expression",
                        "file": "sad.exp3.json",
                        "note": "悲伤播报",
                    },
                )
                motion_saved = client.patch(
                    "/api/web/live2d/annotations",
                    json={
                        "selection_key": selection_key,
                        "kind": "motion",
                        "file": "jump.motion3.json",
                        "note": "高兴时播放",
                    },
                )
                refreshed = client.get("/api/web/config")

            self.assertEqual(200, expression_saved.status_code)
            self.assertEqual(200, motion_saved.status_code)
            annotations_path = (
                workspace
                / ".echobot"
                / "live2d"
                / "yumi"
                / "echobot.live2d.json"
            )
            annotations_payload = json.loads(annotations_path.read_text(encoding="utf-8"))
            self.assertEqual("悲伤播报", annotations_payload["expressions"]["sad.exp3.json"])
            self.assertEqual("高兴时播放", annotations_payload["motions"]["jump.motion3.json"])
            self.assertEqual(
                "悲伤播报",
                next(
                    item["note"]
                    for item in refreshed.json()["live2d"]["expressions"]
                    if item["file"] == "sad.exp3.json"
                ),
            )
            self.assertEqual(
                "高兴时播放",
                next(
                    item["note"]
                    for item in refreshed.json()["live2d"]["motions"]
                    if item["file"] == "jump.motion3.json"
                ),
            )

    def test_web_console_can_save_live2d_hotkeys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            write_test_vtube_live2d_model(workspace)

            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
                tts_service_builder=build_test_tts_service,
                asr_service_builder=build_test_asr_service,
            )

            with TestClient(app) as client:
                config = client.get("/api/web/config")
                selection_key = config.json()["live2d"]["selection_key"]
                hotkey_saved = client.patch(
                    "/api/web/live2d/hotkeys",
                    json={
                        "selection_key": selection_key,
                        "hotkey_key": "hk_motion_wave",
                        "shortcut_tokens": ["Shift", "N2"],
                    },
                )
                hotkey_cleared = client.patch(
                    "/api/web/live2d/hotkeys",
                    json={
                        "selection_key": selection_key,
                        "hotkey_key": "hk_clear",
                        "shortcut_tokens": [],
                    },
                )
                refreshed = client.get("/api/web/config")

            self.assertEqual(200, hotkey_saved.status_code)
            self.assertEqual(
                ["shift", "digit2"],
                hotkey_saved.json()["shortcut_tokens"],
            )
            self.assertEqual("Shift + 2", hotkey_saved.json()["shortcut_label"])
            self.assertEqual(200, hotkey_cleared.status_code)
            self.assertEqual([], hotkey_cleared.json()["shortcut_tokens"])
            self.assertEqual("Unassigned", hotkey_cleared.json()["shortcut_label"])

            annotations_path = (
                workspace
                / ".echobot"
                / "live2d"
                / "yumi"
                / "echobot.live2d.json"
            )
            annotations_payload = json.loads(annotations_path.read_text(encoding="utf-8"))
            self.assertEqual(
                ["shift", "digit2"],
                annotations_payload["hotkeys"]["hk_motion_wave"]["shortcut_tokens"],
            )
            self.assertEqual(
                [],
                annotations_payload["hotkeys"]["hk_clear"]["shortcut_tokens"],
            )
            refreshed_hotkeys = {
                item["hotkey_key"]: item
                for item in refreshed.json()["live2d"]["hotkeys"]
            }
            self.assertEqual(
                ["shift", "digit2"],
                refreshed_hotkeys["hk_motion_wave"]["shortcut_tokens"],
            )
            self.assertEqual(
                "Shift + 2",
                refreshed_hotkeys["hk_motion_wave"]["shortcut_label"],
            )
            self.assertEqual(
                [],
                refreshed_hotkeys["hk_clear"]["shortcut_tokens"],
            )

    def test_web_console_can_restore_live2d_hotkey_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            write_test_vtube_live2d_model(workspace)

            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
                tts_service_builder=build_test_tts_service,
                asr_service_builder=build_test_asr_service,
            )

            with TestClient(app) as client:
                config = client.get("/api/web/config")
                selection_key = config.json()["live2d"]["selection_key"]
                saved = client.patch(
                    "/api/web/live2d/hotkeys",
                    json={
                        "selection_key": selection_key,
                        "hotkey_key": "hk_motion_wave",
                        "shortcut_tokens": ["Shift", "N2"],
                    },
                )
                restored = client.patch(
                    "/api/web/live2d/hotkeys",
                    json={
                        "selection_key": selection_key,
                        "hotkey_key": "hk_motion_wave",
                        "restore_default": True,
                    },
                )
                refreshed = client.get("/api/web/config")

            self.assertEqual(200, saved.status_code)
            self.assertEqual(200, restored.status_code)
            self.assertEqual(
                ["control", "f3"],
                restored.json()["shortcut_tokens"],
            )
            self.assertEqual("Ctrl + F3", restored.json()["shortcut_label"])

            annotations_path = (
                workspace
                / ".echobot"
                / "live2d"
                / "yumi"
                / "echobot.live2d.json"
            )
            annotations_payload = json.loads(annotations_path.read_text(encoding="utf-8"))
            self.assertNotIn("hk_motion_wave", annotations_payload["hotkeys"])

            refreshed_hotkeys = {
                item["hotkey_key"]: item
                for item in refreshed.json()["live2d"]["hotkeys"]
            }
            self.assertEqual(
                ["control", "f3"],
                refreshed_hotkeys["hk_motion_wave"]["shortcut_tokens"],
            )
            self.assertEqual(
                "Ctrl + F3",
                refreshed_hotkeys["hk_motion_wave"]["shortcut_label"],
            )

    def test_web_console_isolates_live2d_annotations_per_runtime_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            write_test_split_runtime_live2d_models(workspace)

            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
                tts_service_builder=build_test_tts_service,
                asr_service_builder=build_test_asr_service,
            )

            with TestClient(app) as client:
                config = client.get("/api/web/config")
                workspace_models = [
                    item
                    for item in config.json()["live2d"]["models"]
                    if item["source"] == "workspace"
                ]
                alpha_model = next(
                    item
                    for item in workspace_models
                    if item["model_name"] == "alpha"
                )
                beta_model = next(
                    item
                    for item in workspace_models
                    if item["model_name"] == "beta"
                )

                alpha_saved = client.patch(
                    "/api/web/live2d/annotations",
                    json={
                        "selection_key": alpha_model["selection_key"],
                        "kind": "expression",
                        "file": "smile.exp3.json",
                        "note": "alpha custom note",
                    },
                )
                beta_hotkey_saved = client.patch(
                    "/api/web/live2d/hotkeys",
                    json={
                        "selection_key": beta_model["selection_key"],
                        "hotkey_key": "hk_smile",
                        "shortcut_tokens": ["Alt", "Digit9"],
                    },
                )
                refreshed = client.get("/api/web/config")

            self.assertEqual(200, alpha_saved.status_code)
            self.assertEqual(200, beta_hotkey_saved.status_code)

            alpha_annotations_path = (
                workspace
                / ".echobot"
                / "live2d"
                / "duo"
                / "alpha"
                / "echobot.live2d.json"
            )
            beta_annotations_path = (
                workspace
                / ".echobot"
                / "live2d"
                / "duo"
                / "beta"
                / "echobot.live2d.json"
            )
            alpha_annotations = json.loads(alpha_annotations_path.read_text(encoding="utf-8"))
            beta_annotations = json.loads(beta_annotations_path.read_text(encoding="utf-8"))

            self.assertEqual(
                "alpha custom note",
                alpha_annotations["expressions"]["smile.exp3.json"],
            )
            self.assertEqual(
                "beta default note",
                beta_annotations["expressions"]["smile.exp3.json"],
            )
            self.assertNotIn("hk_smile", alpha_annotations["hotkeys"])
            self.assertEqual(
                ["alt", "digit9"],
                beta_annotations["hotkeys"]["hk_smile"]["shortcut_tokens"],
            )

            refreshed_models = {
                item["model_name"]: item
                for item in refreshed.json()["live2d"]["models"]
                if item["source"] == "workspace"
            }
            self.assertEqual(
                "alpha custom note",
                next(
                    item["note"]
                    for item in refreshed_models["alpha"]["expressions"]
                    if item["file"] == "smile.exp3.json"
                ),
            )
            self.assertEqual(
                "beta default note",
                next(
                    item["note"]
                    for item in refreshed_models["beta"]["expressions"]
                    if item["file"] == "smile.exp3.json"
                ),
            )
            self.assertEqual(
                ["alt", "digit9"],
                next(
                    item["shortcut_tokens"]
                    for item in refreshed_models["beta"]["hotkeys"]
                    if item["hotkey_key"] == "hk_smile"
                ),
            )

    def test_web_console_prefers_configured_live2d_model_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            write_test_live2d_model(workspace)
            write_test_hiyori_live2d_model(workspace)

            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
                tts_service_builder=build_test_tts_service,
                asr_service_builder=build_test_asr_service,
            )

            with patch.dict(
                os.environ,
                {"ECHOBOT_WEB_LIVE2D_MODEL": "hiyori_pro_en"},
                clear=False,
            ):
                with TestClient(app) as client:
                    config = client.get("/api/web/config")

            self.assertEqual(200, config.status_code)
            payload = config.json()
            self.assertEqual("hiyori_pro_t11", payload["live2d"]["model_name"])
            self.assertEqual("hiyori_pro_en", payload["live2d"]["directory_name"])
            self.assertEqual(
                "workspace:hiyori_pro_en/runtime/hiyori_pro_t11.model3.json",
                payload["live2d"]["selection_key"],
            )
            self.assertIn(
                "/api/web/live2d/workspace/hiyori_pro_en/runtime/hiyori_pro_t11.model3.json",
                payload["live2d"]["model_url"],
            )

    def test_web_console_falls_back_when_configured_live2d_model_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            write_test_live2d_model(workspace)
            write_test_hiyori_live2d_model(workspace)

            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
                tts_service_builder=build_test_tts_service,
                asr_service_builder=build_test_asr_service,
            )

            with patch.dict(
                os.environ,
                {"ECHOBOT_WEB_LIVE2D_MODEL": "missing-model"},
                clear=False,
            ):
                with TestClient(app) as client:
                    config = client.get("/api/web/config")

            self.assertEqual(200, config.status_code)
            payload = config.json()
            self.assertIn("%E5%85%94%E5%85%94", payload["live2d"]["model_url"])

    def test_web_console_uses_builtin_live2d_when_workspace_has_none(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)

            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
                tts_service_builder=build_test_tts_service,
                asr_service_builder=build_test_asr_service,
            )

            with TestClient(app) as client:
                config = client.get("/api/web/config")

            self.assertEqual(200, config.status_code)
            payload = config.json()
            self.assertTrue(payload["live2d"]["available"])
            self.assertIn("/api/web/live2d/builtin/", payload["live2d"]["model_url"])

    def test_web_console_can_select_builtin_live2d_by_source_prefixed_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)

            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
                tts_service_builder=build_test_tts_service,
                asr_service_builder=build_test_asr_service,
            )

            with patch.dict(
                os.environ,
                {"ECHOBOT_WEB_LIVE2D_MODEL": "builtin:mao_pro_en"},
                clear=False,
            ):
                with TestClient(app) as client:
                    config = client.get("/api/web/config")
                    payload = config.json()
                    model_response = client.get(payload["live2d"]["model_url"])

            self.assertEqual(200, config.status_code)
            self.assertEqual("mao_pro_en", payload["live2d"]["directory_name"])
            self.assertEqual(
                "builtin:mao_pro_en/runtime/mao_pro.model3.json",
                payload["live2d"]["selection_key"],
            )
            self.assertEqual(["ParamA"], payload["live2d"]["lip_sync_parameter_ids"])
            self.assertIn("/api/web/live2d/builtin/mao_pro_en/", payload["live2d"]["model_url"])
            self.assertEqual(200, model_response.status_code)

    def test_web_console_persists_selected_live2d_model_for_all_frontends(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)

            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
                tts_service_builder=build_test_tts_service,
                asr_service_builder=build_test_asr_service,
            )

            with TestClient(app) as client:
                initial_config = client.get("/api/web/config")
                persisted = client.patch(
                    "/api/web/live2d/selection",
                    json={
                        "selection_key": "builtin:mao_pro_en/runtime/mao_pro.model3.json",
                    },
                )
                updated_config = client.get("/api/web/config")

            self.assertEqual(200, initial_config.status_code)
            self.assertEqual(200, persisted.status_code)
            self.assertEqual(200, updated_config.status_code)
            self.assertEqual(
                "builtin:mao_pro_en/runtime/mao_pro.model3.json",
                persisted.json()["selection_key"],
            )
            self.assertEqual(
                "builtin:mao_pro_en/runtime/mao_pro.model3.json",
                updated_config.json()["live2d"]["selection_key"],
            )
            self.assertEqual("mao_pro_en", updated_config.json()["live2d"]["directory_name"])

            settings_path = workspace / ".echobot" / "runtime_settings.json"
            self.assertTrue(settings_path.exists())
            settings_payload = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(
                "builtin:mao_pro_en/runtime/mao_pro.model3.json",
                settings_payload["selected_live2d_model"],
            )

            restarted_app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
                tts_service_builder=build_test_tts_service,
                asr_service_builder=build_test_asr_service,
            )

            with TestClient(restarted_app) as client:
                restarted_config = client.get("/api/web/config")

            self.assertEqual(200, restarted_config.status_code)
            self.assertEqual(
                "builtin:mao_pro_en/runtime/mao_pro.model3.json",
                restarted_config.json()["live2d"]["selection_key"],
            )
            self.assertEqual("mao_pro_en", restarted_config.json()["live2d"]["directory_name"])

    def test_web_tts_routes_work_with_injected_service(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
                tts_service_builder=build_test_tts_service,
                asr_service_builder=build_test_asr_service,
            )

            with TestClient(app) as client:
                voices = client.get("/api/web/tts/voices?provider=edge")
                kokoro_voices = client.get("/api/web/tts/voices?provider=kokoro")
                speech = client.post(
                    "/api/web/tts",
                    json={
                        "text": "你好",
                        "provider": "edge",
                        "voice": "zh-CN-XiaoxiaoNeural",
                    },
                )
                kokoro_speech = client.post(
                    "/api/web/tts",
                    json={
                        "text": "你好",
                        "provider": "kokoro",
                        "voice": "zf_001",
                    },
                )

            self.assertEqual(200, voices.status_code)
            self.assertEqual("edge", voices.json()["provider"])
            self.assertEqual("zh-CN-XiaoxiaoNeural", voices.json()["voices"][0]["short_name"])
            self.assertEqual(200, kokoro_voices.status_code)
            self.assertEqual("kokoro", kokoro_voices.json()["provider"])
            self.assertEqual("zf_001", kokoro_voices.json()["voices"][0]["short_name"])

            self.assertEqual(200, speech.status_code)
            self.assertEqual("audio/mpeg", speech.headers["content-type"])
            self.assertEqual("edge", speech.headers["x-tts-provider"])
            self.assertEqual("zh-CN-XiaoxiaoNeural", speech.headers["x-tts-voice"])
            self.assertIn(b"fake-audio:zh-CN-XiaoxiaoNeural:\xe4\xbd\xa0\xe5\xa5\xbd", speech.content)
            self.assertEqual(200, kokoro_speech.status_code)
            self.assertEqual("audio/wav", kokoro_speech.headers["content-type"])
            self.assertEqual("kokoro", kokoro_speech.headers["x-tts-provider"])
            self.assertEqual("zf_001", kokoro_speech.headers["x-tts-voice"])
            self.assertIn(b"fake-kokoro:zf_001:\xe4\xbd\xa0\xe5\xa5\xbd", kokoro_speech.content)

    def test_web_tts_endpoint_ignores_emojis(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
                tts_service_builder=build_test_tts_service,
                asr_service_builder=build_test_asr_service,
            )

            with TestClient(app) as client:
                speech = client.post(
                    "/api/web/tts",
                    json={
                        "text": "Hello 😊 world",
                        "provider": "edge",
                        "voice": "zh-CN-XiaoxiaoNeural",
                    },
                )
                emoji_only_speech = client.post(
                    "/api/web/tts",
                    json={
                        "text": "😊🎉",
                        "provider": "edge",
                        "voice": "zh-CN-XiaoxiaoNeural",
                    },
                )

            self.assertEqual(200, speech.status_code)
            self.assertEqual(
                b"fake-audio:zh-CN-XiaoxiaoNeural:Hello world",
                speech.content,
            )
            self.assertEqual(400, emoji_only_speech.status_code)
            self.assertEqual(
                "TTS text must not be empty",
                emoji_only_speech.json()["detail"],
            )

    def test_web_asr_routes_work_with_injected_service(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            app = create_app(
                runtime_options=RuntimeOptions(
                    workspace=workspace,
                    no_tools=True,
                    no_skills=True,
                    no_memory=True,
                    no_heartbeat=True,
                ),
                channel_config_path=workspace / ".echobot" / "channels.json",
                context_builder=build_test_context,
                tts_service_builder=build_test_tts_service,
                asr_service_builder=build_test_asr_service,
            )

            with TestClient(app) as client:
                status = client.get("/api/web/asr/status")
                transcript = client.post(
                    "/api/web/asr",
                    content=b"fake-wav",
                    headers={"content-type": "audio/wav"},
                )
                with client.websocket_connect("/api/web/asr/ws") as websocket:
                    ready_event = websocket.receive_json()
                    websocket.send_bytes(b"hello from websocket")
                    transcript_event = websocket.receive_json()
                    websocket.send_text("flush")
                    flush_event = websocket.receive_json()

            self.assertEqual(200, status.status_code)
            self.assertTrue(status.json()["available"])
            self.assertEqual("ready", status.json()["state"])

            self.assertEqual(200, transcript.status_code)
            self.assertEqual("zh", transcript.json()["language"])
            self.assertEqual("voice-8", transcript.json()["text"])

            self.assertEqual("ready", ready_event["type"])
            self.assertEqual(16000, ready_event["sample_rate"])
            self.assertEqual("transcript", transcript_event["type"])
            self.assertEqual("hello from websocket", transcript_event["text"])
            self.assertEqual("flush_complete", flush_event["type"])
