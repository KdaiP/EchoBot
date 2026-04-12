import { DOM } from "../../core/dom.js";
import { live2dState } from "../../core/store.js";
import { readJson, removeStoredValue, writeJson } from "../../core/storage.js";
import { createStageBackgroundController } from "./backgrounds.js";
import { createLive2DConfigController } from "./config.js";
import { createLive2DControlsController } from "./controls.js";
import { createStageEffectsController } from "./effects.js";
import { createLive2DModelController } from "./model.js";
import { createLive2DSceneController } from "./scene.js";

export function createLive2DModule(deps) {
    const {
        clamp,
        onLive2DExpressionToggled,
        onLive2DModelLoaded,
        onLive2DMotionPlayed,
        onLive2DMouthValue,
        onLive2DMouseFollowChanged,
        requestJson,
        roundTo,
        responseToError,
        setRunStatus,
    } = deps;

    function setStageMessage(text) {
        const message = String(text || "").trim();
        if (!DOM.stageMessage) {
            return;
        }

        DOM.stageMessage.textContent = message;
        DOM.stageMessage.hidden = message === "";
    }

    let sceneController = null;

    const effectsController = createStageEffectsController({
        clamp,
        roundTo,
        setRunStatus,
        applyStageLightingVars(...args) {
            sceneController?.applyStageLightingVars(...args);
        },
        updateStageAtmosphereFrame(...args) {
            sceneController?.updateStageAtmosphereFrame(...args);
        },
    });

    const backgroundController = createStageBackgroundController({
        clamp,
        roundTo,
        responseToError,
        setRunStatus,
        applyStageEffectsToRuntime(...args) {
            effectsController.applyStageEffectsToRuntime(...args);
        },
    });

    const modelController = createLive2DModelController({
        clamp,
        roundTo,
        readJson,
        removeStoredValue,
        setStageMessage,
        writeJson,
    });

    const controlsController = createLive2DControlsController({
        getSelectionRuntimeState(...args) {
            return modelController.getSelectionRuntimeState(...args);
        },
        isExpressionActive(...args) {
            return modelController.isExpressionActive(...args);
        },
        async playMotion(...args) {
            const [motionItem, selectionKey = ""] = args;
            const result = await modelController.playMotion(...args);
            if (typeof onLive2DMotionPlayed === "function") {
                onLive2DMotionPlayed({
                    motionItem,
                    selectionKey,
                    result,
                });
            }
            return result;
        },
        requestJson,
        setRunStatus,
        async toggleExpression(...args) {
            const [expressionItem, selectionKey = ""] = args;
            const result = await modelController.toggleExpression(...args);
            if (typeof onLive2DExpressionToggled === "function") {
                onLive2DExpressionToggled({
                    expressionItem,
                    selectionKey,
                    result,
                });
            }
            return result;
        },
        triggerHotkey(...args) {
            return modelController.triggerHotkey(...args);
        },
    });

    sceneController = createLive2DSceneController({
        clamp,
        roundTo,
        applyStageEffectsSettings(...args) {
            effectsController.applyStageEffectsSettings(...args);
        },
        applyStageBackgroundTransform(...args) {
            backgroundController.applyStageBackgroundTransform(...args);
        },
        currentStageBackgroundOption(...args) {
            return backgroundController.currentStageBackgroundOption(...args);
        },
        refreshLive2DFocusFromLastPointer(...args) {
            modelController.refreshLive2DFocusFromLastPointer(...args);
        },
        syncPixiStageBackground(...args) {
            return backgroundController.syncPixiStageBackground(...args);
        },
    });

    async function loadLive2DModelWithHooks(live2dConfig) {
        const didLoadModel = await modelController.loadLive2DModel(live2dConfig);
        if (didLoadModel && typeof onLive2DModelLoaded === "function") {
            onLive2DModelLoaded({
                live2dConfig,
            });
        }
        return didLoadModel;
    }

    const configController = createLive2DConfigController({
        requestJson,
        responseToError,
        setRunStatus,
        setStageMessage,
        applyLive2DMouseFollowSetting(...args) {
            modelController.applyLive2DMouseFollowSetting(...args);
        },
        applyStageBackgroundByKey(...args) {
            backgroundController.applyStageBackgroundByKey(...args);
        },
        applyStageEffectsSettings(...args) {
            effectsController.applyStageEffectsSettings(...args);
        },
        buildStageConfig(...args) {
            return backgroundController.normalizeStageConfig(...args);
        },
        loadLive2DModel(...args) {
            return loadLive2DModelWithHooks(...args);
        },
        loadSavedStageEffectsSettings(...args) {
            return effectsController.loadSavedStageEffectsSettings(...args);
        },
        resetLive2DHotkeyState(...args) {
            return controlsController.resetHotkeyState(...args);
        },
        renderLive2DControls(...args) {
            return controlsController.renderLive2DControls(...args);
        },
        renderStageBackgroundOptions(...args) {
            backgroundController.renderStageBackgroundOptions(...args);
        },
        resolveInitialStageBackgroundKey(...args) {
            return backgroundController.resolveInitialStageBackgroundKey(...args);
        },
    });

    return {
        applyConfigToUI: configController.applyConfigToUI,
        applyLive2DMouseFollowSetting: modelController.applyLive2DMouseFollowSetting,
        applyExternalFocusPoint: modelController.applyExternalFocusPoint,
        applyMouthValue(live2dConfig, value) {
            modelController.applyMouthValue(live2dConfig, value);
            if (typeof onLive2DMouthValue === "function") {
                onLive2DMouthValue({
                    value,
                });
            }
        },
        handleLive2DDirectoryUpload: configController.handleLive2DDirectoryUpload,
        handleLive2DModelChange: configController.handleLive2DModelChange,
        handleLive2DControlsClick: controlsController.handleControlsClick,
        handleLive2DHotkeyKeyDown: controlsController.handleWindowKeyDown,
        handleLive2DHotkeyKeyUp: controlsController.handleWindowKeyUp,
        handleLive2DHotkeyWindowBlur: controlsController.handleWindowBlur,
        handleHotkeysToggle: configController.handleHotkeysToggle,
        handleMouseFollowToggle(...args) {
            configController.handleMouseFollowToggle(...args);
            if (typeof onLive2DMouseFollowChanged === "function") {
                onLive2DMouseFollowChanged({
                    enabled: live2dState.live2dMouseFollowEnabled,
                });
            }
        },
        handleStageBackgroundChange: backgroundController.handleStageBackgroundChange,
        handleStageBackgroundReset: backgroundController.handleStageBackgroundReset,
        handleStageBackgroundTransformInput: backgroundController.handleStageBackgroundTransformInput,
        handleStageBackgroundTransformReset: backgroundController.handleStageBackgroundTransformReset,
        handleStageBackgroundUpload: backgroundController.handleStageBackgroundUpload,
        handleStageEffectsInput: effectsController.handleStageEffectsInput,
        handleStageEffectsReset: effectsController.handleStageEffectsReset,
        handleStageWheel: modelController.handleStageWheel,
        initializePixiApplication: sceneController.initializePixiApplication,
        isExpressionActive: modelController.isExpressionActive,
        loadLive2DModel: loadLive2DModelWithHooks,
        playMotion: modelController.playMotion,
        renderLive2DControls: controlsController.renderLive2DControls,
        resetLive2DViewToDefault: modelController.resetLive2DViewToDefault,
        setLive2DMouseFollowEnabled(enabled) {
            live2dState.live2dMouseFollowEnabled = Boolean(enabled);
            modelController.applyLive2DMouseFollowSetting();
        },
        setDesktopCursorOverlapFromStagePoint: modelController.setDesktopCursorOverlapFromStagePoint,
        setStageMessage,
        toggleExpression: modelController.toggleExpression,
    };
}
