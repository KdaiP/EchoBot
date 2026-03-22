import {
    cancelChatJob,
    requestChatJob,
    requestChatJobTrace,
    requestChatStream,
    requestJson,
    responseToError,
} from "./modules/api.js";
import { createAsrModule } from "./modules/asr.js";
import { createChatModule } from "./modules/chat.js";
import { createLayoutModule } from "./modules/layout.js";
import { createLive2DModule } from "./modules/live2d.js";
import {
    addMessage,
    addSystemMessage,
    clearMessages,
    initializeMessageInteractions,
    removeMessage,
    scheduleMessagesScrollToBottom,
    updateMessage,
} from "./modules/messages.js";
import { createRolesModule } from "./modules/roles.js";
import { createSessionsModule } from "./modules/sessions.js";
import { DOM, UI_STATE } from "./modules/state.js";
import { createTraceModule } from "./modules/traces.js";
import { createTtsModule } from "./modules/tts.js";
import {
    clamp,
    delay,
    formatTimestamp,
    normalizeSessionName,
    roundTo,
    smoothValue,
} from "./modules/utils.js";
import { createVideoCallIntegration } from "./modules/video-call-integration.js";

const layout = createLayoutModule({
    addMessage: addMessage,
    formatTimestamp: formatTimestamp,
    requestJson: requestJson,
    setRunStatus: setRunStatus,
});

const live2d = createLive2DModule({
    clamp: clamp,
    roundTo: roundTo,
    responseToError: responseToError,
    setRunStatus: setRunStatus,
});

const tts = createTtsModule({
    addMessage: addMessage,
    applyMouthValue: live2d.applyMouthValue,
    clamp: clamp,
    requestJson: requestJson,
    responseToError: responseToError,
    setConnectionState: setConnectionState,
    setRunStatus: setRunStatus,
    smoothValue: smoothValue,
});

const asr = createAsrModule({
    addSystemMessage: addSystemMessage,
    clamp: clamp,
    delay: delay,
    ensureAudioContextReady: tts.ensureAudioContextReady,
    requestJson: requestJson,
    responseToError: responseToError,
    setRunStatus: setRunStatus,
});

tts.bindHooks({
    updateVoiceInputControls: asr.updateVoiceInputControls,
});

const sessions = createSessionsModule({
    addMessage: addMessage,
    addSystemMessage: addSystemMessage,
    clearMessages: clearMessages,
    formatTimestamp: formatTimestamp,
    normalizeSessionName: normalizeSessionName,
    requestJson: requestJson,
    speakText: tts.speakText,
    setRunStatus: setRunStatus,
    stopSpeechPlayback: tts.stopSpeechPlayback,
});

const roles = createRolesModule({
    addMessage: addMessage,
    normalizeSessionName: normalizeSessionName,
    requestJson: requestJson,
    setRunStatus: setRunStatus,
});

const traces = createTraceModule();

sessions.bindRoleHooks({
    closeRoleEditor: roles.closeRoleEditor,
    syncRolePanelForCurrentSession: roles.syncRolePanelForCurrentSession,
});

roles.bindSessionHooks({
    applySessionDetail: sessions.applySessionDetail,
});

const chat = createChatModule({
    addMessage: addMessage,
    applySessionSummaries: sessions.applySessionSummaries,
    cancelChatJob: cancelChatJob,
    createSpeechSession: tts.createSpeechSession,
    drainVoicePromptQueue: asr.drainVoicePromptQueue,
    ensureAudioContextReady: tts.ensureAudioContextReady,
    finalizeSpeechSession: tts.finalizeSpeechSession,
    normalizeSessionName: normalizeSessionName,
    queueSpeechSessionText: tts.queueSpeechSessionText,
    removeMessage: removeMessage,
    requestChatJob: requestChatJob,
    requestChatJobTrace: requestChatJobTrace,
    requestChatStream: requestChatStream,
    resetTracePanel: traces.resetTracePanel,
    syncCurrentSessionFromServer: sessions.syncCurrentSessionFromServer,
    requestSessionSummaries: sessions.requestSessionSummaries,
    applyTracePayload: traces.applyTracePayload,
    setActiveBackgroundJob: setActiveBackgroundJob,
    setChatBusy: setChatBusy,
    setRunStatus: setRunStatus,
    speakText: tts.speakText,
    startTracePanel: traces.startTracePanel,
    stopSpeechPlayback: tts.stopSpeechPlayback,
    updateMessage: updateMessage,
});

