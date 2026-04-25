import { createLive2DControlsPersistence } from "./controls/persistence.js";
import { createLive2DControlsRenderer } from "./controls/render.js";
import { createLive2DControlRuntime } from "./controls/runtime.js";

export function createLive2DControlsController(deps) {
    const controllerState = {
        pressedTokens: new Set(),
        activeHotkeyIds: new Set(),
        annotationDrafts: new Map(),
        annotationSaveStates: new Map(),
        hotkeyDrafts: new Map(),
        hotkeySaveStates: new Map(),
    };

    const persistence = createLive2DControlsPersistence({
        controllerState: controllerState,
        requestJson: deps.requestJson,
        setRunStatus: deps.setRunStatus,
    });

    const renderer = createLive2DControlsRenderer({
        getSelectionRuntimeState: deps.getSelectionRuntimeState,
        isExpressionActive: deps.isExpressionActive,
        persistence: persistence,
    });

    const runtime = createLive2DControlRuntime({
        controllerState: controllerState,
        getSelectionRuntimeState: deps.getSelectionRuntimeState,
        playMotion: deps.playMotion,
        renderLive2DControls: renderer.renderLive2DControls,
        restoreHotkeyToDefault: persistence.restoreHotkeyToDefault,
        setRunStatus: deps.setRunStatus,
        toggleExpression: deps.toggleExpression,
        triggerHotkey: deps.triggerHotkey,
    });

    return {
        handleControlsClick: runtime.handleControlsClick,
        handleControlsFocusIn: persistence.handleControlFocusIn,
        handleControlsFocusOut: persistence.handleControlFocusOut,
        handleControlsInput: persistence.handleControlInput,
        handleControlsKeyDown: persistence.handleControlKeyDown,
        resetHotkeyState: runtime.resetHotkeyState,
        handleWindowBlur: runtime.handleWindowBlur,
        handleWindowKeyDown: runtime.handleWindowKeyDown,
        handleWindowKeyUp: runtime.handleWindowKeyUp,
        renderLive2DControls: renderer.renderLive2DControls,
    };
}
