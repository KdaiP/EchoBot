export function hasUsableDesktopPassthroughBridge(desktopBridge) {
    return Boolean(
        desktopBridge
        && typeof desktopBridge.setMousePassthrough === "function",
    );
}

export function isDesktopInteractiveRegion(target) {
    return Boolean(
        target
        && typeof target.closest === "function"
        && target.closest("[data-desktop-interactive='true']"),
    );
}

export function getDesktopResizeEdge(target) {
    if (!target || typeof target.closest !== "function") {
        return "";
    }

    const hotspot = target.closest("[data-desktop-resize-edge]");
    return String(hotspot?.getAttribute("data-desktop-resize-edge") || "").trim();
}

export function isDesktopMouseCaptureRegion(target) {
    return isDesktopInteractiveRegion(target) || getDesktopResizeEdge(target) !== "";
}