const videoCall = createVideoCallIntegration({
    addSystemMessage: addSystemMessage,
    requestJson: requestJson,
});

document.addEventListener("DOMContentLoaded", initializePage);

async function initializePage() {
    layout.ensureSidebarToggleButtons();
    wireBasicEvents();
    layout.restoreSettingsPanelState();
    layout.restoreCronPanelState();
    layout.restoreHeartbeatPanelState();
    layout.restoreLive2DPanelState();
    layout.restoreStageBackgroundPanelState();
    layout.restoreStageEffectsPanelState();
    layout.handleSettingsPanelToggle();
    live2d.setStageMessage("正在加载 Live2D 模型…");
    addSystemMessage("正在连接 EchoBot…");

    try {
        const config = await requestJson("/api/web/config");
        UI_STATE.config = config;
        layout.applyRuntimeConfig(config.runtime);
        const activeLive2DConfig = live2d.applyConfigToUI(config);

        live2d.initializePixiApplication();
        await live2d.loadLive2DModel(activeLive2DConfig);
        layout.restoreSessionSidebarState();
        layout.restoreRoleSidebarState();
        await sessions.initializeSessionPanel(config.session_name);
        await roles.initializeRolePanel();
        await tts.loadTtsOptions(config.tts);
        asr.applyAsrStatus(config.asr);
        asr.startAsrStatusPolling();
        traces.resetTracePanel();
        videoCall.initialize();

        setConnectionState("ready", "已连接");
        setRunStatus("准备就绪");
        setActiveBackgroundJob("");
    } catch (error) {
        console.error(error);
        setConnectionState("error", "初始化失败");
        setRunStatus(error.message || "初始化失败");
        live2d.setStageMessage(error.message || "初始化失败");
        addSystemMessage(`初始化失败：${error.message || error}`);
    }
}

function addSliderResetOnAltClick(element, onReset) {
    element.addEventListener("mousedown", (event) => {
        if (event.altKey || event.ctrlKey) {
            event.preventDefault();
            element.value = element.defaultValue;
            onReset();
        }
    });
}

