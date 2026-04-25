export const NOTE_AUTOSAVE_DELAY_MS = 600;
export const NOTE_FILE_LABEL = "echobot.live2d.json";

export { normalizeLive2DConfig } from "../schema.js";

export function buildHotkeyKey(hotkeyItem) {
    return hotkeyItem.hotkey_key || hotkeyItem.hotkey_id || `${hotkeyItem.action}:${hotkeyItem.file}`;
}

export function buildAnnotationStateKey(selectionKey, kind, file) {
    return JSON.stringify([selectionKey, kind, file]);
}

export function buildHotkeyStateKey(selectionKey, hotkeyKey) {
    return JSON.stringify([selectionKey, hotkeyKey]);
}

export function normalizeShortcutTokens(shortcutTokens) {
    if (!Array.isArray(shortcutTokens)) {
        return [];
    }

    return shortcutTokens
        .filter((token) => typeof token === "string")
        .map((token) => token.trim().toLowerCase())
        .filter(Boolean)
        .slice(0, 3);
}

export function sameShortcutTokens(left, right) {
    const leftTokens = normalizeShortcutTokens(left);
    const rightTokens = normalizeShortcutTokens(right);
    if (leftTokens.length !== rightTokens.length) {
        return false;
    }

    return leftTokens.every((token, index) => token === rightTokens[index]);
}

export function shortcutTokensMatchPressed(shortcutTokens, pressedTokens) {
    const normalizedTokens = normalizeShortcutTokens(shortcutTokens);
    if (normalizedTokens.length === 0 || normalizedTokens.length !== pressedTokens.size) {
        return false;
    }

    return normalizedTokens.every((token) => pressedTokens.has(token));
}

export function findShortcutConflict(hotkeys, currentHotkeyKey, shortcutTokens) {
    const normalizedTokens = normalizeShortcutTokens(shortcutTokens);
    if (normalizedTokens.length === 0 || !Array.isArray(hotkeys)) {
        return null;
    }

    return hotkeys.find((hotkeyItem) => {
        if (buildHotkeyKey(hotkeyItem) === currentHotkeyKey) {
            return false;
        }
        return sameShortcutTokens(hotkeyItem.shortcut_tokens || [], normalizedTokens);
    }) || null;
}

export function formatShortcutTokens(shortcutTokens) {
    if (!shortcutTokens.length) {
        return "";
    }

    return shortcutTokens.map(displayHotkeyToken).join(" + ");
}

export function displayHotkeyToken(token) {
    const displayMap = {
        alt: "Alt",
        control: "Ctrl",
        shift: "Shift",
        meta: "Meta",
        space: "Space",
        tab: "Tab",
        enter: "Enter",
        escape: "Esc",
        backspace: "Backspace",
        delete: "Delete",
        insert: "Insert",
        home: "Home",
        end: "End",
        pageup: "PageUp",
        pagedown: "PageDown",
        arrowup: "Up",
        arrowdown: "Down",
        arrowleft: "Left",
        arrowright: "Right",
        minus: "-",
        equal: "=",
        comma: ",",
        period: ".",
        slash: "/",
        backslash: "\\",
        semicolon: ";",
        quote: "'",
        backquote: "`",
        capslock: "CapsLock",
    };
    if (displayMap[token]) {
        return displayMap[token];
    }
    if (token.startsWith("digit")) {
        return token.slice(5);
    }
    if (token.startsWith("key")) {
        return token.slice(3).toUpperCase();
    }
    if (token.startsWith("numpad")) {
        const suffix = token.slice(6);
        return `Numpad ${suffix.charAt(0).toUpperCase()}${suffix.slice(1)}`;
    }
    if (/^f\d{1,2}$/.test(token)) {
        return token.toUpperCase();
    }
    return token;
}

export function describeError(error) {
    if (error instanceof Error) {
        return error.message;
    }
    return String(error || "未知错误");
}

