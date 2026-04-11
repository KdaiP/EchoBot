import test from "node:test";
import assert from "node:assert/strict";

import {
    hasUsableDesktopPassthroughBridge,
    isDesktopInteractiveRegion,
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