function wireBasicEvents() {
    const form = document.getElementById("chat-form");
    form.addEventListener("submit", chat.handleChatSubmit);

    DOM.promptInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
            if (UI_STATE.chatBusy) {
                return;
            }
            event.preventDefault();
            form.requestSubmit();
        }
    });
    if (DOM.composerImageButton) {
        DOM.composerImageButton.addEventListener("click", () => {
            chat.handleComposerImageButtonClick();
        });
    }
    if (DOM.composerImageInput) {
        DOM.composerImageInput.addEventListener("change", () => {
            void chat.handleComposerImageInputChange();
        });
    }
    if (DOM.composerImages) {
        DOM.composerImages.addEventListener("click", (event) => {
            chat.handleComposerImagesClick(event);
        });
    }
    initializeMessageInteractions();

    DOM.autoTtsCheckbox.addEventListener("change", () => {
        UI_STATE.ttsEnabled = DOM.autoTtsCheckbox.checked;
        if (!UI_STATE.ttsEnabled) {
            tts.stopSpeechPlayback();
        }
        asr.updateVoiceInputControls();
    });

    if (DOM.live2dMouseFollowCheckbox) {
        DOM.live2dMouseFollowCheckbox.addEventListener("change", live2d.handleMouseFollowToggle);
    }

    DOM.voiceSelect.addEventListener("change", tts.handleVoiceSelectionChange);
    if (DOM.ttsProviderSelect) {
        DOM.ttsProviderSelect.addEventListener("change", () => {
            void tts.handleTtsProviderChange();
        });
    }

    if (DOM.modelSelect) {
        DOM.modelSelect.addEventListener("change", () => {
            void live2d.handleLive2DModelChange(DOM.modelSelect.value);
        });
    }
    if (DOM.live2dUploadButton) {
        DOM.live2dUploadButton.addEventListener("click", () => {
            if (DOM.live2dUploadInput) {
                DOM.live2dUploadInput.click();
            }
        });
    }
    if (DOM.live2dUploadInput) {
        DOM.live2dUploadInput.addEventListener("change", () => {
            void live2d.handleLive2DDirectoryUpload();
        });
    }
    if (DOM.live2dPanel) {
        DOM.live2dPanel.addEventListener("toggle", () => {
            layout.handleLive2DPanelToggle();
        });
    }
    if (DOM.stageBackgroundSelect) {
        DOM.stageBackgroundSelect.addEventListener("change", () => {
            live2d.handleStageBackgroundChange(DOM.stageBackgroundSelect.value);
        });
    }
    if (DOM.stageBackgroundUploadButton) {
        DOM.stageBackgroundUploadButton.addEventListener("click", () => {
            if (DOM.stageBackgroundUploadInput) {
                DOM.stageBackgroundUploadInput.click();
            }
        });
    }
    if (DOM.stageBackgroundUploadInput) {
        DOM.stageBackgroundUploadInput.addEventListener("change", () => {
            void live2d.handleStageBackgroundUpload();
        });
    }
    if (DOM.stageBackgroundResetButton) {
        DOM.stageBackgroundResetButton.addEventListener("click", () => {
            live2d.handleStageBackgroundReset();
        });
    }
    if (DOM.stageBackgroundPositionXInput) {
        DOM.stageBackgroundPositionXInput.addEventListener("input", () => {
            live2d.handleStageBackgroundTransformInput();
        });
        addSliderResetOnAltClick(DOM.stageBackgroundPositionXInput, live2d.handleStageBackgroundTransformInput);
    }
    if (DOM.stageBackgroundPositionYInput) {
        DOM.stageBackgroundPositionYInput.addEventListener("input", () => {
            live2d.handleStageBackgroundTransformInput();
        });
        addSliderResetOnAltClick(DOM.stageBackgroundPositionYInput, live2d.handleStageBackgroundTransformInput);
    }
    if (DOM.stageBackgroundScaleInput) {
        DOM.stageBackgroundScaleInput.addEventListener("input", () => {
            live2d.handleStageBackgroundTransformInput();
        });
        addSliderResetOnAltClick(DOM.stageBackgroundScaleInput, live2d.handleStageBackgroundTransformInput);
    }
    if (DOM.stageBackgroundTransformResetButton) {
        DOM.stageBackgroundTransformResetButton.addEventListener("click", () => {
            live2d.handleStageBackgroundTransformReset();
        });
    }
    if (DOM.stageBackgroundPanel) {
        DOM.stageBackgroundPanel.addEventListener("toggle", () => {
            layout.handleStageBackgroundPanelToggle();
        });
    }
    if (DOM.stageEffectsPanel) {
        DOM.stageEffectsPanel.addEventListener("toggle", () => {
            layout.handleStageEffectsPanelToggle();
        });
    }
    [
        DOM.stageEffectsEnabledCheckbox,
        DOM.stageEffectsBackgroundBlurCheckbox,
        DOM.stageEffectsLightEnabledCheckbox,
        DOM.stageEffectsLightFloatCheckbox,
        DOM.stageEffectsParticlesEnabledCheckbox,
    ].forEach((element) => {
        if (element) {
            element.addEventListener("change", () => {
                live2d.handleStageEffectsInput();
            });
        }
    });
    [
        DOM.stageEffectsBackgroundBlurInput,
        DOM.stageEffectsLightXInput,
        DOM.stageEffectsLightYInput,
        DOM.stageEffectsGlowInput,
        DOM.stageEffectsVignetteInput,
        DOM.stageEffectsGrainInput,
        DOM.stageEffectsParticleDensityInput,
        DOM.stageEffectsParticleOpacityInput,
        DOM.stageEffectsParticleSizeInput,
        DOM.stageEffectsParticleSpeedInput,
        DOM.stageEffectsHueInput,
        DOM.stageEffectsSaturationInput,
        DOM.stageEffectsContrastInput,
    ].forEach((element) => {
        if (element) {
            element.addEventListener("input", () => {
                live2d.handleStageEffectsInput();
            });
            addSliderResetOnAltClick(element, live2d.handleStageEffectsInput);
        }
    });
    if (DOM.stageEffectsResetButton) {
        DOM.stageEffectsResetButton.addEventListener("click", (event) => {
            layout.stopSummaryButtonToggle(event);
            live2d.handleStageEffectsReset();
        });
    }
    if (DOM.roleSelect) {
        DOM.roleSelect.addEventListener("change", () => {
            void roles.handleRoleSelectionChange();
        });
    }
    if (DOM.routeModeSelect) {
        DOM.routeModeSelect.addEventListener("change", () => {
            void sessions.handleRouteModeChange();
        });
    }
    if (DOM.delegatedAckCheckbox) {
        DOM.delegatedAckCheckbox.addEventListener("change", () => {
            void layout.handleDelegatedAckToggle();
        });
    }
    if (DOM.roleSidebarToggle) {
        DOM.roleSidebarToggle.addEventListener("click", (event) => {
            layout.stopSummaryButtonToggle(event);
            layout.setRoleSidebarOpen(!UI_STATE.roleSidebarOpen);
        });
    }
    if (DOM.roleSidebarClose) {
        DOM.roleSidebarClose.addEventListener("click", () => {
            layout.setRoleSidebarOpen(false);
        });
    }
    if (DOM.roleSidebarBackdrop) {
        DOM.roleSidebarBackdrop.addEventListener("click", () => {
            layout.setRoleSidebarOpen(false);
        });
    }
    if (DOM.roleRefreshButton) {
        DOM.roleRefreshButton.addEventListener("click", () => {
            void roles.refreshRolePanel();
        });
    }
    if (DOM.roleNewButton) {
        DOM.roleNewButton.addEventListener("click", () => {
            roles.openRoleEditor("create");
        });
    }
    if (DOM.roleEditButton) {
        DOM.roleEditButton.addEventListener("click", () => {
            void roles.handleEditRoleClick();
        });
    }
    if (DOM.roleDeleteButton) {
        DOM.roleDeleteButton.addEventListener("click", () => {
            void roles.handleDeleteRoleClick();
        });
    }
    if (DOM.roleSaveButton) {
        DOM.roleSaveButton.addEventListener("click", () => {
            void roles.handleSaveRoleClick();
        });
    }
    if (DOM.roleCancelButton) {
        DOM.roleCancelButton.addEventListener("click", roles.closeRoleEditor);
    }

    if (DOM.sessionSidebarToggle) {
        DOM.sessionSidebarToggle.addEventListener("click", (event) => {
            layout.stopSummaryButtonToggle(event);
            layout.setSessionSidebarOpen(!UI_STATE.sessionSidebarOpen);
        });
    }
    if (DOM.sessionSidebarClose) {
        DOM.sessionSidebarClose.addEventListener("click", () => {
            layout.setSessionSidebarOpen(false);
        });
    }
    if (DOM.sessionSidebarBackdrop) {
        DOM.sessionSidebarBackdrop.addEventListener("click", () => {
            layout.setSessionSidebarOpen(false);
        });
    }
    if (DOM.sessionCreateButton) {
        DOM.sessionCreateButton.addEventListener("click", () => {
            void sessions.handleCreateSession();
        });
    }
    if (DOM.sessionRefreshButton) {
        DOM.sessionRefreshButton.addEventListener("click", () => {
            void sessions.refreshSessionList();
        });
    }
    if (DOM.sessionList) {
        DOM.sessionList.addEventListener("click", (event) => {
            void sessions.handleSessionListClick(event);
        });
    }

    DOM.resetViewButton.addEventListener("click", () => {
        live2d.resetLive2DViewToDefault();
        setRunStatus("已重置模型位置");
    });

    DOM.stopAudioButton.addEventListener("click", () => {
        tts.stopSpeechPlayback();
        setRunStatus("已停止语音");
    });
    if (DOM.stopAgentButton) {
        DOM.stopAgentButton.addEventListener("click", () => {
            void chat.handleStopBackgroundJob();
        });
    }

    if (DOM.recordButton) {
        DOM.recordButton.addEventListener("click", () => {
            void asr.handleRecordButtonClick();
        });
    }
    if (DOM.alwaysListenCheckbox) {
        DOM.alwaysListenCheckbox.addEventListener("change", () => {
            void asr.handleAlwaysListenToggle();
        });
    }

    if (DOM.cronPanel) {
        DOM.cronPanel.addEventListener("toggle", layout.handleCronPanelToggle);
    }
    if (DOM.settingsPanel) {
        DOM.settingsPanel.addEventListener("toggle", layout.handleSettingsPanelToggle);
    }
    if (DOM.cronRefreshButton) {
        DOM.cronRefreshButton.addEventListener("click", () => {
            void layout.refreshCronPanel();
        });
    }
    if (DOM.heartbeatPanel) {
        DOM.heartbeatPanel.addEventListener("toggle", layout.handleHeartbeatPanelToggle);
    }
    if (DOM.heartbeatRefreshButton) {
        DOM.heartbeatRefreshButton.addEventListener("click", () => {
            void layout.refreshHeartbeatPanel({ force: true });
        });
    }
    if (DOM.heartbeatSaveButton) {
        DOM.heartbeatSaveButton.addEventListener("click", () => {
            void layout.saveHeartbeat();
        });
    }
    if (DOM.heartbeatInput) {
        DOM.heartbeatInput.addEventListener("input", layout.handleHeartbeatInputChange);
    }

    DOM.stageElement.addEventListener(
        "wheel",
        live2d.handleStageWheel,
        { passive: false },
    );

    document.body.addEventListener("pointerdown", () => {
        void tts.ensureAudioContextReady();
    });
    window.addEventListener("beforeunload", asr.handleBeforeUnload);
}

