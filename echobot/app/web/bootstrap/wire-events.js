import { DOM } from "../core/dom.js";
import { audioState, chatState, panelState } from "../core/store.js";

export function wireAppEvents(features) {
    const {
        asr,
        chat,
        layout,
        live2d,
        roles,
        sessions,
        tts,
        status,
    } = features;
    const form = document.getElementById("chat-form");
    form.addEventListener("submit", chat.handleChatSubmit);

    DOM.promptInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
            if (chatState.chatBusy) {
                return;
            }
            event.preventDefault();
            form.requestSubmit();
        }
    });
    DOM.promptInput.addEventListener("paste", (event) => {
        void chat.handlePromptPaste(event);
    });
    window.addEventListener("dragenter", (event) => {
        void chat.handleWindowFileDragEnter(event);
    });
    window.addEventListener("dragover", (event) => {
        void chat.handleWindowFileDragOver(event);
    });
    window.addEventListener("dragleave", (event) => {
        void chat.handleWindowFileDragLeave(event);
    });
    window.addEventListener("drop", (event) => {
        void chat.handleWindowFileDrop(event);
    });

    bindOptionalClick(DOM.composerFileButton, chat.handleComposerFileButtonClick);
    bindOptionalInputChange(DOM.composerFileInput, chat.handleComposerFileInputChange);
    bindOptionalClickHandler(DOM.composerFiles, chat.handleComposerFilesClick);
    bindOptionalClick(DOM.composerImageButton, chat.handleComposerImageButtonClick);
    bindOptionalInputChange(DOM.composerImageInput, chat.handleComposerImageInputChange);
    bindOptionalClickHandler(DOM.composerImages, chat.handleComposerImagesClick);

    DOM.autoTtsCheckbox.addEventListener("change", () => {
        audioState.ttsEnabled = DOM.autoTtsCheckbox.checked;
        if (!audioState.ttsEnabled) {
            tts.stopSpeechPlayback();
        }
        asr.updateVoiceInputControls();
    });

    bindOptionalChange(DOM.live2dHotkeysCheckbox, live2d.handleHotkeysToggle);
    bindOptionalChange(DOM.live2dMouseFollowCheckbox, live2d.handleMouseFollowToggle);
    [
        DOM.live2dExpressionList,
        DOM.live2dMotionList,
        DOM.live2dHotkeyList,
    ].forEach((element) => {
        bindOptionalClickHandler(element, live2d.handleLive2DControlsClick);
        bindOptionalEventHandler(element, "input", live2d.handleLive2DControlsInput);
        bindOptionalEventHandler(element, "keydown", live2d.handleLive2DControlsKeyDown);
        bindOptionalEventHandler(element, "focusin", live2d.handleLive2DControlsFocusIn);
        bindOptionalEventHandler(element, "focusout", live2d.handleLive2DControlsFocusOut);
    });
    DOM.voiceSelect.addEventListener("change", tts.handleVoiceSelectionChange);
    bindOptionalAsyncChange(DOM.ttsProviderSelect, tts.handleTtsProviderChange);
    bindOptionalAsyncChange(DOM.asrProviderSelect, asr.handleAsrProviderChange);
    bindOptionalAsyncChange(DOM.modelSelect, () => live2d.handleLive2DModelChange(DOM.modelSelect.value));
    bindOptionalClick(DOM.live2dUploadButton, () => DOM.live2dUploadInput?.click());
    bindOptionalInputChange(DOM.live2dUploadInput, live2d.handleLive2DDirectoryUpload);
    bindOptionalToggle(DOM.live2dPanel, layout.handleLive2DPanelToggle);
    bindOptionalToggle(DOM.runtimePanel, layout.handleRuntimePanelToggle);
    bindOptionalClick(DOM.live2dDrawerToggle, () => {
        layout.setLive2DDrawerOpen(!panelState.live2dDrawerOpen);
    });
    bindOptionalClick(DOM.live2dDrawerClose, () => layout.setLive2DDrawerOpen(false));
    bindOptionalClick(DOM.live2dDrawerBackdrop, () => layout.setLive2DDrawerOpen(false));
    bindOptionalClick(DOM.live2dTabExpression, () => layout.setLive2DDrawerTab("expression"));
    bindOptionalClick(DOM.live2dTabMotion, () => layout.setLive2DDrawerTab("motion"));
    bindOptionalClick(DOM.live2dTabHotkey, () => layout.setLive2DDrawerTab("hotkey"));

    bindOptionalAsyncChange(DOM.stageBackgroundSelect, () => {
        live2d.handleStageBackgroundChange(DOM.stageBackgroundSelect.value);
    });
    bindOptionalClick(DOM.stageBackgroundUploadButton, () => DOM.stageBackgroundUploadInput?.click());
    bindOptionalInputChange(DOM.stageBackgroundUploadInput, live2d.handleStageBackgroundUpload);
    bindOptionalClick(DOM.stageBackgroundResetButton, live2d.handleStageBackgroundReset);
    bindRangeInput(DOM.stageBackgroundPositionXInput, live2d.handleStageBackgroundTransformInput);
    bindRangeInput(DOM.stageBackgroundPositionYInput, live2d.handleStageBackgroundTransformInput);
    bindRangeInput(DOM.stageBackgroundScaleInput, live2d.handleStageBackgroundTransformInput);
    bindOptionalClick(DOM.stageBackgroundTransformResetButton, live2d.handleStageBackgroundTransformReset);
    bindOptionalToggle(DOM.stageBackgroundPanel, layout.handleStageBackgroundPanelToggle);
    bindOptionalToggle(DOM.stageEffectsPanel, layout.handleStageEffectsPanelToggle);

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

    bindOptionalClick(DOM.stageEffectsResetButton, (event) => {
        layout.stopSummaryButtonToggle(event);
        live2d.handleStageEffectsReset();
    });
    bindOptionalAsyncChange(DOM.roleSelect, roles.handleRoleSelectionChange);
    bindOptionalAsyncChange(DOM.routeModeSelect, sessions.handleRouteModeChange);
    bindOptionalAsyncChange(DOM.delegatedAckCheckbox, layout.handleDelegatedAckToggle);
    bindOptionalAsyncChange(DOM.fileWriteEnabledCheckbox, layout.handleFileWriteToggle);
    bindOptionalAsyncChange(DOM.cronMutationEnabledCheckbox, layout.handleCronMutationToggle);
    bindOptionalAsyncChange(
        DOM.webPrivateNetworkEnabledCheckbox,
        layout.handleWebPrivateNetworkToggle,
    );
    bindOptionalAsyncChange(DOM.shellSafetyModeSelect, layout.handleShellSafetyModeChange);
    bindOptionalClick(DOM.runtimeResetButton, (event) => {
        layout.stopSummaryButtonToggle(event);
        layout.handleRuntimeReset();
    });

    bindOptionalClick(DOM.roleSidebarToggle, (event) => {
        layout.stopSummaryButtonToggle(event);
        layout.setRoleSidebarOpen(!panelState.roleSidebarOpen);
    });
    bindOptionalClick(DOM.roleSidebarClose, () => layout.setRoleSidebarOpen(false));
    bindOptionalClick(DOM.roleSidebarBackdrop, () => layout.setRoleSidebarOpen(false));
    bindOptionalClick(DOM.roleRefreshButton, roles.refreshRolePanel);
    bindOptionalClick(DOM.roleNewButton, () => roles.openRoleEditor("create"));
    bindOptionalClick(DOM.roleEditButton, roles.handleEditRoleClick);
    bindOptionalClick(DOM.roleDeleteButton, roles.handleDeleteRoleClick);
    bindOptionalClick(DOM.roleSaveButton, roles.handleSaveRoleClick);
    bindOptionalClick(DOM.roleCancelButton, roles.closeRoleEditor);

    bindOptionalClick(DOM.sessionSidebarToggle, (event) => {
        layout.stopSummaryButtonToggle(event);
        layout.setSessionSidebarOpen(!panelState.sessionSidebarOpen);
    });
    bindOptionalClick(DOM.sessionSidebarClose, () => layout.setSessionSidebarOpen(false));
    bindOptionalClick(DOM.sessionSidebarBackdrop, () => layout.setSessionSidebarOpen(false));
    bindOptionalClick(DOM.sessionCreateButton, sessions.handleCreateSession);
    bindOptionalClick(DOM.sessionRefreshButton, sessions.refreshSessionList);
    bindOptionalClickHandler(DOM.sessionList, sessions.handleSessionListClick);

    DOM.resetViewButton.addEventListener("click", () => {
        live2d.resetLive2DViewToDefault();
        status.setRunStatus("已重置模型位置");
    });
    DOM.stopAudioButton.addEventListener("click", () => {
        tts.stopSpeechPlayback();
        status.setRunStatus("已停止语音");
    });
    bindOptionalClick(DOM.stopAgentButton, chat.handleStopBackgroundJob);
    bindOptionalClick(DOM.recordButton, asr.handleRecordButtonClick);
    bindOptionalAsyncChange(DOM.alwaysListenCheckbox, asr.handleAlwaysListenToggle);
    bindOptionalToggle(DOM.cronPanel, layout.handleCronPanelToggle);
    bindOptionalToggle(DOM.settingsPanel, layout.handleSettingsPanelToggle);
    bindOptionalClick(DOM.cronRefreshButton, layout.refreshCronPanel);
    bindOptionalToggle(DOM.heartbeatPanel, layout.handleHeartbeatPanelToggle);
    bindOptionalClick(DOM.heartbeatRefreshButton, () => layout.refreshHeartbeatPanel({ force: true }));
    bindOptionalClick(DOM.heartbeatSaveButton, layout.saveHeartbeat);
    if (DOM.heartbeatInput) {
        DOM.heartbeatInput.addEventListener("input", layout.handleHeartbeatInputChange);
    }

    DOM.stageElement.addEventListener("wheel", live2d.handleStageWheel, { passive: false });
    window.addEventListener("keydown", (event) => {
        live2d.handleLive2DHotkeyKeyDown(event);
    });
    window.addEventListener("keyup", (event) => {
        live2d.handleLive2DHotkeyKeyUp(event);
    });
    window.addEventListener("blur", live2d.handleLive2DHotkeyWindowBlur);

    document.body.addEventListener("pointerdown", () => {
        void tts.ensureAudioContextReady();
    });
    window.addEventListener("beforeunload", asr.handleBeforeUnload);
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

function bindOptionalAsyncChange(element, handler) {
    if (!element) {
        return;
    }

    element.addEventListener("change", () => {
        void handler();
    });
}

function bindOptionalChange(element, handler) {
    if (element) {
        element.addEventListener("change", handler);
    }
}

function bindOptionalClick(element, handler) {
    if (element) {
        element.addEventListener("click", (event) => {
            void handler(event);
        });
    }
}

function bindOptionalClickHandler(element, handler) {
    if (element) {
        element.addEventListener("click", (event) => {
            void handler(event);
        });
    }
}

function bindOptionalEventHandler(element, eventName, handler) {
    if (element) {
        element.addEventListener(eventName, (event) => {
            void handler(event);
        });
    }
}

function bindOptionalInputChange(element, handler) {
    if (element) {
        element.addEventListener("change", () => {
            void handler();
        });
    }
}

function bindOptionalToggle(element, handler) {
    if (element) {
        element.addEventListener("toggle", handler);
    }
}

function bindRangeInput(element, handler) {
    if (!element) {
        return;
    }

    element.addEventListener("input", () => {
        handler();
    });
    addSliderResetOnAltClick(element, handler);
}
