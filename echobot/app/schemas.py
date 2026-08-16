from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..models import LLMMessage, normalize_message_content
from ..orchestration import (
    DEFAULT_ROUTE_MODE,
    RouteMode,
    role_name_from_metadata,
    route_mode_from_metadata,
)
from ..runtime.sessions import Session, SessionInfo


MAX_CHAT_IMAGES = 20
MAX_CHAT_FILES = 20


class StrictRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


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
    id: str
    title: str
    message_count: int
    updated_at: str


class SessionDetailModel(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    role_name: str = "default"
    route_mode: RouteMode = DEFAULT_ROUTE_MODE
    history: list[MessageModel] = Field(default_factory=list)


class CreateSessionRequest(StrictRequestModel):
    title: str | None = None


class SetCurrentSessionRequest(StrictRequestModel):
    session_id: str


class RenameSessionRequest(StrictRequestModel):
    title: str


class SetSessionRoleRequest(StrictRequestModel):
    role_name: str


class SetSessionRouteModeRequest(StrictRequestModel):
    route_mode: RouteMode


class ChatRequest(StrictRequestModel):
    prompt: str
    session_id: str | None = None
    role_name: str | None = None
    route_mode: RouteMode | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    images: list["ChatImageInput"] = Field(
        default_factory=list,
        max_length=MAX_CHAT_IMAGES,
    )
    files: list["ChatFileInput"] = Field(
        default_factory=list,
        max_length=MAX_CHAT_FILES,
    )


class ChatImageInput(BaseModel):
    attachment_id: str


class ChatFileInput(BaseModel):
    attachment_id: str


class ImageAttachmentResponse(BaseModel):
    attachment_id: str
    url: str
    preview_url: str
    content_type: str
    size_bytes: int
    width: int
    height: int
    original_filename: str = ""


class FileAttachmentResponse(BaseModel):
    attachment_id: str
    url: str
    download_url: str
    content_type: str
    size_bytes: int
    original_filename: str = ""
    workspace_path: str


class ChatResponse(BaseModel):
    session_id: str
    session_title: str
    response: str
    response_content: str | list[dict[str, Any]] = ""
    updated_at: str
    steps: int
    agent_summary: str = ""
    delegated: bool = False
    completed: bool = True
    run_id: str | None = None
    status: str = "completed"
    role_name: str = "default"


class AgentRunResponse(BaseModel):
    run_id: str
    session_id: str
    prompt: str
    role_name: str
    status: str
    attempt: int = 1
    retry_of_run_id: str | None = None
    can_retry: bool = False
    response: str = ""
    response_content: str | list[dict[str, Any]] = ""
    error: str = ""
    steps: int = 0
    pending_user_input: dict[str, Any] | None = None
    created_at: str
    started_at: str
    finished_at: str = ""
    updated_at: str


class AgentRunSummaryModel(BaseModel):
    run_id: str
    session_id: str
    prompt: str
    role_name: str
    status: str
    attempt: int = 1
    retry_of_run_id: str | None = None
    can_retry: bool = False
    error: str = ""
    created_at: str
    started_at: str
    finished_at: str = ""
    updated_at: str


class AgentRunsResponse(BaseModel):
    runs: list[AgentRunSummaryModel] = Field(default_factory=list)


class AgentRunEventsResponse(BaseModel):
    run_id: str
    session_id: str
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
    session_id: str = "heartbeat"
    next_run_at: str | None = None
    last_run_at: str | None = None
    last_status: str | None = None
    last_error: str | None = None


class CronJobsResponse(BaseModel):
    jobs: list[CronJobModel] = Field(default_factory=list)


class CronDeleteResponse(BaseModel):
    deleted: bool = True
    job_id: str


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
    state: str = "ready"
    detail: str = ""


class WebTTSConfigModel(BaseModel):
    default_provider: str = "edge"
    default_voice: str = ""
    default_voices: dict[str, str] = Field(default_factory=dict)
    providers: list[WebTTSProviderModel] = Field(default_factory=list)


class WebSpeechProviderModel(BaseModel):
    kind: str = "asr"
    name: str = ""
    label: str = ""
    selected: bool = False
    available: bool = False
    state: str = "missing"
    detail: str = ""
    resource_directory: str = ""


class WebASRConfigModel(BaseModel):
    revision: int = 0
    available: bool = False
    state: str = "missing"
    detail: str = ""
    sample_rate: int = 16000
    selected_asr_provider: str = ""
    selected_vad_provider: str = ""
    always_listen_supported: bool = False
    asr_providers: list[WebSpeechProviderModel] = Field(default_factory=list)
    vad_providers: list[WebSpeechProviderModel] = Field(default_factory=list)


class UpdateWebASRProviderRequest(BaseModel):
    provider: str = ""
    expected_revision: int | None = None


class WebLLMProviderModel(BaseModel):
    name: str = ""
    label: str = ""
    model: str = ""
    base_url: str = ""
    timeout: float = 60.0
    max_retries: int = 2
    extra_headers: dict[str, str] = Field(default_factory=dict)
    extra_body: dict[str, Any] = Field(default_factory=dict)
    supports_image_input: bool = True
    source: str = "environment"
    editable: bool = False
    api_key_configured: bool = False
    selected: bool = False


class WebLLMConfigModel(BaseModel):
    revision: int = 0
    config_revision: int = 0
    active_provider: str = ""
    providers: list[WebLLMProviderModel] = Field(default_factory=list)


class UpdateWebLLMProviderRequest(BaseModel):
    provider: str = ""
    expected_revision: int | None = None


class CreateWebLLMProviderRequest(BaseModel):
    name: str
    label: str
    model: str
    base_url: str
    api_key: str | None = None
    timeout: float = 60.0
    max_retries: int = 2
    extra_headers: dict[str, str] = Field(default_factory=dict)
    extra_body: dict[str, Any] = Field(default_factory=dict)
    supports_image_input: bool = True
    expected_config_revision: int | None = None

    def profile_dict(self) -> dict[str, object]:
        return self.model_dump(
            exclude={"api_key", "expected_config_revision"},
        )


class EditWebLLMProviderRequest(BaseModel):
    label: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    clear_api_key: bool = False
    timeout: float | None = None
    max_retries: int | None = None
    extra_headers: dict[str, str] | None = None
    extra_body: dict[str, Any] | None = None
    supports_image_input: bool | None = None
    expected_config_revision: int | None = None

    def updates_dict(self) -> dict[str, object]:
        return self.model_dump(
            exclude={
                "api_key",
                "clear_api_key",
                "expected_config_revision",
            },
            exclude_none=True,
        )


class TestWebLLMProviderRequest(CreateWebLLMProviderRequest):
    existing_name: str | None = None
    expected_config_revision: int | None = Field(default=None, exclude=True)

    def profile_dict(self) -> dict[str, object]:
        return self.model_dump(
            exclude={
                "api_key",
                "existing_name",
                "expected_config_revision",
            },
        )


class WebLLMProviderTestResponse(BaseModel):
    success: bool = False
    message: str = ""
    model: str = ""


class WebLLMModelsResponse(BaseModel):
    models: list[str] = Field(default_factory=list)


class WebLive2DExpressionModel(BaseModel):
    name: str = ""
    file: str = ""
    url: str = ""
    note: str = ""


class WebLive2DMotionModel(WebLive2DExpressionModel):
    group: str = ""
    index: int = 0


class WebLive2DHotkeyModel(BaseModel):
    hotkey_key: str = ""
    hotkey_id: str = ""
    name: str = ""
    action: str = ""
    file: str = ""
    shortcut_tokens: list[str] = Field(default_factory=list)
    shortcut_label: str = ""
    target_kind: str = ""
    supported: bool = False


class UpdateWebLive2DAnnotationRequest(BaseModel):
    selection_key: str = ""
    kind: str = ""
    file: str = ""
    note: str = ""


class WebLive2DAnnotationResponse(BaseModel):
    selection_key: str = ""
    kind: str = ""
    file: str = ""
    note: str = ""


class UpdateWebLive2DHotkeyRequest(BaseModel):
    selection_key: str = ""
    hotkey_key: str = ""
    shortcut_tokens: list[str] = Field(default_factory=list)
    restore_default: bool = False


class WebLive2DHotkeyResponse(WebLive2DHotkeyModel):
    selection_key: str = ""


class WebLive2DModelOptionModel(BaseModel):
    source: str = ""
    selection_key: str = ""
    model_name: str = ""
    model_url: str = ""
    directory_name: str = ""
    lip_sync_parameter_ids: list[str] = Field(default_factory=list)
    mouth_form_parameter_id: str | None = None
    expressions: list[WebLive2DExpressionModel] = Field(default_factory=list)
    motions: list[WebLive2DMotionModel] = Field(default_factory=list)
    hotkeys: list[WebLive2DHotkeyModel] = Field(default_factory=list)
    annotations_writable: bool = False


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
    revision: int = 0
    delegated_ack_enabled: bool = True
    shell_safety_mode: str = "danger-full-access"
    file_write_enabled: bool = True
    cron_mutation_enabled: bool = True
    web_private_network_enabled: bool = False


class WebConfigResponse(BaseModel):
    session_id: str = ""
    session_title: str = ""
    role_name: str = "default"
    route_mode: RouteMode = DEFAULT_ROUTE_MODE
    runtime: WebRuntimeConfigModel = Field(default_factory=WebRuntimeConfigModel)
    llm: WebLLMConfigModel = Field(default_factory=WebLLMConfigModel)
    live2d: WebLive2DConfigModel = Field(default_factory=WebLive2DConfigModel)
    stage: WebStageConfigModel = Field(default_factory=WebStageConfigModel)
    asr: WebASRConfigModel = Field(default_factory=WebASRConfigModel)
    tts: WebTTSConfigModel = Field(default_factory=WebTTSConfigModel)


class UpdateWebRuntimeConfigRequest(BaseModel):
    expected_revision: int | None = None
    delegated_ack_enabled: bool | None = None
    shell_safety_mode: str | None = None
    file_write_enabled: bool | None = None
    cron_mutation_enabled: bool | None = None
    web_private_network_enabled: bool | None = None


class ASRTranscriptionResponse(BaseModel):
    text: str = ""
    language: str = ""


def message_model_from_message(
    message: LLMMessage,
    *,
    sanitize_user_content: bool = False,
) -> MessageModel:
    del sanitize_user_content
    content = normalize_message_content(message.content)

    return MessageModel(
        role=message.role,
        content=content,
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
        id=info.id,
        title=info.title,
        message_count=info.message_count,
        updated_at=info.updated_at,
    )


def session_detail_model_from_session(session: Session) -> SessionDetailModel:
    return SessionDetailModel(
        id=session.id,
        title=session.title,
        created_at=session.created_at,
        updated_at=session.updated_at,
        role_name=role_name_from_metadata(session.metadata),
        route_mode=route_mode_from_metadata(session.metadata),
        history=[
            message_model_from_message(
                message,
                sanitize_user_content=True,
            )
            for message in session.history
        ],
    )


def channel_config_payload(config: dict[str, Any]) -> dict[str, Any]:
    return dict(config)
