from __future__ import annotations

import asyncio
import base64
import json
import os
import tempfile
import time
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

from PIL import Image
from fastapi.testclient import TestClient

from echobot import AgentCore, AgentTraceStore, LLMMessage, LLMResponse
from echobot.app import create_app
from echobot.asr import ASRStatusSnapshot, TranscriptionResult
from echobot.channels import ChannelAddress
from echobot.orchestration import (
    ConversationCoordinator,
    DecisionEngine,
    RoleCardRegistry,
    RoleplayEngine,
)
from echobot.providers.base import LLMProvider
from echobot.runtime.bootstrap import RuntimeContext, RuntimeOptions
from echobot.runtime.session_runner import SessionAgentRunner
from echobot.runtime.sessions import SessionStore
from echobot.runtime.settings import RuntimeSettingsStore
from echobot.scheduling.cron import (
    CronJob,
    CronJobState,
    CronPayload,
    CronSchedule,
    CronService,
    CronStore,
)
from echobot.scheduling.heartbeat import HeartbeatService
from echobot.tts import SynthesizedSpeech, TTSProvider, TTSService, VoiceOption

os.environ.setdefault("ECHOBOT_ASR_AUTO_DOWNLOAD", "false")


def make_chat_png_data_url() -> str:
    image = Image.new("RGBA", (2, 2), (255, 0, 0, 128))
    output = BytesIO()
    image.save(output, format="PNG")
    encoded_bytes = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded_bytes}"


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
        voice: str | None = None,
        rate: str | None = None,
        volume: str | None = None,
        pitch: str | None = None,
    ) -> SynthesizedSpeech:
        del rate, volume, pitch
        payload = f"fake-audio:{voice or self.default_voice}:{text}".encode("utf-8")
        return SynthesizedSpeech(
            audio_bytes=payload,
            content_type="audio/mpeg",
            file_extension="mp3",
            provider=self.name,
            voice=voice or self.default_voice,
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
        voice: str | None = None,
        rate: str | None = None,
        volume: str | None = None,
        pitch: str | None = None,
    ) -> SynthesizedSpeech:
        del rate, volume, pitch
        payload = f"fake-kokoro:{voice or self.default_voice}:{text}".encode("utf-8")
        return SynthesizedSpeech(
            audio_bytes=payload,
            content_type="audio/wav",
            file_extension="wav",
            provider=self.name,
            voice=voice or self.default_voice,
        )


class FakeRealtimeASRSession:
    def accept_audio_bytes(self, audio_bytes: bytes) -> list[dict[str, object]]:
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

    def flush(self) -> list[dict[str, object]]:
        return []

    def reset(self) -> None:
        return None


class FakeASRService:
    async def on_startup(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def status_snapshot(self) -> ASRStatusSnapshot:
        return ASRStatusSnapshot(
            available=True,
            state="ready",
            detail="ASR ready",
            auto_download=True,
            model_directory="D:/fake-models",
            sample_rate=16000,
            provider="cpu",
            always_listen_supported=True,
        )

    async def transcribe_wav_bytes(self, audio_bytes: bytes) -> TranscriptionResult:
        text = f"voice-{len(audio_bytes)}" if audio_bytes else ""
        return TranscriptionResult(text=text, language="zh")

    async def create_realtime_session(self) -> FakeRealtimeASRSession:
        return FakeRealtimeASRSession()


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
                replied = client.post(
                    "/api/chat",
                    json={
                        "session_name": "vision",
                        "prompt": "",
                        "images": [
                            {
                                "data_url": make_chat_png_data_url(),
                            }
                        ],
                    },
                )
                detail = client.get("/api/sessions/vision")

            self.assertEqual(200, replied.status_code)
            self.assertEqual("pong", replied.json()["response"])
            self.assertEqual(200, detail.status_code)
            self.assertIsInstance(detail.json()["history"][0]["content"], list)
            self.assertTrue(
                detail.json()["history"][0]["content"][0]["image_url"]["url"].startswith(
                    "data:image/jpeg;base64,",
                )
            )

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

    def test_heartbeat_endpoint_returns_content_and_allows_updates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir).resolve()
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
                self.assertIn('id="route-mode-select"', page.text)
                self.assertIn('id="delegated-ack-checkbox"', page.text)
                self.assertIn('id="heartbeat-panel"', page.text)
                self.assertIn('id="heartbeat-input"', page.text)
                self.assertIn('id="heartbeat-save-button"', page.text)
                self.assertIn('id="agent-trace-panel"', page.text)
                self.assertIn('id="agent-trace-events"', page.text)
                self.assertIn('id="live2d-panel"', page.text)
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
                    },
                )
                config = client.get("/api/web/config")

            self.assertEqual(200, updated.status_code)
            self.assertFalse(updated.json()["delegated_ack_enabled"])
            self.assertEqual(200, config.status_code)
            self.assertFalse(config.json()["runtime"]["delegated_ack_enabled"])

            self.assertTrue(settings_path.exists())
            settings_payload = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(
                {
                    "delegated_ack_enabled": False,
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
