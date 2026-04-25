import { appState } from "../../../core/store.js";
import {
    NOTE_AUTOSAVE_DELAY_MS,
    buildAnnotationStateKey,
    buildHotkeyKey,
    buildHotkeyStateKey,
    captureShortcutTokens,
    containsPrimaryShortcutToken,
    describeError,
    findShortcutConflict,
    formatShortcutTokens,
    normalizeShortcutTokens,
    sameShortcutTokens,
} from "./common.js";

export function createLive2DControlsPersistence(deps) {
    const {
        controllerState,
        requestJson,
        setRunStatus,
    } = deps;

    function handleNoteInput(event) {
        const input = findDelegatedElement(event, ".live2d-note-input");
        if (!input) {
            return;
        }

        scheduleAnnotationSave({
            selectionKey: String(input.dataset.live2dSelectionKey || ""),
            kind: String(input.dataset.live2dKind || ""),
            file: String(input.dataset.live2dFile || ""),
            note: input.value,
        });
    }

    function handleNoteBlur(event) {
        const input = findDelegatedElement(event, ".live2d-note-input");
        if (!input) {
            return;
        }

        scheduleAnnotationSave(
            {
                selectionKey: String(input.dataset.live2dSelectionKey || ""),
                kind: String(input.dataset.live2dKind || ""),
                file: String(input.dataset.live2dFile || ""),
                note: input.value,
            },
            {
                immediate: true,
            },
        );
    }

    function handleHotkeyInputFocus(event) {
        const input = findDelegatedElement(event, ".live2d-hotkey-input");
        const shell = input && input.closest(".live2d-hotkey-input-shell");
        if (shell) {
            shell.classList.add("is-recording");
        }
    }

    function handleHotkeyInputBlur(event) {
        const input = findDelegatedElement(event, ".live2d-hotkey-input");
        const shell = input && input.closest(".live2d-hotkey-input-shell");
        if (shell) {
            shell.classList.remove("is-recording");
        }
    }

    async function handleHotkeyInputKeyDown(event) {
        const input = findDelegatedElement(event, ".live2d-hotkey-input");
        if (!input) {
            return;
        }

        event.preventDefault();

        if (!input || input.disabled || event.repeat) {
            return;
        }

        const selectionKey = String(input.dataset.live2dSelectionKey || "");
        const hotkeyKey = String(input.dataset.live2dHotkeyKey || "");
        if (!selectionKey || !hotkeyKey) {
            return;
        }

        if (event.key === "Escape") {
            input.blur();
            return;
        }

        if (event.key === "Backspace" || event.key === "Delete") {
            setHotkeyInputValue(input, []);
            await queueHotkeySave({
                selectionKey: selectionKey,
                hotkeyKey: hotkeyKey,
                shortcutTokens: [],
            });
            return;
        }

        const shortcutTokens = captureShortcutTokens(event);
        if (shortcutTokens.length === 0 || !containsPrimaryShortcutToken(shortcutTokens)) {
            return;
        }

        const conflict = findCurrentShortcutConflict(selectionKey, hotkeyKey, shortcutTokens);
        if (conflict) {
            restoreHotkeyInputFromConfig(input, selectionKey, hotkeyKey);
            setRunStatus(`热键冲突：已被 ${conflict.name || buildHotkeyKey(conflict)} 使用`);
            return;
        }

        setHotkeyInputValue(input, shortcutTokens);
        await queueHotkeySave({
            selectionKey: selectionKey,
            hotkeyKey: hotkeyKey,
            shortcutTokens: shortcutTokens,
        });
    }

    function handleControlInput(event) {
        handleNoteInput(event);
    }

    function handleControlFocusIn(event) {
        handleHotkeyInputFocus(event);
    }

    function handleControlFocusOut(event) {
        handleHotkeyInputBlur(event);
        handleNoteBlur(event);
    }

    function handleControlKeyDown(event) {
        void handleHotkeyInputKeyDown(event);
    }

    function readAnnotationDraftValue(selectionKey, kind, file, fallbackValue) {
        const stateKey = buildAnnotationStateKey(selectionKey, kind, file);
        return controllerState.annotationDrafts.has(stateKey)
            ? String(controllerState.annotationDrafts.get(stateKey) || "")
            : fallbackValue;
    }

    function readHotkeyDraftValue(selectionKey, hotkeyKey, fallbackTokens) {
        const stateKey = buildHotkeyStateKey(selectionKey, hotkeyKey);
        return controllerState.hotkeyDrafts.has(stateKey)
            ? normalizeShortcutTokens(controllerState.hotkeyDrafts.get(stateKey))
            : normalizeShortcutTokens(fallbackTokens);
    }

    function setHotkeyInputValue(input, shortcutTokens) {
        const normalizedTokens = normalizeShortcutTokens(shortcutTokens);
        input.dataset.shortcutTokens = JSON.stringify(normalizedTokens);
        input.value = formatShortcutTokens(normalizedTokens);
        syncHotkeyClearButtonState(input, normalizedTokens.length > 0);
    }

    function restoreHotkeyInputFromConfig(input, selectionKey, hotkeyKey) {
        const hotkeyItem = findCurrentHotkey(selectionKey, hotkeyKey);
        setHotkeyInputValue(input, hotkeyItem ? hotkeyItem.shortcut_tokens || [] : []);
    }

    async function restoreHotkeyToDefault({ selectionKey, hotkeyKey, shortcutInput, live2dConfig = null }) {
        if (!selectionKey || !hotkeyKey || !shortcutInput) {
            return;
        }

        await queueHotkeyRestoreDefault({
            selectionKey: selectionKey,
            hotkeyKey: hotkeyKey,
        });

        const nextConfig = live2dConfig
            || (appState.config && appState.config.live2d)
            || null;
        const updatedHotkey = nextConfig && Array.isArray(nextConfig.hotkeys)
            ? nextConfig.hotkeys.find((item) => buildHotkeyKey(item) === hotkeyKey)
            : null;
        setHotkeyInputValue(
            shortcutInput,
            updatedHotkey ? updatedHotkey.shortcut_tokens || [] : [],
        );
    }

    function scheduleAnnotationSave(request, options = {}) {
        if (!request.selectionKey || !request.kind || !request.file) {
            return;
        }

        const stateKey = buildAnnotationStateKey(
            request.selectionKey,
            request.kind,
            request.file,
        );
        controllerState.annotationDrafts.set(stateKey, request.note);

        const saveState = getSaveState(controllerState.annotationSaveStates, stateKey);
        saveState.pendingRequest = request;

        if (saveState.timerId) {
            window.clearTimeout(saveState.timerId);
            saveState.timerId = 0;
        }

        if (options.immediate) {
            void flushAnnotationSave(stateKey);
            return;
        }

        saveState.timerId = window.setTimeout(() => {
            saveState.timerId = 0;
            void flushAnnotationSave(stateKey);
        }, NOTE_AUTOSAVE_DELAY_MS);
    }

    async function flushAnnotationSave(stateKey) {
        const saveState = controllerState.annotationSaveStates.get(stateKey);
        if (!saveState) {
            return;
        }

        if (saveState.timerId) {
            window.clearTimeout(saveState.timerId);
            saveState.timerId = 0;
        }

        if (saveState.inFlight || !saveState.pendingRequest) {
            return;
        }

        const request = saveState.pendingRequest;
        const pendingNote = request.note;
        saveState.inFlight = true;

        try {
            const payload = await requestAnnotationSave(request);
            updateLocalAnnotation(payload);
            if (controllerState.annotationDrafts.get(stateKey) === payload.note) {
                controllerState.annotationDrafts.delete(stateKey);
            }
            if (saveState.pendingRequest && saveState.pendingRequest.note === pendingNote) {
                saveState.pendingRequest = null;
            }
            setRunStatus(
                payload.note
                    ? `已保存${payload.kind === "motion" ? "动作" : "表情"}备注`
                    : `已清空${payload.kind === "motion" ? "动作" : "表情"}备注`,
            );
        } catch (error) {
            setRunStatus(`保存备注失败：${describeError(error)}`);
        } finally {
            saveState.inFlight = false;
            if (saveState.pendingRequest && saveState.pendingRequest.note !== pendingNote) {
                void flushAnnotationSave(stateKey);
                return;
            }
            if (!saveState.pendingRequest && !saveState.timerId) {
                controllerState.annotationSaveStates.delete(stateKey);
            }
        }
    }

    async function queueHotkeySave(request) {
        if (!request.selectionKey || !request.hotkeyKey) {
            return;
        }

        const normalizedTokens = normalizeShortcutTokens(request.shortcutTokens);
        const stateKey = buildHotkeyStateKey(request.selectionKey, request.hotkeyKey);
        controllerState.hotkeyDrafts.set(stateKey, normalizedTokens);

        const saveState = getSaveState(controllerState.hotkeySaveStates, stateKey);
        saveState.pendingRequest = {
            selectionKey: request.selectionKey,
            hotkeyKey: request.hotkeyKey,
            shortcutTokens: normalizedTokens,
            restoreDefault: false,
        };

        await flushHotkeySave(stateKey);
    }

    async function queueHotkeyRestoreDefault(request) {
        if (!request.selectionKey || !request.hotkeyKey) {
            return;
        }

        const stateKey = buildHotkeyStateKey(request.selectionKey, request.hotkeyKey);
        controllerState.hotkeyDrafts.delete(stateKey);

        const saveState = getSaveState(controllerState.hotkeySaveStates, stateKey);
        saveState.pendingRequest = {
            selectionKey: request.selectionKey,
            hotkeyKey: request.hotkeyKey,
            shortcutTokens: [],
            restoreDefault: true,
        };

        await flushHotkeySave(stateKey);
    }

    async function flushHotkeySave(stateKey) {
        const saveState = controllerState.hotkeySaveStates.get(stateKey);
        if (!saveState || saveState.inFlight || !saveState.pendingRequest) {
            return;
        }

        const request = saveState.pendingRequest;
        const restoreDefault = Boolean(request.restoreDefault);
        saveState.inFlight = true;

        try {
            const payload = await requestHotkeySave(request);
            updateLocalHotkey(payload);
            if (restoreDefault) {
                controllerState.hotkeyDrafts.delete(stateKey);
            } else if (sameShortcutTokens(controllerState.hotkeyDrafts.get(stateKey), payload.shortcut_tokens)) {
                controllerState.hotkeyDrafts.delete(stateKey);
            }
            if (saveState.pendingRequest && sameHotkeySaveRequest(saveState.pendingRequest, request)) {
                saveState.pendingRequest = null;
            }
            setRunStatus(
                restoreDefault
                    ? `已恢复默认热键：${payload.name}`
                    : payload.shortcut_tokens.length > 0
                    ? `已保存热键：${payload.name}`
                    : `已清空热键：${payload.name}`,
            );
        } catch (error) {
            setRunStatus(`保存热键失败：${describeError(error)}`);
        } finally {
            saveState.inFlight = false;
            if (saveState.pendingRequest && !sameHotkeySaveRequest(saveState.pendingRequest, request)) {
                await flushHotkeySave(stateKey);
                return;
            }
            if (!saveState.pendingRequest) {
                controllerState.hotkeySaveStates.delete(stateKey);
            }
        }
    }

    async function requestAnnotationSave({ selectionKey, kind, file, note }) {
        return requestJson("/api/web/live2d/annotations", {
            method: "PATCH",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({
                selection_key: selectionKey,
                kind: kind,
                file: file,
                note: note,
            }),
        });
    }

    async function requestHotkeySave({ selectionKey, hotkeyKey, shortcutTokens, restoreDefault = false }) {
        return requestJson("/api/web/live2d/hotkeys", {
            method: "PATCH",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({
                selection_key: selectionKey,
                hotkey_key: hotkeyKey,
                shortcut_tokens: shortcutTokens,
                restore_default: restoreDefault,
            }),
        });
    }

    function updateLocalAnnotation(payload) {
        const live2dConfig = appState.config && appState.config.live2d;
        if (!live2dConfig) {
            return;
        }

        [live2dConfig, ...(live2dConfig.models || [])]
            .filter((item) => item && item.selection_key === payload.selection_key)
            .forEach((item) => {
                const listKey = payload.kind === "motion" ? "motions" : "expressions";
                const targetItem = (item[listKey] || []).find((candidate) => candidate.file === payload.file);
                if (targetItem) {
                    targetItem.note = payload.note || "";
                }
            });
    }

    function updateLocalHotkey(payload) {
        const live2dConfig = appState.config && appState.config.live2d;
        if (!live2dConfig) {
            return;
        }

        [live2dConfig, ...(live2dConfig.models || [])]
            .filter((item) => item && item.selection_key === payload.selection_key)
            .forEach((item) => {
                const targetItem = (item.hotkeys || []).find(
                    (candidate) => buildHotkeyKey(candidate) === payload.hotkey_key,
                );
                if (targetItem) {
                    targetItem.hotkey_key = payload.hotkey_key || targetItem.hotkey_key || "";
                    targetItem.shortcut_tokens = Array.isArray(payload.shortcut_tokens)
                        ? payload.shortcut_tokens.filter((token) => typeof token === "string")
                        : [];
                    targetItem.shortcut_label = String(payload.shortcut_label || "");
                }
            });
    }

    function findCurrentHotkeys(selectionKey) {
        const live2dConfig = appState.config && appState.config.live2d;
        if (!live2dConfig) {
            return [];
        }

        const matchingConfig = [live2dConfig, ...(live2dConfig.models || [])]
            .find((item) => item && item.selection_key === selectionKey);
        return matchingConfig && Array.isArray(matchingConfig.hotkeys)
            ? matchingConfig.hotkeys
            : [];
    }

    function findCurrentHotkey(selectionKey, hotkeyKey) {
        return findCurrentHotkeys(selectionKey).find(
            (item) => buildHotkeyKey(item) === hotkeyKey,
        ) || null;
    }

    function findCurrentShortcutConflict(selectionKey, hotkeyKey, shortcutTokens) {
        return findShortcutConflict(
            findCurrentHotkeys(selectionKey),
            hotkeyKey,
            shortcutTokens,
        );
    }

    function sameHotkeySaveRequest(left, right) {
        if (!left || !right) {
            return false;
        }

        return left.selectionKey === right.selectionKey
            && left.hotkeyKey === right.hotkeyKey
            && Boolean(left.restoreDefault) === Boolean(right.restoreDefault)
            && sameShortcutTokens(left.shortcutTokens, right.shortcutTokens);
    }

    function getSaveState(saveMap, stateKey) {
        const existingState = saveMap.get(stateKey);
        if (existingState) {
            return existingState;
        }

        const nextState = {
            timerId: 0,
            inFlight: false,
            pendingRequest: null,
        };
        saveMap.set(stateKey, nextState);
        return nextState;
    }

    function syncHotkeyClearButtonState(input, hasTokens) {
        const shell = input.closest(".live2d-hotkey-input-shell");
        const clearButton = shell && shell.querySelector(".live2d-hotkey-clear");
        if (clearButton) {
            clearButton.disabled = input.disabled || !hasTokens;
        }
    }

    function findDelegatedElement(event, selector) {
        const target = event && event.target;
        if (!target || typeof target.closest !== "function") {
            return null;
        }

        const element = target.closest(selector);
        if (!element) {
            return null;
        }

        const currentTarget = event.currentTarget;
        if (
            currentTarget
            && currentTarget !== element
            && typeof currentTarget.contains === "function"
            && !currentTarget.contains(element)
        ) {
            return null;
        }
        return element;
    }

    return {
        handleControlFocusIn,
        handleControlFocusOut,
        handleControlInput,
        handleControlKeyDown,
        handleHotkeyInputBlur,
        handleHotkeyInputFocus,
        handleHotkeyInputKeyDown,
        handleNoteBlur,
        handleNoteInput,
        readAnnotationDraftValue,
        readHotkeyDraftValue,
        restoreHotkeyToDefault,
        setHotkeyInputValue,
    };
}
