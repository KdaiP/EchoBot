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
