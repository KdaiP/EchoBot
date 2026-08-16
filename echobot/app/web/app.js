import {
    cancelAgentRun,
    deleteAttachment,
    requestAgentRun,
    requestAgentRunEvents,
    requestChatStream,
    requestJson,
    responseToError,
    uploadChatFile,
    uploadChatImage,
} from "./modules/api.js";
import { wireAppEvents } from "./bootstrap/wire-events.js";
import { createUiStatusController } from "./bootstrap/ui-status.js";
import { appState } from "./core/store.js";
import { createAsrModule } from "./features/asr.js";
import { createChatModule } from "./features/chat/index.js";
import { createLayoutModule } from "./features/layout/index.js";
import { createLive2DModule } from "./features/live2d/index.js";
import { createLlmProvidersModule } from "./features/llm-providers.js";
import { createRolesModule } from "./features/roles.js";
import { createSessionsModule } from "./features/sessions.js";
import { createTtsModule } from "./features/tts.js";
import {
    addErrorMessage,
    addMessage,
    addSystemMessage,
    clearMessages,
    initializeMessageInteractions,
    removeMessage,
    updateMessage,
} from "./modules/messages.js";
import { createTraceModule } from "./modules/traces.js";
import {
    clamp,
    delay,
    formatTimestamp,
    roundTo,
    smoothValue,
} from "./modules/utils.js";

const status = createUiStatusController();
const layout = createLayoutModule({
    addMessage,
    formatTimestamp,
    requestJson,
    setRunStatus: status.setRunStatus,
});
const llmProviders = createLlmProvidersModule({
    applyLlmConfig: layout.applyLlmConfig,
    requestJson,
    setRunStatus: status.setRunStatus,
});
const live2d = createLive2DModule({
    clamp,
    requestJson,
    roundTo,
    responseToError,
    setRunStatus: status.setRunStatus,
});
const tts = createTtsModule({
    addMessage,
    applyMouthValue: live2d.applyMouthValue,
    clamp,
    requestJson,
    responseToError,
    setConnectionState: status.setConnectionState,
    setRunStatus: status.setRunStatus,
    smoothValue,
});
const asr = createAsrModule({
    addSystemMessage,
    clamp,
    delay,
    ensureAudioContextReady: tts.ensureAudioContextReady,
    requestJson,
    responseToError,
    setRunStatus: status.setRunStatus,
    stopSpeechPlayback: tts.stopSpeechPlayback,
});

tts.bindHooks({
    updateVoiceInputControls: asr.updateVoiceInputControls,
});

const sessions = createSessionsModule({
    addMessage,
    addSystemMessage,
    clearMessages,
    formatTimestamp,
    requestJson,
    speakText: tts.speakText,
    setRunStatus: status.setRunStatus,
    stopSpeechPlayback: tts.stopSpeechPlayback,
});
const roles = createRolesModule({
    addMessage,
    requestJson,
    setRunStatus: status.setRunStatus,
});
const traces = createTraceModule();
const chat = createChatModule({
    addErrorMessage,
    addMessage,
    applySessionSummaries: sessions.applySessionSummaries,
    cancelAgentRun,
    createSpeechSession: tts.createSpeechSession,
    drainVoicePromptQueue: asr.drainVoicePromptQueue,
    deleteAttachment,
    ensureAudioContextReady: tts.ensureAudioContextReady,
    finalizeSpeechSession: tts.finalizeSpeechSession,
    queueSpeechSessionText: tts.queueSpeechSessionText,
    removeMessage,
    requestAgentRun,
    requestAgentRunEvents,
    requestChatStream,
    requestSessionSummaries: sessions.requestSessionSummaries,
    resetTracePanel: traces.resetTracePanel,
    setActiveAgentRun: status.setActiveAgentRun,
    setChatBusy: status.setChatBusy,
    setRunStatus: status.setRunStatus,
    speakText: tts.speakText,
    startTracePanel: traces.startTracePanel,
    stopSpeechPlayback: tts.stopSpeechPlayback,
    syncCurrentSessionFromServer: sessions.syncCurrentSessionFromServer,
    uploadChatFile,
    uploadChatImage,
    applyTracePayload: traces.applyTracePayload,
    updateMessage,
});

status.bindFeatures({
    asr,
    chat,
    roles,
    sessions,
});

sessions.bindRoleHooks({
    closeRoleEditor: roles.closeRoleEditor,
    syncRolePanelForCurrentSession: roles.syncRolePanelForCurrentSession,
});
roles.bindSessionHooks({
    applySessionDetail: sessions.applySessionDetail,
});

document.addEventListener("DOMContentLoaded", initializePage);

async function initializePage() {
    layout.ensureSidebarToggleButtons();
    layout.initializeLive2DDrawer();
    layout.initializePageSplit();
    initializeMessageInteractions();
    wireAppEvents({
        asr,
        chat,
        layout,
        live2d,
        llmProviders,
        roles,
        sessions,
        status,
        tts,
    });

    layout.restoreSettingsPanelState();
    layout.restoreCronPanelState();
    layout.restoreHeartbeatPanelState();
    layout.restoreLive2DPanelState();
    layout.restoreRuntimePanelState();
    layout.restoreStageBackgroundPanelState();
    layout.restoreStageEffectsPanelState();
    layout.handleSettingsPanelToggle();
    live2d.setStageMessage("正在加载 Live2D 模型…");
    addSystemMessage("正在连接 EchoBot…");

    try {
        const config = await requestJson("/api/web/config");
        appState.config = config;
        layout.applyRuntimeConfig(config.runtime);
        layout.applyLlmConfig(config.llm);
        llmProviders.maybeOpenFirstRun();
        const activeLive2DConfig = live2d.applyConfigToUI(config);

        live2d.initializePixiApplication();
        const live2dLoadPromise = live2d.loadLive2DModel(activeLive2DConfig);
        live2d.renderLive2DControls(activeLive2DConfig);
        await live2dLoadPromise;
        live2d.renderLive2DControls(appState.config.live2d);
        layout.restoreSessionSidebarState();
        layout.restoreRoleSidebarState();
        await sessions.initializeSessionPanel(config.session_id);
        await roles.initializeRolePanel();
        await tts.loadTtsOptions(config.tts);
        asr.applyAsrStatus(config.asr);
        asr.startAsrStatusPolling();
        traces.resetTracePanel();

        status.setConnectionState("ready", "已连接");
        status.setRunStatus("准备就绪");
        status.setActiveAgentRun("");
    } catch (error) {
        console.error(error);
        status.setConnectionState("error", "初始化失败");
        status.setRunStatus(error.message || "初始化失败");
        live2d.setStageMessage(error.message || "初始化失败");
        addSystemMessage(`初始化失败：${error.message || error}`);
    }
}
