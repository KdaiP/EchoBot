import { appState, live2dState } from "../../../core/store.js";
import {
    buildHotkeyKey,
    describeError,
    normalizeKeyboardEventToken,
    normalizeLive2DConfig,
    shouldIgnoreKeyboardEvent,
    shortcutTokensMatchPressed,
    syncModifierTokens,
} from "./common.js";

export function createLive2DControlRuntime(deps) {
    const {
        controllerState,
        getSelectionRuntimeState,
        playMotion,
        renderLive2DControls,
        restoreHotkeyToDefault,
        setRunStatus,
        toggleExpression,
        triggerHotkey,
    } = deps;

    function handleWindowKeyDown(event) {
        if (shouldIgnoreKeyboardEvent(event)) {
            return;
        }

        if (!live2dState.live2dHotkeysEnabled) {
            resetHotkeyState();
            return;
        }

        syncModifierTokens(controllerState.pressedTokens, event);
        const keyToken = normalizeKeyboardEventToken(event);
        if (keyToken) {
            controllerState.pressedTokens.add(keyToken);
        }

        const live2dConfig = currentLive2DConfig();
        if (!getSelectionRuntimeState(live2dConfig.selection_key).canInteract) {
            controllerState.activeHotkeyIds.clear();
            return;
        }

        const hotkeys = live2dConfig.hotkeys.filter(
            (item) => item.supported && item.shortcut_tokens.length > 0,
        );
        hotkeys.forEach((hotkeyItem) => {
            const hotkeyKey = buildHotkeyKey(hotkeyItem);
            if (controllerState.activeHotkeyIds.has(hotkeyKey)) {
                return;
            }
            if (!shortcutTokensMatchPressed(hotkeyItem.shortcut_tokens, controllerState.pressedTokens)) {
                return;
            }

            controllerState.activeHotkeyIds.add(hotkeyKey);
            event.preventDefault();
            void runHotkeyAction(hotkeyItem, {
                sourceLabel: "键盘热键",
            });
        });
    }

    function handleWindowKeyUp(event) {
        if (!live2dState.live2dHotkeysEnabled) {
            resetHotkeyState();
            return;
        }

        syncModifierTokens(controllerState.pressedTokens, event);
        const keyToken = normalizeKeyboardEventToken(event);
        if (keyToken) {
            controllerState.pressedTokens.delete(keyToken);
        }
        pruneActiveHotkeys();
    }

    function handleWindowBlur() {
        resetHotkeyState();
    }

    function resetHotkeyState() {
        controllerState.pressedTokens.clear();
        controllerState.activeHotkeyIds.clear();
    }

    async function handleControlsClick(event) {
        const control = event.target.closest("[data-live2d-action]");
        if (!control) {
            return;
        }
        if (control.dataset.live2dDisabled === "true") {
            return;
        }
        if ("disabled" in control && control.disabled) {
            return;
        }

        const live2dConfig = currentLive2DConfig();
        const actionName = String(control.dataset.live2dAction || "");
        const file = String(control.dataset.live2dFile || "");
        const hotkeyId = String(control.dataset.live2dHotkeyId || "");
        const hotkeyKey = String(control.dataset.live2dHotkeyKey || "");

        try {
            if (actionName === "trigger-expression") {
                const expressionItem = live2dConfig.expressions.find((item) => item.file === file);
                if (!expressionItem) {
                    return;
                }
                const result = await toggleExpression(expressionItem, live2dConfig.selection_key);
                renderLive2DControls(currentLive2DConfig());
                setRunStatus(
                    result.active
                        ? `已启用表情：${result.name}`
                        : `已关闭表情：${result.name}`,
                );
                return;
            }

            if (actionName === "play-motion") {
                const motionItem = live2dConfig.motions.find((item) => item.file === file);
                if (!motionItem) {
                    return;
                }
                await playMotion(motionItem, live2dConfig.selection_key);
                setRunStatus(`已播放动作：${motionItem.name}`);
                return;
            }

            if (actionName === "trigger-hotkey") {
                const hotkeyItem = live2dConfig.hotkeys.find(
                    (item) => item.hotkey_id === hotkeyId || buildHotkeyKey(item) === hotkeyKey,
                );
                if (!hotkeyItem) {
                    return;
                }
                await runHotkeyAction(hotkeyItem, {
                    sourceLabel: "热键",
                });
                return;
            }

            if (actionName === "reset-hotkey") {
                const shortcutInput = control
                    .closest(".live2d-hotkey-input-shell")
                    ?.querySelector(".live2d-hotkey-input");
                await restoreHotkeyToDefault({
                    selectionKey: live2dConfig.selection_key,
                    hotkeyKey: hotkeyKey,
                    shortcutInput: shortcutInput,
                    live2dConfig: live2dConfig,
                });
            }
        } catch (error) {
            console.error(error);
            renderLive2DControls(currentLive2DConfig());
            setRunStatus(describeError(error));
        }
    }

    async function runHotkeyAction(hotkeyItem, options = {}) {
        const live2dConfig = currentLive2DConfig();
        if (!getSelectionRuntimeState(live2dConfig.selection_key).canInteract) {
            return null;
        }

        try {
            const result = await triggerHotkey(hotkeyItem, live2dConfig);
            renderLive2DControls(currentLive2DConfig());

            const sourceLabel = options.sourceLabel || "热键";
            if (hotkeyItem.action === "ToggleExpression" && result.result) {
                setRunStatus(
                    result.result.active
                        ? `${sourceLabel}：启用 ${result.result.name}`
                        : `${sourceLabel}：关闭 ${result.result.name}`,
                );
                return result;
            }
            if (hotkeyItem.action === "TriggerAnimation" && result.result) {
                setRunStatus(`${sourceLabel}：播放 ${result.result.name}`);
                return result;
            }
            if (hotkeyItem.action === "RemoveAllExpressions") {
                setRunStatus(`${sourceLabel}：已清空所有表情`);
            }
            return result;
        } catch (error) {
            console.error(error);
            renderLive2DControls(currentLive2DConfig());
            setRunStatus(describeError(error));
            return null;
        }
    }

    function currentLive2DConfig() {
        return normalizeLive2DConfig(appState.config && appState.config.live2d);
    }

    function pruneActiveHotkeys() {
        const live2dConfig = currentLive2DConfig();
        Array.from(controllerState.activeHotkeyIds).forEach((hotkeyKey) => {
            const hotkeyItem = live2dConfig.hotkeys.find((item) => buildHotkeyKey(item) === hotkeyKey);
            if (!hotkeyItem) {
                controllerState.activeHotkeyIds.delete(hotkeyKey);
                return;
            }
            const stillPressed = shortcutTokensMatchPressed(
                hotkeyItem.shortcut_tokens,
                controllerState.pressedTokens,
            );
            if (!stillPressed) {
                controllerState.activeHotkeyIds.delete(hotkeyKey);
            }
        });
    }

    return {
        handleControlsClick,
        resetHotkeyState,
        handleWindowBlur,
        handleWindowKeyDown,
        handleWindowKeyUp,
    };
}
