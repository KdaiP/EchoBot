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

export function isCursorInsideDesktopWindow(cursorState) {
    const cursorX = Number(cursorState?.cursorX);
    const cursorY = Number(cursorState?.cursorY);
    const windowX = Number(cursorState?.windowBounds?.x);
    const windowY = Number(cursorState?.windowBounds?.y);
    const windowWidth = Number(cursorState?.windowBounds?.width);
    const windowHeight = Number(cursorState?.windowBounds?.height);

    if (
        !Number.isFinite(cursorX)
        || !Number.isFinite(cursorY)
        || !Number.isFinite(windowX)
        || !Number.isFinite(windowY)
        || !Number.isFinite(windowWidth)
        || !Number.isFinite(windowHeight)
    ) {
        return false;
    }

    return (
        cursorX >= windowX
        && cursorX <= windowX + windowWidth
        && cursorY >= windowY
        && cursorY <= windowY + windowHeight
    );
}

export function findDesktopMouseCaptureIntent(cursorState, root, rootRect) {
    if (!root || typeof root.querySelectorAll !== "function") {
        return {
            capture: false,
            resizeEdge: "",
        };
    }

    const localPoint = resolveLocalCursorPoint(cursorState, rootRect);
    if (!localPoint) {
        return {
            capture: false,
            resizeEdge: "",
        };
    }

    const hotspots = root.querySelectorAll("[data-desktop-interactive='true'], [data-desktop-resize-edge]");
    for (const hotspot of hotspots) {
        if (!hotspot || typeof hotspot.getBoundingClientRect !== "function") {
            continue;
        }

        const rect = hotspot.getBoundingClientRect();
        if (!isPointInsideRect(localPoint, rect, rootRect)) {
            continue;
        }

        return {
            capture: true,
            resizeEdge: getDesktopResizeEdge(hotspot),
        };
    }

    return {
        capture: false,
        resizeEdge: "",
    };
}

function resolveLocalCursorPoint(cursorState, rootRect) {
    const cursorX = Number(cursorState?.cursorX);
    const cursorY = Number(cursorState?.cursorY);
    const windowX = Number(cursorState?.windowBounds?.x);
    const windowY = Number(cursorState?.windowBounds?.y);
    const rootLeft = Number(rootRect?.left);
    const rootTop = Number(rootRect?.top);

    if (
        !Number.isFinite(cursorX)
        || !Number.isFinite(cursorY)
        || !Number.isFinite(windowX)
        || !Number.isFinite(windowY)
        || !Number.isFinite(rootLeft)
        || !Number.isFinite(rootTop)
    ) {
        return null;
    }

    return {
        x: cursorX - windowX - rootLeft,
        y: cursorY - windowY - rootTop,
    };
}

function isPointInsideRect(point, rect, rootRect) {
    const left = Number(rect?.left) - Number(rootRect?.left || 0);
    const right = Number(rect?.right) - Number(rootRect?.left || 0);
    const top = Number(rect?.top) - Number(rootRect?.top || 0);
    const bottom = Number(rect?.bottom) - Number(rootRect?.top || 0);

    if (
        !Number.isFinite(left)
        || !Number.isFinite(right)
        || !Number.isFinite(top)
        || !Number.isFinite(bottom)
    ) {
        return false;
    }

    return point.x >= left && point.x <= right && point.y >= top && point.y <= bottom;
}
