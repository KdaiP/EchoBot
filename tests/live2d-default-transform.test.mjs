import test from "node:test";
import assert from "node:assert/strict";

import { resolveDefaultLive2DTransform } from "../echobot/app/web/features/live2d/default-transform.js";

test("resolveDefaultLive2DTransform keeps desktop framing head-first", () => {
    const result = resolveDefaultLive2DTransform({
        stageWidth: 420,
        stageHeight: 320,
        baseWidth: 280,
        baseHeight: 700,
        desktopTransparentStage: true,
    });

    assert.equal(result.x, 210);
    assert.ok(result.scale > 1.3);
    assert.ok(result.y < 200);
});

test("resolveDefaultLive2DTransform keeps web framing fit-based", () => {
    const result = resolveDefaultLive2DTransform({
        stageWidth: 420,
        stageHeight: 320,
        baseWidth: 280,
        baseHeight: 700,
        desktopTransparentStage: false,
    });

    assert.equal(result.x, 210);
    assert.equal(result.scale, 0.3749);
    assert.equal(result.y, 198.4);
});
