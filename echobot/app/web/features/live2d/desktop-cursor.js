export function hasUsableDesktopCursorBridge(desktopBridge) {
    return Boolean(
        desktopBridge
        && typeof desktopBridge.getGlobalCursorState === "function",
    );
}

export function mapScreenPointToStagePoint(cursorState, windowBounds, stageRect) {
    const cursorX = Number(cursorState?.cursorX);
    const cursorY = Number(cursorState?.cursorY);
    const windowX = Number(windowBounds?.x);
    const windowY = Number(windowBounds?.y);
    const stageLeft = Number(stageRect?.left);
    const stageTop = Number(stageRect?.top);

    if (
        !Number.isFinite(cursorX)
        || !Number.isFinite(cursorY)
        || !Number.isFinite(windowX)
        || !Number.isFinite(windowY)
        || !Number.isFinite(stageLeft)
        || !Number.isFinite(stageTop)
    ) {
        return null;
    }

    return {
        x: cursorX - windowX - stageLeft,
        y: cursorY - windowY - stageTop,
    };
}
