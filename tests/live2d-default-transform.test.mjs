import test from "node:test";
import assert from "node:assert/strict";

import { resolveDefaultLive2DTransform } from "../echobot/app/web/features/live2d/default-transform.js";

test("resolveDefaultLive2DTransform keeps desktop framing head-first", () => {
    const stageHeight = 320;
    const baseHeight = 700;
    const result = resolveDefaultLive2DTransform({
        stageWidth: 420,
        stageHeight,
        baseWidth: 280,
        baseHeight,
        desktopTransparentStage: true,
    });
    const scaledHeight = baseHeight * result.scale;
    const topEdge = result.y - scaledHeight * 0.5;
    const bottomEdge = result.y + scaledHeight * 0.5;

    assert.equal(result.x, 210);
    assert.ok(result.scale > 1.3);
    assert.ok(topEdge >= 0, "desktop framing should keep the model top inside the stage");
    assert.ok(
        topEdge <= stageHeight * 0.12,
        "desktop framing should keep only a small top margin around the head",
    );
    assert.ok(
        bottomEdge > stageHeight,
        "desktop framing should let the lower body leave the stage first",
    );
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