function setChatBusy(isBusy) {
    UI_STATE.chatBusy = isBusy;
    if (DOM.sendButton) {
        DOM.sendButton.disabled = isBusy;
    }
    if (DOM.composerImageButton) {
        DOM.composerImageButton.disabled = isBusy || Boolean(UI_STATE.activeChatJobId);
    }
    if (DOM.composerImageInput) {
        DOM.composerImageInput.disabled = isBusy || Boolean(UI_STATE.activeChatJobId);
    }
    if (DOM.sessionCreateButton) {
        DOM.sessionCreateButton.disabled = isBusy || UI_STATE.sessionLoading;
    }
    if (DOM.sessionRefreshButton) {
        DOM.sessionRefreshButton.disabled = isBusy || UI_STATE.sessionLoading;
    }
    sessions.renderSessionList(UI_STATE.sessions);
    roles.updateRoleActionState();
    asr.updateVoiceInputControls();
    if (DOM.routeModeSelect) {
        DOM.routeModeSelect.disabled = (
            isBusy
            || UI_STATE.sessionLoading
            || Boolean(UI_STATE.activeChatJobId)
        );
    }
    updateComposerBackgroundJobState();
    chat.refreshComposerImages();
}

function setActiveBackgroundJob(jobId) {
    UI_STATE.activeChatJobId = String(jobId || "").trim();
    updateComposerBackgroundJobState();
}