export function describeHotkeyAction(action) {
    const actionMap = {
        ToggleExpression: "切换表情",
        TriggerAnimation: "播放动作",
        RemoveAllExpressions: "清空表情",
    };
    return actionMap[action] || action || "热键";
}

export function buildHotkeyMetaText(hotkeyItem) {
    const actionLabel = describeHotkeyAction(hotkeyItem.action);
    if (!hotkeyItem.supported) {
        return `${actionLabel} | 当前不支持`;
    }
    if (!hotkeyItem.file) {
        return actionLabel;
    }
    return `${actionLabel} | ${hotkeyItem.file}`;
}

export function findAttachedHotkeys(hotkeys, targetKind, file) {
    return Array.isArray(hotkeys)
        ? hotkeys.filter(
            (item) => item.target_kind === targetKind && item.file === file,
        )
        : [];
}

export function filterStandaloneHotkeys(hotkeys) {
    return Array.isArray(hotkeys)
        ? hotkeys.filter(
            (item) => !["expression", "motion"].includes(String(item.target_kind || "")),
        )
        : [];
}

export function shouldIgnoreKeyboardEvent(event) {
    const target = event.target;
    if (!target || typeof target.closest !== "function") {
        return false;
    }

    return Boolean(target.closest("input, textarea, select, [contenteditable='true']"));
}

export function syncModifierTokens(pressedTokens, event) {
    ["alt", "control", "shift", "meta"].forEach((token) => {
        pressedTokens.delete(token);
    });

    if (event.altKey) {
        pressedTokens.add("alt");
    }
    if (event.ctrlKey) {
        pressedTokens.add("control");
    }
    if (event.shiftKey) {
        pressedTokens.add("shift");
    }
    if (event.metaKey) {
        pressedTokens.add("meta");
    }
}

export function normalizeKeyboardEventToken(event) {
    const code = String(event.code || "").trim();
    if (!code) {
        return "";
    }

    const normalizedCode = code.toLowerCase();
    const codeMap = {
        altleft: "alt",
        altright: "alt",
        controlleft: "control",
        controlright: "control",
        shiftleft: "shift",
        shiftright: "shift",
        metaleft: "meta",
        metaright: "meta",
        space: "space",
        tab: "tab",
        enter: "enter",
        escape: "escape",
        backspace: "backspace",
        delete: "delete",
        insert: "insert",
        home: "home",
        end: "end",
        pageup: "pageup",
        pagedown: "pagedown",
        arrowup: "arrowup",
        arrowdown: "arrowdown",
        arrowleft: "arrowleft",
        arrowright: "arrowright",
        minus: "minus",
        equal: "equal",
        comma: "comma",
        period: "period",
        slash: "slash",
        backslash: "backslash",
        semicolon: "semicolon",
        quote: "quote",
        backquote: "backquote",
        capslock: "capslock",
    };
    if (codeMap[normalizedCode]) {
        return codeMap[normalizedCode];
    }
    if (normalizedCode.startsWith("digit") || normalizedCode.startsWith("key")) {
        return normalizedCode;
    }
    if (/^f\d{1,2}$/.test(normalizedCode)) {
        return normalizedCode;
    }
    if (normalizedCode.startsWith("numpad")) {
        return normalizedCode;
    }
    return normalizedCode;
}

export function captureShortcutTokens(event) {
    const shortcutTokens = [];

    if (event.ctrlKey) {
        shortcutTokens.push("control");
    }
    if (event.altKey) {
        shortcutTokens.push("alt");
    }
    if (event.shiftKey) {
        shortcutTokens.push("shift");
    }
    if (event.metaKey) {
        shortcutTokens.push("meta");
    }

    const keyToken = normalizeKeyboardEventToken(event);
    if (keyToken && !isModifierToken(keyToken)) {
        shortcutTokens.push(keyToken);
    }

    return shortcutTokens.slice(0, 3);
}

export function containsPrimaryShortcutToken(shortcutTokens) {
    return shortcutTokens.some((token) => !isModifierToken(token));
}

function isModifierToken(token) {
    return ["alt", "control", "shift", "meta"].includes(token);
}
