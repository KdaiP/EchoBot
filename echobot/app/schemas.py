from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..models import LLMMessage
from ..orchestration import (
    DEFAULT_ROUTE_MODE,
    RouteMode,
    role_name_from_metadata,
    route_mode_from_metadata,
)
from ..runtime.sessions import ChatSession, SessionInfo


class ToolCallModel(BaseModel):
    id: str
    name: str
    arguments: str


class MessageModel(BaseModel):
    role: str
    content: str | list[dict[str, Any]]
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCallModel] = Field(default_factory=list)


class SessionSummaryModel(BaseModel):
    name: str
    message_count: int
    updated_at: str


class SessionDetailModel(BaseModel):
    name: str
    updated_at: str
    compressed_summary: str = ""
    role_name: str = "default"
    route_mode: RouteMode = DEFAULT_ROUTE_MODE
    history: list[MessageModel] = Field(default_factory=list)


class CreateSessionRequest(BaseModel):
    name: str | None = None


class SetCurrentSessionRequest(BaseModel):
    name: str


class RenameSessionRequest(BaseModel):
    name: str


class SetSessionRoleRequest(BaseModel):
    role_name: str


class SetSessionRouteModeRequest(BaseModel):
    route_mode: RouteMode


class ChatRequest(BaseModel):
    prompt: str
    session_name: str = "default"
    role_name: str | None = None
    route_mode: RouteMode | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    images: list["ChatImageInput"] = Field(default_factory=list)
    vision_context: list[dict] = Field(default_factory=list)


class ChatImageInput(BaseModel):
    data_url: str


class ChatResponse(BaseModel):
    session_name: str
    response: str
    updated_at: str
    steps: int
    compressed_summary: str = ""
    delegated: bool = False
    completed: bool = True
    job_id: str | None = None
    status: str = "completed"
    role_name: str = "default"


class ChatJobResponse(BaseModel):
    job_id: str
    session_name: str
    status: str
    response: str = ""
    error: str = ""
    steps: int = 0
    created_at: str
    updated_at: str


class ChatJobTraceResponse(BaseModel):
    job_id: str
    session_name: str
    status: str
    updated_at: str
    events: list[dict[str, Any]] = Field(default_factory=list)


class CronStatusResponse(BaseModel):
    enabled: bool = False
    jobs: int = 0
    next_run_at: str | None = None


class CronJobModel(BaseModel):
    id: str
    name: str
    enabled: bool = True
    schedule: str = ""
    payload_kind: str = "agent"
    session_name: str = "default"
    next_run_at: str | None = None
    last_run_at: str | None = None
    last_status: str | None = None
    last_error: str | None = None


class CronJobsResponse(BaseModel):
    jobs: list[CronJobModel] = Field(default_factory=list)


class HeartbeatConfigResponse(BaseModel):
    enabled: bool = False
    interval_seconds: int = 0
    file_path: str = ""
    content: str = ""
    has_meaningful_content: bool = False


class UpdateHeartbeatRequest(BaseModel):
    content: str = ""


class RoleSummaryModel(BaseModel):
    name: str
    editable: bool = True
    deletable: bool = True
    source_path: str | None = None


class RoleDetailModel(RoleSummaryModel):
    prompt: str = ""


class CreateRoleRequest(BaseModel):
    name: str
    prompt: str


class UpdateRoleRequest(BaseModel):
    prompt: str


class TTSRequest(BaseModel):
    text: str
    provider: str | None = None
    voice: str | None = None
    rate: str | None = None
    volume: str | None = None
    pitch: str | None = None


class TTSVoiceModel(BaseModel):
    name: str
    short_name: str
    locale: str = ""
    gender: str = ""
    display_name: str = ""


class TTSVoicesResponse(BaseModel):
    provider: str
    voices: list[TTSVoiceModel] = Field(default_factory=list)


class WebTTSProviderModel(BaseModel):
    name: str
    label: str
    available: bool = True
    detail: str = ""


class WebTTSConfigModel(BaseModel):
    default_provider: str = "edge"
    default_voice: str = ""
    default_voices: dict[str, str] = Field(default_factory=dict)
    providers: list[WebTTSProviderModel] = Field(default_factory=list)


class WebASRConfigModel(BaseModel):
    available: bool = False
    state: str = "missing"
    detail: str = ""
    auto_download: bool = True
    model_directory: str = ""
    sample_rate: int = 16000
    provider: str = "cpu"
    always_listen_supported: bool = True


class WebLive2DModelOptionModel(BaseModel):
    source: str = ""
    selection_key: str = ""
    model_name: str = ""
    model_url: str = ""
    directory_name: str = ""
    lip_sync_parameter_ids: list[str] = Field(default_factory=list)
    mouth_form_parameter_id: str | None = None


class WebLive2DConfigModel(WebLive2DModelOptionModel):
    available: bool = False
    models: list[WebLive2DModelOptionModel] = Field(default_factory=list)


class WebStageBackgroundModel(BaseModel):
    key: str = "default"
    label: str = "不使用背景"
    url: str = ""
    kind: str = "none"


class WebStageConfigModel(BaseModel):
    default_background_key: str = "default"
    backgrounds: list[WebStageBackgroundModel] = Field(default_factory=list)


class WebRuntimeConfigModel(BaseModel):
    delegated_ack_enabled: bool = True


class WebConfigResponse(BaseModel):
    session_name: str = "default"
    role_name: str = "default"
    route_mode: RouteMode = DEFAULT_ROUTE_MODE
    runtime: WebRuntimeConfigModel = Field(default_factory=WebRuntimeConfigModel)
    live2d: WebLive2DConfigModel = Field(default_factory=WebLive2DConfigModel)
    stage: WebStageConfigModel = Field(default_factory=WebStageConfigModel)
    asr: WebASRConfigModel = Field(default_factory=WebASRConfigModel)
    tts: WebTTSConfigModel = Field(default_factory=WebTTSConfigModel)


class UpdateWebRuntimeConfigRequest(BaseModel):
    delegated_ack_enabled: bool


class ASRTranscriptionResponse(BaseModel):
    text: str = ""
    language: str = ""


def message_model_from_message(message: LLMMessage) -> MessageModel:
    return MessageModel(
        role=message.role,
        content=message.content,
        name=message.name,
        tool_call_id=message.tool_call_id,
        tool_calls=[
            ToolCallModel(
                id=tool_call.id,
                name=tool_call.name,
                arguments=tool_call.arguments,
            )
            for tool_call in message.tool_calls
        ],
    )


def session_summary_model_from_info(info: SessionInfo) -> SessionSummaryModel:
    return SessionSummaryModel(
        name=info.name,
        message_count=info.message_count,
        updated_at=info.updated_at,
    )


def session_detail_model_from_session(session: ChatSession) -> SessionDetailModel:
    return SessionDetailModel(
        name=session.name,
        updated_at=session.updated_at,
        compressed_summary=session.compressed_summary,
        role_name=role_name_from_metadata(session.metadata),
        route_mode=route_mode_from_metadata(session.metadata),
        history=[message_model_from_message(message) for message in session.history],
    )


def channel_config_payload(config: dict[str, Any]) -> dict[str, Any]:
    return dict(config)