function setConnectionState(kind, text) {
    DOM.connectionBadge.className = `status-badge status-${kind}`;
    DOM.connectionBadge.textContent = text;
}

function setRunStatus(text) {
    DOM.runStatus.textContent = text;
}

function updateComposerBackgroundJobState() {
    const backgroundJobRunning = Boolean(UI_STATE.activeChatJobId);

    if (DOM.promptInput) {
        DOM.promptInput.disabled = backgroundJobRunning;
    }
    if (DOM.composerImageButton) {
        DOM.composerImageButton.disabled = backgroundJobRunning || UI_STATE.chatBusy;
    }
    if (DOM.composerImageInput) {
        DOM.composerImageInput.disabled = backgroundJobRunning || UI_STATE.chatBusy;
    }
    if (DOM.composerStatusBanner) {
        DOM.composerStatusBanner.hidden = !backgroundJobRunning;
    }
    if (DOM.stopAgentButton) {
        DOM.stopAgentButton.disabled = !backgroundJobRunning;
        DOM.stopAgentButton.classList.toggle("is-active", backgroundJobRunning);
    }
    if (DOM.routeModeSelect) {
        DOM.routeModeSelect.disabled = (
            backgroundJobRunning
            || UI_STATE.chatBusy
            || UI_STATE.sessionLoading
        );
    }

    // Keep the latest reply visible when the composer height changes.
    scheduleMessagesScrollToBottom();
    chat.refreshComposerImages();
}
