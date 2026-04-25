import { DOM } from "../../core/dom.js";
import { readString, removeStoredValue, writeString } from "../../core/storage.js";

const SPLIT_RATIO_STORAGE_KEY = "echobot.web.chat_panel_width_ratio";
const DEFAULT_CHAT_WIDTH_RATIO = 0.34;
const MIN_CHAT_PANEL_WIDTH = 320;
const MIN_STAGE_PANEL_WIDTH = 360;
const MAX_CHAT_PANEL_WIDTH = 960;
const DESKTOP_LAYOUT_QUERY = "(min-width: 1121px)";
const KEYBOARD_STEP_WIDTH = 40;

export function createSplitController() {
    let isDragging = false;
    let activePointerId = null;
    const desktopLayout = window.matchMedia(DESKTOP_LAYOUT_QUERY);

    function initializePageSplit() {
        if (!DOM.pageShell || !DOM.pageResizer) {
            return;
        }

        DOM.pageResizer.addEventListener("pointerdown", handleResizerPointerDown);
        DOM.pageResizer.addEventListener("dblclick", handleResizerDoubleClick);
        DOM.pageResizer.addEventListener("keydown", handleResizerKeyDown);
        window.addEventListener("pointermove", handleWindowPointerMove);
        window.addEventListener("pointerup", handleWindowPointerUp);
        window.addEventListener("pointercancel", handleWindowPointerUp);
        window.addEventListener("resize", restorePageSplit);

        restorePageSplit();
    }

    function restorePageSplit() {
        if (!DOM.pageShell) {
            return;
        }

        if (!desktopLayout.matches) {
            DOM.pageShell.style.removeProperty("--chat-panel-width");
            return;
        }

        const storedRatio = Number.parseFloat(readString(SPLIT_RATIO_STORAGE_KEY, ""));
        const ratio = Number.isFinite(storedRatio) ? storedRatio : DEFAULT_CHAT_WIDTH_RATIO;
        const metrics = getSplitMetrics();
        const chatWidth = normalizeChatWidth(metrics.usableWidth * ratio, metrics);
        applyChatWidth(chatWidth, metrics);
    }

    function handleResizerPointerDown(event) {
        if (!desktopLayout.matches || !DOM.pageResizer) {
            return;
        }

        isDragging = true;
        activePointerId = event.pointerId;
        DOM.pageResizer.setPointerCapture(event.pointerId);
        DOM.pageShell?.classList.add("is-resizing");
        updateChatWidthFromPointer(event.clientX);
        event.preventDefault();
    }

    function handleWindowPointerMove(event) {
        if (!isDragging || event.pointerId !== activePointerId) {
            return;
        }

        updateChatWidthFromPointer(event.clientX);
    }

    function handleWindowPointerUp(event) {
        if (!isDragging || event.pointerId !== activePointerId) {
            return;
        }

        isDragging = false;
        activePointerId = null;
        DOM.pageShell?.classList.remove("is-resizing");
        if (DOM.pageResizer?.hasPointerCapture(event.pointerId)) {
            DOM.pageResizer.releasePointerCapture(event.pointerId);
        }
    }

    function handleResizerDoubleClick(event) {
        removeStoredValue(SPLIT_RATIO_STORAGE_KEY);
        resetPageSplitToDefault();
        event.preventDefault();
    }

    function handleResizerKeyDown(event) {
        if (!desktopLayout.matches) {
            return;
        }

        const direction = getKeyboardDirection(event.key);
        if (direction === 0) {
            return;
        }

        const metrics = getSplitMetrics();
        const currentWidth = getCurrentChatWidth(metrics);
        const nextWidth = currentWidth + direction * KEYBOARD_STEP_WIDTH;
        applyChatWidth(nextWidth, metrics);
        event.preventDefault();
    }

    function getKeyboardDirection(key) {
        if (key === "ArrowLeft") {
            return 1;
        }
        if (key === "ArrowRight") {
            return -1;
        }
        return 0;
    }

    function resetPageSplitToDefault() {
        if (!DOM.pageShell) {
            return;
        }

        if (!desktopLayout.matches) {
            DOM.pageShell.style.removeProperty("--chat-panel-width");
            return;
        }

        const metrics = getSplitMetrics();
        applyChatWidth(metrics.usableWidth * DEFAULT_CHAT_WIDTH_RATIO, metrics);
    }

    function updateChatWidthFromPointer(clientX) {
        const metrics = getSplitMetrics();
        const chatWidth = metrics.contentRight
            - clientX
            - metrics.columnGap
            - metrics.resizerWidth / 2;
        applyChatWidth(chatWidth, metrics);
    }

    function applyChatWidth(rawWidth, metrics = getSplitMetrics()) {
        if (!DOM.pageShell || metrics.usableWidth <= 0) {
            return;
        }

        const width = normalizeChatWidth(rawWidth, metrics);
        const ratio = width / metrics.usableWidth;
        DOM.pageShell.style.setProperty("--chat-panel-width", `${Math.round(width)}px`);
        writeString(SPLIT_RATIO_STORAGE_KEY, ratio.toFixed(4));
    }

    function getCurrentChatWidth(metrics) {
        const chatPanelWidth = DOM.chatPanel?.getBoundingClientRect().width || 0;
        if (chatPanelWidth > 0) {
            return chatPanelWidth;
        }
        return metrics.usableWidth * DEFAULT_CHAT_WIDTH_RATIO;
    }

    function normalizeChatWidth(width, metrics) {
        const maxWidth = Math.max(
            MIN_CHAT_PANEL_WIDTH,
            Math.min(MAX_CHAT_PANEL_WIDTH, metrics.usableWidth - MIN_STAGE_PANEL_WIDTH),
        );
        const minWidth = Math.min(MIN_CHAT_PANEL_WIDTH, maxWidth);
        return clamp(width, minWidth, maxWidth);
    }

    function getSplitMetrics() {
        const shell = DOM.pageShell;
        if (!shell) {
            return {
                columnGap: 0,
                contentRight: 0,
                resizerWidth: 0,
                usableWidth: 0,
            };
        }

        const rect = shell.getBoundingClientRect();
        const style = window.getComputedStyle(shell);
        const paddingLeft = readPixelValue(style.paddingLeft);
        const paddingRight = readPixelValue(style.paddingRight);
        const columnGap = readPixelValue(style.columnGap);
        const resizerWidth = DOM.pageResizer?.getBoundingClientRect().width || 0;
        const contentWidth = rect.width - paddingLeft - paddingRight;
        const usableWidth = contentWidth - resizerWidth - columnGap * 2;

        return {
            columnGap,
            contentRight: rect.right - paddingRight,
            resizerWidth,
            usableWidth: Math.max(usableWidth, 0),
        };
    }

    function readPixelValue(value) {
        const numberValue = Number.parseFloat(value);
        return Number.isFinite(numberValue) ? numberValue : 0;
    }

    function clamp(value, min, max) {
        return Math.min(Math.max(value, min), max);
    }

    return {
        initializePageSplit,
        restorePageSplit,
    };
}
