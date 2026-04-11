import test from "node:test";
import assert from "node:assert/strict";

import {
    findDesktopMouseCaptureIntent,
    hasUsableDesktopPassthroughBridge,
    getDesktopResizeEdge,
    isDesktopInteractiveRegion,
    isDesktopMouseCaptureRegion,
} from "../echobot/app/web/features/live2d/desktop-passthrough.js";

test("hasUsableDesktopPassthroughBridge accepts bridge with setMousePassthrough", () => {
    assert.equal(
        hasUsableDesktopPassthroughBridge({
            setMousePassthrough() {},
        }),
        true,
    );
    assert.equal(
        hasUsableDesktopPassthroughBridge({
            getGlobalCursorState() {},
        }),
        false,
    );
    assert.equal(hasUsableDesktopPassthroughBridge({}), false);
    assert.equal(hasUsableDesktopPassthroughBridge(null), false);
});

test("isDesktopInteractiveRegion detects toolbar hotspot markers", () => {
    const toolbar = {
        closest(selector) {
            return selector === "[data-desktop-interactive='true']" ? this : null;
        },
    };

    assert.equal(isDesktopInteractiveRegion(toolbar), true);
    assert.equal(isDesktopInteractiveRegion(null), false);
});

test("isDesktopInteractiveRegion matches nested toolbar content", () => {
    const toolbar = {
        marker: true,
        closest(selector) {
            if (selector !== "[data-desktop-interactive='true']") {
                return null;
            }
            return this.marker ? this : null;
        },
    };
    const icon = {
        closest(selector) {
            return selector === "[data-desktop-interactive='true']" ? toolbar : null;
        },
    };

    assert.equal(isDesktopInteractiveRegion(icon), true);
});

test("getDesktopResizeEdge reads corner resize hotspot markers", () => {
    const hotspot = {
        getAttribute(name) {
            return name === "data-desktop-resize-edge" ? "top-left" : null;
        },
        closest(selector) {
            return selector === "[data-desktop-resize-edge]" ? this : null;
        },
    };

    assert.equal(getDesktopResizeEdge(hotspot), "top-left");
    assert.equal(getDesktopResizeEdge(null), "");
});

test("isDesktopMouseCaptureRegion accepts resize hotspots", () => {
    const hotspot = {
        getAttribute(name) {
            return name === "data-desktop-resize-edge" ? "bottom-right" : null;
        },
        closest(selector) {
            if (selector === "[data-desktop-resize-edge]") {
                return this;
            }
            return null;
        },
    };

    assert.equal(isDesktopMouseCaptureRegion(hotspot), true);
});

test("isDesktopMouseCaptureRegion accepts drag hotspot markers", () => {
    const hotspot = {
        closest(selector) {
            return selector === "[data-desktop-interactive='true']" ? this : null;
        },
    };

    assert.equal(isDesktopMouseCaptureRegion(hotspot), true);
});

test("findDesktopMouseCaptureIntent detects interactive hotspot from global cursor point", () => {
    const captureRegion = {
        getBoundingClientRect() {
            return {
                left: 260,
                top: 220,
                right: 314,
                bottom: 274,
            };
        },
        closest(selector) {
            return selector === "[data-desktop-interactive='true']" ? this : null;
        },
    };

    const intent = findDesktopMouseCaptureIntent(
        {
            cursorX: 380,
            cursorY: 350,
            windowBounds: {
                x: 100,
                y: 100,
            },
        },
        {
            querySelectorAll(selector) {
                return selector === "[data-desktop-interactive='true'], [data-desktop-resize-edge]"
                    ? [captureRegion]
                    : [];
            },
        },
        {
            left: 0,
            top: 0,
        },
    );

    assert.deepEqual(intent, {
        capture: true,
        resizeEdge: "",
    });
});
